"""Readers: each yields a header and, on demand (twice: infer, then COPY), its data rows.

CSV and Excel cells are trimmed; a row whose cells are all empty is skipped. Parquet keeps its
own schema (T3, T10).
"""

from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dataload.manifest import ManifestError, Source

SNIFF_BYTES = 64 * 1024
DELIMITERS = ",;\t|"
Cell = str | int | float | bool | dt.date | dt.datetime | Decimal | None


class ReadError(ValueError):
    """A file that can't be read as the manifest describes."""


@dataclass
class Table:
    """One table to load: where it came from, its header, and a fresh row iterator per call."""

    label: str  # for messages and the table comment: "<file>" or "<file>, sheet <name>"
    default_name: str  # the file stem or sheet name, before normalising
    headers: list[object]
    rows: Callable[[], Iterator[list[Cell]]]
    arrow_types: list[str] | None = None  # Parquet: the Postgres type per column, from its schema


def _clean(cell: object) -> Cell:
    if isinstance(cell, str):
        return cell.strip()
    return cell  # type: ignore[return-value]


def _empty(cell: Cell) -> bool:
    return cell is None or cell == ""


def _find_header(rows: Iterator[list[object]], source: Source, label: str) -> tuple[list[object], int]:
    """The header row and its 1-based number. Rows above it are skipped."""
    for n, row in enumerate(rows, 1):
        if source.header_match is not None:
            first = _clean(row[0]) if row else None
            if isinstance(first, str) and first.lower() == source.header_match.strip().lower():
                return list(row), n
        elif n == (source.header_row or 1):
            return list(row), n
    if source.header_match is not None:
        raise ReadError(f"{label}: no row starts with {source.header_match!r}")
    raise ReadError(f"{label}: has no row {source.header_row or 1} for the header")


def _trim_header(headers: list[object]) -> list[object]:
    """Drop empty header cells at the right edge (a sheet's used range often runs past the data)."""
    end = len(headers)
    while end and _empty(_clean(headers[end - 1])):
        end -= 1
    return headers[:end]


def _shaped(raw: Iterator[list[object]], width: int, label: str, first_row: int) -> Iterator[list[Cell]]:
    for n, row in enumerate(raw, first_row):
        cells = [_clean(c) for c in row]
        if any(not _empty(c) for c in cells[width:]):
            raise ReadError(f"{label}: row {n} has more cells than the header")
        cells = (cells + [None] * width)[:width]
        if all(_empty(c) for c in cells):
            continue
        yield cells


# --- CSV -----------------------------------------------------------------------------------------


def _csv_tables(path: Path, source: Source) -> list[Table]:
    label = path.name
    try:
        with path.open(encoding=source.encoding, newline="") as f:
            sample = f.read(SNIFF_BYTES)
    except (OSError, UnicodeDecodeError) as e:
        raise ReadError(f"{label}: cannot read it as {source.encoding}: {e}") from None
    delimiter = source.delimiter
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
        except csv.Error:
            delimiter = ","

    def raw() -> Iterator[list[object]]:
        with path.open(encoding=source.encoding, newline="") as f:
            yield from csv.reader(f, delimiter=delimiter)

    rows = raw()
    headers, at = _find_header(rows, source, label)
    rows.close()
    headers = _trim_header(headers)

    def data() -> Iterator[list[Cell]]:
        it = raw()
        for _ in range(at):
            next(it)
        yield from _shaped(it, len(headers), label, at + 1)

    return [Table(label, path.stem, headers, data)]


# --- Excel ---------------------------------------------------------------------------------------


def _excel_tables(path: Path, source: Source) -> list[Table]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as e:  # openpyxl raises several types for a bad file
        raise ReadError(f"{path.name}: not a readable .xlsx workbook ({type(e).__name__})") from None
    try:
        if source.sheet == "*":
            names = list(wb.sheetnames)
        elif source.sheet is not None:
            if source.sheet not in wb.sheetnames:
                raise ReadError(f"{path.name}: no sheet named {source.sheet!r} (sheets: {', '.join(wb.sheetnames)})")
            names = [source.sheet]
        else:
            names = [wb.sheetnames[0]]
    finally:
        wb.close()
    return [_sheet_table(path, source, name, many=source.sheet == "*") for name in names]


