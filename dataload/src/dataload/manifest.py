"""`load.toml`: what to load, where, and with which owner login (SPEC "Manifest", L4, L8, T9, T12).

A manifest never holds a secret: the owner login is a URL variable, or split fields whose
password comes only from the environment. Nothing here ever returns or prints a login.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from psycopg.conninfo import make_conninfo

from dataload.names import normalise

ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
SCHEMA = re.compile(r"[a-z_][a-z0-9_]*")
TYPES = ("text", "boolean", "bigint", "numeric", "double precision", "date", "timestamp", "timestamptz")
MANIFEST_KEYS = {"staging_schema", "readonly_role", "grant_schemas", "transforms", "owner", "source"}
SOURCE_KEYS = {"file", "table", "sheet", "header_row", "header_match", "null_values", "types", "delimiter",
               "encoding", "thousands"}
OWNER_FIELDS = ("host", "port", "database", "user")
OWNER_KEYS = {"url_env", "password_env", *OWNER_FIELDS, *(f"{f}_env" for f in OWNER_FIELDS)}


class ManifestError(ValueError):
    """A manifest that can't be used. The message never holds a login."""


@dataclass(frozen=True)
class Owner:
    """How to find the owner login: `url_env`, or split fields (`*_env` names or literals)."""

    url_env: str | None = None
    fields: Mapping[str, str] = field(default_factory=dict)  # host/port/database/user -> literal
    field_envs: Mapping[str, str] = field(default_factory=dict)  # host/port/database/user -> variable
    password_env: str | None = None

    def conninfo(self, env: Mapping[str, str]) -> str:
        """The login, from the environment. Raises ManifestError naming the missing variable only."""
        if self.url_env:
            value = env.get(self.url_env, "").strip()
            if not value:
                raise ManifestError(f"{self.url_env} is not set")
            return value
        parts: dict[str, str] = dict(self.fields)
        for key, var in self.field_envs.items():
            value = env.get(var, "").strip()
            if not value:
                raise ManifestError(f"{var} is not set")
            parts[key] = value
        if self.password_env:
            password = env.get(self.password_env, "")
            if not password:
                raise ManifestError(f"{self.password_env} is not set")
            parts["password"] = password
        if "database" in parts:
            parts["dbname"] = parts.pop("database")
        return make_conninfo(**parts)

    def describe(self) -> str:
        """The variables it reads, for messages. Never a value."""
        if self.url_env:
            return self.url_env
        names = [*self.field_envs.values(), *([self.password_env] if self.password_env else [])]
        return ", ".join(names) or "its fields"


@dataclass(frozen=True)
class Source:
    file: str  # relative to the manifest, may be a glob
    table: str | None = None
    sheet: str | None = None  # Excel: a name, "*" (every sheet), or None (the first)
    header_row: int | None = None  # 1-based
    header_match: str | None = None
    null_values: tuple[str, ...] = ("",)
    types: Mapping[str, str] = field(default_factory=dict)
    delimiter: str | None = None
    encoding: str = "utf-8-sig"
    thousands: str | None = None


@dataclass(frozen=True)
class Manifest:
    base_dir: Path
    sources: tuple[Source, ...]
    staging_schema: str = "staging"
    readonly_role: str | None = None
    grant_schemas: tuple[str, ...] = ()
    transforms: tuple[str, ...] = ()
    owner: Owner | None = None

    def transform_files(self) -> list[Path]:
        """Each pattern's matches in name order, patterns in the order given."""
        out: list[Path] = []
        for pattern in self.transforms:
            matches = sorted(self.base_dir.glob(pattern))
            if not matches:
                raise ManifestError(f"transforms: nothing matches {pattern}")
            out += [m for m in matches if m not in out]
        return out


def _no_secret(where: str, value: object) -> None:
    if isinstance(value, str) and "://" in value:
        raise ManifestError(f"{where}: logins go in the environment, not in the manifest")


