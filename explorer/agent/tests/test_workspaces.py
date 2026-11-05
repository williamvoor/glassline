"""Workspaces (specs/workspaces/SPEC.md phase 1, W2–W7). All model calls are stubbed."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from explorer_agent.__main__ import EXPLORER_ROOT, _workspace_builder
from explorer_agent.config import (
    ConfigError,
    WorkspaceUnavailable,
    data_dir,
    load_workspace_list,
    workspace_settings,
)
from explorer_agent.testing.stub_model import ScriptedCompletion, text, tool_calls
from explorer_agent.workspaces import AVAILABLE, UNAVAILABLE, Built, Guard, Registry, workspace_app
from tests.test_server import CAPS, EXT_URI, final_parts

REPO = EXPLORER_ROOT.parent
DATASET = "explorer/agent/tests/fixtures/austender"
BASE = "http://127.0.0.1:10002"
ORIGIN = "http://127.0.0.1:5173"


def write_list(data: Path, text_: str) -> None:
    (data / "workspaces.toml").write_text(text_, encoding="utf-8")


def entry(name: str, dsn_env: str = "A_DATABASE_URL", **extra) -> str:
    lines = [f'name = "{name}"', f'dataset_dir = "{DATASET}"', f'dsn_env = "{dsn_env}"']
    lines += [f"{k} = {json.dumps(v)}" for k, v in extra.items()]
    return "[[workspace]]\n" + "\n".join(lines) + "\n"


@pytest.fixture
def data(tmp_path):
    return data_dir(str(tmp_path / "data"), REPO)


# --- W2, W3 (no database) -----------------------------------------------------------------------


def test_w2_the_list_loads_in_order_and_a_bad_entry_fails_alone(data):
    write_list(data, entry("austender") + entry("Bad Name") + entry("extra", nope=1) + entry("austender")
               + entry("other", model="openrouter/x") + entry("tuned", model="meta/muse-wonder-1.0", sql_row_cap=10,
                                                             sql_timeout_ms=500, reasoning_effort="low")
               + entry("zero", sql_row_cap=0))
    items = load_workspace_list(data / "workspaces.toml", REPO)
    assert [(i.name, i.error) for i in items] == [
        ("austender", None),
        ("Bad Name", "invalid entry: name must match [a-z0-9][a-z0-9_-]*"),
        ("extra", "invalid entry: unknown key nope"),
        ("austender", "invalid entry: duplicate name"),
        ("other", "invalid entry: model must be one of meta/muse-spark-1.3, meta/muse-wonder-1.0"),
        ("tuned", None),
        ("zero", "invalid entry: sql_row_cap must be a positive integer"),
    ]
    assert items[0].entry.dataset_dir == (REPO / DATASET).resolve()
    assert dict(items[5].entry.overrides) == {"MODEL": "meta/muse-wonder-1.0", "REASONING_EFFORT": "low",
                                             "SQL_TIMEOUT_MS": "500", "SQL_ROW_CAP": "10"}


def test_w2_a_list_that_is_not_toml_is_an_error_and_a_missing_one_is_empty(data):
    assert load_workspace_list(data / "workspaces.toml", REPO) == []
    write_list(data, "[[workspace]\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_workspace_list(data / "workspaces.toml", REPO)


def test_w2_overrides_and_the_prompt_cache_key_apply(data):
    write_list(data, entry("a", sql_row_cap=7, model="meta/muse-wonder-1.0") + entry("b"))
    a, b = (i.entry for i in load_workspace_list(data / "workspaces.toml", REPO))
    env = {"A_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x", "SQL_ROW_CAP": "99"}
    sa, sb = workspace_settings(env, a, EXPLORER_ROOT), workspace_settings(env, b, EXPLORER_ROOT)
    assert (sa.sql_row_cap, sa.model, sa.prompt_cache_key) == (7, "meta/muse-wonder-1.0", "explorer-a")
    assert (sb.sql_row_cap, sb.prompt_cache_key) == (99, "explorer-b")
    assert workspace_settings({**env, "PROMPT_CACHE_KEY": "k"}, b, EXPLORER_ROOT).prompt_cache_key == "k-b"


def test_w5_reasons_never_hold_the_dsn(data):
    write_list(data, entry("a"))
    a = load_workspace_list(data / "workspaces.toml", REPO)[0].entry
    with pytest.raises(WorkspaceUnavailable, match=r"^`A_DATABASE_URL` not set$"):
        workspace_settings({}, a, EXPLORER_ROOT)
    with pytest.raises(WorkspaceUnavailable) as e:
        workspace_settings({"A_DATABASE_URL": "not a dsn secret=hunter2"}, a, EXPLORER_ROOT)
    assert str(e.value) == "`A_DATABASE_URL` is malformed" and "hunter2" not in str(e.value)


def test_w3_a_data_directory_inside_the_repo_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="outside the repo"):
        data_dir(str(REPO / "explorer" / "data"), REPO)
    with pytest.raises(ConfigError, match="outside the repo"):
        data_dir(str(REPO), REPO)
    made = data_dir(str(tmp_path / "x" / "data"), REPO)
    assert made.is_dir() and made.stat().st_mode & 0o777 == 0o700


# --- W6, W7 with a fake builder (no database) ----------------------------------------------------


class Fake:
    """Builds Workspaces that answer one route, and records what was built and closed."""

    def __init__(self):
        self.built: list[str] = []
        self.closed: list[str] = []
        self.running: set[str] = set()
        self.fail: set[str] = set()

    async def __call__(self, entry, settings, url, saved_path):
        if entry.name in self.fail:
            raise WorkspaceUnavailable("database unreachable")
        self.built.append(entry.name)

        async def app(scope, receive, send):
            body = json.dumps({"name": entry.name, "path": scope["path"], "root": scope["root_path"]}).encode()
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        async def close():
            self.closed.append(entry.name)

        return Built(app, close, f"{entry.name} data", lambda: entry.name in self.running)


def client(registry: Registry, host="127.0.0.1:10002") -> httpx.AsyncClient:
    app = workspace_app(registry, ORIGIN, "127.0.0.1", 10002)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}")


ENV = {"A_DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x"}


async def test_w6_reload_mounts_retries_rebuilds_unmounts_and_409s(data):
    fake = Fake()
    env = dict(ENV)
    write_list(data, entry("one") + entry("two", "B_DATABASE_URL"))
    registry = Registry(data, REPO, EXPLORER_ROOT, env, fake, BASE)
    await registry.start()
    async with client(registry) as c:
        listed = (await c.get("/workspaces")).json()
        assert [(w["name"], w["status"], w.get("reason")) for w in listed] == [
            ("one", AVAILABLE, None), ("two", UNAVAILABLE, "`B_DATABASE_URL` not set")]
        assert listed[0]["url"] == f"{BASE}/w/one" and listed[0]["description"] == "one data"
        routed = (await c.get("/w/one/conversations")).json()
        assert routed == {"name": "one", "path": "/conversations", "root": "/w/one"}
        assert (await c.get("/w/two/conversations")).status_code == 503
        assert (await c.get("/w/two/conversations")).json() == {"error": "`B_DATABASE_URL` not set"}
        assert (await c.get("/w/nope/x")).status_code == 404

        env["B_DATABASE_URL"] = ENV["A_DATABASE_URL"]  # retried on reload
        write_list(data, entry("one") + entry("two", "B_DATABASE_URL") + entry("three"))
        r = await c.post("/workspaces/reload", json={})
        assert r.status_code == 200
        assert [(w["name"], w["status"]) for w in r.json()] == [("one", AVAILABLE), ("two", AVAILABLE), ("three", AVAILABLE)]
        assert fake.built == ["one", "two", "three"]  # "one" unchanged: kept, not rebuilt

        fake.running.add("three")
        write_list(data, entry("one") + entry("two", "B_DATABASE_URL"))
        r = await c.post("/workspaces/reload", json={})
        assert r.status_code == 409 and "three" in r.json()["error"]
        assert [w["name"] for w in (await c.get("/workspaces")).json()] == ["one", "two", "three"]
        fake.running.clear()
        r = await c.post("/workspaces/reload", json={})
        assert [w["name"] for w in r.json()] == ["one", "two"] and fake.closed == ["three"]

        write_list(data, entry("one", sql_row_cap=5) + entry("two", "B_DATABASE_URL"))  # changed: rebuilt
        fake.running.add("one")
        assert (await c.post("/workspaces/reload", json={})).status_code == 409
        fake.running.clear()
        assert (await c.post("/workspaces/reload", json={})).status_code == 200
        assert fake.built[-1] == "one" and fake.closed[-1] == "one"

        write_list(data, "not toml [[")
        r = await c.post("/workspaces/reload", json={})
        assert r.status_code == 400 and [w["name"] for w in (await c.get("/workspaces")).json()] == ["one", "two"]
    await registry.close()


async def test_w6_a_changed_dataset_file_rebuilds_on_reload(data, tmp_path):
    import shutil

    copy = tmp_path / "ds"
    shutil.copytree(REPO / DATASET, copy, ignore=shutil.ignore_patterns(".*"))
    fake = Fake()
    write_list(data, f'[[workspace]]\nname = "c"\ndataset_dir = "{copy}"\ndsn_env = "A_DATABASE_URL"\n')
    registry = Registry(data, REPO, EXPLORER_ROOT, ENV, fake, BASE)
    await registry.start()
    await registry.reload()
    assert fake.built == ["c"]
    (copy / "notes.md").write_text((copy / "notes.md").read_text() + "\nMore.\n")
    await registry.reload()
    assert fake.built == ["c", "c"] and fake.closed == ["c"]
    await registry.close()


async def test_w5_no_available_workspace_still_serves_the_list(data):
    fake = Fake()
    fake.fail.add("one")
    write_list(data, entry("one"))
    registry = Registry(data, REPO, EXPLORER_ROOT, ENV, fake, BASE)
    await registry.start()
    async with client(registry) as c:
        assert (await c.get("/workspaces")).json()[0]["reason"] == "database unreachable"


async def test_w7_post_and_delete_refuse_a_foreign_host_and_a_non_json_post(data):
    write_list(data, entry("one"))
    registry = Registry(data, REPO, EXPLORER_ROOT, ENV, Fake(), BASE)
    await registry.start()
    async with client(registry, "evil.example:10002") as c:
        assert (await c.post("/workspaces/reload", json={})).status_code == 403
        assert (await c.delete("/w/one/conversations")).status_code == 403
        # Refuter F2: reads hold saved questions and rows, so a foreign Host is refused for them too.
        assert (await c.get("/workspaces")).status_code == 403
        assert (await c.get("/w/one/conversations")).status_code == 403
    async with client(registry) as c:
        assert (await c.post("/workspaces/reload", content="x", headers={"content-type": "text/plain"})).status_code == 403
        assert (await c.post("/w/one/", content="{}", headers={"content-type": "text/plain"})).status_code == 403
        assert (await c.delete("/w/one/conversations")).status_code == 200  # the fake answers 200
        assert (await c.post("/workspaces/reload", json={})).status_code == 200
    async with client(registry, "localhost:10002") as c:
        r = await c.options("/workspaces/reload", headers={"origin": ORIGIN, "access-control-request-method": "POST"})
        assert r.headers["access-control-allow-origin"] == ORIGIN
        r = await c.options("/workspaces/reload", headers={"origin": "http://evil.example",
                                                           "access-control-request-method": "POST"})
        assert "access-control-allow-origin" not in r.headers


def test_w3_an_existing_data_directory_keeps_its_mode(tmp_path):
    """Refuter F9: only a directory this call creates is made 0700."""
    existing = tmp_path / "shared"
    existing.mkdir(mode=0o755)
    existing.chmod(0o755)
    data_dir(str(existing), REPO)
    assert existing.stat().st_mode & 0o777 == 0o755


def test_guard_hosts_include_the_configured_host():
    g = Guard.for_port(lambda *a: None, "0.0.0.0", 9)
    assert g.hosts == {b"0.0.0.0:9", b"127.0.0.1:9", b"localhost:9"}


# --- W4, W5 against the seed database ------------------------------------------------------------


def stub_builder(models: dict[str, ScriptedCompletion], pools: dict | None = None):
    async def build(entry, settings, url, saved_path):
        built = await _workspace_builder(entry, settings, url, saved_path, connect_timeout_s=2,
                                         completion=models.get(entry.name))
        if pools is not None:
            pools[entry.name] = built.pool
        return built
    return build


async def ask(c: httpx.AsyncClient, path: str, question: str) -> list[dict]:
    message = {"kind": "message", "role": "user", "messageId": question, "parts": [{"kind": "text", "text": question}],
               "metadata": CAPS}
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/stream", "params": {"message": message}}
    events = []
    async with c.stream("POST", path, json=body, headers={"X-A2A-Extensions": EXT_URI}) as r:
        assert r.status_code == 200, await r.aread()
        async for line in r.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:])["result"])
    return events


@pytest.mark.db
async def test_w4_two_workspaces_mount_with_their_own_card_pool_and_cache_key(pg, data):
    # Each Workspace's login names itself (application_name), so a query shows whose database
    # connection ran it: a question in a must run on a's pool only.
    app_sql = "SELECT current_setting('application_name') AS app"
    cite = "Ran on {r1[0].app}."
    models = {"a": ScriptedCompletion([tool_calls(("run_sql", {"sql": app_sql, "purpose": "who"})), text(cite),
                                       text("From a again.")]),
              "b": ScriptedCompletion([tool_calls(("run_sql", {"sql": app_sql, "purpose": "who"})), text(cite)])}
    write_list(data, entry("a") + entry("b", "B_DATABASE_URL"))
    sep = "&" if "?" in pg.ro_dsn else "?"
    env = {"A_DATABASE_URL": f"{pg.ro_dsn}{sep}application_name=ws_a", "B_DATABASE_URL": f"{pg.ro_dsn}{sep}application_name=ws_b"}
    pools: dict = {}
    registry = Registry(data, REPO, EXPLORER_ROOT, env, stub_builder(models, pools), BASE)
    await registry.start()
    try:
        assert [w["status"] for w in registry.listing()] == [AVAILABLE, AVAILABLE]
        async with client(registry) as c:
            cards = [(await c.get(f"/w/{n}/.well-known/agent-card.json")).json() for n in "ab"]
            assert [card["url"] for card in cards] == [f"{BASE}/w/a", f"{BASE}/w/b"]
            assert final_parts(await ask(c, "/w/a", "q1"))[0]["text"] == "Ran on ws\\_a."
            assert final_parts(await ask(c, "/w/a", "q2"))[0]["text"] == "From a again."
            assert final_parts(await ask(c, "/w/b", "q3"))[0]["text"] == "Ran on ws\\_b."
            assert (await c.get("/w/a/conversations")).status_code == 200
        a_calls, b_calls = models["a"].calls, models["b"].calls
        assert [m["messages"][-1]["content"] for m in a_calls if m["messages"][-1]["role"] == "user"] == ["q1", "q2"]
        assert [m["messages"][-1]["content"] for m in b_calls if m["messages"][-1]["role"] == "user"] == ["q3"]
        assert {m["prompt_cache_key"] for m in a_calls} == {"explorer-a"} and b_calls[0]["prompt_cache_key"] == "explorer-b"
        assert a_calls[0]["messages"][0] == a_calls[2]["messages"][0]  # the byte-identical prefix
        assert pools["a"] is not pools["b"] and pools["a"] is not None
        assert (data / "workspaces" / "a" / "conversations.sqlite").stat().st_mode & 0o777 == 0o600
    finally:
        await registry.close()


@pytest.mark.db
async def test_w5_a_writing_login_an_unset_variable_or_an_unreachable_database_is_unavailable(pg, data):
    write_list(data, entry("good") + entry("writes", "W_DATABASE_URL") + entry("unset", "U_DATABASE_URL")
               + entry("down", "D_DATABASE_URL"))
    env = {"A_DATABASE_URL": pg.ro_dsn, "W_DATABASE_URL": pg.loader_dsn,
           "D_DATABASE_URL": "postgresql://explorer_ro:hunter2@127.0.0.1:1/austender"}
    registry = Registry(data, REPO, EXPLORER_ROOT, env, stub_builder({}), BASE)
    await registry.start()
    try:
        listed = registry.listing()
        assert [(w["name"], w["status"], w.get("reason")) for w in listed] == [
            ("good", AVAILABLE, None), ("writes", UNAVAILABLE, "login can write"),
            ("unset", UNAVAILABLE, "`U_DATABASE_URL` not set"), ("down", UNAVAILABLE, "database unreachable")]
        assert "hunter2" not in json.dumps(listed) and "austender_loader" not in json.dumps(listed)
        async with client(registry) as c:
            assert (await c.get("/w/good/.well-known/agent-card.json")).status_code == 200
            assert (await c.get("/w/writes/conversations")).json() == {"error": "login can write"}
    finally:
        await registry.close()


@pytest.mark.db
async def test_w5_an_allowed_schema_with_no_visible_tables_is_not_called_a_writing_login(pg, data, tmp_path):
    """Refuter F6: only the write-rights check says "login can write"."""
    import shutil

    copy = tmp_path / "ds"
    shutil.copytree(REPO / DATASET, copy, ignore=shutil.ignore_patterns(".*", "boards", "comments.toml"))
    text_ = (copy / "dataset.toml").read_text()
    (copy / "dataset.toml").write_text(text_.replace('allowed_schemas = ["mart"]', 'allowed_schemas = ["mart", "nothing_here"]'))
    assert "nothing_here" in (copy / "dataset.toml").read_text()
    write_list(data, f'[[workspace]]\nname = "e"\ndataset_dir = "{copy}"\ndsn_env = "A_DATABASE_URL"\n')
    registry = Registry(data, REPO, EXPLORER_ROOT, {"A_DATABASE_URL": pg.ro_dsn}, stub_builder({}), BASE)
    await registry.start()
    try:
        reason = registry.listing()[0]["reason"]
        assert reason.startswith("dataset invalid: ") and "nothing_here" in reason
    finally:
        await registry.close()


@pytest.mark.db
def test_workspace_mode_starts_from_the_command_line(pg, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_list(data, entry("aus"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MODEL", "META_API_KEY")}
    env.update({"EXPLORER_ENV_FILE": "/nonexistent/.env", "EXPLORER_DATA_DIR": str(data), "AGENT_PORT": str(port),
                "A_DATABASE_URL": pg.ro_dsn, "DATASET_DIR": "agent/tests/fixtures/austender"})
    proc = subprocess.Popen([sys.executable, "-m", "explorer_agent"], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        line = ""
        while time.monotonic() < deadline and "listening" not in line:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                pytest.fail(f"agent exited: {proc.stderr.read()}")
        listed = httpx.get(f"http://127.0.0.1:{port}/workspaces", timeout=10).json()
        assert [(w["name"], w["status"]) for w in listed] == [("aus", AVAILABLE)]
        card = httpx.get(f"http://127.0.0.1:{port}/w/aus/.well-known/agent-card.json", timeout=10).json()
        assert card["url"] == f"http://127.0.0.1:{port}/w/aus"
        assert httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=10).status_code == 404
    finally:
        proc.terminate()
        _, err = proc.communicate(timeout=10)
    assert "DATASET_DIR is ignored in Workspace mode" in err and pg.ro_dsn not in err


def test_workspace_mode_refuses_a_data_directory_in_the_repo():
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL",)}
    env.update({"EXPLORER_ENV_FILE": "/nonexistent/.env", "EXPLORER_DATA_DIR": str(REPO / "explorer" / "tmpdata")})
    done = subprocess.run([sys.executable, "-m", "explorer_agent"], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 1 and done.stderr.strip().startswith("explorer: EXPLORER_DATA_DIR must be outside")
    assert not (REPO / "explorer" / "tmpdata").exists()



def test_w3_a_relative_data_directory_is_refused():
    """Refuter (phase 2) F9: the agent and the bootstrap server run from different directories."""
    with pytest.raises(ConfigError, match="absolute"):
        data_dir("data/explorer", REPO)
