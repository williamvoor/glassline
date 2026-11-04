"""The Boards step (SPEC.md phase 3, ADR 0032): a second tool loop, after the dataset draft, that
drafts `boards/*.toml` from the templates. Every Board is checked by the agent's own code
(`explorer_agent.boards`), so one this step accepts is one the agent starts with, and previewed
with one cold render (no model).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from explorer_agent.boards import (
    BOARD_COLD_TARGET_S,
    BOARD_FILE_RULES,
    EXPLORER,
    QUERY_NAME,
    Board,
    BoardError,
    BoardRenderer,
    OptionsError,
    build_board,
    check_board,
    check_board_set,
    entity_columns,
    instance_results,
    layout_components,
    parse_board,
)
from explorer_agent.config import Dataset
from explorer_agent.macros import NO_ROWS, PANEL_UNDRAWABLE, VISUAL_MACROS
from explorer_agent.prompt import dataset_context
from explorer_agent.results import StoredResult, to_json_safe
from explorer_agent.sql import SqlFail, SqlRunner
from explorer_agent.ui import UiPipeline

from explorer_bootstrap.draft import Completion, EventFn, estimate_for, tool_loop
from explorer_bootstrap.draft_schema import RUN_SQL_TOOL, _obj, _STR
from explorer_bootstrap.drafts import draft_dataset, schema_section
from explorer_bootstrap.settings import DraftSettings

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "boards.md"
TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"  # explorer/bootstrap/templates
TEMPLATE_FILES = ("explorer.toml", "entity.toml", "controls.md")
USER_MESSAGE = "Draft the Boards for this Dataset: the Explorer Board and its Entity Boards."
NUDGE = "Call submit_boards with the Boards."
BOARDS_VERSION = 1
# The agent's defaults for a render (explorer SPEC §18): the preview is one cold render as the
# agent would do it.
PREVIEW_CONCURRENCY = 2
PREVIEW_ROW_CAP = 500
PANEL_MACROS = VISUAL_MACROS | {"Metric"}
_FAILED_PREFIX = "This panel couldn't load"

SUBMIT_BOARDS_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_boards",
        "description": "Hand in every Board: its file name (no .toml), the whole TOML file, and one line for "
        "the reviewer on why. Include the Explorer Board (name explorer). Call it once, when done.",
        "parameters": _obj({"boards": {"type": "array", "items": _obj({"name": _STR, "toml": _STR, "why": _STR})}}),
    },
}
TOOLS = [RUN_SQL_TOOL, SUBMIT_BOARDS_TOOL]


def _clean(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))


def express_rules() -> str:
    """The agent's layer 1 (A2UI Express rules and the inference catalog), so a layout uses the
    agent's own Macro signatures."""
    return _clean(UiPipeline().format.prompt_generator.generate())


def build_boards_prompt(dataset: Dataset, section: str, max_entity_boards: int) -> str:
    """Stable for the same draft and settings: no dates, IDs or paths."""
    templates = "\n\n".join(
        f"### {name}\n```\n{_clean((TEMPLATES_DIR / name).read_text(encoding='utf-8'))}\n```" for name in TEMPLATE_FILES
    )
    parts = [
        express_rules(),
        f"## Instructions\n{_clean(PROMPT_PATH.read_text(encoding='utf-8'))}",
        f"## Board file rules\n{BOARD_FILE_RULES}",
        f"## Templates\n{templates}",
        _clean(dataset_context(dataset, section)),
        f"## Limit\nDraft at most {max_entity_boards} Entity Boards.",
    ]
    return "\n\n".join(parts) + "\n"


def cache_key(dataset: Dataset) -> str:
    return f"bootstrap-boards-{dataset.name}"


def boards_runner(pool: Any, dataset: Dataset, timeout_ms: int, row_cap: int) -> SqlRunner:
    """The runner for the Boards loop and its checks: the drafted Dataset's allowed schemas."""
    return SqlRunner(pool, dataset.allowed_schemas, timeout_ms, row_cap)


# --- checks --------------------------------------------------------------------------------------


