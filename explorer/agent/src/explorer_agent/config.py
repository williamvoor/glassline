"""Settings: environment variables plus the dataset directory (parent spec §7).

`load_settings` is pure: it reads only the mapping it is given and the dataset files. The entry
point loads the repo root's `.env` first, so tests never pick up real secrets.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from explorer_agent.sql import check_dsn

FORMATS = ("express", "direct_json")
HISTORY_MODES = ("compact", "full")
SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]*")  # used with fullmatch


class ConfigError(ValueError):
    """Bad configuration. The message names the variable or file, never a value from the env."""


@dataclass(frozen=True)
class Currency:
    code: str
    symbol: str


@dataclass(frozen=True)
class OverlayEntry:
    comment: str | None
    columns: Mapping[str, str]


@dataclass(frozen=True)
class CommentOverlay:
    """The Comment overlay (ADR 0019): entries keyed by "schema.table", replacing database comments."""

    path: Path
    entries: Mapping[str, OverlayEntry]


@dataclass(frozen=True)
class Dataset:
    name: str
    description: str
    allowed_schemas: tuple[str, ...]
    notes: str
    glossary: str
    currency: Currency
    comment_overlay: CommentOverlay | None = None


@dataclass(frozen=True)
class Settings:
    model: str
    format: str
    reasoning_effort: str | None
    database_url: str
    dataset_dir: Path
    prompt_cache_key: str
    sql_timeout_ms: int
    sql_row_cap: int
    rows_to_model: int
    table_row_cap: int
    max_tool_iterations: int
    max_ui_retries: int
    agent_host: str
    agent_port: int
    client_origin: str
    log_level: str
    history_mode: str
    turn_timeout_s: int
    slow_turn_s: int
    dataset: Dataset

    def __repr__(self) -> str:  # keep the DSN (it holds a password) out of logs and tracebacks
        return f"Settings(model={self.model!r}, format={self.format!r}, dataset={self.dataset.name!r})"


_INTS = {
    "SQL_TIMEOUT_MS": 15000,
    "SQL_ROW_CAP": 5000,
    "ROWS_TO_MODEL": 20,
    "TABLE_ROW_CAP": 200,
    "MAX_TOOL_ITERATIONS": 8,
    "MAX_UI_RETRIES": 2,
    "AGENT_PORT": 10002,
    "TURN_TIMEOUT_S": 60,
    "SLOW_TURN_S": 30,
}


def _positive_int(env: Mapping[str, str], key: str) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return _INTS[key]
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a positive integer") from None
    if value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return value


def _read_text(path: Path, what: str) -> str:
    if not path.is_file():
        raise ConfigError(f"dataset {what} file not found: {path}")
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise ConfigError(f"dataset {what} file is not valid UTF-8: {path}") from None


def load_dataset(dataset_dir: Path) -> Dataset:
    toml_path = dataset_dir / "dataset.toml"
    if not toml_path.is_file():
        raise ConfigError(f"dataset file not found: {toml_path}")
    try:
        data = tomllib.loads(_read_text(toml_path, "config"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{toml_path} is not valid TOML: {e}") from None

    def key(name: str, table: Mapping[str, object] = data, prefix: str = "") -> object:
        if name not in table:
            raise ConfigError(f"{toml_path}: missing key {prefix}{name}")
        return table[name]

    schemas = key("allowed_schemas")
    if not isinstance(schemas, list) or not schemas:
        raise ConfigError(f"{toml_path}: allowed_schemas must be a non-empty list")
    for s in schemas:
        if not isinstance(s, str) or not SCHEMA_NAME.fullmatch(s):
            raise ConfigError(f"{toml_path}: allowed_schemas has an invalid schema name")
    currency = key("currency")
    if not isinstance(currency, dict):
        raise ConfigError(f"{toml_path}: [currency] must be a table")
    allowed = tuple(sorted(set(schemas)))
    overlay = None
    if "comments" in data:
        comments = data["comments"]
        if not isinstance(comments, dict):
            raise ConfigError(f"{toml_path}: [comments] must be a table")
        extra = sorted(set(comments) - {"overlay"})
        if extra:
            raise ConfigError(f"{toml_path}: [comments] has unknown key {extra[0]}")
        path = key("overlay", comments, "comments.")
        if not isinstance(path, str) or not path.strip():
            raise ConfigError(f"{toml_path}: comments.overlay must be a file name")
        overlay = load_overlay(dataset_dir / path, allowed)
    return Dataset(
        name=str(key("name")),
        description=str(key("description")),
        allowed_schemas=allowed,
        notes=_read_text(dataset_dir / str(key("notes")), "notes"),
        glossary=_read_text(dataset_dir / str(key("glossary")), "glossary"),
        currency=Currency(
            code=str(key("code", currency, "currency.")),
            symbol=str(key("symbol", currency, "currency.")),
        ),
        comment_overlay=overlay,
    )


def _comment_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_overlay(path: Path, allowed_schemas: tuple[str, ...]) -> CommentOverlay:
    """Parse and statically check a Comment overlay file. Messages never quote a comment's text."""
    if not path.is_file():
        raise ConfigError(f"comment overlay file not found: {path}")
    try:
        data = tomllib.loads(_read_text(path, "comment overlay"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None
    entries: dict[str, OverlayEntry] = {}
    for name, value in data.items():
        schema, dot, table = name.partition(".")
        if not dot or not schema or not table:
            raise ConfigError(
                f'{path}: entry {name!r} must be a quoted "schema.table" key, e.g. ["mart.my_table"]'
            )
        if schema not in allowed_schemas:
            raise ConfigError(f"{path}: entry {name!r} is not in an allowed schema")
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: entry {name!r} must be a table")
        extra = sorted(set(value) - {"comment", "columns"})
        if extra:
            raise ConfigError(f"{path}: entry {name!r} has unknown key {extra[0]!r}")
        comment = value.get("comment")
        if comment is not None and not _comment_text(comment):
            raise ConfigError(f"{path}: entry {name!r}: comment must be a non-empty string")
        columns = value.get("columns", {})
        if not isinstance(columns, dict):
            raise ConfigError(f"{path}: entry {name!r}: columns must be a table")
        for col, text in columns.items():
            if not _comment_text(text):
                raise ConfigError(f"{path}: entry {name!r}: column {col!r} must have a non-empty string comment")
        if comment is None and not columns:
            raise ConfigError(f"{path}: entry {name!r} has neither comment nor columns")
        entries[name] = OverlayEntry(comment=comment, columns=dict(columns))
    return CommentOverlay(path=path, entries=entries)


def load_settings(env: Mapping[str, str], explorer_root: Path) -> Settings:
    fmt = env.get("FORMAT", "").strip() or "express"
    if fmt not in FORMATS:
        raise ConfigError(f"FORMAT must be one of {', '.join(FORMATS)}")
    history_mode = env.get("HISTORY_MODE", "").strip() or "compact"
    if history_mode not in HISTORY_MODES:
        raise ConfigError(f"HISTORY_MODE must be one of {', '.join(HISTORY_MODES)}")
    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError("DATABASE_URL is required (the dataset's read-only login)")
    try:
        check_dsn(database_url)
    except ValueError as e:
        raise ConfigError(f"DATABASE_URL: {e}") from None
    dataset_dir = explorer_root / (env.get("DATASET_DIR", "").strip() or "datasets/austender")
    dataset = load_dataset(dataset_dir)
    return Settings(
        model=env.get("MODEL", "").strip() or "meta/muse-spark-1.3",
        format=fmt,
        reasoning_effort=env.get("REASONING_EFFORT", "minimal").strip() or None,
        database_url=database_url,
        dataset_dir=dataset_dir,
        prompt_cache_key=env.get("PROMPT_CACHE_KEY", "").strip() or f"explorer-{dataset.name}",
        sql_timeout_ms=_positive_int(env, "SQL_TIMEOUT_MS"),
        sql_row_cap=_positive_int(env, "SQL_ROW_CAP"),
        rows_to_model=_positive_int(env, "ROWS_TO_MODEL"),
        table_row_cap=_positive_int(env, "TABLE_ROW_CAP"),
        max_tool_iterations=_positive_int(env, "MAX_TOOL_ITERATIONS"),
        max_ui_retries=_positive_int(env, "MAX_UI_RETRIES"),
        agent_host=env.get("AGENT_HOST", "").strip() or "127.0.0.1",
        agent_port=_positive_int(env, "AGENT_PORT"),
        client_origin=env.get("CLIENT_ORIGIN", "").strip() or "http://127.0.0.1:5173",
        log_level=env.get("LOG_LEVEL", "").strip() or "INFO",
        history_mode=history_mode,
        turn_timeout_s=_positive_int(env, "TURN_TIMEOUT_S"),
        slow_turn_s=_positive_int(env, "SLOW_TURN_S"),
        dataset=dataset,
    )
