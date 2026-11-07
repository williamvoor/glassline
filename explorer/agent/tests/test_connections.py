"""Connections (specs/connections/SPEC.md A1–A3, ADR 0051). No database, no model."""

from __future__ import annotations

import asyncio

import pytest

from explorer_agent.__main__ import EXPLORER_ROOT
from explorer_agent.config import (
    NEEDS_MIGRATION,
    WorkspaceUnavailable,
    data_dir,
    load_connections,
    load_workspace_list,
    parse_connections,
    workspace_settings,
)
from explorer_agent.workspaces import UNAVAILABLE, Registry

REPO = EXPLORER_ROOT.parent
DATASET = "explorer/agent/tests/fixtures/austender"
URL = "postgresql://u:p@127.0.0.1:1/x"


def conn(name="a", url_env="A_DATABASE_URL", **extra) -> str:
    lines = [f'name = "{name}"', f'url_env = "{url_env}"'] + [f'{k} = "{v}"' for k, v in extra.items()]
    return "[[connection]]\n" + "\n".join(lines) + "\n"


@pytest.fixture
def data(tmp_path):
    return data_dir(str(tmp_path / "data"), REPO)


# --- A1: validation ------------------------------------------------------------------------------


def test_a1_a_valid_list_loads_and_the_label_defaults_to_the_name():
    got = parse_connections(conn("vicprop", "VICPROP_DATABASE_URL", label="Victorian house prices") + conn("b", "B_DATABASE_URL"))
    assert got.file_error is None and dict(got.errors) == {}
    assert got.connections["vicprop"].label == "Victorian house prices"
    assert got.connections["b"].label == "b"
    assert got.connections["b"].url_env == "B_DATABASE_URL"


@pytest.mark.parametrize(("text", "name", "reason"), [
    (conn("a", extra="1"), "a", "unknown key extra"),
    (conn("a", "postgresql://u:hunter2@h/db"), "a", "logins go in .env, not in the file"),
    (conn("a", label="postgres://x"), "a", "logins go in .env, not in the file"),
    (conn("Bad Name"), "Bad Name", "name must match [a-z0-9][a-z0-9_-]*"),
    (conn("a", "A_URL"), "a", "url_env must be a variable named <NAME>_DATABASE_URL"),
    (conn("a", "BOOTSTRAP_DATABASE_URL"), "a", "url_env can't be BOOTSTRAP_DATABASE_URL (the bootstrap CLI's login)"),
    (conn("a", label="x" * 81), "a", "label must be one line of 1-80 characters"),
    (conn("a", label="   "), "a", "label must be one line of 1-80 characters"),
    (conn("a") + conn("a", "B_DATABASE_URL"), "a", "duplicate name"),
    (conn("a") + conn("b", "A_DATABASE_URL"), "b", "A_DATABASE_URL is already Connection 'a'"),
])
def test_a1_each_refused_case_fails_with_its_reason(text, name, reason):
    got = parse_connections(text)
    assert got.errors[name] == reason
    assert name not in got.connections


def test_a1_a_secret_never_appears_in_a_reason():
    got = parse_connections(conn("a", "postgresql://u:hunter2@h/db"))
    assert "hunter2" not in repr(got)


def test_a1_bad_toml_or_shape_is_a_file_error_and_a_missing_file_is_empty(data):
    assert parse_connections("[[connection]\n").file_error.startswith("not valid TOML")
    assert parse_connections("other = 1\n").file_error == "unknown key other"
    assert parse_connections("connection = 1\n").file_error.startswith("connection must be an array")
    empty = load_connections(data / "connections.toml")
    assert dict(empty.connections) == {} and empty.file_error is None


# --- A2, A3: Workspaces name a Connection --------------------------------------------------------


def write(data, workspaces: str, connections: str | None = None) -> None:
    (data / "workspaces.toml").write_text(workspaces, encoding="utf-8")
    if connections is not None:
        (data / "connections.toml").write_text(connections, encoding="utf-8")