def shape_problems(args: Any) -> list[str]:
    """`submit_boards` arguments against the schema (ADR 0023: we check them ourselves)."""
    if not isinstance(args, dict) or set(args) != {"boards"}:
        return ["arguments must be an object with exactly one key, boards"]
    boards = args["boards"]
    if not isinstance(boards, list) or not boards:
        return ["boards must be a non-empty list"]
    problems = []
    for i, b in enumerate(boards):
        if not isinstance(b, dict) or set(b) != {"name", "toml", "why"}:
            problems.append(f"boards[{i}] must have exactly name, toml and why")
        elif not all(isinstance(b[k], str) for k in ("name", "toml", "why")):
            problems.append(f"boards[{i}]: name, toml and why must be strings")
        elif not b["why"].strip() or "\n" in b["why"].strip():
            problems.append(f"boards[{i}]: why must be one line")
    return problems


def board_path(name: str) -> Path:
    return Path("boards") / f"{name}.toml"


@dataclass
class BoardCheck:
    """A Boards set checked: problems per Board (every name, [] when clean), set problems, and the
    parsed Boards that passed, with the pipeline they were checked through."""

    problems: dict[str, list[str]]
    set_problems: list[str]
    boards: dict[str, Board] = field(default_factory=dict)
    pipeline: UiPipeline | None = None

    @property
    def ok(self) -> bool:
        return not self.set_problems and not any(self.problems.values())

    def flat(self) -> list[str]:
        return [*self.set_problems, *(p for ps in self.problems.values() for p in ps)]

    def to_dict(self) -> dict[str, Any]:
        return {"problems": self.problems, "set_problems": self.set_problems}


def make_pipeline(dataset: Dataset, boards: dict[str, Board]) -> UiPipeline:
    """The pipeline the agent would build for this Dataset with these Boards (`UiPipeline.from_settings`)."""
    return UiPipeline(
        "express", dataset.currency.symbol, allowed_schemas=dataset.allowed_schemas,
        entity=entity_columns(boards), key_types={b.name: b.key_type for b in boards.values() if b.key},
    )


async def check_boards(files: list[dict[str, Any]], dataset: Dataset, runner: Any, max_entity_boards: int) -> BoardCheck:
    """Names, `parse_board`, the set rules and `check_board` on each Board (the agent's startup check)."""
    problems: dict[str, list[str]] = {}
    set_problems: list[str] = []
    parsed: dict[str, Board] = {}
    for f in files:
        name = f["name"]
        if name in problems:
            set_problems.append(f"boards: {name} appears twice")
            continue
        problems[name] = []
        if not QUERY_NAME.fullmatch(name):
            problems[name].append(f"{board_path(name)}: a Board file name must be lower case letters, digits and _")
            continue
        try:
            parsed[name] = parse_board(f["toml"], board_path(name), dataset.allowed_schemas)
        except BoardError as e:
            problems[name].append(str(e))
    keyed = [n for n, b in parsed.items() if b.key]
    if len(keyed) > max_entity_boards:
        set_problems.append(f"boards: at most {max_entity_boards} Entity Boards; this set has {len(keyed)} "
                            f"({', '.join(sorted(keyed))})")
    if EXPLORER not in problems or EXPLORER in parsed:
        try:
            check_board_set(parsed)
        except BoardError as e:
            set_problems.append(str(e))
    pipeline = make_pipeline(dataset, parsed)
    for name, board in parsed.items():
        try:
            await check_board(board, runner, pipeline)
        except BoardError as e:
            problems[name].append(str(e))
        except Exception as e:  # e.g. a pool timeout: report it on the Board, never end the run
            problems[name].append(f"{board_path(name)}: the check failed unexpectedly ({type(e).__name__})")
    ok = {n: b for n, b in parsed.items() if not problems[n]}
    return BoardCheck(problems, set_problems, ok, pipeline)


# --- the loop ------------------------------------------------------------------------------------


@dataclass
class BoardsDraft:
    files: list[dict[str, str]]
    check: BoardCheck
    usage: dict[str, Any]


