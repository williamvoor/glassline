"""Macros: what the model writes. Each one reads a stored result and builds components (ADR 0001).

Data comes only from the ResultStore set for the current request with `macro_context`. The
SDK's `MacroExpander` logs a failing macro and leaves the node unexpanded, so every macro
records its exception in the context's error list before re-raising; the UI pipeline turns
those errors into feedback for the model.
"""

from __future__ import annotations

import contextlib
import functools
import re
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from a2ui.builder.v0_9.catalogs.basic import Card, Column, Text
from a2ui.transformers.macros import MacroExpander, macro

from explorer_agent.catalog import DataGrid, SqlDetails, VegaChart
from explorer_agent.results import INTEGER_TYPES, ResultStore, StoredResult

PASSTHROUGH_COMPONENTS = ["Column", "Row", "Card", "Text", "Divider", "Tabs"]
TABLE_ROW_CAP = 200
MAX_COLOR_CATEGORIES = 8
FORMATS = ("auto", "currency", "currency_m", "int", "decimal", "pct", "fraction_pct", "text", "date")
DEFAULT_CURRENCY_SYMBOL = "$"
OTHER = "Other"


class MacroError(ValueError):
    """A macro call the model can fix: its message goes back to the model."""


@dataclass
class MacroContext:
    store: ResultStore
    table_row_cap: int = TABLE_ROW_CAP
    max_color_categories: int = MAX_COLOR_CATEGORIES
    currency_symbol: str = DEFAULT_CURRENCY_SYMBOL  # from the dataset's [currency] table
    errors: list[str] = field(default_factory=list)


_context: ContextVar[MacroContext | None] = ContextVar("explorer_macro_context", default=None)


@contextlib.contextmanager
def macro_context(store: ResultStore, **settings: Any) -> Iterator[MacroContext]:
    ctx = MacroContext(store, **settings)
    token = _context.set(ctx)
    try:
        yield ctx
    finally:
        _context.reset(token)


def _ctx() -> MacroContext:
    ctx = _context.get()
    if ctx is None:
        raise RuntimeError("macro called outside macro_context()")
    return ctx


