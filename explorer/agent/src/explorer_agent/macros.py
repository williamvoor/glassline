"""Macros: what the model writes. Each one reads a stored result and builds components (ADR 0001).

Data comes only from the ResultStore set for the current request with `macro_context`. The
SDK's `MacroExpander` logs a failing macro and leaves the node unexpanded, so every macro
records its exception in the context's error list before re-raising; the UI pipeline turns
those errors into feedback for the model.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from a2ui.builder.v0_9 import Action, ActionEvent
from a2ui.builder.v0_9.catalogs.basic import Button, Card, Column, Text
from a2ui.transformers.macros import MacroExpander, macro

from explorer_agent.actions import FollowUpSpec, IdAllocator, SourceSpec
from explorer_agent.catalog import DataGrid, SqlDetails, VegaChart
from explorer_agent.drill import BINDABLE, DRILL_AS, DrillSpec, DrillSqlError, Rewritten, null_dropping_params, rewrite
from explorer_agent.results import INTEGER_TYPES, NUMERIC_TYPES, ResultStore, StoredResult
from explorer_agent.sql import GuardError, guard

log = logging.getLogger(__name__)

PASSTHROUGH_COMPONENTS = ["Column", "Row", "Card", "Text", "Divider", "Tabs"]
TABLE_ROW_CAP = 200
MAX_COLOR_CATEGORIES = 8
FORMATS = ("auto", "currency", "currency_m", "currency_compact", "int", "decimal", "pct", "fraction_pct", "text", "date")
DEFAULT_CURRENCY_SYMBOL = "$"
OTHER = "Other"
MAX_DRILLS_PER_SURFACE = 2
MAX_FOLLOW_UPS = 3  # per Surface, a fixed rule
FOLLOW_UP_REQUEST_MAX = 500
ROW_LABEL_MAX = 80
ROW_FIELD = "_row"  # hidden field on a clickable chart datum: its row index in the Result
LABEL_FIELD = "_label"  # hidden field on a clickable chart datum: its row label (SPEC §18)
MAX_TREEMAP_TILES = 12
NO_ROWS = "No rows"
PANEL_FAILED = "This panel couldn't load: {reason}"
PANEL_UNDRAWABLE = "This panel couldn't be drawn from its rows."
DRILL_ERROR_PREFIX = "drill: "


class MacroError(ValueError):
    """A macro call the model can fix: its message goes back to the model."""


class DrillError(MacroError):
    """A Drill query check (step 1) failed for the Visual with index `visual`."""

    def __init__(self, message: str, visual: int):
        super().__init__(message)
        self.visual = visual


@dataclass
class MacroContext:
    store: ResultStore
    table_row_cap: int = TABLE_ROW_CAP
    max_color_categories: int = MAX_COLOR_CATEGORIES
    currency_symbol: str = DEFAULT_CURRENCY_SYMBOL  # from the dataset's [currency] table
    errors: list[str] = field(default_factory=list)
    # Actions (SPEC §15). IDs are reserved while macros run; nothing is registered here.
    ids: IdAllocator = field(default_factory=IdAllocator)
    allowed_schemas: tuple[str, ...] = ()  # for the guard on Drill queries
    max_drills: int = MAX_DRILLS_PER_SURFACE
    ignore_drills: frozenset[int] = frozenset()  # Visual indices whose drill arguments are ignored
    visuals: int = 0  # Visuals built so far: the next Visual's index
    sources: list[SourceSpec] = field(default_factory=list)
    drills: list[DrillSpec] = field(default_factory=list)
    follow_ups: list[FollowUpSpec] = field(default_factory=list)
    drill_errors: set[int] = field(default_factory=set)  # Visual indices with a drill error
    drill_error_count: int = 0  # entries of `errors` that are drill errors
    # Boards (SPEC §18). In Board mode a zero-row Result or a failed query replaces its panel with
    # a Card instead of failing, and placeholders name queries (`{by_agency[0].value}`).
    board: bool = False
    failed: Mapping[str, str] = field(default_factory=dict)  # Board mode: query name -> SQLSTATE
    # Board mode: each Control's SQL cast and bound value, for Drill queries (SPEC §18 slice 2).
    controls: Mapping[str, tuple[str, Any]] = field(default_factory=dict)


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


class _PanelReplaced(Exception):  # noqa: N818 - control flow, not an error
    """Board mode: the macro's panel is replaced by `card` (no rows, or a failed query)."""

    def __init__(self, card: Any):
        super().__init__("panel replaced")
        self.card = card


def _captured(func: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except _PanelReplaced as e:
            return e.card
        except Exception as e:
            ctx = _context.get()
            if ctx is not None and ctx.board and isinstance(e, MacroError):
                # A data-dependent error (negative bars, ...): only this panel is lost (SPEC §18).
                # The message can quote a value, so only the macro's name is logged.
                log.warning("board panel could not be drawn: macro=%s", func.__name__)
                return _card([Text(text=PANEL_UNDRAWABLE)])
            if ctx is not None:
                if isinstance(e, DrillError):
                    ctx.errors.append(f"{func.__name__}: {DRILL_ERROR_PREFIX}{e}")
                    ctx.drill_errors.add(e.visual)
                    ctx.drill_error_count += 1
                else:
                    ctx.errors.append(f"{func.__name__}: {e}")
            raise

    return wrapper


# --- lookups -------------------------------------------------------------------------------


def _result(result_id: str) -> StoredResult:
    ctx = _ctx()
    store = ctx.store
    if ctx.board and result_id in ctx.failed:
        raise _PanelReplaced(_card([Text(text=PANEL_FAILED.format(reason=ctx.failed[result_id] or "query error"))]))
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
    value: Any,
    fmt: str = "auto",
    column_type: str = "",
    currency_symbol: str = DEFAULT_CURRENCY_SYMBOL,
    unit: tuple[float, str] | None = None,
) -> str:
    """Formats one stored value as display text. `unit` fixes `currency_compact`'s unit, so a
    table column or chart shares one; by default each value picks its own."""
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
        case "currency_compact":
            n = _number(value, fmt)
            return _compact(n, sym, unit or compact_unit(abs(n)))
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


def compact_unit(magnitude: float) -> tuple[float, str]:
    """Divisor and suffix for `currency_compact`, chosen after rounding: 999.96M shows as 1.0B."""
    if not math.isfinite(magnitude) or round(magnitude) < 1000:
        return 1.0, ""
    for divisor, suffix in ((1e3, "K"), (1e6, "M")):
        if round(magnitude / divisor, 1) < 1000:
            return divisor, suffix
    return 1e9, "B"


def _compact(n: float | int, sym: str, unit: tuple[float, str]) -> str:
    divisor, suffix = unit if math.isfinite(n) else (1.0, "")
    text = f"{abs(n) / divisor:,.1f}{suffix}" if suffix else f"{abs(n):,.0f}"
    negative = n < 0 and any(d in "123456789" for d in text)  # no "-$0.0B"
    return f"-{sym}{text}" if negative else f"{sym}{text}"


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _largest(values: Iterable[Any]) -> float:
    return max((abs(v) for v in values if _is_number(v)), default=0.0)


def _format(
    result: StoredResult, value: Any, column: str, fmt: str, unit: tuple[float, str] | None = None
) -> str:
    return format_value(value, fmt, result.column_type(column), _ctx().currency_symbol, unit)


def _cell(
    result: StoredResult, row: dict[str, Any], column: str, fmt: str, unit: tuple[float, str] | None = None
) -> str:
    return _format(result, row.get(column), column, fmt, unit)


# --- charts --------------------------------------------------------------------------------

# Axis format (d3), scale applied to the values, and axis title suffix. The currency symbol
# goes in the title ("{sym}" is replaced), not the d3 format, whose "$" is locale-bound.
_AXIS: dict[str, tuple[str, float, str]] = {
    "currency": (",.0f", 1.0, " ({sym})"),
    "currency_m": (",.1f", 1e-6, " ({sym}M)"),
    "currency_compact": (",.0f", 1.0, " ({sym})"),  # unit picked from the data in _chart
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
    result: StoredResult, x: str, y: str, color: str, scale: float, clickable: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    """Chart rows, with colour groups beyond the top N (by total) folded into "Other".

    With `clickable`, each datum built from exactly one row (not "Other", not past the table
    row cap) carries that row's index in `_row`.
    """
    cap = _ctx().table_row_cap
    if not color:
        rows = []
        for i, r in enumerate(result.rows):
            datum = {x: r.get(x), y: _scaled(r.get(y), scale)}
            if clickable and i < cap:
                datum[ROW_FIELD] = i
            rows.append(datum)
        return rows, []
    totals: dict[Any, float] = {}
    for r in result.rows:
        totals[r.get(color)] = totals.get(r.get(color), 0.0) + (r.get(y) or 0)
    ranked = sorted(totals, key=lambda k: (-totals[k], str(k)))
    keep = set(ranked[: _ctx().max_color_categories])
    folded: dict[tuple[Any, Any], float] = {}
    sources: dict[tuple[Any, Any], list[int]] = {}
    order: list[tuple[Any, Any]] = []
    for i, r in enumerate(result.rows):
        group = r.get(color) if r.get(color) in keep else OTHER
        key = (r.get(x), group)
        if key not in folded:
            folded[key] = 0.0
            sources[key] = []
            order.append(key)
        folded[key] += r.get(y) or 0
        sources[key].append(i)
    rows = []
    for k in order:
        datum = {x: k[0], color: k[1], y: _scaled(folded[k], scale)}
        (first, *more) = sources[k]
        if clickable and not more and first < cap and not (k[1] == OTHER and result.rows[first].get(color) not in keep):
            datum[ROW_FIELD] = first
        rows.append(datum)
    domain = [str(k) for k in ranked if k in keep]
    if len(ranked) > len(keep):
        domain.append(OTHER)
    return rows, domain


def _field(column: str) -> str:
    """A Vega-Lite field reference: "." and "[]" would otherwise read as a nested path."""
    return re.sub(r"([.\[\]\\])", r"\\\1", column)


def _plotted_extents(values: list[dict[str, Any]], x: str, y: str, stacked: bool) -> list[float]:
    """The sizes the axis must show: each value, or for stacked bars each stack's total (the
    positive and the negative parts stack separately)."""
    if not stacked:
        return [v[y] for v in values if _is_number(v[y])]
    up: dict[Any, float] = {}
    down: dict[Any, float] = {}
    for v in values:
        if _is_number(v[y]):
            side = up if v[y] >= 0 else down
            side[v[x]] = side.get(v[x], 0.0) + v[y]
    return [*up.values(), *down.values()]


def _scaled(value: Any, scale: float) -> Any:
    return value * scale if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def label_text(result: StoredResult, column: str, value: Any) -> str:
    """A category value as the chart shows it: dates as dates, whole floats without ".0"."""
    if value is None:
        return "—"
    if result.is_temporal(column):
        return str(value)[:10]
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _bar_order(values: list[dict[str, Any]], x: str, y: str, color: str, domain: list[str],
               result: StoredResult) -> tuple[list[Any], list[dict[str, Any]]]:
    """Bars by value (stacked: by the bar total) descending, ties by label ascending. Returns
    the x domain for Vega and the data in displayed order (within a bar, by colour domain)."""
    totals: dict[Any, float] = {}
    for v in values:
        totals[v[x]] = totals.get(v[x], 0.0) + (v[y] if _is_number(v[y]) else 0.0)
    xs = sorted(totals, key=lambda k: (-totals[k], label_text(result, x, k)))
    rank = {k: i for i, k in enumerate(xs)}
    colour_rank = {c: i for i, c in enumerate(domain)}
    ordered = sorted(
        values,
        key=lambda v: (rank[v[x]], colour_rank.get(str(v[color]), len(domain)) if color else 0),
    )
    return xs, ordered


def static_drill(columns: list[str], result_name: str, *, drill_sql: str, drill_as: str, drill_x: str, drill_y: str,
                 allowed_schemas: tuple[str, ...], controls: Mapping[str, str] | None = None) -> Rewritten:
    """The checks of a Drill query that need no rows (SPEC §15 step 1): shared by answers and by
    the Board startup check (SPEC §18). `controls`: a Board's Control names and their SQL casts,
    which a Board's Drill query may use beside the row's columns. Raises ValueError."""
    controls = controls or {}
    if drill_as not in DRILL_AS:
        raise ValueError(f"drill_as must be one of: {', '.join(DRILL_AS)}")
    if drill_as != "table" and not (drill_x.strip() and drill_y.strip()):
        raise ValueError(f'drill_as "{drill_as}" needs drill_x and drill_y (output columns of drill_sql)')
    try:
        rewritten = rewrite(drill_sql, controls)
    except DrillSqlError as e:
        raise ValueError(str(e)) from None
    if not [n for n in rewritten.names if n not in controls]:
        raise ValueError("drill_sql needs at least one :column placeholder for the clicked row's values")
    for name in rewritten.names:
        if name not in columns and name not in controls:
            raise ValueError(
                f"drill_sql placeholder :{name} is not a column of {result_name}; its columns are: {', '.join(columns)}"
            )
    try:
        guard(rewritten.sql, allowed_schemas)
    except GuardError as e:
        raise ValueError(f"drill_sql: {e}") from None
    return rewritten


def _drill(
    result: StoredResult,
    *,
    visual: int,
    source: str,
    clickable_rows: list[int],
    title: str,
    drill_sql: str,
    drill_as: str,
    drill_x: str,
    drill_y: str,
    drill_title: str,
) -> DrillSpec | None:
    """Check step 1 of a Drill query (SPEC §15): no database, raises DrillError."""
    ctx = _ctx()
    if not drill_sql.strip():
        return None
    if visual in ctx.ignore_drills:
        log.warning("drill query of visual %d dropped after its Repairs ran out", visual)
        return None

    def fail(message: str) -> DrillError:
        return DrillError(message, visual)

    # A Board was reviewed by a person, so it has no cap (SPEC §18).
    if not ctx.board and len(ctx.drills) >= ctx.max_drills:
        raise fail(f"at most {ctx.max_drills} Visuals per Surface may have a drill_sql")
    casts = {name: cast for name, (cast, _value) in ctx.controls.items()}
    try:
        rewritten = static_drill(result.columns, result.id, drill_sql=drill_sql, drill_as=drill_as, drill_x=drill_x,
                                 drill_y=drill_y, allowed_schemas=ctx.allowed_schemas, controls=casts)
    except ValueError as e:
        raise fail(str(e)) from None
    row_names = [n for n in rewritten.names if n not in casts]
    for name in row_names:
        if any(not isinstance(result.rows[i].get(name), BINDABLE) for i in clickable_rows):
            raise fail(f"drill_sql placeholder :{name}: column {name!r} holds lists or JSON, which can't be bound")
    for n in sorted(null_dropping_params(rewritten.sql)):
        name = rewritten.names[n - 1]
        if name in casts:
            continue  # a Control's NULL means "All", not a dropped row (SPEC §18)
        if any(result.rows[i].get(name) is None for i in clickable_rows):
            raise fail(
                f"drill_sql compares :{name} with =, <>, IN or ANY, but {name!r} is NULL in a clickable row; "
                f"use IS NOT DISTINCT FROM"
            )
    spec = DrillSpec(
        source=source, visual=visual, sql=rewritten.sql, names=rewritten.names, drill_as=drill_as,
        drill_x=drill_x.strip(), drill_y=drill_y.strip(), title=_fill(drill_title) if drill_title.strip() else title,
        fixed=tuple((n, ctx.controls[n][1]) for n in rewritten.names if n in casts),
    )
    ctx.drills.append(spec)
    return spec


@dataclass(frozen=True)
class _Drill:
    sql: str = ""
    drill_as: str = "table"
    x: str = ""
    y: str = ""
    title: str = ""


def _register_visual(
    result: StoredResult, *, visual: int, clickable: bool, rows: list[int], labels: dict[int, str],
    title: str, drill: _Drill,
) -> dict[str, Any] | None:
    """Reserve an action source for a clickable Visual, check its Drill query, and return the
    component's `action`. A Visual with no clickable rows isn't clickable."""
    ctx = _ctx()
    if not clickable:
        return None
    if not rows:
        if drill.sql.strip():
            log.warning("drill query of visual %d ignored: the visual has no clickable rows", visual)
        return None
    source = ctx.ids.source()
    try:
        spec = _drill(
            result, visual=visual, source=source, clickable_rows=rows, title=title, drill_sql=drill.sql,
            drill_as=drill.drill_as, drill_x=drill.x, drill_y=drill.y, drill_title=drill.title,
        )
    except DrillError:
        if not ctx.board:
            raise
        # The startup check passed the rest, so this depends on the rows (a NULL under `=`, a list):
        # only the Drill query is dropped, and the Visual keeps its Ask (SPEC §18). No message: it
        # can quote a value.
        log.warning("board drill query dropped: visual=%d", visual)
        spec = None
    ctx.sources.append(SourceSpec(id=source, visual=visual, result=result, title=title, rows=tuple(rows), labels=labels))
    return {"source": source, "rows": rows, "drillable": spec is not None}


def _card(children: list[Any]) -> Card:
    """Every Visual is a Card holding its title, the Visual and its SQL (SPEC §16)."""
    return Card(child=Column(children=children))


def _no_rows(result: StoredResult, title: str) -> _PanelReplaced:
    """Board mode: a panel whose Result has nothing to show (SPEC §18), with its title and SQL."""
    heading = [Text(text=title, variant="h4")] if title else []
    return _PanelReplaced(_card([*heading, Text(text=NO_ROWS, variant="caption"), _sql_details(result)]))


def _chart(*args: Any, **kwargs: Any) -> Card:
    return _card(_chart_parts(*args, **kwargs))


def _chart_parts(
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
    clickable: bool = True,
    drill: _Drill = _Drill(),
) -> list[Any]:
    """`title` is display text (placeholders already filled)."""
    ctx = _ctx()
    visual = ctx.visuals
    ctx.visuals += 1
    _column(result, x)
    _numeric_column(result, y, "y")
    if color:
        _column(result, color)
    axis_format, scale, suffix = _axis(result, y, fmt)
    values, domain = _chart_values(result, x, y, color, scale, clickable)
    if ctx.board and not any(_is_number(v[y]) for v in values):
        raise _no_rows(result, title)
    if fmt == "currency_compact":  # one unit for the chart, from the largest value plotted
        divisor, unit = compact_unit(_largest(_plotted_extents(values, x, y, stacked=mark == "bar" and bool(color))))
        values = [{**v, y: v[y] / divisor if _is_number(v[y]) else v[y]} for v in values]
        axis_format, suffix = (",.1f" if unit else ",.0f"), f" ({ctx.currency_symbol}{unit})"

    temporal = result.is_temporal(x)
    x_enc: dict[str, Any] = {"field": _field(x), "type": "temporal" if temporal else "ordinal", "title": column_label(x)}
    if mark == "bar" and sort_by_value and not temporal:
        # An explicit domain, so the screen order and the server's displayed order agree.
        x_domain, values = _bar_order(values, x, y, color, domain, result)
        x_enc["sort"] = x_domain
    elif mark == "bar" and temporal:
        values = sorted(values, key=lambda v: (v[x] is None, str(v[x])))
    elif not temporal:
        x_enc["sort"] = None
    y_enc: dict[str, Any] = {
        "field": _field(y),
        "type": "quantitative",
        "title": column_label(y) + suffix,
        "axis": {"format": axis_format},
    }
    if mark == "bar" and not temporal:
        x_enc["type"] = "nominal"
    encoding: dict[str, Any] = {"y": x_enc, "x": y_enc} if horizontal else {"x": x_enc, "y": y_enc}
    if color:
        encoding["color"] = {"field": _field(color), "type": "nominal", "title": column_label(color), "sort": domain}
    encoding["tooltip"] = [
        {"field": _field(x), "type": x_enc["type"]},
        *([{"field": _field(color), "type": "nominal"}] if color else []),
        {"field": _field(y), "type": "quantitative", "format": axis_format},
    ]
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": values},
        "mark": {"type": mark, "point": True} if mark == "line" else {"type": mark},
        "encoding": encoding,
    }
    rows = [v[ROW_FIELD] for v in values if ROW_FIELD in v]
    labels = {}
    for v in values:
        if ROW_FIELD in v:
            text = label_text(result, x, v[x])
            labels[v[ROW_FIELD]] = f"{text} · {label_text(result, color, v[color])}" if color else text
            v[LABEL_FIELD] = labels[v[ROW_FIELD]]  # the client's confirmation label (SPEC §18)
    action = _register_visual(result, visual=visual, clickable=clickable, rows=rows, labels=labels, title=title, drill=drill)
    chart = VegaChart(spec=spec, action=action) if action else VegaChart(spec=spec)
    # A cut Result draws a partial series: say so, as tables do (SPEC §18).
    caption = [Text(text=f"query truncated at {result.row_count:,} rows", variant="caption")] if result.truncated else []
    return [Text(text=title, variant="h4"), chart, *caption, _sql_details(result)]


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
    clickable: bool = True,
    drill_sql: str = "",
    drill_as: str = "table",
    drill_x: str = "",
    drill_y: str = "",
    drill_title: str = "",
) -> Card:
    """Bar chart of a stored query result, followed by its SQL.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        x: Column for the categories (or dates).
        y: Numeric column for bar length.
        title: Chart title, plain text.
        color: Optional column to stack by; the top 8 groups are kept, the rest become "Other".
        horizontal: Draw bars horizontally (good for long category names).
        format: Value format: auto, currency, currency_m, currency_compact, int, decimal, pct, fraction_pct. currency_compact = money in K, M or B (38414946047 -> $38.4B), pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%).
        clickable: Bars can be clicked to explore them (default true); false turns it off.
        drill_sql: Optional SQL for the next level of detail below a clicked bar: one SELECT with :column placeholders naming columns of this result (WHERE region IS NOT DISTINCT FROM :region). It runs without you when a bar is clicked.
        drill_as: How the drill-down is shown: table (default), bar or line.
        drill_x: For drill_as bar or line: the drill_sql output column for categories or dates.
        drill_y: For drill_as bar or line: the numeric drill_sql output column.
        drill_title: Optional title of the drill-down (default: this title); the clicked bar's label is appended.
    """
    title_text = _fill(title)
    return _chart(
        _result(result_id), mark="bar", x=x, y=y, title=title_text, color=color, fmt=format,
        sort_by_value=True, horizontal=horizontal, clickable=clickable,
        drill=_Drill(drill_sql, drill_as, drill_x, drill_y, drill_title),
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
    clickable: bool = True,
    drill_sql: str = "",
    drill_as: str = "table",
    drill_x: str = "",
    drill_y: str = "",
    drill_title: str = "",
) -> Card:
    """Line chart of a stored query result in its row order (or by date), followed by its SQL.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        x: Column for the horizontal axis; dates are plotted on a time scale.
        y: Numeric column for the vertical axis.
        title: Chart title, plain text.
        color: Optional column with one line per group; the top 8 are kept, the rest become "Other".
        format: Value format: auto, currency, currency_m, currency_compact, int, decimal, pct, fraction_pct. currency_compact = money in K, M or B (38414946047 -> $38.4B), pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%).
        clickable: Points can be clicked to explore them (default true); false turns it off.
        drill_sql: Optional SQL for the next level of detail below a clicked point: one SELECT with :column placeholders naming columns of this result (WHERE year IS NOT DISTINCT FROM :year). It runs without you when a point is clicked.
        drill_as: How the drill-down is shown: table (default), bar or line.
        drill_x: For drill_as bar or line: the drill_sql output column for categories or dates.
        drill_y: For drill_as bar or line: the numeric drill_sql output column.
        drill_title: Optional title of the drill-down (default: this title); the clicked point's label is appended.
    """
    title_text = _fill(title)
    return _chart(
        _result(result_id), mark="line", x=x, y=y, title=title_text, color=color, fmt=format,
        sort_by_value=False, clickable=clickable,
        drill=_Drill(drill_sql, drill_as, drill_x, drill_y, drill_title),
    )


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _treemap_parts(
    result: StoredResult, *, title: str, label: str, value: str, fmt: str, clickable: bool, drill: _Drill,
) -> list[Any]:
    """A full Vega treemap (Vega-Lite has none): a flat `stratify` under one root, then
    `treemap`. Only the leaves are drawn, so the root has no mark (SPEC §18)."""
    ctx = _ctx()
    visual = ctx.visuals
    ctx.visuals += 1
    _column(result, label)
    _numeric_column(result, value, "value")
    fmt = _format_name(fmt or "auto")
    drawn = [(i, r) for i, r in enumerate(result.rows) if _is_number(r.get(value)) and r[value] > 0]
    left_out = result.row_count - len(drawn)
    # Displayed order, which prefetch uses: by value descending, ties by label ascending.
    drawn.sort(key=lambda t: (-t[1][value], label_text(result, label, t[1].get(label))))
    shown, rest = drawn[:MAX_TREEMAP_TILES], drawn[MAX_TREEMAP_TILES:]
    if not shown:
        if ctx.board:
            raise _no_rows(result, title)
        raise MacroError(f"{result.id} has no rows with a positive {value!r} to draw")
    rest_total = sum(r[value] for _, r in rest)
    unit = compact_unit(max(shown[0][1][value], rest_total)) if fmt == "currency_compact" else None
    label_title, value_title = column_label(label), column_label(value)

    def tile(tile_id: str, name: str, size: float) -> dict[str, Any]:
        text = _format(result, size, value, fmt, unit)
        return {"_id": tile_id, "_parent": "root", "_size": size, "_name": name, "_value": text,
                "_tooltip": {label_title: name, value_title: text}}

    leaves: list[dict[str, Any]] = []
    rows: list[int] = []
    labels: dict[int, str] = {}
    for i, r in shown:
        name = label_text(result, label, r.get(label))
        leaf = tile(f"t{i}", name, r[value])
        if clickable and i < ctx.table_row_cap:
            leaf[ROW_FIELD], leaf[LABEL_FIELD] = i, name
            rows.append(i)
            labels[i] = name
        leaves.append(leaf)
    if rest:  # not clickable: it stands for many rows
        leaves.append(tile("other", f"{OTHER} (incomplete)" if result.truncated else OTHER, rest_total))
    spec = {
        "$schema": "https://vega.github.io/schema/vega/v5.json",
        "data": [
            {
                "name": "tree",
                "values": [{"_id": "root", "_parent": None}, *leaves],
                "transform": [
                    {"type": "stratify", "key": "_id", "parentKey": "_parent"},
                    {"type": "treemap", "field": "_size", "sort": {"field": "value", "order": "descending"},
                     "round": True, "method": "squarify", "size": [{"signal": "width"}, {"signal": "height"}]},
                ],
            },
            {"name": "tiles", "source": "tree", "transform": [{"type": "filter", "expr": "datum._parent != null"}]},
        ],
        "scales": [
            {"name": "color", "type": "ordinal", "domain": ["tile"], "range": "category"},
        ],
        "marks": [
            {
                "type": "rect",
                "from": {"data": "tiles"},
                "encode": {"update": {
                    "x": {"field": "x0"}, "y": {"field": "y0"}, "x2": {"field": "x1"}, "y2": {"field": "y1"},
                    "fill": {"scale": "color", "value": "tile"},
                    "stroke": {"value": "white"}, "strokeWidth": {"value": 1},
                    "tooltip": {"field": "_tooltip"},
                }},
            },
            {
                "type": "text",
                "from": {"data": "tiles"},
                "interactive": False,
                "encode": {"update": {
                    "x": {"signal": "datum.x0 + 6"}, "y": {"signal": "datum.y0 + 16"},
                    "text": {"signal": "datum.x1 - datum.x0 >= 70 && datum.y1 - datum.y0 >= 36 ? [datum._name, datum._value] : ''"},
                    "limit": {"signal": "max(0, datum.x1 - datum.x0 - 12)"},
                    "fontSize": {"value": 12}, "fontWeight": {"value": "bold"},  # fill: the client theme's text colour
                }},
            },
        ],
    }
    notes = []
    if rest:
        notes.append(f"{_plural(len(rest), 'smaller row')} in “{OTHER}”")
    if left_out:
        notes.append(f"{_plural(left_out, 'row')} with no positive {value_title.lower()} left out")
    if result.truncated:
        notes.append(f"query truncated at {result.row_count:,} rows, so “{OTHER}” is incomplete")
    action = _register_visual(result, visual=visual, clickable=clickable, rows=rows, labels=labels, title=title, drill=drill)
    if action:  # Vega sets the cursor from the hovered item's own `cursor`, so clickable tiles carry it
        spec["marks"][0]["encode"]["update"]["cursor"] = {"signal": f"isValid(datum.{ROW_FIELD}) ? 'pointer' : null"}
    chart = VegaChart(spec=spec, action=action) if action else VegaChart(spec=spec)
    caption = [Text(text="; ".join(notes), variant="caption")] if notes else []  # caption Text isn't markdown
    return [Text(text=title, variant="h4"), chart, *caption, _sql_details(result)]


@macro
@_captured
def Treemap(
    result_id: str,
    title: str,
    label: str,
    value: str,
    format: str = "",
    clickable: bool = True,
    drill_sql: str = "",
    drill_as: str = "table",
    drill_x: str = "",
    drill_y: str = "",
    drill_title: str = "",
) -> Card:
    """Treemap of a stored query result: one tile per row, its area proportional to the value, followed by its SQL. Shows the 12 largest rows; the rest become one "Other" tile.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        title: Chart title, plain text.
        label: Text column naming each tile.
        value: Numeric column for tile area; rows with no positive value are left out.
        format: Value format: auto, currency, currency_m, currency_compact, int, decimal, pct, fraction_pct.
        clickable: Tiles can be clicked to explore them (default true); false turns it off.
        drill_sql: Optional SQL for the next level of detail below a clicked tile: one SELECT with :column placeholders naming columns of this result. It runs without you when a tile is clicked.
        drill_as: How the drill-down is shown: table (default), bar or line.
        drill_x: For drill_as bar or line: the drill_sql output column for categories or dates.
        drill_y: For drill_as bar or line: the numeric drill_sql output column.
        drill_title: Optional title of the drill-down (default: this title); the clicked tile's label is appended.
    """
    return _card(_treemap_parts(
        _result(result_id), title=_fill(title), label=label, value=value, fmt=format, clickable=clickable,
        drill=_Drill(drill_sql, drill_as, drill_x, drill_y, drill_title),
    ))


