"""Write a reviewed draft to a new dataset directory (SPEC.md phase 2b, "Export").

Never commits, never runs DDL: `comments.sql` is for the person to apply.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from explorer_bootstrap.claims import export_blockers
from explorer_bootstrap.drafts import FILE_NAMES, accepted_overlay, validate_files

NAME = re.compile(r"[a-z][a-z0-9_]*")
TARGETS = ("toml", "sql")
_COMMENT_ON = {"table": "TABLE", "view": "VIEW", "materialized view": "MATERIALIZED VIEW",
               "foreign table": "FOREIGN TABLE"}


class ExportError(ValueError):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _toml_string(text: str) -> str:
    # A JSON string is a TOML basic string, except: ensure_ascii would emit surrogate pairs for
    # characters outside the BMP (TOML rejects those), and DEL must be escaped in TOML.
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def _column_order(report: dict[str, Any]) -> dict[str, list[str]]:
    return {f"{o['schema']}.{o['name']}": [c["name"] for c in o["columns"]] for o in report["objects"]}


def render_comments_toml(draft: dict[str, Any]) -> str:
    order = _column_order(draft["report"])
    blocks = []
    for name, entry in sorted(accepted_overlay(draft).entries.items()):
        key = _toml_string(name)
        lines = [f"[{key}]"]
        if entry.comment is not None:
            lines.append(f"comment = {_toml_string(entry.comment)}")
        if entry.columns:
            lines.append(f"[{key}.columns]")
            lines.extend(
                f"{_toml_string(col)} = {_toml_string(entry.columns[col])}" for col in order[name] if col in entry.columns
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(text: str) -> str:
    # standard_conforming_strings is on by default since PostgreSQL 9.1: backslashes are literal.
    return "'" + text.replace("'", "''") + "'"


def render_comments_sql(draft: dict[str, Any]) -> str:
    report = draft["report"]
    kinds = {f"{o['schema']}.{o['name']}": o["kind"] for o in report["objects"]}
    order = _column_order(report)
    lines = ["BEGIN;"]
    for name, entry in sorted(accepted_overlay(draft).entries.items()):
        schema, table = name.split(".", 1)
        target = f"{quote_ident(schema)}.{quote_ident(table)}"
        if entry.comment is not None:
            lines.append(f"COMMENT ON {_COMMENT_ON[kinds[name]]} {target} IS {quote_literal(entry.comment)};")
        for col in order[name]:
            if col in entry.columns:
                lines.append(f"COMMENT ON COLUMN {target}.{quote_ident(col)} IS {quote_literal(entry.columns[col])};")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def export_dataset(draft: dict[str, Any], name: str, target: str, root: Path) -> Path:
    """Write root/name atomically (temp sibling, then rename). Raises ExportError with every reason."""
    reasons = []
    if not NAME.fullmatch(name or ""):
        reasons.append("name must be lowercase letters, digits and underscores, starting with a letter")
    if target not in TARGETS:
        reasons.append("target must be toml or sql")
    dest = root / name if name else root
    if name and dest.exists():
        reasons.append(f"{dest} already exists")
    if not root.is_dir():
        reasons.append(f"{root} is not a directory")
    validation = validate_files(draft)
    reasons.extend(validation["errors"])
    reasons.extend(export_blockers(draft["claims"]))
    if reasons:
        raise ExportError(reasons)

    files = draft["files"]
    dataset_toml = files["dataset_toml"].rstrip("\n") + "\n"
    if target == "toml":
        dataset_toml += '\n[comments]\noverlay = "comments.toml"\n'
    tmp = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
    try:
        for key, file_name in FILE_NAMES.items():
            text = dataset_toml if key == "dataset_toml" else files[key]
            (tmp / file_name).write_text(text, encoding="utf-8")
        if target == "toml":
            (tmp / "comments.toml").write_text(render_comments_toml(draft), encoding="utf-8")
        else:
            (tmp / "comments.sql").write_text(render_comments_sql(draft), encoding="utf-8")
        umask = os.umask(0)
        os.umask(umask)
        tmp.chmod(0o777 & ~umask)  # mkdtemp makes it 0700; give it the usual directory mode
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest
