"""The `submit_draft` tool (ADR 0023): its JSON schema and our own check of the arguments.

The schema stays inside Meta's strict subset (no oneOf, additionalProperties false, every key
required) even though we don't send `strict`, so a later switch to structured output is easy.
"""

from __future__ import annotations

import copy
import math
import tomllib
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from explorer_bootstrap.json_shape import MAX_PROBLEMS, shape_problems

FILES = ("dataset.toml", "notes.md", "glossary.md", "comments")


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

_DRAFT_VALIDATOR = Draft202012Validator(DRAFT_PARAMETERS)


@lru_cache
def _claims_validator(extra_keys: frozenset[str]) -> Draft202012Validator:
    schema = copy.deepcopy(DRAFT_PARAMETERS["properties"]["claims"])
    schema["items"]["properties"].update({key: {} for key in extra_keys})
    return Draft202012Validator(schema)


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


def _has_examples(toml_text: str) -> bool | None:
    """Whether the drafted dataset.toml proposes starter examples: True, False, or None
    when the TOML doesn't parse (validate_files reports invalid TOML)."""
    try:
        examples = tomllib.loads(toml_text).get("examples", [])
    except tomllib.TOMLDecodeError:
        return None
    return isinstance(examples, list) and len(examples) > 0


def validate_draft_args(args: Any, report: dict[str, Any]) -> list[str]:
    """Problems with the model's submit_draft arguments, each naming the field path. Empty = valid."""
    problems = shape_problems(_DRAFT_VALIDATOR, args)
    if problems:
        return problems
    bad = problems.append
    files = args["files"]
    for key in ("dataset_toml", "notes_md", "glossary_md"):
        if not _nonblank(files[key]):
            bad(f"files.{key} must be a non-blank string")
    if _has_comments_table(files["dataset_toml"]):
        bad("files.dataset_toml must not have a [comments] table (export adds it)")
    if _has_examples(files["dataset_toml"]) is False:
        bad("files.dataset_toml must propose starter examples (examples = [...])")

    index = report_index(report)
    seen: set[tuple[str, str]] = set()
    for i, c in enumerate(args["comments"]):
        where = f"comments[{i}]"
        obj, col = c["object"], c["column"]
        if obj not in index:
            bad(f"{where}.object is not a table or view in the report")
            continue
        if col and col not in index[obj]["columns"]:
            bad(f"{where}.column is not a column of {obj}")
            continue
        if (obj, col) in seen:
            bad(f"{where} repeats {obj}{'.' + col if col else ''}")
        seen.add((obj, col))
        if not _nonblank(c["comment"]):
            bad(f"{where}.comment must be a non-blank string")

    problems.extend(_claim_semantics(args["claims"]))
    return problems[:MAX_PROBLEMS]


def claim_problems(claims: Any, *, extra_keys: frozenset[str] = frozenset()) -> list[str]:
    """Problems with a list of claims (the model's, or the person's edits with `extra_keys`)."""
    problems = shape_problems(_claims_validator(extra_keys), claims)
    if problems:
        # This validator's root is the claims array, not the whole tool argument object.
        return [p.replace("arguments", "claims", 1) if p.startswith("arguments") else "claims" + p
                for p in problems]
    return _claim_semantics(claims)


def _claim_semantics(claims: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    bad = problems.append
    for i, cl in enumerate(claims):
        where = f"claims[{i}]"
        if not _nonblank(cl["text"]):
            bad(f"{where}.text must be a non-blank string")
        e = cl["expect"]
        kind = e["kind"]
        if kind != "none" and not _nonblank(cl["sql"]):
            bad(f"{where}.sql is required for a {kind} expectation")
        if kind == "scalar":
            if not all(_is_number(e[k]) for k in ("value", "abs_tol", "rel_tol")):
                bad(f"{where}.expect value, abs_tol and rel_tol must be finite numbers")
            elif e["abs_tol"] < 0 or e["rel_tol"] < 0:
                bad(f"{where}.expect tolerances must be >= 0")
        if kind == "set":
            vals = e["values"]
            if len(set(vals)) != len(vals):
                bad(f"{where}.expect.values has duplicates")
    return problems[:MAX_PROBLEMS]