def _parse_formats(result: StoredResult, spec: str) -> dict[str, str]:
    formats: dict[str, str] = {}
    for pair in filter(None, (p.strip() for p in spec.split(","))):
        column, sep, fmt = pair.rpartition(":")
        if not sep:
            raise MacroError(f"format entry {pair!r} must look like column:format")
        formats[_column(result, column.strip())] = _format_name(fmt.strip())
    return formats


def _row_label(cells: list[str], result: StoredResult, shown: list[str]) -> str:
    """A table row's label: the text columns' cells joined with " · ", at most 80 characters."""
    text = [cell for cell, c in zip(cells, shown, strict=True) if not result.is_numeric(c)] or cells[:1]
    label = " · ".join(text)
    return label if len(label) <= ROW_LABEL_MAX else label[: ROW_LABEL_MAX - 1] + "…"


def _table(*args: Any, **kwargs: Any) -> Card:
    return _card(_table_parts(*args, **kwargs))


def _bar_shares(result: StoredResult, rows: list[dict[str, Any]], column: str) -> list[float | None] | None:
    """A bar column's shares of its largest shown value, 0 to 1, `None` for NULL (SPEC §18).
    None when every shown value is zero or NULL: no bars."""
    values = [r.get(column) for r in rows]
    if any(_is_number(v) and v < 0 for v in values):
        raise MacroError(f"bars column {column!r} of {result.id} has negative values; bars need values of 0 or more")
    largest = max((v for v in values if _is_number(v)), default=0)
    if largest <= 0:
        return None
    return [round(v / largest, 4) if _is_number(v) else None for v in values]


