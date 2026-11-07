"""Settings mode (explorer/specs/workspaces/SPEC.md phase 2, S1–S4): no network, a stub model."""

import tomllib
from pathlib import Path

import explorer_agent  # noqa: F401  (first: keeps LiteLLM from fetching its price map)
import httpx
import pytest
from explorer_agent.__main__ import EXPLORER_ROOT, _workspace_builder
from explorer_agent.workspaces import AVAILABLE, Registry

from explorer_bootstrap.registry import (
    connection_choices,
    read_connections,
    read_entries,
    render,
    write_connections,
    write_entries,
)
from explorer_bootstrap.server import build_app
from explorer_bootstrap.stats import open_report_pool

from test_server import BASE, PORT, _model, _wait

REPO = EXPLORER_ROOT.parent


class Agent:
    """The agent's reload, in process: a real Workspace registry over the data directory."""

    def __init__(self, data: Path, env: dict[str, str]):
        self.registry = Registry(data, REPO, EXPLORER_ROOT, env, self._build, "http://127.0.0.1:10002")
        self.calls = 0
        self.status: int | None = None  # force an answer (409) instead of reloading

    async def _build(self, entry, settings, url, saved_path):
        return await _workspace_builder(entry, settings, url, saved_path, connect_timeout_s=5)

    async def reload(self):
        self.calls += 1
        if self.status is not None:
            return self.status, {"error": "busy"}
        return await self.registry.reload()


async def _open(dsn: str):
    pool = open_report_pool(dsn, 5000)
    await pool.open(wait=True, timeout=10)
    return pool, {"host": "h", "name": "d"}


def _settings_app(tmp_path, env, agent, completion=None):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    root = tmp_path / "datasets"
    root.mkdir(exist_ok=True)
    app = build_app(None, database={}, env=env, port=PORT, timeout_ms=5000, datasets_root=root, repo_root=REPO,
                    completion=completion, data_dir=data, agent_url="http://127.0.0.1:10002",
                    client_origin="http://127.0.0.1:5173", open_pool=_open, reload=agent.reload if agent else None)
    return app, data


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


# --- S1 ------------------------------------------------------------------------------------------