def ws(name: str, login: str = 'connection = "a"') -> str:
    return f'[[workspace]]\nname = "{name}"\ndataset_dir = "{DATASET}"\n{login}\n'


def test_a2_a_workspace_resolves_its_url_through_its_connection(data):
    write(data, ws("one"))
    entry = load_workspace_list(data / "workspaces.toml", REPO)[0].entry
    conns = parse_connections(conn("a", "SHARED_DATABASE_URL"))
    settings = workspace_settings({"SHARED_DATABASE_URL": URL}, entry, EXPLORER_ROOT, conns)
    assert settings.database_url == URL


@pytest.mark.parametrize(("connections", "env", "reason"), [
    (conn("other", "B_DATABASE_URL"), {}, "no Connection named 'a'"),
    (conn("a", "A_URL"), {}, "Connection 'a' is invalid: url_env must be a variable named <NAME>_DATABASE_URL"),
    ("[[connection\n", {}, "connections.toml: not valid TOML"),
    (conn("a"), {}, "`A_DATABASE_URL` not set"),
    (conn("a"), {"A_DATABASE_URL": "nonsense secret=hunter2"}, "`A_DATABASE_URL` is malformed"),
])
def test_a2_unknown_invalid_or_unset_connections_make_the_workspace_unavailable(data, connections, env, reason):
    write(data, ws("one"))
    entry = load_workspace_list(data / "workspaces.toml", REPO)[0].entry
    with pytest.raises(WorkspaceUnavailable) as e:
        workspace_settings(env, entry, EXPLORER_ROOT, parse_connections(connections))
    assert str(e.value).startswith(reason)
    assert "hunter2" not in str(e.value)


def test_a3_dsn_env_needs_migration_and_both_keys_are_refused(data):
    write(data, ws("old", 'dsn_env = "A_DATABASE_URL"') + ws("both", 'connection = "a"\ndsn_env = "A_DATABASE_URL"')
          + ws("none", ""))
    old, both, none = load_workspace_list(data / "workspaces.toml", REPO)
    assert old.entry is not None and old.entry.connection is None
    assert old.entry.raw["dsn_env"] == "A_DATABASE_URL"  # kept for Settings' migration
    with pytest.raises(WorkspaceUnavailable, match="^needs migration"):
        workspace_settings({"A_DATABASE_URL": URL}, old.entry, EXPLORER_ROOT, parse_connections(conn("a")))
    assert both.error == "invalid entry: connection and dsn_env can't both be set"
    assert none.error == "invalid entry: connection must name a Connection ([a-z0-9][a-z0-9_-]*)"


def test_a2_a3_the_registry_lists_reasons_and_mounts_the_rest(data):
    async def builder(*_):
        raise AssertionError("no Workspace here is buildable")

    write(data, ws("old", 'dsn_env = "A_DATABASE_URL"') + ws("missing", 'connection = "nope"') + ws("unset"),
          conn("a"))
    registry = Registry(data, REPO, EXPLORER_ROOT, {}, builder, "http://127.0.0.1:10002")
    asyncio.run(registry.start())
    assert [(w.name, w.status, w.reason) for w in registry.workspaces.values()] == [
        ("old", UNAVAILABLE, NEEDS_MIGRATION),
        ("missing", UNAVAILABLE, "no Connection named 'nope'"),
        ("unset", UNAVAILABLE, "`A_DATABASE_URL` not set"),
    ]


def test_a3_a_legacy_variable_settings_cant_migrate_says_how_to_fix_it(data):
    """Refuter: `dsn_env = "FOO_DSN"` was valid before, but Settings can't make it a Connection."""
    write(data, ws("old", 'dsn_env = "FOO_DSN"') + ws("cli", 'dsn_env = "BOOTSTRAP_DATABASE_URL"'))
    for item in load_workspace_list(data / "workspaces.toml", REPO):
        with pytest.raises(WorkspaceUnavailable) as e:
            workspace_settings({}, item.entry, EXPLORER_ROOT, parse_connections(""))
        assert "rename it to <NAME>_DATABASE_URL" in str(e.value)
