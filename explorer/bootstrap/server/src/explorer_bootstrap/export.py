"""Write a reviewed draft to a dataset directory (SPEC.md phase 2b, "Export").

Never commits, never runs DDL: `comments.sql` is for the person to apply.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from explorer_agent.boards import QUERY_NAME

from explorer_bootstrap.claims import export_blockers
from explorer_bootstrap.drafts import FILE_NAMES, accepted_overlay, validate_files

NAME = re.compile(r"[a-z][a-z0-9_]*")
BOARDS_DIR = "boards"
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


@dataclass(frozen=True)
class ExportResult:
    dest: Path
    overwrote: list[str] = field(default_factory=list)  # file names replaced in an existing directory
    backup: Path | None = None  # where the replaced files were kept, if any


def _utc_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")


def export_dataset(draft: dict[str, Any], name: str, target: str, root: Path, *, force: bool = False,
                   boards: list[dict[str, Any]] | None = None, board_problems: list[str] | tuple[str, ...] = ()) -> ExportResult:
    """Write the draft's files to root/name: dataset.toml, notes.md, glossary.md and the
    accepted comments (comments.toml or comments.sql), and the Boards (`[{name, toml}]`, phase 3)
    to boards/. `board_problems` are the Boards' check problems: any refuses the export.

    A new directory is written atomically (temp sibling, then rename). With force=True an
    existing directory is kept and only those files are replaced, anything else in it
    (scenarios, examples, a previous comments file for the other target) left alone; files
    it replaces are kept in a dot-prefixed sibling backup the dataset picker ignores.
    Under force, an existing boards/ is replaced whole (and kept in the backup) when Boards are
    given, and left alone when they aren't. Raises ExportError with every reason."""
    reasons = []
    if not NAME.fullmatch(name or ""):
        reasons.append("name must be lowercase letters, digits and underscores, starting with a letter")
    if target not in TARGETS:
        reasons.append("target must be toml or sql")
    dest = root / name if name else root
    if name and dest.exists() and not force:
        reasons.append(f"{dest} already exists")
    if name and dest.exists() and force and not dest.is_dir():
        reasons.append(f"{dest} is not a directory")
    if not root.is_dir():
        reasons.append(f"{root} is not a directory")
    validation = validate_files(draft)
    reasons.extend(validation["errors"])
    reasons.extend(export_blockers(draft["claims"]))
    reasons.extend(board_problems)
    for b in boards or []:  # callers check the Boards first; the file name is still never trusted
        if not QUERY_NAME.fullmatch(str(b.get("name", ""))):
            reasons.append(f"boards: {b.get('name')!r} is not a Board file name")
    if reasons:
        raise ExportError(reasons)

    files = draft["files"]
    dataset_toml = files["dataset_toml"].rstrip("\n") + "\n"
    if target == "toml":
        dataset_toml += '\n[comments]\noverlay = "comments.toml"\n'
    new_texts = {
        file_name: dataset_toml if key == "dataset_toml" else files[key]
        for key, file_name in FILE_NAMES.items()
    }
    new_texts["comments.toml" if target == "toml" else "comments.sql"] = (
        render_comments_toml(draft) if target == "toml" else render_comments_sql(draft)
    )
    if not (name and dest.exists()):
        tmp = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
        try:
            for file_name, text in new_texts.items():
                (tmp / file_name).write_text(text, encoding="utf-8")
            if boards:
                _write_boards(tmp / BOARDS_DIR, boards)
            umask = os.umask(0)
            os.umask(umask)
            tmp.chmod(0o777 & ~umask)  # mkdtemp makes it 0700; give it the usual directory mode
            tmp.rename(dest)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return ExportResult(dest)
    overwrote = sorted(f for f in new_texts if (dest / f).exists())
    old_boards = dest / BOARDS_DIR
    replace_boards = bool(boards) and old_boards.is_dir()
    backup: Path | None = None
    if overwrote or replace_boards:
        backup = root / f".{name}.bak-{_utc_stamp()}"
        backup.mkdir(mode=0o777)
        for f in overwrote:
            shutil.copy2(dest / f, backup / f)
        if replace_boards:
            shutil.copytree(old_boards, backup / BOARDS_DIR)
            overwrote.append(f"{BOARDS_DIR}/")
    try:
        for file_name, text in new_texts.items():
            tmp_file = dest / f".{file_name}.new"
            tmp_file.write_text(text, encoding="utf-8")
            os.replace(tmp_file, dest / file_name)  # atomic per file: readers never see half a file
    except BaseException:
        for leftover in dest.glob(".*.new"):
            leftover.unlink(missing_ok=True)
        raise
    if boards:
        _replace_boards(dest, boards)
    return ExportResult(dest, overwrote, backup)


def _write_boards(directory: Path, boards: list[dict[str, Any]]) -> None:
    directory.mkdir(mode=0o777)
    for b in boards:
        (directory / f"{b['name']}.toml").write_text(b["toml"], encoding="utf-8")


def _replace_boards(dest: Path, boards: list[dict[str, Any]]) -> None:
    """boards/ replaced whole: written beside it, then swapped in, so a Board left out of the new
    set doesn't linger and a failure leaves the old directory in place."""
    new = Path(tempfile.mkdtemp(prefix=f".{BOARDS_DIR}-", dir=dest))
    old = dest / f".{BOARDS_DIR}.old"
    try:
        new.rmdir()
        _write_boards(new, boards)
        if (dest / BOARDS_DIR).exists():
            (dest / BOARDS_DIR).rename(old)
        new.rename(dest / BOARDS_DIR)
    except BaseException:
        shutil.rmtree(new, ignore_errors=True)
        if old.exists() and not (dest / BOARDS_DIR).exists():
            old.rename(dest / BOARDS_DIR)
        raise
    shutil.rmtree(old, ignore_errors=True)
