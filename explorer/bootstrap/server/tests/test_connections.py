"""Connections in Settings (explorer/specs/connections/SPEC.md A4–A6): no network, no model."""

import asyncio
from pathlib import Path

import explorer_agent  # noqa: F401  (first: keeps LiteLLM from fetching its price map)
import httpx
import pytest
from explorer_agent.__main__ import EXPLORER_ROOT

from explorer_bootstrap import registry, server
from explorer_bootstrap.registry import (
    RegistryError,
    delete_connection,
    migrate,
    read_connections,
    read_entries,
    set_label,
    write_connections,
    write_entries,
)
from explorer_bootstrap.server import build_app

from test_server import BASE, PORT

REPO = EXPLORER_ROOT.parent


@pytest.fixture
def data(tmp_path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


def ws(name: str, **login) -> dict:
    return {"name": name, "dataset_dir": f"/d/{name}", **login}


# --- A4: migration -------------------------------------------------------------------------------


def test_a4_dsn_env_entries_become_connections_and_a_second_run_changes_nothing(data):
    write_connections(data, [{"name": "vic", "label": "Victoria", "url_env": "VIC_DATABASE_URL"}])
    write_entries(data, [ws("a", dsn_env="VIC_DATABASE_URL", sql_row_cap=7), ws("b", dsn_env="AUS_DATABASE_URL"),
                         ws("c", dsn_env="AUS_DATABASE_URL"), ws("d", connection="vic")])
    assert migrate(data) == ["a", "b", "c"]
    assert read_entries(data) == [ws("a", connection="vic", sql_row_cap=7), ws("b", connection="aus"),
                                  ws("c", connection="aus"), ws("d", connection="vic")]
    assert read_connections(data) == [{"name": "vic", "label": "Victoria", "url_env": "VIC_DATABASE_URL"},
                                      {"name": "aus", "url_env": "AUS_DATABASE_URL"}]
    before = ((data / "workspaces.toml").read_bytes(), (data / "connections.toml").read_bytes())
    assert migrate(data) == []
    assert ((data / "workspaces.toml").read_bytes(), (data / "connections.toml").read_bytes()) == before


def test_a4_a_derived_name_that_is_taken_gets_a_suffix(data):
    write_connections(data, [{"name": "aus", "url_env": "OTHER_DATABASE_URL"}])
    write_entries(data, [ws("a", dsn_env="AUS_DATABASE_URL")])
    migrate(data)
    assert read_entries(data) == [ws("a", connection="aus_2")]


def test_a4_an_entry_that_cant_become_a_connection_is_left_for_the_agent_to_report(data):
    write_entries(data, [ws("a", dsn_env="NOT_A_URL_VAR"), ws("b", dsn_env="BOOTSTRAP_DATABASE_URL")])
    assert migrate(data) == []
    assert [e.get("dsn_env") for e in read_entries(data)] == ["NOT_A_URL_VAR", "BOOTSTRAP_DATABASE_URL"]


def test_a4_a_failed_workspace_write_puts_the_connection_list_back(data, monkeypatch):
    write_connections(data, [{"name": "vic", "url_env": "VIC_DATABASE_URL"}])
    write_entries(data, [ws("a", dsn_env="AUS_DATABASE_URL")])
    before = ((data / "workspaces.toml").read_bytes(), (data / "connections.toml").read_bytes())

    def boom(*_):
        raise OSError("disk full")

    monkeypatch.setattr(registry, "write_entries", boom)
    with pytest.raises(OSError):
        migrate(data)
    assert ((data / "workspaces.toml").read_bytes(), (data / "connections.toml").read_bytes()) == before


def test_a4_a_failed_write_with_no_connection_list_leaves_none(data, monkeypatch):
    write_entries(data, [ws("a", dsn_env="AUS_DATABASE_URL")])
    monkeypatch.setattr(registry, "write_entries", lambda *_: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        migrate(data)
    assert not (data / "connections.toml").exists()


async def test_a4_settings_migrates_on_startup_and_retries_the_agent_reload(data, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MIGRATION_RELOAD_DELAY_S", 0)
    write_entries(data, [ws("a", dsn_env="AUS_DATABASE_URL")])
    answers = [0, 0, 200]  # the agent isn't up yet, then it is
    calls = []

    async def reload():
        calls.append(1)
        return answers.pop(0), None

    app = build_app(None, database={}, env={}, port=PORT, timeout_ms=5000, datasets_root=tmp_path, repo_root=REPO,
                    data_dir=data, agent_url="x", reload=reload)
    inner = app.app  # CORS off (no client_origin): _Guard wraps the Starlette app
    async with inner.router.lifespan_context(inner):
        for _ in range(50):
            if len(calls) == 3:
                break
            await asyncio.sleep(0.01)
    assert len(calls) == 3
    assert read_entries(data) == [ws("a", connection="aus")]


async def test_a4_no_legacy_entries_means_no_reload(data, tmp_path):
    write_entries(data, [ws("a", connection="aus")])
    calls = []

    async def reload():
        calls.append(1)
        return 200, None

    app = build_app(None, database={}, env={}, port=PORT, timeout_ms=5000, datasets_root=tmp_path, repo_root=REPO,
                    data_dir=data, agent_url="x", reload=reload)
    async with app.app.router.lifespan_context(app.app):
        await asyncio.sleep(0.01)
    assert calls == []


# --- A5: label and delete ------------------------------------------------------------------------


def test_a5_a_connection_in_use_cant_be_deleted_and_names_its_workspaces(data):
    write_connections(data, [{"name": "aus", "url_env": "AUS_DATABASE_URL"},
                             {"name": "free", "url_env": "FREE_DATABASE_URL"}])
    write_entries(data, [ws("one", connection="aus"), ws("two", connection="aus")])
    with pytest.raises(RegistryError, match="used by Workspace one, two") as e:
        delete_connection(data, "aus")
    assert e.value.status == 409
    delete_connection(data, "free")
    assert [c["name"] for c in read_connections(data)] == ["aus"]
    with pytest.raises(RegistryError, match="no such Connection"):
        delete_connection(data, "free")


def test_a5_labels_are_checked_and_written(data):
    write_connections(data, [{"name": "aus", "url_env": "AUS_DATABASE_URL"}])
    set_label(data, "aus", "  Australian tenders ")
    assert read_connections(data) == [{"name": "aus", "label": "Australian tenders", "url_env": "AUS_DATABASE_URL"}]
    for bad in ("", "x" * 81, "postgresql://u:p@h/d"):
        with pytest.raises(RegistryError):
            set_label(data, "aus", bad)
    assert read_connections(data)[0]["label"] == "Australian tenders"


def test_a1_settings_never_writes_an_invalid_list(data):
    with pytest.raises(RegistryError):
        write_connections(data, [{"name": "a", "url_env": "A_DATABASE_URL"}, {"name": "b", "url_env": "A_DATABASE_URL"}])
    assert not (data / "connections.toml").exists()


async def test_a5_the_label_and_delete_routes(data, tmp_path):
    write_connections(data, [{"name": "aus", "url_env": "AUS_DATABASE_URL"}])
    write_entries(data, [ws("one", connection="aus")])
    app = build_app(None, database={}, env={"AUS_DATABASE_URL": "postgresql://u:hunter2@h/d"}, port=PORT,
                    timeout_ms=5000, datasets_root=tmp_path, repo_root=REPO, data_dir=data, agent_url="x")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        r = await c.post("/api/connections/aus/label", json={"label": "Tenders"})
        assert r.status_code == 200 and r.json()["connections"][0]["label"] == "Tenders"
        assert "hunter2" not in r.text
        assert (await c.post("/api/connections/aus/label", json={"label": 3})).status_code == 400
        r = await c.post("/api/connections/aus/delete", json={})
        assert r.status_code == 409 and "one" in r.json()["error"]
        assert (await c.post("/api/connections/nope/delete", json={})).status_code == 404
        assert (await c.post("/api/connect", json={"connection": "aus"})).status_code == 502  # host h: unreachable
        assert (await c.post("/api/connect", json={"connection": "nope"})).status_code == 400


async def test_a6_an_unset_connection_is_refused_before_connecting(data, tmp_path):
    write_connections(data, [{"name": "aus", "url_env": "AUS_DATABASE_URL"}])
    app = build_app(None, database={}, env={}, port=PORT, timeout_ms=5000, datasets_root=tmp_path, repo_root=REPO,
                    data_dir=data, agent_url="x")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        r = await c.post("/api/connect", json={"connection": "aus"})
        assert r.status_code == 400 and r.json()["error"] == "AUS_DATABASE_URL is not set"



@pytest.mark.db
async def test_c9_the_connection_the_wizard_holds_cant_be_deleted(pg, data, tmp_path):
    """Refuter: deleting it mid-wizard made the next export name a missing Connection."""
    from test_settings import _open

    write_connections(data, [{"name": "bs", "url_env": "BS_DATABASE_URL"}])
    app = build_app(None, database={}, env={"BS_DATABASE_URL": pg.ro_dsn}, port=PORT, timeout_ms=5000,
                    datasets_root=tmp_path, repo_root=REPO, data_dir=data, agent_url="x", open_pool=_open)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        assert (await c.post("/api/connect", json={"connection": "bs"})).status_code == 200
        r = await c.post("/api/connections/bs/delete", json={})
        assert r.status_code == 409 and "New Workspace" in r.json()["error"]
        assert [x["name"] for x in read_connections(data)] == ["bs"]