def _str(where: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{where} must be a non-empty string")
    _no_secret(where, value)
    return value


def _env(where: str, value: object) -> str:
    if not isinstance(value, str) or not ENV_NAME.fullmatch(value):
        raise ManifestError(f"{where} must be an environment variable name")
    return value


def _schema(where: str, value: object) -> str:
    if not isinstance(value, str) or not SCHEMA.fullmatch(value):
        raise ManifestError(f"{where} must be a lower-case schema name")
    return value


def parse_owner(data: object) -> Owner:
    if not isinstance(data, dict):
        raise ManifestError("owner must be a table ([owner])")
    for k, v in data.items():
        _no_secret(f"owner.{k}", v)
    if "password" in data:
        raise ManifestError("owner: a password never goes in the manifest; use password_env")
    unknown = sorted(set(data) - OWNER_KEYS)
    if unknown:
        raise ManifestError(f"owner: unknown key {unknown[0]}")
    split = set(data) - {"url_env"}
    if "url_env" in data:
        if split:
            raise ManifestError("owner: use url_env or split fields, not both")
        return Owner(url_env=_env("owner.url_env", data["url_env"]))
    fields: dict[str, str] = {}
    envs: dict[str, str] = {}
    for f in OWNER_FIELDS:
        if f in data and f"{f}_env" in data:
            raise ManifestError(f"owner: {f} and {f}_env can't both be set")
        if f in data:
            value = data[f]
            if f == "port" and isinstance(value, int) and not isinstance(value, bool):
                value = str(value)
            fields[f] = _str(f"owner.{f}", value)
        elif f"{f}_env" in data:
            envs[f] = _env(f"owner.{f}_env", data[f"{f}_env"])
    if "database" not in fields and "database" not in envs:
        raise ManifestError("owner: database (or database_env) is required")
    password_env = _env("owner.password_env", data["password_env"]) if "password_env" in data else None
    return Owner(fields=fields, field_envs=envs, password_env=password_env)


def _source(i: int, data: object) -> Source:
    where = f"source {i}"
    if not isinstance(data, dict):
        raise ManifestError(f"{where} must be a table ([[source]])")
    for k, v in data.items():
        _no_secret(f"{where}.{k}", v)
    unknown = sorted(set(data) - SOURCE_KEYS)
    if unknown:
        raise ManifestError(f"{where}: unknown key {unknown[0]}")
    file = _str(f"{where}.file", data.get("file"))
    table = data.get("table")
    if table is not None and normalise(_str(f"{where}.table", table)) != table:
        raise ManifestError(f"{where}.table must be lower snake_case (like {normalise(table) or 'my_table'})")
    if "header_row" in data and "header_match" in data:
        raise ManifestError(f"{where}: header_row and header_match can't both be set")
    header_row = data.get("header_row")
    if header_row is not None and (isinstance(header_row, bool) or not isinstance(header_row, int) or header_row < 1):
        raise ManifestError(f"{where}.header_row must be a positive integer (1-based)")
    header_match = _str(f"{where}.header_match", data["header_match"]) if "header_match" in data else None
    nulls = data.get("null_values", [""])
    if not isinstance(nulls, list) or not all(isinstance(n, str) for n in nulls):
        raise ManifestError(f"{where}.null_values must be a list of strings")
    types = data.get("types", {})
    if not isinstance(types, dict):
        raise ManifestError(f"{where}.types must be a table of column = type")
    for col, typ in types.items():
        if typ not in TYPES:
            raise ManifestError(f"{where}.types.{col} must be one of {', '.join(TYPES)}")
    sheet = _str(f"{where}.sheet", data["sheet"]) if "sheet" in data else None
    delimiter = data.get("delimiter")
    if delimiter is not None and (not isinstance(delimiter, str) or len(delimiter) != 1):
        raise ManifestError(f"{where}.delimiter must be one character")
    thousands = data.get("thousands")
    if thousands is not None and thousands not in (",", ".", " ", "'", "_"):
        raise ManifestError(f"{where}.thousands must be one of , . ' _ or a space")
    encoding = _str(f"{where}.encoding", data["encoding"]) if "encoding" in data else "utf-8-sig"
    if encoding.lower().replace("_", "-") == "utf-8":
        encoding = "utf-8-sig"  # strips a BOM, reads plain UTF-8 too
    return Source(file, table, sheet, header_row, header_match, tuple(nulls), dict(types), delimiter, encoding,
                  thousands)


def parse_manifest(text: str, base_dir: Path) -> Manifest:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"not valid TOML: {e}") from None
    unknown = sorted(set(data) - MANIFEST_KEYS)
    if unknown:
        raise ManifestError(f"unknown key {unknown[0]}")
    items = data.get("source", [])
    if not isinstance(items, list) or not items:
        raise ManifestError("at least one [[source]] is required")
    sources = tuple(_source(i, s) for i, s in enumerate(items, 1))
    staging = _schema("staging_schema", data.get("staging_schema", "staging"))
    role = data.get("readonly_role")
    if role is not None and (not isinstance(role, str) or not SCHEMA.fullmatch(role)):
        raise ManifestError("readonly_role must be a lower-case role name")
    grants = data.get("grant_schemas", [])
    if not isinstance(grants, list):
        raise ManifestError("grant_schemas must be a list")
    grant_schemas = tuple(_schema("grant_schemas", s) for s in grants)
    if grant_schemas and role is None:
        raise ManifestError("grant_schemas needs readonly_role")
    transforms = data.get("transforms", [])
    if not isinstance(transforms, list) or not all(isinstance(t, str) and t for t in transforms):
        raise ManifestError("transforms must be a list of file patterns")
    owner = parse_owner(data["owner"]) if "owner" in data else None
    return Manifest(base_dir, sources, staging, role, grant_schemas, tuple(transforms), owner)


def load_manifest(path: Path) -> Manifest:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestError(f"cannot read {path}: {e.strerror}") from None
    return parse_manifest(text, path.resolve().parent)
