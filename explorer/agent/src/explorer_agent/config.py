"""Settings: environment variables plus the dataset directory (parent spec §7).

`load_settings` is pure: it reads only the mapping it is given and the dataset files. The entry
point loads the repo root's `.env` first, so tests never pick up real secrets.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from explorer_agent.boards import Board, BoardError, load_boards
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
    examples: tuple[str, ...] = ()  # example questions for the client; never in the prompt
    boards: Mapping[str, Board] = field(default_factory=dict)  # SPEC §18; empty: no Explorer tab


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
    # Actions and drill-down (SPEC §15)
    drill_prefetch_rows: int = 3
    drill_prefetch_timeout_ms: int = 3000
    drill_check_timeout_ms: int = 2000
    max_drills_per_surface: int = 2
    max_action_sources: int = 50
    max_prefetched: int = 30
    max_pending_pairs: int = 5
    # Boards (SPEC §18)
    board_query_concurrency: int = 2
    board_row_cap: int = 500
    board_cache_ttl_s: int = 300
    max_board_cache: int = 20
    board_rows_to_model: int = 5
    board_pair_max_chars: int = 6000
    max_control_options: int = 100
    max_board_instances: int = 10
    board_prefetch_rows: int = 1
    # Behind a proxy (specs/deployment): the URL the browser reaches the agent at, for the card.
    agent_public_url: str | None = None
    # A public deploy (specs/deployment): model runs per UTC day across all users; 0 = no cap.

    def __repr__(self) -> str:  # keep the DSN (it holds a password) out of logs and tracebacks
        return f"Settings(model={self.model!r}, format={self.format!r}, dataset={self.dataset.name!r})"


_INTS = {
    "SQL_TIMEOUT_MS": 15000,
    "MAX_TURNS_PER_DAY": 0,
    "SQL_ROW_CAP": 5000,
    "ROWS_TO_MODEL": 20,
    "TABLE_ROW_CAP": 200,
    "MAX_TOOL_ITERATIONS": 8,
    "MAX_UI_RETRIES": 2,
    "AGENT_PORT": 10002,
    "TURN_TIMEOUT_S": 60,
    "SLOW_TURN_S": 30,
    "DRILL_PREFETCH_ROWS": 3,
    "DRILL_PREFETCH_TIMEOUT_MS": 3000,
    "DRILL_CHECK_TIMEOUT_MS": 2000,
    "MAX_DRILLS_PER_SURFACE": 2,
    "MAX_ACTION_SOURCES": 50,
    "MAX_PREFETCHED": 30,
    "MAX_PENDING_PAIRS": 5,
    "BOARD_QUERY_CONCURRENCY": 2,
    "BOARD_ROW_CAP": 500,
    "BOARD_CACHE_TTL_S": 300,
    "MAX_BOARD_CACHE": 20,
    "BOARD_ROWS_TO_MODEL": 5,
    "BOARD_PAIR_MAX_CHARS": 6000,
    "MAX_CONTROL_OPTIONS": 100,
    "MAX_BOARD_INSTANCES": 10,
    "BOARD_PREFETCH_ROWS": 1,
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


def _non_negative_int(env: Mapping[str, str], key: str) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return _INTS[key]
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a whole number, 0 or more") from None
    if value < 0:
        raise ConfigError(f"{key} must be a whole number, 0 or more")
    return value


def _public_url(env: Mapping[str, str]) -> str | None:
    """AGENT_PUBLIC_URL: an absolute http(s) URL without query or fragment, or None when unset.
    The browser's A2A client posts to the card's `url`, so this is where a proxy exposes the agent."""
    raw = env.get("AGENT_PUBLIC_URL", "").strip()
    if not raw:
        return None
    error = "AGENT_PUBLIC_URL must be an absolute http(s) URL with a valid host and port, without query or fragment"
    try:
        parts = urlsplit(raw)
        # urlsplit defers port validation until this property is accessed.
        _ = parts.port
        hostname = parts.hostname
    except ValueError:
        raise ConfigError(error) from None
    if parts.scheme not in ("http", "https") or not hostname or parts.query or parts.fragment:
        raise ConfigError(error)
    return raw


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
    examples = data.get("examples", [])
    if not isinstance(examples, list) or not all(isinstance(e, str) and e.strip() for e in examples):
        raise ConfigError(f"{toml_path}: examples must be a list of non-empty strings")
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
        examples=tuple(e.strip() for e in examples),
        boards=_boards(dataset_dir, allowed),
    )


def _boards(dataset_dir: Path, allowed: tuple[str, ...]) -> dict[str, Board]:
    try:
        return load_boards(dataset_dir, allowed)
    except BoardError as e:
        raise ConfigError(str(e)) from None


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


def load_settings(env: Mapping[str, str], dataset_dir: Path, database_url: str) -> Settings:
    """One Workspace's Settings: its Dataset directory and read-only login, the rest from `env`
    (ADR 0048: there is no single-Dataset mode, so neither comes from the environment)."""
    fmt = env.get("FORMAT", "").strip() or "express"
    if fmt not in FORMATS:
        raise ConfigError(f"FORMAT must be one of {', '.join(FORMATS)}")
    history_mode = env.get("HISTORY_MODE", "").strip() or "compact"
    if history_mode not in HISTORY_MODES:
        raise ConfigError(f"HISTORY_MODE must be one of {', '.join(HISTORY_MODES)}")
    database_url = database_url.strip()
    if not database_url:
        raise ConfigError("the dataset's read-only login is required")
    try:
        check_dsn(database_url)
    except ValueError as e:
        raise ConfigError(f"login: {e}") from None
    dataset = load_dataset(dataset_dir)
    if dataset.boards and fmt != "express":
        raise ConfigError(f"the dataset has Boards, whose layouts are Express, so FORMAT must be express (it is {fmt})")
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
        drill_prefetch_rows=_non_negative_int(env, "DRILL_PREFETCH_ROWS"),
        drill_prefetch_timeout_ms=_positive_int(env, "DRILL_PREFETCH_TIMEOUT_MS"),
        drill_check_timeout_ms=_positive_int(env, "DRILL_CHECK_TIMEOUT_MS"),
        max_drills_per_surface=_positive_int(env, "MAX_DRILLS_PER_SURFACE"),
        max_action_sources=_positive_int(env, "MAX_ACTION_SOURCES"),
        max_prefetched=_positive_int(env, "MAX_PREFETCHED"),
        max_pending_pairs=_positive_int(env, "MAX_PENDING_PAIRS"),
        board_query_concurrency=_positive_int(env, "BOARD_QUERY_CONCURRENCY"),
        board_row_cap=_positive_int(env, "BOARD_ROW_CAP"),
        board_cache_ttl_s=_positive_int(env, "BOARD_CACHE_TTL_S"),
        max_board_cache=_positive_int(env, "MAX_BOARD_CACHE"),
        board_rows_to_model=_positive_int(env, "BOARD_ROWS_TO_MODEL"),
        board_pair_max_chars=_positive_int(env, "BOARD_PAIR_MAX_CHARS"),
        max_control_options=_positive_int(env, "MAX_CONTROL_OPTIONS"),
        max_board_instances=_positive_int(env, "MAX_BOARD_INSTANCES"),
        board_prefetch_rows=_non_negative_int(env, "BOARD_PREFETCH_ROWS"),
        agent_public_url=_public_url(env),
    )


# --- Workspaces (specs/workspaces/SPEC.md, phase 1) ----------------------------------------------

WORKSPACE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")  # fullmatch; the URL segment
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
WORKSPACE_KEYS = frozenset({"name", "dataset_dir", "connection", "model", "reasoning_effort", "sql_timeout_ms",
                            "sql_row_cap"})
LEGACY_LOGIN_KEY = "dsn_env"  # the old form (ADR 0051): read only so Settings can migrate it
# B3: a Workspace's `model` override, per the repo's model rules.
WORKSPACE_MODELS = ("meta/muse-spark-1.3", "meta/muse-wonder-1.0")
WORKSPACES_FILE = "workspaces.toml"
NEEDS_MIGRATION = "needs migration: Settings rewrites dsn_env as a Connection when it starts"


# --- Connections (specs/connections/SPEC.md, ADR 0051) -------------------------------------------

CONNECTIONS_FILE = "connections.toml"
CONNECTION_KEYS = frozenset({"name", "label", "url_env"})
CONNECTION_URL_ENV = re.compile(r"[A-Z][A-Z0-9_]*_DATABASE_URL")  # fullmatch
CLI_URL_ENV = "BOOTSTRAP_DATABASE_URL"  # the bootstrap CLI's own login, never a Connection's
MAX_LABEL = 80


@dataclass(frozen=True)
class Connection:
    """One named read-only login. `url_env` names the variable; the URL itself is never kept."""

    name: str
    label: str
    url_env: str


@dataclass(frozen=True)
class ConnectionList:
    """`connections.toml` as read: the valid Connections, the reason each bad one fails, and a
    whole-file error (bad TOML) that makes every Connection unusable."""

    connections: Mapping[str, Connection] = field(default_factory=dict)
    errors: Mapping[str, str] = field(default_factory=dict)
    file_error: str | None = None

    def resolve(self, name: str) -> Connection:
        """The Connection called `name`, or WorkspaceUnavailable with the reason."""
        if self.file_error:
            raise WorkspaceUnavailable(f"{CONNECTIONS_FILE}: {self.file_error}")
        if name in self.errors:
            raise WorkspaceUnavailable(f"Connection {name!r} is invalid: {self.errors[name]}")
        if name not in self.connections:
            raise WorkspaceUnavailable(f"no Connection named {name!r}")
        return self.connections[name]


def connection_entry(item: object) -> Connection:
    """One `[[connection]]` table, checked (A1). Raises ConfigError with the reason."""
    if not isinstance(item, dict):
        raise ConfigError("an entry must be a table")
    for value in item.values():
        if isinstance(value, str) and "://" in value:
            raise ConfigError("logins go in .env, not in the file")
    unknown = sorted(set(item) - CONNECTION_KEYS)
    if unknown:
        raise ConfigError(f"unknown key {unknown[0]}")
    name = item.get("name")
    if not isinstance(name, str) or not WORKSPACE_NAME.fullmatch(name):
        raise ConfigError("name must match [a-z0-9][a-z0-9_-]*")
    url_env = item.get("url_env")
    if not isinstance(url_env, str) or not CONNECTION_URL_ENV.fullmatch(url_env):
        raise ConfigError("url_env must be a variable named <NAME>_DATABASE_URL")
    if url_env == CLI_URL_ENV:
        raise ConfigError(f"url_env can't be {CLI_URL_ENV} (the bootstrap CLI's login)")
    label = item.get("label", name)
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > MAX_LABEL or "\n" in label:
        raise ConfigError(f"label must be one line of 1-{MAX_LABEL} characters")
    return Connection(name, label.strip(), url_env)


def parse_connections(text: str) -> ConnectionList:
    """Parse the list. Duplicate names or variables fail both entries' later twin (A1)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return ConnectionList(file_error=f"not valid TOML: {e}")
    extra = sorted(set(data) - {"connection"})
    if extra:
        return ConnectionList(file_error=f"unknown key {extra[0]}")
    items = data.get("connection", [])
    if not isinstance(items, list):
        return ConnectionList(file_error="connection must be an array of tables ([[connection]])")
    good: dict[str, Connection] = {}
    errors: dict[str, str] = {}
    used_vars: dict[str, str] = {}
    for i, item in enumerate(items, 1):
        written = item.get("name") if isinstance(item, dict) else None
        key = written if isinstance(written, str) and written else f"#{i}"
        try:
            conn = connection_entry(item)
        except ConfigError as e:
            errors.setdefault(key, str(e))
            continue
        if conn.name in good or conn.name in errors:
            errors[conn.name] = "duplicate name"
            good.pop(conn.name, None)
            continue
        if conn.url_env in used_vars:
            errors[conn.name] = f"{conn.url_env} is already Connection {used_vars[conn.url_env]!r}"
            continue
        used_vars[conn.url_env] = conn.name
        good[conn.name] = conn
    return ConnectionList(good, errors)