def _table_parts(
    result: StoredResult, *, title: str, columns: str = "", format: str = "", clickable: bool = True,
    drill: _Drill = _Drill(), bars: str = "",
) -> list[Any]:
    """`title` is display text (placeholders already filled)."""
    ctx = _ctx()
    visual = ctx.visuals
    ctx.visuals += 1
    shown = [_column(result, c.strip()) for c in columns.split(",") if c.strip()] or result.columns
    formats = _parse_formats(result, format)
    cap = ctx.table_row_cap
    rows = result.rows[:cap]
    grid_columns = [
        {"key": c, "label": column_label(c), **({"align": "end"} if result.is_numeric(c) else {})}
        for c in shown
    ]
    for c in (b.strip() for b in bars.split(",") if b.strip()):
        if c not in shown:
            raise MacroError(f"bars column {c!r} is not one of the shown columns: {', '.join(shown)}")
        _numeric_column(result, c, "bars")
        shares = _bar_shares(result, rows, c)
        if shares is not None:
            grid_columns[shown.index(c)]["bars"] = shares
    # currency_compact columns share one unit, from the largest value shown.
    units = {
        c: compact_unit(_largest(r.get(c) for r in rows)) for c in shown if formats.get(c) == "currency_compact"
    }
    cells = [[_cell(result, r, c, formats.get(c, "auto"), units.get(c)) for c in shown] for r in rows]
    notes = []
    if result.row_count > cap:
        notes.append(f"Showing {cap:,} of {result.row_count:,} rows")
    if result.truncated:
        notes.append(f"query truncated at {result.row_count:,} rows")
    caption = "; ".join(notes) or None
    labels = {i: _row_label(row_cells, result, shown) for i, row_cells in enumerate(cells)}
    action = _register_visual(
        result, visual=visual, clickable=clickable, rows=list(range(len(cells))), labels=labels, title=title, drill=drill,
    )
    grid = DataGrid(columns=grid_columns, rows=cells, caption=caption, **({"action": action} if action else {}))
    return [Text(text=title, variant="h4"), grid, _sql_details(result)]


@macro
@_captured
def DataTable(
    result_id: str,
    title: str,
    columns: str = "",
    format: str = "",
    clickable: bool = True,
    drill_sql: str = "",
    drill_as: str = "table",
    drill_x: str = "",
    drill_y: str = "",
    drill_title: str = "",
    bars: str = "",
) -> Card:
    """Table of a stored query result, followed by its SQL.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        title: Table title, plain text.
        columns: Optional comma-separated columns to show, in order (default: all).
        format: Optional comma-separated column:format pairs, e.g. "value:currency_compact,share:fraction_pct". currency_compact = money in K, M or B (38414946047 -> $38.4B), pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%).
        clickable: Rows can be clicked to explore them (default true); false turns it off.
        drill_sql: Optional SQL for the next level of detail below a clicked row: one SELECT with :column placeholders naming columns of this result (WHERE region IS NOT DISTINCT FROM :region). It runs without you when a row is clicked.
        drill_as: How the drill-down is shown: table (default), bar or line.
        drill_x: For drill_as bar or line: the drill_sql output column for categories or dates.
        drill_y: For drill_as bar or line: the numeric drill_sql output column.
        drill_title: Optional title of the drill-down (default: this title); the clicked row's label is appended.
        bars: Optional comma-separated numeric shown columns to draw as share bars under their numbers (each cell's share of the column's largest value), e.g. "value".
    """
    return _table(
        _result(result_id), title=_fill(title), columns=columns, format=format, clickable=clickable,
        drill=_Drill(drill_sql, drill_as, drill_x, drill_y, drill_title), bars=bars,
    )


