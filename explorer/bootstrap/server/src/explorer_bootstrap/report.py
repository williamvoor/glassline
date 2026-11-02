"""Phase 1 report: comment coverage, planner statistics and Dataset context size (SPEC.md)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from psycopg_pool import AsyncConnectionPool

from explorer_agent.config import Dataset
from explorer_agent.prompt import dataset_context
from explorer_agent.results import to_json_safe
from explorer_agent.schema import (
    SchemaCatalog,
    TableInfo,
    apply_overlay,
    overlay_problems,
    render_schema_section,
)
from explorer_agent.sql import SqlRunner

from explorer_bootstrap.stats import (
    column_ranges,
    column_stats,
    commentable,
    distinct_estimate,
    is_range_type,
)

REPORT_VERSION = 1
CONTEXT_TARGET = 20_000  # explorer/SPEC.md §6, layer 3


@dataclass(frozen=True)
class ColumnReport:
    name: str
    type: str
    nullable: bool
    comment: str | None
    comment_source: str | None  # overlay | database | None (no comment)
    database_comment: str | None  # the database's comment, only when the overlay replaced it
    null_frac: float | None
    distinct_estimate: int | None
    most_common: list[dict[str, Any]]
    min: Any
    max: Any
    range_status: str  # ok | skipped | error | n/a | off
    range_error: str | None


@dataclass(frozen=True)
class ObjectReport:
    schema: str
    name: str
    kind: str
    comment: str | None
    comment_source: str | None
    database_comment: str | None
    est_rows: int | None
    analysed: bool | None  # None for views: no statistics apply
    can_comment: bool
    columns: list[ColumnReport]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class Summary:
    objects: int
    columns: int
    objects_with_comment: int
    columns_with_comment: int
    coverage_pct: float
    can_comment: bool
    overlay_comments: int
    overlay_replacements: int
    context_chars: int
    context_target: int
    context_scope: str  # full | schema_only


@dataclass(frozen=True)
class Report:
    report_version: int
    database: dict[str, str]  # host and name only
    dataset: str | None
    comment_overlay: str | None  # the overlay's file name
    schemas: list[str]
    empty_schemas: list[str]
    summary: Summary
    objects: list[ObjectReport]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def missing_comments(self) -> list[str]:
        missing = []
        for o in self.objects:
            if not o.comment:
                missing.append(o.qualified_name)
            missing.extend(f"{o.qualified_name}.{c.name}" for c in o.columns if not c.comment)
        return missing


_VIEW_KINDS = {"view"}


class OverlayDriftError(ValueError):
    """An overlay entry names a table or column that isn't in the chosen schemas."""


def _source(final: str | None, raw: str | None, overlaid: bool) -> tuple[str | None, str | None]:
    """(comment_source, database_comment) for one comment, given whether the overlay names it."""
    if overlaid:
        return "overlay", raw
    return ("database" if final else None), None


def _json_value(value: Any) -> Any:
    """JSON-safe, with NaN and ±Infinity as null: they aren't valid JSON."""
    v = to_json_safe(value)
    return None if isinstance(v, float) and not math.isfinite(v) else v


def _analysed(table: TableInfo, has_stats: bool) -> bool | None:
    if table.kind in _VIEW_KINDS:
        return None
    return has_stats or table.est_rows is not None