def load_connections(path: Path) -> ConnectionList:
    """The Connection list beside the Workspace list. A missing file is an empty list."""
    if not path.is_file():
        return ConnectionList()
    return parse_connections(_read_text(path, "connection list"))


class WorkspaceUnavailable(Exception):
    """A Workspace that fails a check. The message is its reason: it never holds a DSN."""


@dataclass(frozen=True)
class WorkspaceEntry:
    """One valid `[[workspace]]` entry. `overrides` are environment-style (G14)."""

    name: str
    dataset_dir: Path
    connection: str | None  # None: an old `dsn_env` entry that needs migration
    overrides: Mapping[str, str]
    raw: Mapping[str, object]  # the entry as written, for change detection on reload


@dataclass(frozen=True)
class WorkspaceListing:
    """A list position: a valid entry, or the reason it fails (W2). `name` is as written."""

    name: str
    entry: WorkspaceEntry | None
    error: str | None = None


def data_dir(raw: str, repo_root: Path) -> Path:
    """`EXPLORER_DATA_DIR`, resolved, created 0700 if missing, refused inside the repo (W3):
    saved Conversations hold questions and model history."""
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():  # the agent and the bootstrap server run from different directories
        raise ConfigError("EXPLORER_DATA_DIR must be an absolute path (or start with ~)")
    path = expanded.resolve()
    root = repo_root.resolve()
    if path == root or path.is_relative_to(root):
        raise ConfigError("EXPLORER_DATA_DIR must be outside the repo (saved Conversations hold questions)")
    if not path.exists():  # an existing directory keeps its mode (refuter F9)
        path.mkdir(parents=True, mode=0o700)
        path.chmod(0o700)
    elif not path.is_dir():
        raise ConfigError("EXPLORER_DATA_DIR is not a directory")
    return path