def test_c5_lists_connections_by_label_then_bare_variables_never_a_value(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_connections(data, [{"name": "zed", "label": "Alpha", "url_env": "AUS_DATABASE_URL"},
                             {"name": "unset", "url_env": "GONE_DATABASE_URL"}])
    env = {"AUS_DATABASE_URL": "postgresql://a:secret@h/d", "VIC_DATABASE_URL": "postgresql://v:secret@h/d",
           "EMPTY_DATABASE_URL": " ", "DATABASE_URL": "postgresql://x", "OTHER": "postgresql://y",
           "lower_DATABASE_URL": "postgresql://z", "BOOTSTRAP_DATABASE_URL": "postgresql://cli"}
    assert connection_choices(env, data) == [  # BOOTSTRAP_ is the CLI's login (D2)
        {"name": "zed", "label": "Alpha", "url_env": "AUS_DATABASE_URL", "listed": True, "ready": True},
        {"name": "unset", "label": "unset", "url_env": "GONE_DATABASE_URL", "listed": True, "ready": False},
        {"name": "vic", "label": "vic", "url_env": "VIC_DATABASE_URL", "listed": False, "ready": True},
    ]
    assert "secret" not in repr(connection_choices(env, data))


def test_c5_a_bare_variable_gets_a_free_name(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_connections(data, [{"name": "vic", "url_env": "OTHER_DATABASE_URL"}])
    env = {"OTHER_DATABASE_URL": "postgresql://h/d", "VIC_DATABASE_URL": "postgresql://h/d",
           "MY_DATA_DATABASE_URL": "postgresql://h/d"}
    assert [(c["name"], c["url_env"]) for c in connection_choices(env, data)] == [
        ("vic", "OTHER_DATABASE_URL"), ("my_data", "MY_DATA_DATABASE_URL"), ("vic_2", "VIC_DATABASE_URL")]


async def test_c5_the_connections_route_is_empty_without_logins(tmp_path):
    app, _ = _settings_app(tmp_path, {"BOOTSTRAP_DATABASE_URL": "postgresql://cli", "DATABASE_URL": "postgresql://x"}, None)
    async with _client(app) as c:
        assert (await c.get("/api/connections")).json() == {"connections": [], "current": None}


async def test_s1_the_connections_route_names_variables_only(tmp_path):
    env = {"VIC_DATABASE_URL": "postgresql://v:hunter2@h/d"}
    app, _ = _settings_app(tmp_path, env, None)
    async with _client(app) as c:
        r = await c.get("/api/connections")
        assert r.json() == {"connections": [{"name": "vic", "label": "vic", "url_env": "VIC_DATABASE_URL",
                                             "listed": False, "ready": True}], "current": None}
        assert "hunter2" not in r.text
        assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 409  # no login yet
        assert (await c.post("/api/connect", json={"connection": "not_declared"})).status_code == 400
        r = await c.options("/api/connect", headers={"origin": "http://127.0.0.1:5173", "access-control-request-method": "POST"})
        assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_the_list_is_written_atomically_and_reads_back(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    entries = [{"name": "a", "dataset_dir": '/p/with "quotes"', "connection": "a", "sql_row_cap": 7}]
    write_entries(data, entries)
    assert read_entries(data) == entries
    assert not [p for p in data.iterdir() if p.name.startswith(".workspaces-")]
    with pytest.raises(ValueError):
        render([{"name": "a", "nope": 1}])


# --- S2 ------------------------------------------------------------------------------------------


@pytest.mark.db
async def test_s2_a_login_that_can_write_is_refused_before_drafting(pg, tmp_path):
    env = {"W_DATABASE_URL": pg.loader_dsn, "SU_DATABASE_URL": pg.superuser_dsn}
    model = _model()
    app, _ = _settings_app(tmp_path, env, None, model)
    async with _client(app) as c:
        for name in ("w", "su"):
            r = await c.post("/api/connect", json={"connection": name})
            assert r.status_code == 403 and "read-only" in r.json()["error"], r.text
        assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 409  # still no login
    assert model.calls == []


# --- S3, S4 --------------------------------------------------------------------------------------


async def _export(c, name="bs_ws"):
    assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
    run_id = (await c.post("/api/draft", json={})).json()["run_id"]
    done, _ = await _wait(c, run_id)
    assert "error" not in done, done
    draft = done["draft"]
    return await c.post("/api/export", json={"files": draft["files"], "comments": draft["comments"], "name": name,
                                             "target": "sql", "dismissed": [0], "force": False})


@pytest.mark.db
async def test_s3_export_goes_to_the_data_directory_and_the_agent_mounts_it(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    data = tmp_path / "data"
    data.mkdir()
    agent = Agent(data, env)
    model = _model()
    for _ in range(2):  # the duplicate and the bad name each draft again
        model.steps.extend(_model().steps)
    app, data = _settings_app(tmp_path, env, agent, model)
    try:
        async with _client(app) as c:
            assert (await c.post("/api/connect", json={"connection": "bs"})).status_code == 200
            r = await _export(c)
            assert r.status_code == 200, r.text
            assert r.json() == {"path": "workspaces/bs_ws/dataset", "workspace": "bs_ws", "overwrote": [],
                                "backup": None, "reloaded": True}
            dataset = data / "workspaces" / "bs_ws" / "dataset"
            assert (dataset / "comments.toml").is_file() and not (dataset / "comments.sql").exists()  # target toml
            assert tomllib.loads((data / "workspaces.toml").read_text())["workspace"] == [
                {"name": "bs_ws", "dataset_dir": str(dataset.resolve()), "connection": "bs"}]
            assert [(w["name"], w["status"]) for w in agent.registry.listing()] == [("bs_ws", AVAILABLE)]
            # Choosing the bare variable wrote its Connection, which stays offered (Datasets share it).
            assert read_connections(data) == [{"name": "bs", "url_env": "BS_DATABASE_URL"}]
            assert [c["name"] for c in (await c.get("/api/connections")).json()["connections"]] == ["bs"]
            listed = (await c.get("/api/workspaces")).json()["workspaces"]
            assert listed == [{"name": "bs_ws", "connection": "bs", "kind": "data"}]

            env["BS2_DATABASE_URL"] = pg.ro_dsn
            assert (await c.post("/api/connect", json={"connection": "bs2"})).status_code == 200
            dup = await _export(c)
            assert dup.status_code == 409 and agent.calls == 1
            bad = await _export(c, "Bad-Name")
            assert bad.status_code == 422
    finally:
        await agent.registry.close()


@pytest.mark.db
async def test_s3_regenerate_exports_into_the_same_folder_with_a_backup(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    data = tmp_path / "data"
    data.mkdir()
    agent = Agent(data, env)
    model = _model()
    app, data = _settings_app(tmp_path, env, agent, model)
    try:
        async with _client(app) as c:
            await c.post("/api/connect", json={"connection": "bs"})
            assert (await _export(c)).status_code == 200
            model.steps.extend(_model().steps)
            report = await c.post("/api/workspaces/bs_ws/regenerate", json={})
            assert report.status_code == 200 and report.json()["schemas"] == ["bs"]
            run_id = (await c.post("/api/draft", json={})).json()["run_id"]
            done, _ = await _wait(c, run_id)
            r = await c.post("/api/export", json={"files": done["draft"]["files"], "comments": done["draft"]["comments"],
                                                  "name": "ignored", "target": "sql", "dismissed": [0]})
            assert r.status_code == 200, r.text
            assert r.json()["workspace"] == "bs_ws" and r.json()["backup"].startswith(".dataset.bak-")
            assert len(read_entries(data)) == 1 and agent.calls == 2
    finally:
        await agent.registry.close()


async def test_s4_delete_removes_a_data_workspace_and_never_a_repo_datasets_files(tmp_path):
    repo = tmp_path / "repo"
    repo_ds = repo / "explorer" / "datasets" / "demo"
    repo_ds.mkdir(parents=True)
    (repo_ds / "dataset.toml").write_text("x")
    before = sorted(p.name for p in repo_ds.iterdir())

    class Reload:
        status = 200
        calls = 0

        async def __call__(self):
            Reload.calls += 1
            return Reload.status, []

    reload = Reload()
    data = tmp_path / "data"
    data.mkdir()
    app_with = build_app(None, database={}, env={}, port=PORT, timeout_ms=5000, datasets_root=tmp_path, repo_root=repo,
                         data_dir=data, agent_url="x", reload=reload)
    (data / "workspaces" / "mine" / "dataset").mkdir(parents=True)
    (data / "workspaces" / "mine" / "dataset" / "dataset.toml").write_text("x")
    (data / "workspaces" / "aus").mkdir(parents=True)
    (data / "workspaces" / "aus" / "conversations.sqlite").write_text("x")
    write_entries(data, [
        {"name": "mine", "dataset_dir": str(data / "workspaces" / "mine" / "dataset"), "connection": "m"},
        {"name": "aus", "dataset_dir": "explorer/datasets/demo", "connection": "aus"},
    ])
    async with _client(app_with) as c:
        listed = (await c.get("/api/workspaces")).json()["workspaces"]
        assert [(w["name"], w["kind"]) for w in listed] == [("mine", "data"), ("aus", "repo")]
        Reload.status = 409  # a run in progress: nothing changes
        r = await c.post("/api/workspaces/mine/delete", json={})
        assert r.status_code == 409 and len(read_entries(data)) == 2 and (data / "workspaces" / "mine").exists()
        Reload.status = 200
        r = await c.post("/api/workspaces/mine/delete", json={})
        assert r.status_code == 200 and [w["name"] for w in r.json()["workspaces"]] == ["aus"]
        assert not (data / "workspaces" / "mine").exists()
        r = await c.post("/api/workspaces/aus/delete", json={})
        assert r.status_code == 200 and read_entries(data) == []
        assert not (data / "workspaces" / "aus").exists()  # its Conversations
        assert (await c.post("/api/workspaces/aus/regenerate", json={})).status_code == 404
        assert (await c.post("/api/workspaces/nope/delete", json={})).status_code == 404
        assert (await c.post("/api/workspaces/mine/delete", content="x")).status_code == 403  # not JSON
    assert sorted(p.name for p in repo_ds.iterdir()) == before


async def test_s4_a_repo_workspace_cannot_be_regenerated(tmp_path):
    app, data = _settings_app(tmp_path, {"AUS_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x"}, None)
    write_entries(data, [{"name": "aus", "dataset_dir": "explorer/agent/tests/fixtures/austender", "connection": "aus"}])
    async with _client(app) as c:
        r = await c.post("/api/workspaces/aus/regenerate", json={})
        assert r.status_code == 400 and "regen_dataset.sh" in r.json()["error"]


# --- refuter (phase 2) regressions ---------------------------------------------------------------


def test_workspace_dir_refuses_traversal(tmp_path):
    from explorer_bootstrap.registry import RegistryError, workspace_dir

    for bad in ("..", ".", "a/b", "../x"):
        with pytest.raises(RegistryError):
            workspace_dir(tmp_path, bad)
    assert workspace_dir(tmp_path, "ok") == (tmp_path / "workspaces" / "ok").resolve()


@pytest.mark.db
async def test_s2_regenerate_with_a_writing_login_is_refused(pg, tmp_path):
    env = {"W_DATABASE_URL": pg.loader_dsn}
    app, data = _settings_app(tmp_path, env, None, _model())
    ds = data / "workspaces" / "w" / "dataset"
    ds.mkdir(parents=True)
    write_entries(data, [{"name": "w", "dataset_dir": str(ds), "connection": "w"}])
    async with _client(app) as c:
        r = await c.post("/api/workspaces/w/regenerate", json={})
        assert r.status_code == 403
        assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 409  # no login kept


@pytest.mark.db
async def test_s3_a_failed_reload_keeps_the_export_and_reports_it(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    data = tmp_path / "data"
    data.mkdir()
    agent = Agent(data, env)
    agent.status = 0  # the agent can't be reached
    app, data = _settings_app(tmp_path, env, agent, _model())
    try:
        async with _client(app) as c:
            await c.post("/api/connect", json={"connection": "bs"})
            r = await _export(c)
            assert r.status_code == 200 and r.json()["reloaded"] is False
            assert [e["name"] for e in read_entries(data)] == ["bs_ws"]
            agent.status = None
            r = await c.post("/api/reload", json={})  # Settings' own Reload (refuter F5)
            assert r.status_code == 200 and r.json()["reloaded"] is True
            assert [w["name"] for w in agent.registry.listing()] == ["bs_ws"]
    finally:
        await agent.registry.close()


async def test_s4_delete_keeps_the_folder_unless_the_agent_reloaded_or_is_down(tmp_path):
    """Refuter F7: a 5xx means the Workspace may still be mounted with its store open."""
    data = tmp_path / "data"
    data.mkdir()
    status = {"v": 500}

    async def reload():
        return status["v"], None

    app = build_app(None, database={}, env={}, port=PORT, timeout_ms=5000, datasets_root=tmp_path, repo_root=tmp_path / "repo",
                    data_dir=data, agent_url="x", reload=reload)
    (data / "workspaces" / "a").mkdir(parents=True)
    write_entries(data, [{"name": "a", "dataset_dir": str(data / "workspaces" / "a"), "connection": "a"}])
    async with _client(app) as c:
        r = await c.post("/api/workspaces/a/delete", json={})
        assert r.status_code == 502 and (data / "workspaces" / "a").exists() and len(read_entries(data)) == 1
        status["v"] = 0
        r = await c.post("/api/workspaces/a/delete", json={})
        assert r.status_code == 200 and not (data / "workspaces" / "a").exists()


@pytest.mark.db
async def test_d5_a_failed_regenerate_clears_the_regenerate_target(pg, tmp_path):
    """Refuter F2: after regenerating A, a regenerate of B that fails must not send B's export into A."""
    env = {"BS_DATABASE_URL": pg.ro_dsn, "BS2_DATABASE_URL": pg.ro_dsn}
    data = tmp_path / "data"
    data.mkdir()
    agent = Agent(data, env)
    model = _model()
    model.steps.extend(_model().steps)
    app, data = _settings_app(tmp_path, env, agent, model)
    try:
        async with _client(app) as c:
            await c.post("/api/connect", json={"connection": "bs"})
            assert (await _export(c, "aaa")).status_code == 200
            broken = data / "workspaces" / "bbb" / "dataset"
            broken.mkdir(parents=True)
            entries = read_entries(data)
            write_entries(data, [*entries, {"name": "bbb", "dataset_dir": str(broken), "connection": "bs2"}])
            assert (await c.post("/api/workspaces/aaa/regenerate", json={})).status_code == 200
            assert (await c.post("/api/workspaces/bbb/regenerate", json={})).status_code == 400  # no dataset.toml
            r = await _export(c, "ccc")
            assert r.status_code == 200 and r.json()["workspace"] == "ccc" and r.json()["backup"] is None
    finally:
        await agent.registry.close()


# --- phase 3: the New Workspace wizard (E5–E9, N6, N10, N17) ------------------------------------

import asyncio  # noqa: E402

from test_boards_draft import EXPLORER, GRADE  # noqa: E402


@pytest.mark.db
async def test_n10_session_and_discard(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    app, _ = _settings_app(tmp_path, env, None, _model())
    async with _client(app) as c:
        empty = (await c.get("/api/session")).json()
        assert empty == {"connection": None, "report": None, "regenerating": None, "draft": False, "boards": False,
                         "run": None}
        await c.post("/api/connect", json={"connection": "bs"})
        assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
        s = await c.get("/api/session")
        assert s.json()["connection"] == "bs" and s.json()["report"]["schemas"] == ["bs"]
        assert pg.ro_dsn not in s.text
        run_id = (await c.post("/api/draft", json={})).json()["run_id"]
        await _wait(c, run_id)
        assert (await c.get("/api/session")).json()["draft"] is True
        r = await c.post("/api/discard", json={})
        assert r.status_code == 200
        after = (await c.get("/api/session")).json()
        assert after == {"connection": "bs", "report": None, "regenerating": None, "draft": False,
                         "boards": False, "run": None}  # the login stays
        assert (await c.get("/api/draft")).status_code == 404


@pytest.mark.db
async def test_n10_discard_is_refused_during_a_run_and_the_session_names_the_run(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    gate = asyncio.Event()

    async def blocked(**_kw):
        await gate.wait()
        raise RuntimeError("stopped")

    app, _ = _settings_app(tmp_path, env, None, blocked)
    async with _client(app) as c:
        await c.post("/api/connect", json={"connection": "bs"})
        await c.post("/api/report", json={"schemas": ["bs"]})
        run_id = (await c.post("/api/draft", json={})).json()["run_id"]
        assert (await c.get("/api/session")).json()["run"] == {"id": run_id, "kind": "draft"}
        assert (await c.post("/api/discard", json={})).status_code == 409
        gate.set()
        await _wait(c, run_id)
        assert (await c.get("/api/session")).json()["run"] is None


async def _regenerated_with_kept_boards(c, data, model):
    """A Workspace exported, regenerated and drafted again, with its current Boards kept."""
    await c.post("/api/connect", json={"connection": "bs"})
    assert (await _export(c)).status_code == 200
    boards = data / "workspaces" / "bs_ws" / "dataset" / "boards"
    boards.mkdir()
    (boards / "explorer.toml").write_text(EXPLORER)
    assert (await c.post("/api/workspaces/bs_ws/regenerate", json={})).status_code == 200
    model.steps.extend(_model().steps)
    await _wait(c, (await c.post("/api/draft", json={})).json()["run_id"])
    assert (await c.post("/api/boards/keep", json={})).status_code == 200
    s = (await c.get("/api/session")).json()
    assert s["boards"] is True and s["regenerating"] == "bs_ws" and s["draft"] is True


@pytest.mark.db
async def test_n17_a_new_report_clears_the_draft_and_boards(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    data = tmp_path / "data"
    data.mkdir()
    agent = Agent(data, env)
    model = _model()
    app, data = _settings_app(tmp_path, env, agent, model)
    try:
        async with _client(app) as c:
            await _regenerated_with_kept_boards(c, data, model)
            assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
            assert (await c.get("/api/draft")).status_code == 404
            assert (await c.get("/api/boards")).json() is None
            s = (await c.get("/api/session")).json()
            assert s["draft"] is False and s["boards"] is False
    finally:
        await agent.registry.close()


@pytest.mark.db
async def test_n10_discard_clears_boards_the_regenerate_target_and_the_kept_boards(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    data = tmp_path / "data"
    data.mkdir()
    agent = Agent(data, env)
    model = _model()
    app, data = _settings_app(tmp_path, env, agent, model)
    try:
        async with _client(app) as c:
            await _regenerated_with_kept_boards(c, data, model)
            assert (await c.post("/api/discard", json={})).status_code == 200
            s = (await c.get("/api/session")).json()
            assert s == {"connection": "bs", "report": None, "regenerating": None, "draft": False,
                         "boards": False, "run": None}
            # A new Draft on the same login has nothing to keep: the kept Boards went too.
            assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
            model.steps.extend(_model().steps)
            await _wait(c, (await c.post("/api/draft", json={})).json()["run_id"])
            assert (await c.post("/api/boards/keep", json={})).status_code == 404
    finally:
        await agent.registry.close()


@pytest.mark.db
async def test_n6_regenerate_keeps_the_current_boards_checked_against_the_new_draft(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    data = tmp_path / "data"
    data.mkdir()
    agent = Agent(data, env)
    model = _model()
    app, data = _settings_app(tmp_path, env, agent, model)
    try:
        async with _client(app) as c:
            await c.post("/api/connect", json={"connection": "bs"})
            assert (await _export(c)).status_code == 200
            await c.post("/api/workspaces/bs_ws/regenerate", json={})  # no boards/ yet
            model.steps.extend(_model().steps)
            await _wait(c, (await c.post("/api/draft", json={})).json()["run_id"])
            assert (await c.post("/api/boards/keep", json={})).status_code == 404  # nothing to keep

            boards = data / "workspaces" / "bs_ws" / "dataset" / "boards"
            boards.mkdir()
            (boards / "explorer.toml").write_text(EXPLORER)
            (boards / "grade.toml").write_text(GRADE.replace("bs.mixed WHERE", "bs.gone WHERE"))
            assert (await c.post("/api/boards/keep", json={})).status_code == 404  # no draft after regenerate
            assert (await c.post("/api/workspaces/bs_ws/regenerate", json={})).json()["schemas"] == ["bs"]
            assert (await c.get("/api/session")).json()["regenerating"] == "bs_ws"
            model.steps.extend(_model().steps)
            done, _ = await _wait(c, (await c.post("/api/draft", json={})).json()["run_id"])

            r = await c.post("/api/boards/keep", json={})
            assert r.status_code == 200, r.text
            state = r.json()
            assert [b["name"] for b in state["boards"]] == ["explorer", "grade"]
            assert state["model"] is None and state["boards"][0]["why"] == "Kept from the current Workspace."
            assert state["problems"]["explorer"] == [] and state["problems"]["grade"]  # the stale query
            assert (await c.get("/api/session")).json()["boards"] is True

            fixed = [state["boards"][0], {**state["boards"][1], "toml": GRADE}]
            assert (await c.post("/api/boards/validate", json={"boards": fixed})).json()["problems"]["grade"] == []
            r = await c.post("/api/export", json={"files": done["draft"]["files"], "comments": done["draft"]["comments"],
                                                  "name": "ignored", "target": "toml", "dismissed": [0]})
            assert r.status_code == 200, r.text
            assert (boards / "grade.toml").read_text() == GRADE
    finally:
        await agent.registry.close()
