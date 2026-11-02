"""The `submit_draft` tool (ADR 0023): its JSON schema and our own check of the arguments.

The schema stays inside Meta's strict subset (no oneOf, additionalProperties false, every key
required) even though we don't send `strict`, so a later switch to structured output is easy.
"""

from __future__ import annotations

import math
import tomllib
from typing import Any

FILES = ("dataset.toml", "notes.md", "glossary.md", "comments")
MAX_PROBLEMS = 20


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_NUM = {"type": "number"}

DRAFT_PARAMETERS = _obj(
    {
        "files": _obj({"dataset_toml": _STR, "notes_md": _STR, "glossary_md": _STR}),
        "comments": {
            "type": "array",
            "items": _obj({"object": _STR, "column": _STR, "comment": _STR}),
        },
        "claims": {
            "type": "array",
            "items": _obj(
                {
                    "text": _STR,
                    "file": {"type": "string", "enum": list(FILES)},
                    "sql": _STR,
                    "expect": {
                        "anyOf": [
                            _obj({"kind": {"type": "string", "enum": ["scalar"]}, "value": _NUM, "abs_tol": _NUM,
                                  "rel_tol": _NUM}),
                            _obj({"kind": {"type": "string", "enum": ["set"]},
                                  "values": {"type": "array", "items": _STR}}),
                            _obj({"kind": {"type": "string", "enum": ["none"]}}),
                        ]
                    },
                }
            ),
        },
    }
)

RUN_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Run one read-only SELECT (or WITH ... SELECT) against the chosen schemas. "
        "Tables must be schema-qualified. Returns columns and the first rows.",
        "parameters": _obj({"sql": _STR, "purpose": _STR}),
    },
}

SUBMIT_DRAFT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_draft",
        "description": "Hand in the finished draft: the three files, the comments and every claim with its SQL "
        "and expectation. Call it once, when done.",
        "parameters": DRAFT_PARAMETERS,
    },
}

TOOLS = [RUN_SQL_TOOL, SUBMIT_DRAFT_TOOL]

_EXPECT_KEYS = {"scalar": {"kind", "value", "abs_tol", "rel_tol"}, "set": {"kind", "values"}, "none": {"kind"}}


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _nonblank(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def report_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """"schema.table" -> {"comment": ..., "columns": {name: comment}} from the report JSON."""
    return {
        f"{o['schema']}.{o['name']}": {"comment": o["comment"], "columns": {c["name"]: c["comment"] for c in o["columns"]}}
        for o in report["objects"]
    }


def _has_comments_table(toml_text: str) -> bool:
    try:
        return "comments" in tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError:
        return False  # validate_files reports invalid TOML


def validate_draft_args(args: Any, report: dict[str, Any]) -> list[str]:
    """Problems with the model's submit_draft arguments, each naming the field path. Empty = valid."""
    problems: list[str] = []

    def bad(msg: str) -> None:
        problems.append(msg)

    if not isinstance(args, dict):
        return ["arguments must be an object"]
    extra = sorted(set(args) - {"files", "comments", "claims"})
    if extra:
        bad(f"unknown key {extra[0]}")
    files = args.get("files")
    if not isinstance(files, dict):
        bad("files must be an object")
    else:
        for key in ("dataset_toml", "notes_md", "glossary_md"):
            if not _nonblank(files.get(key)):
                bad(f"files.{key} must be a non-blank string")
        if _nonblank(files.get("dataset_toml")) and _has_comments_table(files["dataset_toml"]):
            bad("files.dataset_toml must not have a [comments] table (export adds it)")

    index = report_index(report)
    comments = args.get("comments")
    if not isinstance(comments, list):
        bad("comments must be an array")
        comments = []
    seen: set[tuple[str, str]] = set()
    for i, c in enumerate(comments):
        where = f"comments[{i}]"
        if not isinstance(c, dict) or set(c) != {"object", "column", "comment"}:
            bad(f"{where} must have exactly object, column, comment")
            continue
        obj, col = c["object"], c["column"]
        if not isinstance(obj, str) or obj not in index:
            bad(f"{where}.object {obj!r} is not a table or view in the report")
            continue
        if not isinstance(col, str) or (col and col not in index[obj]["columns"]):
            bad(f"{where}.column {col!r} is not a column of {obj}")
            continue
        if (obj, col) in seen:
            bad(f"{where} repeats {obj}{'.' + col if col else ''}")
        seen.add((obj, col))
        if not _nonblank(c["comment"]):
            bad(f"{where}.comment must be a non-blank string")

    claims = args.get("claims")
    if not isinstance(claims, list):
        bad("claims must be an array")
        claims = []
    for i, cl in enumerate(claims):
        where = f"claims[{i}]"
        if not isinstance(cl, dict) or set(cl) != {"text", "file", "sql", "expect"}:
            bad(f"{where} must have exactly text, file, sql, expect")
            continue
        if not _nonblank(cl["text"]):
            bad(f"{where}.text must be a non-blank string")
        if not isinstance(cl["file"], str) or cl["file"] not in FILES:
            bad(f"{where}.file must be one of {', '.join(FILES)}")
        if not isinstance(cl["sql"], str):
            bad(f"{where}.sql must be a string")
        e = cl["expect"]
        kind = e.get("kind") if isinstance(e, dict) else None
        if not isinstance(kind, str) or kind not in _EXPECT_KEYS or set(e) != _EXPECT_KEYS[kind]:
            bad(f"{where}.expect must be scalar {{value, abs_tol, rel_tol}}, set {{values}} or none")
            continue
        if kind != "none" and not _nonblank(cl["sql"]):
            bad(f"{where}.sql is required for a {kind} expectation")
        if kind == "scalar":
            if not all(_is_number(e[k]) for k in ("value", "abs_tol", "rel_tol")):
                bad(f"{where}.expect value, abs_tol and rel_tol must be finite numbers")
            elif e["abs_tol"] < 0 or e["rel_tol"] < 0:
                bad(f"{where}.expect tolerances must be >= 0")
        if kind == "set":
            vals = e["values"]
            if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
                bad(f"{where}.expect.values must be an array of strings")
            elif len(set(vals)) != len(vals):
                bad(f"{where}.expect.values has duplicates")
    return problems[:MAX_PROBLEMS]