def _override(entry: Mapping[str, object], key: str) -> str | None:
    if key not in entry:
        return None
    value = entry[key]
    if key == "model":
        if value not in WORKSPACE_MODELS:
            raise ConfigError(f"model must be one of {', '.join(WORKSPACE_MODELS)}")
        return str(value)
    if key == "reasoning_effort":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError("reasoning_effort must be a non-empty string")
        return value.strip()
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return str(value)


def _workspace_entry(item: object, repo_root: Path) -> WorkspaceEntry:
    if not isinstance(item, dict):
        raise ConfigError("an entry must be a table")
    unknown = sorted(set(item) - WORKSPACE_KEYS - {LEGACY_LOGIN_KEY})
    if unknown:
        raise ConfigError(f"unknown key {unknown[0]}")
    name = item.get("name")
    if not isinstance(name, str) or not WORKSPACE_NAME.fullmatch(name):
        raise ConfigError("name must match [a-z0-9][a-z0-9_-]*")
    dataset = item.get("dataset_dir")
    if not isinstance(dataset, str) or not dataset.strip():
        raise ConfigError("dataset_dir is required")
    if "connection" in item and LEGACY_LOGIN_KEY in item:
        raise ConfigError("connection and dsn_env can't both be set")
    connection: str | None = None
    if LEGACY_LOGIN_KEY in item:
        if not isinstance(item[LEGACY_LOGIN_KEY], str) or not ENV_NAME.fullmatch(item[LEGACY_LOGIN_KEY]):
            raise ConfigError("dsn_env must be an environment variable name")
    else:
        connection = item.get("connection")
        if not isinstance(connection, str) or not WORKSPACE_NAME.fullmatch(connection):
            raise ConfigError("connection must name a Connection ([a-z0-9][a-z0-9_-]*)")
    path = Path(dataset).expanduser()
    overrides = {}
    for key, env_key in (("model", "MODEL"), ("reasoning_effort", "REASONING_EFFORT"),
                         ("sql_timeout_ms", "SQL_TIMEOUT_MS"), ("sql_row_cap", "SQL_ROW_CAP")):
        value = _override(item, key)
        if value is not None:
            overrides[env_key] = value
    return WorkspaceEntry(name, (path if path.is_absolute() else repo_root / path).resolve(), connection,
                          overrides, dict(item))


