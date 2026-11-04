"""The draft as data (`draft.json`), and checking its files (SPEC.md phase 2b).

A draft is plain JSON-able dicts, because the CLI steps and later the UI hand it around and the
person may edit it by hand.
"""

from __future__ import annotations

import json
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from explorer_agent.config import CommentOverlay, ConfigError, Dataset, OverlayEntry, load_dataset
from explorer_agent.prompt import dataset_context
from explorer_agent.schema import ColumnInfo, TableInfo, apply_overlay, render_schema_section

from explorer_bootstrap.draft_schema import report_index


def comment_problems(draft: dict[str, Any]) -> list[str]:
    """Accepted comments must name a reported object or column, once, with non-blank text.

    The model's comments were checked on submit, but the person may edit draft.json by hand.
    """
    index = report_index(draft["report"])
    problems, seen = [], set()
    for c in draft["comments"]:
        if not c.get("accepted"):
            continue
        obj, col, text = c.get("object"), c.get("column"), c.get("comment")
        label = f"{obj}.{col}" if col else str(obj)
        if not isinstance(obj, str) or obj not in index:
            problems.append(f"comments: {obj} is not a table or view in the report")
            continue
        if not isinstance(col, str) or (col and col not in index[obj]["columns"]):
            problems.append(f"comments: {label} is not a column in the report")
            continue
        if (obj, col) in seen:
            problems.append(f"comments: {label} is accepted twice")
        seen.add((obj, col))
        if not isinstance(text, str) or not text.strip():
            problems.append(f"comments: {label} has a blank comment")
    return problems

DRAFT_VERSION = 1
FILE_NAMES = {"dataset_toml": "dataset.toml", "notes_md": "notes.md", "glossary_md": "glossary.md"}


class DraftFileError(ValueError):
    pass


def new_draft(
    report: dict[str, Any], args: dict[str, Any], *, model: str, effort: str, usage: dict[str, Any]
) -> dict[str, Any]:
    """A draft from valid submit_draft arguments. Rewrites of existing comments start unaccepted."""
    index = report_index(report)
    comments = []
    for c in args["comments"]:
        existing = index[c["object"]]["columns"][c["column"]] if c["column"] else index[c["object"]]["comment"]
        kind = "rewrite" if existing else "new"
        comments.append({**c, "kind": kind, "accepted": kind == "new"})
    return {
        "draft_version": DRAFT_VERSION,
        "model": model,
        "effort": effort,
        "report": report,
        "files": dict(args["files"]),
        "comments": comments,
        "claims": [{**cl, "dismissed": False, "result": None} for cl in args["claims"]],
        "usage": usage,
    }


def draft_json(draft: dict[str, Any]) -> str:
    return json.dumps(draft, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def load_draft(path: Path) -> dict[str, Any]:
    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise DraftFileError(f"cannot read {path}: {e.strerror}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise DraftFileError(f"{path} is not valid JSON: {e}") from None
    if not isinstance(draft, dict) or draft.get("draft_version") != DRAFT_VERSION:
        raise DraftFileError(f"{path} is not a version {DRAFT_VERSION} draft")
    for key in ("report", "files", "comments", "claims"):
        if key not in draft:
            raise DraftFileError(f"{path}: missing {key}")
    return draft


def report_tables(report: dict[str, Any]) -> list[TableInfo]:
    """The report's objects as the agent's TableInfo, database comments as reported."""
    return [
        TableInfo(
            schema=o["schema"],
            name=o["name"],
            kind=o["kind"],
            comment=o["database_comment"] if o.get("comment_source") == "overlay" else o["comment"],
            est_rows=o["est_rows"],
            columns=tuple(
                ColumnInfo(
                    c["name"], c["type"], c["nullable"],
                    c["database_comment"] if c.get("comment_source") == "overlay" else c["comment"],
                )
                for c in o["columns"]
            ),
        )
        for o in report["objects"]
    ]


def accepted_overlay(draft: dict[str, Any]) -> CommentOverlay:
    """The accepted comments as a Comment overlay (the same shape export writes)."""
    entries: dict[str, dict[str, Any]] = {}
    for c in draft["comments"]:
        if not c.get("accepted"):
            continue
        e = entries.setdefault(c["object"], {"comment": None, "columns": {}})
        if c["column"]:
            e["columns"][c["column"]] = c["comment"]
        else:
            e["comment"] = c["comment"]
    return CommentOverlay(
        Path("comments.toml"), {k: OverlayEntry(v["comment"], v["columns"]) for k, v in sorted(entries.items())}
    )


def validate_files(draft: dict[str, Any]) -> dict[str, Any]:
    """`load_dataset` on the drafted files, plus our own rules; the context size when valid."""
    files = draft["files"]
    errors: list[str] = comment_problems(draft)
    try:
        data = tomllib.loads(files["dataset_toml"])
    except tomllib.TOMLDecodeError as e:
        return {"errors": [*errors, f"dataset.toml is not valid TOML: {e}"], "context_chars": None}
    if "comments" in data:
        errors.append("dataset.toml must not have a [comments] table (export adds it)")
    if not isinstance(data.get("examples"), list) or not data["examples"]:
        errors.append("dataset.toml must propose starter examples (examples = [...])")
    for key, name in (("notes", "notes.md"), ("glossary", "glossary.md")):
        if data.get(key) != name:
            errors.append(f'dataset.toml: {key} must be "{name}"')
    report_schemas = set(draft["report"]["schemas"])
    schemas = data.get("allowed_schemas")
    if isinstance(schemas, list) and not set(map(str, schemas)) <= report_schemas:
        errors.append("dataset.toml: allowed_schemas must be among the reported schemas: "
                      + ", ".join(sorted(report_schemas)))
    with tempfile.TemporaryDirectory(prefix="bootstrap-validate-") as tmp:
        d = Path(tmp)
        for key, name in FILE_NAMES.items():
            (d / name).write_text(files[key], encoding="utf-8")
        try:
            dataset = load_dataset(d)
        except ConfigError as e:
            errors.append(str(e).replace(str(d), "<dataset>"))
            dataset = None
    if errors or dataset is None:
        return {"errors": errors, "context_chars": None}
    return {"errors": [], "context_chars": len(dataset_context(dataset, schema_section(draft, dataset)))}


def schema_section(draft: dict[str, Any], dataset: Dataset) -> str:
    """The Dataset context's schema section: the reported allowed objects with accepted comments."""
    allowed = set(dataset.allowed_schemas)
    tables = [t for t in report_tables(draft["report"]) if t.schema in allowed]
    return render_schema_section(apply_overlay(tables, accepted_overlay(draft)))


def draft_dataset(draft: dict[str, Any]) -> Dataset:
    """The Dataset the drafted files describe (phase 3: Boards are drafted and checked against it).
    Raises DraftFileError with the validation errors when the files don't validate."""
    errors = validate_files(draft)["errors"]
    if errors:
        raise DraftFileError("; ".join(errors))
    with tempfile.TemporaryDirectory(prefix="bootstrap-dataset-") as tmp:
        d = Path(tmp)
        for key, name in FILE_NAMES.items():
            (d / name).write_text(draft["files"][key], encoding="utf-8")
        return load_dataset(d)