async def run_boards(
    draft: dict[str, Any],
    runner: Any,
    settings: DraftSettings,
    *,
    completion: Completion | None = None,
    on_event: EventFn | None = None,
) -> BoardsDraft:
    """Run the Boards loop on a draft whose files validate. `runner` is on the drafted Dataset's
    allowed schemas (`boards_runner`). Raises DraftFileError (files invalid) or DraftError."""
    dataset = draft_dataset(draft)
    section = schema_section(draft, dataset)
    last: dict[str, Any] = {}

    async def check(raw: str) -> tuple[Any, list[str]]:
        try:
            args = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, [f"arguments are not valid JSON: {e.msg}"]
        problems = shape_problems(args)
        if problems:
            return None, problems
        files = [{"name": b["name"], "toml": b["toml"], "why": b["why"].strip()} for b in args["boards"]]
        result = await check_boards(files, dataset, runner, settings.max_entity_boards)
        last["check"] = result
        return files, result.flat()

    files, usage = await tool_loop(
        system=build_boards_prompt(dataset, section, settings.max_entity_boards), user=USER_MESSAGE, tools=TOOLS,
        prompt_cache_key=cache_key(dataset), submit="submit_boards", check_submit=check, nudge=NUDGE,
        what="Boards", runner=runner, settings=settings, completion=completion, on_event=on_event,
    )
    return BoardsDraft(files, last["check"], usage)


def estimate_boards(draft: dict[str, Any], settings: DraftSettings) -> dict[str, Any]:
    """`estimate_cost` for the Boards prompt. Raises DraftFileError when the files don't validate."""
    dataset = draft_dataset(draft)
    prompt = build_boards_prompt(dataset, schema_section(draft, dataset), settings.max_entity_boards)
    return estimate_for(len(prompt) + len(USER_MESSAGE), settings)


# --- preview (no model) --------------------------------------------------------------------------


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def sample_key(board: Board, report: dict[str, Any], dataset: Dataset, runner: Any) -> dict[str, Any] | None:
    """A key value for previewing an Entity Board: the first non-NULL value of a `key_columns`
    column in the first reported allowed object (report order) that has one."""
    allowed = set(dataset.allowed_schemas)
    for o in report["objects"]:
        if o["schema"] not in allowed:
            continue
        columns = {c["name"] for c in o["columns"]}
        for column in board.key_columns:
            if column not in columns:
                continue
            sql = (f"SELECT {_ident(column)} AS key FROM {_ident(o['schema'])}.{_ident(o['name'])} "
                   f"WHERE {_ident(column)} IS NOT NULL LIMIT 1")
            outcome = await runner.run(sql)
            if isinstance(outcome, SqlFail) or not outcome.rows:
                continue
            return {"value": outcome.rows[0]["key"], "source": f"{o['schema']}.{o['name']}.{column}"}
    return None


def _text_value(value: Any) -> str:
    safe = to_json_safe(value)
    return "" if safe is None else str(safe)