def load_workspace_list(path: Path, repo_root: Path) -> list[WorkspaceListing]:
    """The Workspace list in file order. A bad entry fails alone (W2); a file that isn't TOML
    raises ConfigError. A missing file is an empty list."""
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(_read_text(path, "workspace list"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None
    extra = sorted(set(data) - {"workspace"})
    if extra:
        raise ConfigError(f"{path}: unknown key {extra[0]}")
    items = data.get("workspace", [])
    if not isinstance(items, list):
        raise ConfigError(f"{path}: workspace must be an array of tables ([[workspace]])")
    out: list[WorkspaceListing] = []
    seen: set[str] = set()
    for i, item in enumerate(items, 1):
        written = item.get("name") if isinstance(item, dict) else None
        label = written if isinstance(written, str) and written else f"#{i}"
        try:
            entry = _workspace_entry(item, repo_root)
        except ConfigError as e:
            out.append(WorkspaceListing(label, None, f"invalid entry: {e}"))
            continue
        if entry.name in seen:
            out.append(WorkspaceListing(label, None, "invalid entry: duplicate name"))
            continue
        seen.add(entry.name)
        out.append(WorkspaceListing(entry.name, entry))
    return out


def connection_url_env(entry: WorkspaceEntry, connections: ConnectionList) -> str:
    """The variable holding the Workspace's login, through its Connection. Raises
    WorkspaceUnavailable: needs migration, or the Connection is missing or invalid."""
    if entry.connection is None:
        var = str(entry.raw.get(LEGACY_LOGIN_KEY, ""))
        if not CONNECTION_URL_ENV.fullmatch(var) or var == CLI_URL_ENV:  # Settings' migration skips these
            raise WorkspaceUnavailable(f"needs migration: {var} can't be a Connection's login; rename it to "
                                       "<NAME>_DATABASE_URL in .env and in workspaces.toml, then restart")
        raise WorkspaceUnavailable(NEEDS_MIGRATION)
    return connections.resolve(entry.connection).url_env


def workspace_settings(env: Mapping[str, str], entry: WorkspaceEntry, explorer_root: Path,
                       connections: ConnectionList) -> Settings:
    """A Workspace's Settings (B2): the process environment, with its login from its Connection,
    its Dataset path, its overrides and its own `prompt_cache_key`. Raises WorkspaceUnavailable."""
    url_env = connection_url_env(entry, connections)
    dsn = env.get(url_env, "").strip()
    if not dsn:
        raise WorkspaceUnavailable(f"`{url_env}` not set")
    try:
        check_dsn(dsn)
    except ValueError:
        raise WorkspaceUnavailable(f"`{url_env}` is malformed") from None
    base = env.get("PROMPT_CACHE_KEY", "").strip()
    local = {**env, **entry.overrides,
             "PROMPT_CACHE_KEY": f"{base}-{entry.name}" if base else f"explorer-{entry.name}"}
    try:
        return load_settings(local, entry.dataset_dir, dsn)
    except ConfigError as e:
        raise WorkspaceUnavailable(f"dataset invalid: {e}") from None


def required_data_dir(env: Mapping[str, str], repo_root: Path) -> Path:
    """`EXPLORER_DATA_DIR`, which the explorer can't run without (ADR 0048)."""
    raw = env.get("EXPLORER_DATA_DIR", "").strip()
    if not raw:
        raise ConfigError("EXPLORER_DATA_DIR is required (the Workspaces directory, outside the repo; see .env.example)")
    return data_dir(raw, repo_root)


def named_workspace_settings(env: Mapping[str, str], name: str, repo_root: Path, explorer_root: Path) -> Settings:
    """The Settings of the Workspace called `name` in `EXPLORER_DATA_DIR`, for the dev tools
    (`boards`, `scripts/scenarios.py`). Raises ConfigError naming it when it's missing or unusable."""
    root = required_data_dir(env, repo_root)
    path = root / WORKSPACES_FILE
    connections = load_connections(root / CONNECTIONS_FILE)
    for item in load_workspace_list(path, repo_root):
        if item.name != name:
            continue
        if item.entry is None:
            raise ConfigError(f"Workspace {name!r}: {item.error}")
        try:
            return workspace_settings(env, item.entry, explorer_root, connections)
        except WorkspaceUnavailable as e:
            raise ConfigError(f"Workspace {name!r}: {e}") from None
    raise ConfigError(f"no Workspace named {name!r} in {path}")


@dataclass(frozen=True)
class ProcessSettings:
    """What Workspace mode reads for the process itself; the rest is per Workspace (B2)."""

    agent_host: str
    agent_port: int
    client_origin: str
    log_level: str
    agent_public_url: str | None = None
    max_turns_per_day: int = 0  # one budget for the process, shared by every Workspace
    turn_limit_contact: str | None = None


def process_settings(env: Mapping[str, str]) -> ProcessSettings:
    return ProcessSettings(
        agent_host=env.get("AGENT_HOST", "").strip() or "127.0.0.1",
        agent_port=_positive_int(env, "AGENT_PORT"),
        client_origin=env.get("CLIENT_ORIGIN", "").strip() or "http://127.0.0.1:5173",
        log_level=env.get("LOG_LEVEL", "").strip() or "INFO",
        agent_public_url=_public_url(env),
        max_turns_per_day=_non_negative_int(env, "MAX_TURNS_PER_DAY"),
        turn_limit_contact=env.get("TURN_LIMIT_CONTACT", "").strip() or None,
    )