def _sheet_table(path: Path, source: Source, sheet: str, many: bool) -> Table:
    from openpyxl import load_workbook

    label = f"{path.name}, sheet {sheet}"

    def raw() -> Iterator[list[object]]:
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            for row in wb[sheet].iter_rows(values_only=True):
                yield list(row)
        finally:
            wb.close()

    rows = raw()
    headers, at = _find_header(rows, source, label)
    rows.close()
    headers = _trim_header(headers)

    def data() -> Iterator[list[Cell]]:
        it = raw()
        for _ in range(at):
            next(it)
        yield from _shaped(it, len(headers), label, at + 1)

    return Table(label, sheet if many or source.sheet else path.stem, headers, data)


# --- Parquet -------------------------------------------------------------------------------------


def _arrow_type(t) -> str | None:  # noqa: ANN001  (a pyarrow DataType)
    import pyarrow.types as pt

    if pt.is_boolean(t):
        return "boolean"
    if pt.is_integer(t):
        return "numeric" if pt.is_uint64(t) else "bigint"
    if pt.is_floating(t):
        return "double precision"
    if pt.is_decimal(t):
        return "numeric"
    if pt.is_string(t) or pt.is_large_string(t):
        return "text"
    if pt.is_date(t):
        return "date"
    if pt.is_timestamp(t):
        return "timestamptz" if t.tz else "timestamp"
    return None


def _parquet_tables(path: Path, source: Source) -> list[Table]:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise ReadError(f"{path.name}: Parquet needs pyarrow; install dataload[parquet]") from None
    try:
        pf = pq.ParquetFile(path)
    except Exception as e:  # pyarrow raises ArrowInvalid and OSError for a bad file
        raise ReadError(f"{path.name}: not a readable Parquet file ({type(e).__name__})") from None
    schema = pf.schema_arrow
    types: list[str] = []
    for f in schema:
        mapped = _arrow_type(f.type)
        if mapped is None:
            raise ReadError(f"{path.name}: column {f.name!r} has type {f.type}, which dataload can't load")
        types.append(mapped)

    def data() -> Iterator[list[Cell]]:
        for batch in pq.ParquetFile(path).iter_batches():
            columns = [c.to_pylist() for c in batch.columns]
            for row in zip(*columns, strict=True):
                yield list(row)

    return [Table(path.name, path.stem, list(schema.names), data, types)]


READERS: dict[str, Callable[[Path, Source], list[Table]]] = {
    ".csv": _csv_tables, ".tsv": _csv_tables, ".txt": _csv_tables,
    ".xlsx": _excel_tables, ".xlsm": _excel_tables,
    ".parquet": _parquet_tables, ".pq": _parquet_tables,
}


def tables_for(source: Source, base_dir: Path) -> list[Table]:
    """Every table a source names: one per matching file (and per sheet with `sheet = "*"`)."""
    if Path(source.file).is_absolute():  # a caller's override (glob takes relative patterns only)
        paths = [Path(source.file)] if Path(source.file).is_file() else []
    else:
        paths = sorted(p for p in base_dir.glob(source.file) if p.is_file())
    if not paths:
        raise ManifestError(f"no file matches {source.file}")
    out: list[Table] = []
    for path in paths:
        reader = READERS.get(path.suffix.lower())
        if reader is None:
            raise ReadError(f"{path.name}: dataload reads {', '.join(sorted(READERS))} files")
        if reader is not _excel_tables and source.sheet is not None:
            raise ManifestError(f"{path.name}: sheet applies to Excel files only")
        if reader is _parquet_tables and (source.header_row or source.header_match):
            raise ManifestError(f"{path.name}: Parquet has no header row to choose")
        out += reader(path, source)
    return out