@macro
@_captured
def FollowUp(label: str, request: str) -> Button:
    """A button that asks you a follow-up question after the user confirms it. At most 3 per Surface.

    Args:
        label: Button text, plain text.
        request: The question to answer when clicked, written as the user would ask it (at most 500 characters).
    """
    ctx = _ctx()
    if len(ctx.follow_ups) >= MAX_FOLLOW_UPS:
        raise MacroError(f"at most {MAX_FOLLOW_UPS} FollowUp buttons per Surface")
    if not label.strip():
        raise MacroError("FollowUp needs a label")
    if not request.strip():
        raise MacroError("FollowUp needs a request")
    if len(request) > FOLLOW_UP_REQUEST_MAX:
        raise MacroError(f"FollowUp request is {len(request)} characters; at most {FOLLOW_UP_REQUEST_MAX}")
    text = _fill(label)
    fid = ctx.ids.follow_up()
    ctx.follow_ups.append(FollowUpSpec(id=fid, label=text, request=request.strip()))
    return Button(child=Text(text=text), action=Action(event=ActionEvent(name="ask", context={"follow_up": fid})))


ASK_BUTTON_TEXT = "Ask the agent about this"


def drill_down_view(result: StoredResult, spec: DrillSpec, title: str) -> Card:
    """The Visual of a Drill-down Level (no model): the `drill_as` Visual, clickable for Asks
    but with no Drill query, its SQL, and an Ask button. `title` is display text."""
    if spec.drill_as == "table":
        parts = _table_parts(result, title=title)
    else:
        parts = _chart_parts(
            result, mark=spec.drill_as, x=spec.drill_x, y=spec.drill_y, title=title, color="", fmt="auto",
            sort_by_value=spec.drill_as == "bar",
        )
    ctx = _ctx()
    source = ctx.sources[-1].id if ctx.sources and ctx.sources[-1].result is result else None
    if source is None:  # no clickable rows: register a source for the Ask button alone
        source = ctx.ids.source()
        ctx.sources.append(SourceSpec(id=source, visual=ctx.visuals - 1, result=result, title=title, rows=(), labels={}))
    button = Button(
        child=Text(text=ASK_BUTTON_TEXT),
        action=Action(event=ActionEvent(name="ask", context={"source": source})),
    )
    return _card([*parts, button])


@macro
@_captured
def Metric(result_id: str, column: str, label: str, row: int = 0, format: str = "auto") -> Card:
    """One headline number from a stored query result, with its label and SQL.

    Args:
        result_id: ID of a stored query result, e.g. "r1".
        column: Column holding the value.
        label: Short label shown above the value, plain text.
        row: Row index (0 = first row).
        format: Value format: auto, currency, currency_m, currency_compact, int, decimal, pct, fraction_pct, text, date. currency_compact = money in K, M or B (38414946047 -> $38.4B), pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%).
    """
    result = _result(result_id)
    _column(result, column)
    if _ctx().board and not 0 <= row < result.row_count:
        raise _no_rows(result, _fill(label))
    value = _row(result, row)[column]
    return Card(
        child=Column(
            children=[
                Text(text=_fill(label), variant="caption"),
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
# Board layouts name queries where answers name Result IDs (SPEC §18).
_BOARD_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)(?:\|([a-z_]+))?\}")
# Anything placeholder-shaped, valid or not, so its digits are not counted as bare numbers.
_PLACEHOLDER_SHAPE = re.compile(r"\{[a-z_][a-z0-9_]*\[\d+\]\.[^}]*\}")  # r<n> or a Board query name
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def escape_markdown(text: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


_ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~$])")


def unescape_markdown(text: str) -> str:
    """The inverse of `escape_markdown`, for text shown as plain text (a tab title, SPEC §18)."""
    return _ESCAPED.sub(r"\1", text)


def placeholders(template: str, board: bool = False) -> list[tuple[str, int, str, str]]:
    """The `Cite` placeholders in a template: (Result ID or query name, row, column, format)."""
    pattern = _BOARD_PLACEHOLDER if board else _PLACEHOLDER
    return [(m.group(1), int(m.group(2)), m.group(3), m.group(4) or "auto") for m in pattern.finditer(template)]


def _fill(template: str, on_error: Callable[[Exception], None] | None = None) -> str:
    board = _ctx().board

    def fill(m: re.Match[str]) -> str:
        try:
            result = _result(m.group(1))
            column = _column(result, m.group(3))
            row = int(m.group(2))
            if board and not 0 <= row < result.row_count:
                raise _no_rows(result, "")
            value = _row(result, row)[column]
            return escape_markdown(_format(result, value, column, m.group(4) or "auto"))
        except _PanelReplaced:
            if on_error is None:  # inside a macro: the macro's panel is replaced
                raise
            return "—"  # a Board's own Text: no row, or its query failed
        except MacroError as e:
            if on_error is None:
                raise
            on_error(e)
            return m.group(0)

    return (_BOARD_PLACEHOLDER if board else _PLACEHOLDER).sub(fill, template)