def _descendants(root: str, by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out, todo, seen = [], [root], set()
    while todo:
        cid = todo.pop()
        if cid in seen or cid not in by_id:
            continue
        seen.add(cid)
        comp = by_id[cid]
        out.append(comp)
        kids = comp.get("children")
        if isinstance(kids, list):
            todo.extend(k for k in kids if isinstance(k, str))
        child = comp.get("child")
        if isinstance(child, str):
            todo.append(child)
    return out


def visual_verdicts(board: Board, pipeline: UiPipeline, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per Macro call in the layout: drawn, no_rows or failed, from the built Surface."""
    by_id: dict[str, dict[str, Any]] = {}
    for m in messages:
        for c in (m.get("updateComponents") or {}).get("components", []):
            by_id[c["id"]] = c
    out = []
    for comp in layout_components(board, pipeline):
        macro = comp.get("component")
        if macro not in PANEL_MACROS:
            continue
        texts = [c.get("text") for c in _descendants(comp["id"], by_id) if c.get("component") == "Text"]
        texts = [t for t in texts if isinstance(t, str)]
        if any(t == PANEL_UNDRAWABLE or t.startswith(_FAILED_PREFIX) for t in texts):
            verdict = "failed"
        elif NO_ROWS in texts:
            verdict = "no_rows"
        else:
            verdict = "drawn"
        out.append({"id": comp["id"], "macro": macro, "query": comp.get("result_id", ""), "verdict": verdict})
    return out


async def preview_board(board: Board, dataset: Dataset, runner: Any, report: dict[str, Any], pipeline: UiPipeline,
                        preview_rows: int) -> dict[str, Any]:
    """One cold render at the default Controls, as the agent's `boards` CLI times it."""
    queries: dict[str, dict[str, Any]] = {}

    def record(_board: str, name: str, ms: int, outcome: Any) -> None:
        if isinstance(outcome, StoredResult):
            queries[name] = {"ms": ms, "rows": len(outcome.rows), "truncated": outcome.truncated,
                             "columns": list(outcome.columns),
                             "first_rows": [[_text_value(r.get(c)) for c in outcome.columns] for r in outcome.rows[:preview_rows]]}
        else:
            queries[name] = {"ms": ms, "error": f"SQLSTATE {outcome.sqlstate}: {outcome.message}" if outcome.sqlstate
                             else outcome.message}

    out: dict[str, Any] = {"target_s": BOARD_COLD_TARGET_S, "key": None}
    key = None
    if board.key:
        sample = await sample_key(board, report, dataset, runner)
        if sample is None:
            return {**out, "skipped": "no sample key: no reported column is named in key_columns"}
        key = sample["value"]
        out["key"] = {"value": _text_value(key), "source": sample["source"]}
    renderer = BoardRenderer({board.name: board}, runner, concurrency=PREVIEW_CONCURRENCY, row_cap=PREVIEW_ROW_CAP,
                             ttl_s=300, max_cache=20, on_query=record)
    start = time.perf_counter()
    try:
        options, state, bound = await renderer.defaults(board, fresh=True) if board.controls else ({}, None, None)
        outcomes = await renderer.outcomes(board, bound, fresh=True, key=key)
        counter = iter(f"r{i}" for i in range(1, 10_000))
        results, failed = instance_results(outcomes, counter.__next__)
        ui = build_board(board, results, failed, pipeline, "bootstrap-preview", options=options, state=state)
    except OptionsError as e:
        return {**out, "queries": queries, "error": f"the option or range query {e} failed"}
    except Exception as e:  # a build failure: the agent would show "couldn't be shown"
        return {**out, "queries": queries, "error": f"the Board could not be built ({type(e).__name__})"}
    seconds = time.perf_counter() - start
    return {
        **out, "queries": queries, "visuals": visual_verdicts(board, pipeline, ui.messages),
        "seconds": round(seconds, 3), "verdict": "ok" if seconds < BOARD_COLD_TARGET_S else "slow",
    }


async def preview_boards(check: BoardCheck, dataset: Dataset, runner: Any, report: dict[str, Any],
                         preview_rows: int) -> dict[str, Any]:
    """Every Board that passed its checks, Explorer first. Informs the reviewer; never blocks."""
    names = sorted(check.boards, key=lambda n: (n != EXPLORER, n))
    assert check.pipeline is not None
    return {n: await preview_board(check.boards[n], dataset, runner, report, check.pipeline, preview_rows) for n in names}


# --- the state the server and CLI keep -----------------------------------------------------------


async def boards_state(files: list[dict[str, str]], check: BoardCheck, dataset: Dataset, runner: Any,
                       report: dict[str, Any], settings: DraftSettings, usage: dict[str, Any] | None) -> dict[str, Any]:
    preview = await preview_boards(check, dataset, runner, report, settings.board_preview_rows)
    return {"boards_version": BOARDS_VERSION, "boards": files, **check.to_dict(), "preview": preview, "usage": usage}


def board_files_problems(boards: Any) -> list[str]:
    """A client's edited Boards (`[{name, toml, why}]`) before they're stored."""
    return shape_problems({"boards": boards})