def _captured(func: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            ctx = _context.get()
            if ctx is not None:
                ctx.errors.append(f"{func.__name__}: {e}")
            raise

    return wrapper


# --- lookups -------------------------------------------------------------------------------


def _result(result_id: str) -> StoredResult:
    store = _ctx().store
    result = store.get(result_id)
    if result is None:
        valid = ", ".join(store.ids()) or "none yet: call run_sql first"
        raise MacroError(f"unknown result_id {result_id!r}; valid IDs: {valid}")
    return result


def _column(result: StoredResult, column: str) -> str:
    if column not in result.columns:
        raise MacroError(
            f"{result.id} has no column {column!r}; its columns are: {', '.join(result.columns)}"
        )
    return column


def _numeric_column(result: StoredResult, column: str, role: str) -> str:
    _column(result, column)
    if not result.is_numeric(column):
        raise MacroError(
            f"{role} column {column!r} of {result.id} is {result.column_type(column)}, not a number"
        )
    return column


def _format_name(fmt: str) -> str:
    if fmt not in FORMATS:
        raise MacroError(f"unknown format {fmt!r}; use one of: {', '.join(FORMATS)}")
    return fmt


def column_label(column: str) -> str:
    """Readable label for a column: `total_value` → "Total value". SQL aliases with spaces or
    capitals (`AS "Value (AUD)"`) are the model's own wording and are kept as they are."""
    if column != column.lower() or " " in column:
        return column
    text = column.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _sql_details(result: StoredResult) -> SqlDetails:
    return SqlDetails(sql=result.sql, summary=result.purpose)


# --- value formatting ----------------------------------------------------------------------


def _number(value: Any, fmt: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MacroError(f"format {fmt!r} needs a number, got {value!r}")
    return value


def format_value(
    value: Any, fmt: str = "auto", column_type: str = "", currency_symbol: str = DEFAULT_CURRENCY_SYMBOL
) -> str:
    """Formats one stored value as display text."""
    sym = currency_symbol
    if value is None:
        return "—"
    match _format_name(fmt):
        case "auto":
            if isinstance(value, bool):
                return str(value).lower()
            if isinstance(value, int) or (isinstance(value, float) and column_type in INTEGER_TYPES):
                return f"{round(value):,}"
            if isinstance(value, float):
                return f"{value:,.2f}"
            return str(value)
        case "currency":
            n = _number(value, fmt)
            return f"-{sym}{-n:,.0f}" if n < 0 else f"{sym}{n:,.0f}"
        case "currency_m":
            n = _number(value, fmt) / 1e6
            return f"-{sym}{-n:,.1f}M" if n < 0 else f"{sym}{n:,.1f}M"
        case "int":
            return f"{round(_number(value, fmt)):,}"
        case "decimal":
            return f"{_number(value, fmt):,.2f}"
        case "pct":
            return f"{_number(value, fmt):.1f}%"
        case "fraction_pct":
            return f"{_number(value, fmt) * 100:.1f}%"
        case "date":
            return str(value)[:10]
        case _:  # "text"
            return str(value)


def _format(result: StoredResult, value: Any, column: str, fmt: str) -> str:
    return format_value(value, fmt, result.column_type(column), _ctx().currency_symbol)


def _cell(result: StoredResult, row: dict[str, Any], column: str, fmt: str) -> str:
    return _format(result, row.get(column), column, fmt)


# --- charts --------------------------------------------------------------------------------

# Axis format (d3), scale applied to the values, and axis title suffix. The currency symbol
# goes in the title ("{sym}" is replaced), not the d3 format, whose "$" is locale-bound.
_AXIS: dict[str, tuple[str, float, str]] = {
    "currency": (",.0f", 1.0, " ({sym})"),
    "currency_m": (",.1f", 1e-6, " ({sym}M)"),
    "int": (",d", 1.0, ""),
    "decimal": (",.2f", 1.0, ""),
    "pct": (".1f", 1.0, " (%)"),
    "fraction_pct": (".1%", 1.0, ""),
}


def _axis(result: StoredResult, y: str, fmt: str) -> tuple[str, float, str]:
    if _format_name(fmt) == "auto":
        return (",d", 1.0, "") if result.column_type(y) in INTEGER_TYPES else (",.2~f", 1.0, "")
    if fmt not in _AXIS:
        raise MacroError(f"format {fmt!r} does not apply to a chart value; use one of: {', '.join(_AXIS)}")
    axis_format, scale, suffix = _AXIS[fmt]
    return axis_format, scale, suffix.replace("{sym}", _ctx().currency_symbol)


def _chart_values(
    result: StoredResult, x: str, y: str, color: str, scale: float
) -> tuple[list[dict[str, Any]], list[str]]:
    """Chart rows, with colour groups beyond the top N (by total) folded into "Other"."""
    if not color:
        rows = [{x: r.get(x), y: _scaled(r.get(y), scale)} for r in result.rows]
        return rows, []
    totals: dict[Any, float] = {}
    for r in result.rows:
        totals[r.get(color)] = totals.get(r.get(color), 0.0) + (r.get(y) or 0)
    ranked = sorted(totals, key=lambda k: (-totals[k], str(k)))
    keep = set(ranked[: _ctx().max_color_categories])
    folded: dict[tuple[Any, Any], float] = {}
    order: list[tuple[Any, Any]] = []
    for r in result.rows:
        group = r.get(color) if r.get(color) in keep else OTHER
        key = (r.get(x), group)
        if key not in folded:
            folded[key] = 0.0
            order.append(key)
        folded[key] += r.get(y) or 0
    rows = [{x: k[0], color: k[1], y: _scaled(folded[k], scale)} for k in order]
    domain = [str(k) for k in ranked if k in keep]
    if len(ranked) > len(keep):
        domain.append(OTHER)
    return rows, domain


def _scaled(value: Any, scale: float) -> Any:
    return value * scale if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _chart(
    result: StoredResult,
    *,
    mark: str,
    x: str,
    y: str,
    title: str,
    color: str,
    fmt: str,
    sort_by_value: bool,
    horizontal: bool = False,
) -> Column:
    _column(result, x)
    _numeric_column(result, y, "y")
    if color:
        _column(result, color)
    axis_format, scale, suffix = _axis(result, y, fmt)
    values, domain = _chart_values(result, x, y, color, scale)

    temporal = result.is_temporal(x)
    x_enc: dict[str, Any] = {"field": x, "type": "temporal" if temporal else "ordinal", "title": column_label(x)}
    if not temporal:
        x_enc["sort"] = "-y" if sort_by_value and not horizontal else ("-x" if sort_by_value else None)
    y_enc: dict[str, Any] = {
        "field": y,
        "type": "quantitative",
        "title": column_label(y) + suffix,
        "axis": {"format": axis_format},
    }
    if mark == "bar" and not temporal:
        x_enc["type"] = "nominal"
    encoding: dict[str, Any] = {"y": x_enc, "x": y_enc} if horizontal else {"x": x_enc, "y": y_enc}
    if color:
        encoding["color"] = {"field": color, "type": "nominal", "title": column_label(color), "sort": domain}
    encoding["tooltip"] = [
        {"field": x, "type": x_enc["type"]},
        *([{"field": color, "type": "nominal"}] if color else []),
        {"field": y, "type": "quantitative", "format": axis_format},
    ]
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": values},
        "mark": {"type": mark, "point": True} if mark == "line" else {"type": mark},
        "encoding": encoding,
    }
    return Column(children=[Text(text=title, variant="h4"), VegaChart(spec=spec), _sql_details(result)])


@macro
@_captured
def BarChart(
    result_id: str,
    x: str,
    y: str,
    title: str,
    color: str = "",
    horizontal: bool = False,
    format: str = "auto",
) -> Column:
    """Bar chart of a stored query result, followed by its SQL.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        x: Column for the categories (or dates).
        y: Numeric column for bar length.
        title: Chart title, plain text.
        color: Optional column to stack by; the top 8 groups are kept, the rest become "Other".
        horizontal: Draw bars horizontally (good for long category names).
        format: Value format: auto, currency, currency_m, int, decimal, pct, fraction_pct. pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%).
    """
    return _chart(
        _result(result_id), mark="bar", x=x, y=y, title=title, color=color, fmt=format,
        sort_by_value=True, horizontal=horizontal,
    )


@macro
@_captured
def LineChart(
    result_id: str,
    x: str,
    y: str,
    title: str,
    color: str = "",
    format: str = "auto",
) -> Column:
    """Line chart of a stored query result in its row order (or by date), followed by its SQL.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        x: Column for the horizontal axis; dates are plotted on a time scale.
        y: Numeric column for the vertical axis.
        title: Chart title, plain text.
        color: Optional column with one line per group; the top 8 are kept, the rest become "Other".
        format: Value format: auto, currency, currency_m, int, decimal, pct, fraction_pct. pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%).
    """
    return _chart(
        _result(result_id), mark="line", x=x, y=y, title=title, color=color, fmt=format,
        sort_by_value=False,
    )


def _parse_formats(result: StoredResult, spec: str) -> dict[str, str]:
    formats: dict[str, str] = {}
    for pair in filter(None, (p.strip() for p in spec.split(","))):
        column, sep, fmt = pair.rpartition(":")
        if not sep:
            raise MacroError(f"format entry {pair!r} must look like column:format")
        formats[_column(result, column.strip())] = _format_name(fmt.strip())
    return formats


@macro
@_captured
def DataTable(result_id: str, title: str, columns: str = "", format: str = "") -> Column:
    """Table of a stored query result, followed by its SQL.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        title: Table title, plain text.
        columns: Optional comma-separated columns to show, in order (default: all).
        format: Optional comma-separated column:format pairs, e.g. "value:currency_m,share:fraction_pct". pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%).
    """
    result = _result(result_id)
    shown = [_column(result, c.strip()) for c in columns.split(",") if c.strip()] or result.columns
    formats = _parse_formats(result, format)
    cap = _ctx().table_row_cap
    rows = result.rows[:cap]
    grid_columns = [
        {"key": c, "label": column_label(c), **({"align": "end"} if result.is_numeric(c) else {})}
        for c in shown
    ]
    cells = [[_cell(result, r, c, formats.get(c, "auto")) for c in shown] for r in rows]
    notes = []
    if result.row_count > cap:
        notes.append(f"Showing {cap:,} of {result.row_count:,} rows")
    if result.truncated:
        notes.append(f"query truncated at {result.row_count:,} rows")
    caption = "; ".join(notes) or None
    return Column(
        children=[
            Text(text=title, variant="h4"),
            DataGrid(columns=grid_columns, rows=cells, caption=caption),
            _sql_details(result),
        ]
    )


@macro
@_captured
def Metric(result_id: str, column: str, label: str, row: int = 0, format: str = "auto") -> Card:
    """One headline number from a stored query result, with its label and SQL.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        column: Column holding the value.
        label: Short label shown above the value, plain text.
        row: Row index (0 = first row).
        format: Value format: auto, currency, currency_m, int, decimal, pct, fraction_pct, text, date. pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%).
    """
    result = _result(result_id)
    _column(result, column)
    value = _row(result, row)[column]
    return Card(
        child=Column(
            children=[
                Text(text=label, variant="caption"),
                Text(text=_format(result, value, column, format), variant="h2"),
                _sql_details(result),
            ]
        )
    )


def _row(result: StoredResult, index: int) -> dict[str, Any]:
    if not 0 <= index < result.row_count:
        raise MacroError(f"{result.id} has {result.row_count} rows; row {index} does not exist")
    return result.rows[index]


_PLACEHOLDER = re.compile(r"\{(r\d+)\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)(?:\|([a-z_]+))?\}")
# Anything placeholder-shaped, valid or not, so its digits are not counted as bare numbers.
_PLACEHOLDER_SHAPE = re.compile(r"\{r\d+\[\d+\]\.[^}]*\}")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def escape_markdown(text: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _fill(template: str, on_error: Callable[[Exception], None] | None = None) -> str:
    def fill(m: re.Match[str]) -> str:
        try:
            result = _result(m.group(1))
            column = _column(result, m.group(3))
            value = _row(result, int(m.group(2)))[column]
            return escape_markdown(_format(result, value, column, m.group(4) or "auto"))
        except MacroError as e:
            if on_error is None:
                raise
            on_error(e)
            return m.group(0)

    return _PLACEHOLDER.sub(fill, template)


@macro
@_captured
def Cite(template: str) -> Text:
    """A sentence with numbers filled in from stored results, so they are never retyped.

    Args:
        template: Markdown text with placeholders like {r1[0].value|currency_m}: result ID, row index, column, optional format (the Metric formats; pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%)).
    """
    return Text(text=_fill(template))


_BARE_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


@dataclass
class FilledProse:
    text: str
    bare_numbers: list[str]  # numbers the model typed itself; logged, not rejected (ADR 0001)


def fill_prose(prose: str) -> FilledProse:
    """Fills `Cite` placeholders in the Turn's prose outside `<a2ui>`.

    Placeholder errors are recorded in the context's error list, like macro errors, so they go
    back to the model; the placeholder is left as written.
    """
    ctx = _ctx()
    text = _fill(prose, on_error=lambda e: ctx.errors.append(f"prose: {e}"))
    bare = _BARE_NUMBER.findall(_PLACEHOLDER_SHAPE.sub("", prose))
    return FilledProse(text=text, bare_numbers=bare)


MACROS = [BarChart, LineChart, DataTable, Metric, Cite]


def macro_expander() -> MacroExpander:
    return MacroExpander(MACROS, passthrough_components=PASSTHROUGH_COMPONENTS)