@macro
@_captured
def Cite(template: str) -> Text:
    """A sentence with numbers filled in from stored results, so they are never retyped.

    Args:
        template: Markdown text with placeholders like {r1[0].value|currency_m}: result ID, row index, column, optional format (the Metric formats; currency_compact = money in K, M or B (38414946047 -> $38.4B), pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction (0.066 -> 6.6%)).
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
    return FilledProse(text=text, bare_numbers=_bare_numbers(prose))


def fill_surface_text(components: Iterable[dict[str, Any]]) -> list[str]:
    """Fills placeholders in the `Text` and `Tabs` titles the model wrote on the Surface, in place
    (ADR 0013).

    Run before macro expansion, so only the model's own strings are touched: macro-built Text
    holds formatted values from Results. Errors go to the context's error list, naming the
    component. Returns the numbers the model typed in those strings.
    """
    ctx = _ctx()
    bare: list[str] = []

    def fill(holder: dict[str, Any], key: str, name: str, cid: Any) -> None:
        text = holder.get(key)
        if not isinstance(text, str):
            return  # a data binding
        holder[key] = _fill(text, on_error=lambda e: ctx.errors.append(f"{name} {cid!r}: {e}"))
        bare.extend(_bare_numbers(text))

    for comp in components:
        if comp.get("component") == "Text":
            fill(comp, "text", "Text", comp.get("id"))
        elif comp.get("component") == "Tabs":
            for tab in comp.get("tabs") or []:
                if isinstance(tab, dict):
                    fill(tab, "title", "Tabs", comp.get("id"))
    return bare


_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# Dates and years say what a figure covers; they aren't data the model could have retyped, and
# counting them buried the real typed figures in the log (ADR 0013).
_DATE = re.compile(
    rf"\b(?i:FY)\s?\d{{2}}(?:\d{{2}})?(?:[-–/]\d{{2}}(?:\d{{2}})?)?\b"  # FY2024-25, FY24-25, FY 2024
    rf"|\b\d{{4}}-\d{{2}}-\d{{2}}\b"  # 2014-01-01
    rf"|\b(?:19|20)\d{{2}}[-–/](?:\d{{4}}|\d{{2}})\b"  # 2023-24, 2023–2024
    rf"|\b\d{{1,2}}\s+{_MONTH}(?:\s+\d{{4}})?"  # 1 Jul 2024, 30 June
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:,\s*\d{{4}})?\b"  # July 1, 2024
    rf"|\b(?:19|20)\d{{2}}\b(?![.,]\d)"  # 2024, but not 2024.5 or 2,024
)


def _bare_numbers(text: str) -> list[str]:
    return _BARE_NUMBER.findall(_DATE.sub(" ", _PLACEHOLDER_SHAPE.sub("", text)))


MACROS = [BarChart, LineChart, Treemap, DataTable, Metric, Cite, FollowUp]
VISUAL_MACROS = frozenset({"BarChart", "LineChart", "Treemap", "DataTable"})


# --- startup checks for Board layouts (SPEC §18) ----------------------------------------------

Outputs = Mapping[str, tuple[list[str], list[str]]]  # query name -> (columns, types)


def _csv(value: Any) -> list[str]:
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def check_columns(components: Iterable[dict[str, Any]], outputs: Outputs) -> list[str]:
    """Columns-only check of a Board layout's macro calls against its queries' output columns and
    types. Runs no macro over rows, so zero-row outputs are fine. Returns the problems found."""
    problems: list[str] = []

    def kind_of(query: str, column: str, role: str, want: str, cid: str) -> None:
        columns, types = outputs[query]
        if column not in columns:
            problems.append(f"{cid}: {role} column {column!r} is not an output column of query {query!r}")
            return
        type_ = types[columns.index(column)]
        if want == "numeric" and type_ not in NUMERIC_TYPES:
            problems.append(f"{cid}: {role} column {column!r} of query {query!r} is {type_}, not a number")

    def template(text: Any, cid: str) -> None:
        if not isinstance(text, str):
            return
        for query, _row_index, column, _fmt in placeholders(text, board=True):
            if query not in outputs:
                problems.append(f"{cid}: placeholder names unknown query {query!r}")
            else:
                kind_of(query, column, "placeholder", "any", cid)

    for comp in components:
        name, cid = comp.get("component"), str(comp.get("id"))
        if name == "Text":
            template(comp.get("text"), cid)
        elif name == "Tabs":
            for tab in comp.get("tabs") or []:
                if isinstance(tab, dict):
                    template(tab.get("title"), cid)
        elif name == "Cite":
            template(comp.get("template"), cid)
        if name not in VISUAL_MACROS and name != "Metric":
            continue
        for key in ("title", "label", "drill_title"):
            template(comp.get(key), cid)
        query = comp.get("result_id")
        if query not in outputs:
            problems.append(f"{cid}: {name} names unknown query {query!r}; queries are: {', '.join(outputs)}")
            continue
        if name in ("BarChart", "LineChart"):
            kind_of(query, comp.get("x", ""), "x", "any", cid)
            kind_of(query, comp.get("y", ""), "y", "numeric", cid)
            if comp.get("color"):
                kind_of(query, comp["color"], "color", "any", cid)
        elif name == "Treemap":
            kind_of(query, comp.get("label", ""), "label", "any", cid)
            kind_of(query, comp.get("value", ""), "value", "numeric", cid)
        elif name == "Metric":
            kind_of(query, comp.get("column", ""), "column", "any", cid)
        elif name == "DataTable":
            shown = _csv(comp.get("columns")) or list(outputs[query][0])
            for c in shown:
                kind_of(query, c, "columns", "any", cid)
            for pair in _csv(comp.get("format")):
                kind_of(query, pair.rpartition(":")[0].strip(), "format", "any", cid)
            for c in _csv(comp.get("bars")):
                if c not in shown:
                    problems.append(f"{cid}: bars column {c!r} is not a shown column")
                else:
                    kind_of(query, c, "bars", "numeric", cid)
    return problems


def macro_expander() -> MacroExpander:
    return MacroExpander(MACROS, passthrough_components=PASSTHROUGH_COMPONENTS)