async def build_report(
    pool: AsyncConnectionPool,
    schemas: Sequence[str],
    *,
    database: dict[str, str],
    dataset: Dataset | None = None,
    timeout_ms: int = 5000,
    minmax: bool = True,
) -> Report:
    schemas = sorted(set(schemas))
    raw = await SchemaCatalog(pool, schemas).objects()
    overlay = dataset.comment_overlay if dataset is not None else None
    problems = overlay_problems(raw, overlay) if raw else []  # nothing visible: the CLI says so instead
    if problems:
        raise OverlayDriftError(f"comment overlay {overlay.path}: {'; '.join(problems)}")
    tables = apply_overlay(raw, overlay)
    raw_by_name = {t.qualified_name: t for t in raw}
    stats = await column_stats(pool, schemas)
    owners = await commentable(pool, schemas)
    runner = SqlRunner(pool, schemas, timeout_ms, row_cap=1)

    objects = []
    for t in tables:
        entry = overlay.entries.get(t.qualified_name) if overlay is not None else None
        raw_t = raw_by_name[t.qualified_name]
        raw_cols = {c.name: c.comment for c in raw_t.columns}
        obj_source, obj_db = _source(t.comment, raw_t.comment, entry is not None and entry.comment is not None)
        range_cols = [c.name for c in t.columns if is_range_type(c.type)]
        outcome = await column_ranges(runner, t.schema, t.name, range_cols) if minmax and range_cols else None
        has_stats = any((t.schema, t.name, c.name) in stats for c in t.columns)
        columns = []
        for c in t.columns:
            s = stats.get((t.schema, t.name, c.name))
            col_source, col_db = _source(c.comment, raw_cols[c.name], entry is not None and c.name in entry.columns)
            if not is_range_type(c.type):
                status, lo, hi, err = "n/a", None, None, None
            elif not minmax:
                status, lo, hi, err = "off", None, None, None
            else:
                assert outcome is not None
                lo, hi = outcome.values.get(c.name, (None, None))
                status, err = outcome.status, outcome.error
            columns.append(
                ColumnReport(
                    name=c.name,
                    type=c.type,
                    nullable=c.nullable,
                    comment=c.comment,
                    comment_source=col_source,
                    database_comment=col_db,
                    null_frac=s.null_frac if s else None,
                    distinct_estimate=distinct_estimate(s.n_distinct, t.est_rows) if s else None,
                    most_common=[{"value": v, "freq": f} for v, f in s.most_common] if s else [],
                    min=_json_value(lo),
                    max=_json_value(hi),
                    range_status=status,
                    range_error=err,
                )
            )
        objects.append(
            ObjectReport(
                schema=t.schema,
                name=t.name,
                kind=t.kind,
                comment=t.comment,
                comment_source=obj_source,
                database_comment=obj_db,
                est_rows=t.est_rows if t.kind not in _VIEW_KINDS else None,
                analysed=_analysed(t, has_stats),
                can_comment=owners.get((t.schema, t.name), False),
                columns=columns,
            )
        )

    section = render_schema_section(tables)
    if dataset is not None:
        context_chars, scope = len(dataset_context(dataset, section)), "full"
    else:
        context_chars, scope = len(section), "schema_only"
    n_cols = sum(len(o.columns) for o in objects)
    obj_commented = sum(1 for o in objects if o.comment)
    col_commented = sum(1 for o in objects for c in o.columns if c.comment)
    total = len(objects) + n_cols
    present = {o.schema for o in objects}
    sources = [*objects, *(c for o in objects for c in o.columns)]
    from_overlay = [x for x in sources if x.comment_source == "overlay"]
    return Report(
        report_version=REPORT_VERSION,
        database=dict(database),
        dataset=dataset.name if dataset is not None else None,
        comment_overlay=overlay.path.name if overlay is not None else None,
        schemas=list(schemas),
        empty_schemas=[s for s in schemas if s not in present],
        summary=Summary(
            objects=len(objects),
            columns=n_cols,
            objects_with_comment=obj_commented,
            columns_with_comment=col_commented,
            coverage_pct=round(100 * (obj_commented + col_commented) / total, 1) if total else 100.0,
            can_comment=bool(objects) and all(o.can_comment for o in objects),
            overlay_comments=len(from_overlay),
            overlay_replacements=sum(1 for x in from_overlay if x.database_comment is not None),
            context_chars=context_chars,
            context_target=CONTEXT_TARGET,
            context_scope=scope,
        ),
        objects=objects,
    )


def render_text(report: Report) -> str:
    s = report.summary
    db = report.database
    lines = [
        f"Database: {db.get('name')} on {db.get('host')}",
        f"Schemas: {', '.join(report.schemas)}" + (f" (dataset {report.dataset})" if report.dataset else ""),
        f"Objects: {s.objects}, columns: {s.columns}",
        f"Comments: {s.objects_with_comment}/{s.objects} objects, {s.columns_with_comment}/{s.columns} columns"
        f" ({s.coverage_pct}%)",
        *(
            [f"Comment overlay {report.comment_overlay}: {s.overlay_comments} comments"
             f" ({s.overlay_replacements} replace a database comment)"]
            if report.comment_overlay
            else []
        ),
        f"Login can add comments: {'yes' if s.can_comment else 'no (use a Comment overlay)'}",
        f"Context size: {s.context_chars:,} chars of {s.context_target:,} target"
        + ("" if s.context_scope == "full" else " (schema section only: no notes or glossary yet)")
        + (" — OVER TARGET" if s.context_chars > s.context_target else ""),
    ]
    if report.empty_schemas:
        lines.append(f"Schemas with no visible objects: {', '.join(report.empty_schemas)}")
    missing = report.missing_comments()
    if missing:
        lines.append("")
        lines.append(f"Missing comments ({len(missing)}):")
        lines.extend(f"  {m}" for m in missing)
    unanalysed = [o.qualified_name for o in report.objects if o.analysed is False]
    if unanalysed:
        lines.append("")
        lines.append("Never analysed (no planner statistics; run ANALYZE as the owner):")
        lines.extend(f"  {n}" for n in unanalysed)
    ranges = [
        f"  {o.qualified_name}.{c.name}: {c.range_status}" + (f" ({c.range_error})" if c.range_error else "")
        for o in report.objects
        for c in o.columns
        if c.range_status in ("skipped", "error")
    ]
    if ranges:
        lines.append("")
        lines.append("Min/max not available:")
        lines.extend(ranges)
    return "\n".join(lines) + "\n"
