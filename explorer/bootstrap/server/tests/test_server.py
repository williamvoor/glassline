"""The local HTTP server, in-process with a stub model: never the network (SPEC.md phase 2c)."""

import asyncio
import copy
import logging
import math

import explorer_agent  # noqa: F401  (first: keeps LiteLLM from fetching its price map)
import httpx
import litellm
import pytest
from explorer_agent.config import load_dataset
from explorer_agent.schema import SchemaCatalog, overlay_problems
from explorer_agent.testing.stub_model import ScriptedCompletion, tool_calls

from explorer_bootstrap.draft import build_draft_prompt, USER_MESSAGE
from explorer_bootstrap.server import build_app

from conftest import RO_PASSWORD, valid_args

pytestmark = pytest.mark.db

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
DB = {"host": "h", "name": "d"}


def _model(args=None, *, error=None):
    if error is not None:
        return ScriptedCompletion([error])
    return ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT count(*) AS n FROM bs.mixed", "purpose": "count rows"})),
        tool_calls(("submit_draft", args or valid_args())),
    ])


def _app(ro_pool, tmp_path, completion, env=None):
    root = tmp_path / "datasets"
    root.mkdir(exist_ok=True)
    return build_app(ro_pool, database=DB, env=env or {}, port=PORT, timeout_ms=2000, datasets_root=root,
                     repo_root=tmp_path, completion=completion), root


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def _wait(client, run_id):
    after, events = 0, []
    for _ in range(200):
        r = (await client.get(f"/api/draft/{run_id}/events", params={"after": after})).json()
        events += r["events"]
        after = r["next"]
        if r["done"]:
            return r, events
        await asyncio.sleep(0.02)
    pytest.fail("draft never finished")


async def _drafted(client, args=None):
    assert (await client.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
    run_id = (await client.post("/api/draft", json={})).json()["run_id"]
    done, events = await _wait(client, run_id)
    return done, events


# S1
async def test_full_flow(ro_pool, tmp_path):
    app, root = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        conn = (await client.get("/api/connection")).json()
        assert conn["host"] == "h" and {"name": "bs", "objects": 3} in conn["schemas"]
        report = (await client.post("/api/report", json={"schemas": ["bs"]})).json()
        assert report["summary"]["objects"] == 3
        est = (await client.post("/api/estimate", json={"model": "meta/muse-spark-1.3"})).json()
        assert est["model"] == "meta/muse-spark-1.3"
        run_id = (await client.post("/api/draft", json={"model": "meta/muse-wonder-1.0", "effort": "low"})).json()["run_id"]
        done, events = await _wait(client, run_id)
        assert "error" not in done, done
        assert [e["type"] for e in events] == ["call", "sql", "call"]
        assert done["validation"]["errors"] == [] and done["validation"]["context_chars"] > 0
        draft = done["draft"]
        assert [c["result"]["state"] for c in draft["claims"]] == ["verified", "verified", "needs_review"]
        assert (await client.get("/api/draft")).json()["draft"] == draft

        claims = copy.deepcopy(draft["claims"])
        claims[0]["expect"]["value"] = 101
        verified = (await client.post("/api/claims/verify", json={"claims": claims})).json()["claims"]
        assert verified[0]["result"]["state"] == "failed"

        edits = {"files": draft["files"], "comments": draft["comments"]}
        bad = {**edits, "files": {**draft["files"], "dataset_toml": "name = "}}
        assert (await client.post("/api/validate", json=bad)).json()["errors"]
        assert (await client.post("/api/validate", json=edits)).json()["errors"] == []

        refused = await client.post("/api/export", json={**edits, "name": "bs_toml", "target": "toml", "dismissed": []})
        assert refused.status_code == 422 and any("claim 1 is failed" in r for r in refused.json()["reasons"])
        for name, target in (("bs_toml", "toml"), ("bs_sql", "sql")):
            r = await client.post("/api/export", json={**edits, "name": name, "target": target, "dismissed": [0]})
            assert r.status_code == 200, r.json()
            assert r.json()["path"] == f"datasets/{name}"
    ds = load_dataset(root / "bs_toml")
    objects = await SchemaCatalog(ro_pool, ds.allowed_schemas, ds.comment_overlay).objects()
    assert overlay_problems(objects, ds.comment_overlay) == []
    assert (root / "bs_sql" / "comments.sql").is_file()


# S2
async def test_request_guard(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        assert (await client.get("/api/connection")).status_code == 200
        assert (await client.get("/api/connection", headers={"host": f"localhost:{PORT}"})).status_code == 200
        assert (await client.get("/api/connection", headers={"host": "evil.example"})).status_code == 403
        assert (await client.get("/api/connection", headers={"host": "127.0.0.1:9999"})).status_code == 403
        r = await client.post("/api/report", content='{"schemas": ["bs"]}', headers={"content-type": "text/plain"})
        assert r.status_code == 403
        r = await client.post("/api/report", content="schemas=bs",
                              headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 403
        assert (await client.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
        assert (await client.get("/")).status_code == 200  # the "build the UI" hint


# S3
async def test_draft_errors(ro_pool, tmp_path):
    gate = asyncio.Event()

    async def slow(_kwargs):
        await gate.wait()
        return tool_calls(("submit_draft", valid_args()))

    app, _ = _app(ro_pool, tmp_path, ScriptedCompletion([slow]))
    async with _client(app) as client:
        assert (await client.post("/api/draft", json={})).json() == {"error": "run the report first"}
        assert (await client.post("/api/estimate", json={})).status_code == 400
        await client.post("/api/report", json={"schemas": ["bs"]})
        r = await client.post("/api/draft", json={"model": "openai/gpt-x"})
        assert r.status_code == 400 and "model must be one of" in r.json()["error"]
        run_id = (await client.post("/api/draft", json={})).json()["run_id"]
        assert (await client.post("/api/draft", json={})).status_code == 409
        gate.set()
        done, _ = await _wait(client, run_id)
        assert "draft" in done
        assert (await client.get("/api/draft/nope/events")).status_code == 404
        assert (await client.post("/api/report", json={"schemas": ["Bad-Name"]})).status_code == 400


# S4
async def test_claims_integrity(ro_pool, tmp_path):
    args = valid_args()
    args["claims"][0]["expect"]["value"] = 99  # fails
    app, root = _app(ro_pool, tmp_path, _model(args))
    async with _client(app) as client:
        done, _ = await _drafted(client)
        draft = done["draft"]
        edits = {"files": draft["files"], "comments": draft["comments"]}
        # a client can't send a state: the stored result is used
        faked = copy.deepcopy(draft["claims"])
        faked[0]["result"] = {"state": "verified", "message": "", "actual": None}
        r = await client.post("/api/export", json={**edits, "name": "x", "target": "toml", "dismissed": [],
                                                   "claims": faked})
        assert r.status_code == 422 and any("claim 1 is failed" in x for x in r.json()["reasons"])
        # a result sent to verify is ignored too; the claim is rerun
        out = (await client.post("/api/claims/verify", json={"claims": faked})).json()["claims"]
        assert out[0]["result"]["state"] == "failed"
        bad = await client.post("/api/claims/verify", json={"claims": [{"text": "t"}]})
        assert bad.status_code == 400 and bad.json()["reasons"]
        # dismissing by index unblocks
        r = await client.post("/api/export", json={**edits, "name": "x", "target": "toml", "dismissed": [0]})
        assert r.status_code == 200
        r = await client.post("/api/export", json={**edits, "name": "y", "target": "toml", "dismissed": [True]})
        assert r.status_code == 400
    assert (root / "x").is_dir() and not (root / "y").exists()


# S5
async def test_no_secrets(ro_pool, tmp_path, caplog, monkeypatch):
    key = "sk-test-SECRETKEY-0123456789abcdef"
    monkeypatch.setenv("META_API_KEY", key)
    caplog.set_level(logging.DEBUG)
    bodies = []
    err = litellm.exceptions.AuthenticationError(message=f"invalid key {key}", llm_provider="meta", model="m")
    for completion in (_model(), _model(error=err)):
        d = tmp_path / str(len(bodies))
        d.mkdir()
        app, _ = _app(ro_pool, d, completion, env={"META_API_KEY": key})
        async with _client(app) as client:
            for r in (await client.get("/api/connection"), await client.post("/api/report", json={"schemas": ["bs"]}),
                      await client.post("/api/estimate", json={})):
                bodies.append(r.text)
            run_id = (await client.post("/api/draft", json={})).json()["run_id"]
            done, _ = await _wait(client, run_id)
            bodies.append(str(done))
            if "draft" in done:
                draft = done["draft"]
                edits = {"files": draft["files"], "comments": draft["comments"]}
                for r in (await client.post("/api/claims/verify", json={"claims": draft["claims"]}),
                          await client.post("/api/validate", json=edits),
                          await client.post("/api/export", json={**edits, "name": "n", "target": "sql", "dismissed": []}),
                          await client.post("/api/export", json={**edits, "name": "n", "target": "sql", "dismissed": []})):
                    bodies.append(r.text)
    assert "AuthenticationError" in bodies[-1]
    text = "\n".join(bodies) + "\n".join(r.getMessage() for r in caplog.records)
    for secret in (key, RO_PASSWORD, "explorer_ro"):
        assert secret not in text


# S6
async def test_connection(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        names = [s["name"] for s in (await client.get("/api/connection")).json()["schemas"]]
    assert "bs" in names and "mart" in names
    assert not [n for n in names if n.startswith("pg_") or n == "information_schema"]
    assert "raw" not in names  # explorer_ro has no USAGE there


# S7
async def test_estimate(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model(), env={"BOOTSTRAP_MAX_TOKENS": "1000", "BOOTSTRAP_MAX_TOOL_ITERATIONS": "5"})
    async with _client(app) as client:
        report = (await client.post("/api/report", json={"schemas": ["bs"]})).json()
        est = (await client.post("/api/estimate", json={"model": "meta/muse-wonder-1.0"})).json()
    chars = len(build_draft_prompt(report)) + len(USER_MESSAGE)
    tokens = math.ceil(chars / 3)
    first = (tokens * 4.0 + 1000 * 20.0) / 1e6
    assert est == {"model": "meta/muse-wonder-1.0", "prompt_chars": chars, "input_tokens_est": tokens,
                   "max_calls": 5, "first_call_usd": round(first, 4), "run_ceiling_usd": round(first * 5, 4)}


async def test_points_to_settings_now_that_the_browser_ui_is_retired(ro_pool, tmp_path):
    # Workspaces G22: explorer/bootstrap/ui is retired; its views live in the explorer's Settings.
    root = tmp_path / "datasets"
    root.mkdir()
    app = build_app(ro_pool, database=DB, env={}, port=PORT, timeout_ms=2000, datasets_root=root,
                    repo_root=tmp_path)
    async with _client(app) as client:
        r = await client.get("/")
        assert r.status_code == 200 and "#/settings" in r.text and "bootstrap/ui" not in r.text
        assert (await client.get("/api/connection")).status_code == 200


# Review fixes (phase 2c refuter)
async def test_bad_bodies_are_400(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        await client.post("/api/report", json={"schemas": ["bs"]})
        for path in ("/api/draft", "/api/estimate"):
            for body in ([1], "not json", {"model": 123}, {"effort": ["low"]}):
                kwargs = {"content": body, "headers": {"content-type": "application/json"}} if isinstance(body, str) \
                    else {"json": body}
                r = await client.post(path, **kwargs)
                assert r.status_code == 400, (path, body, r.text)
        assert app.app.state.bootstrap.run is None  # no paid run started from garbage


async def test_report_is_refused_while_drafting(ro_pool, tmp_path):
    gate = asyncio.Event()

    async def slow(_kwargs):
        await gate.wait()
        return tool_calls(("submit_draft", valid_args()))

    app, _ = _app(ro_pool, tmp_path, ScriptedCompletion([slow]))
    async with _client(app) as client:
        await client.post("/api/report", json={"schemas": ["bs"]})
        run_id = (await client.post("/api/draft", json={})).json()["run_id"]
        assert (await client.post("/api/report", json={"schemas": ["bs"]})).status_code == 409
        gate.set()
        await _wait(client, run_id)
        assert (await client.post("/api/report", json={"schemas": ["bs"]})).status_code == 200


async def test_unexpected_error_logs_only_the_class(ro_pool, tmp_path, caplog, monkeypatch):
    from explorer_bootstrap import server as server_mod

    async def boom(_pool):
        raise RuntimeError("secret-ish detail postgresql://u:pw@h/db")

    monkeypatch.setattr(server_mod, "visible_schemas", boom)
    caplog.set_level(logging.DEBUG)
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        r = await client.get("/api/connection")
    assert r.status_code == 500 and r.json() == {"error": "unexpected RuntimeError"}
    logged = "\n".join(f"{rec.getMessage()} {rec.exc_text or ''}" for rec in caplog.records)
    assert "RuntimeError" in logged and "secret-ish" not in logged and "pw@" not in logged


def _write_dataset(root, name="demo", schemas=("bs",), overlay=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    overlay_toml = ""
    if overlay is not None:
        (d / "comments.toml").write_text(overlay, encoding="utf-8")
        overlay_toml = '\n[comments]\noverlay = "comments.toml"\n'
    (d / "dataset.toml").write_text(
        "name = \"demo\"\ndescription = \"d\"\n"
        f"allowed_schemas = [{', '.join(repr(s) for s in schemas)}]\n"
        'glossary = "glossary.md"\nnotes = "notes.md"\n'
        '[currency]\ncode = "AUD"\nsymbol = "$"\n' + overlay_toml,
        encoding="utf-8",
    )
    (d / "notes.md").write_text("notes\n", encoding="utf-8")
    (d / "glossary.md").write_text("glossary\n", encoding="utf-8")
    return d


# S10: the report resolves a dataset name against the server's own datasets directory,
# so a web client with no filesystem access gets the same overlay and context as --dataset-dir.
async def test_report_by_dataset_name(ro_pool, tmp_path):
    app, root = _app(ro_pool, tmp_path, _model())
    _write_dataset(root)
    (root / "stray").mkdir()  # no dataset.toml: not a dataset
    backup = root / ".demo.bak-20261104T000000"
    backup.mkdir()  # force backups are dot-prefixed: the picker ignores them
    (backup / "dataset.toml").write_text('name = "demo"\n', encoding="utf-8")
    async with _client(app) as client:
        assert (await client.get("/api/datasets")).json() == {"datasets": ["demo"]}
        r = await client.post("/api/report", json={"dataset": "demo"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schemas"] == ["bs"]
        assert body["summary"]["context_scope"] == "full"
        assert body["summary"]["context_chars"] > 0


async def test_report_by_dataset_name_applies_the_overlay(ro_pool, tmp_path):
    app, root = _app(ro_pool, tmp_path, _model())
    _write_dataset(root, overlay='["bs.mixed"]\ncomment = "Overlaid table."\n')
    async with _client(app) as client:
        r = await client.post("/api/report", json={"dataset": "demo"})
        assert r.status_code == 200, r.text
        mixed = next(o for o in r.json()["objects"] if o["name"] == "mixed")
        assert mixed["comment"] == "Overlaid table."
        assert mixed["comment_source"] == "overlay"
        assert mixed["database_comment"] == "A table with some comments."


async def test_report_dataset_selection_errors(ro_pool, tmp_path):
    app, root = _app(ro_pool, tmp_path, _model())
    _write_dataset(root)
    async with _client(app) as client:
        assert (await client.post("/api/report", json={"dataset": "missing"})).status_code == 404
        for bad in ["..", "a/b", "", "a b", 42, None]:
            r = await client.post("/api/report", json={"dataset": bad})
            assert r.status_code == 400, bad
        r = await client.post("/api/report", json={"schemas": ["bs"], "dataset": "demo"})
        assert r.status_code == 400
        assert (await client.post("/api/report", json={})).status_code == 400
        # the schema path still works unchanged
        assert (await client.post("/api/report", json={"schemas": ["bs"]})).status_code == 200


# S11: the web app reads the schema and the model list from the server, never the filesystem.
async def test_schema_dump(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        r = await client.get("/api/schema")
        assert r.status_code == 200, r.text
        by_name = {s["name"]: s["tables"] for s in r.json()["schemas"]}
        assert "bs" in by_name  # plus whatever else the login can see (e.g. austender's mart)
        mixed = next(t for t in by_name["bs"] if t["name"] == "mixed")
        assert mixed["kind"] == "table"
        assert mixed["comment"] == "A table with some comments."
        cols = {c["name"]: c for c in mixed["columns"]}
        assert cols["amount"]["type"] == "numeric(10,2)" and cols["amount"]["comment"] == "Amount in dollars."
        assert cols["name"]["comment"] is None  # uncommented columns dump as null
        r = await client.get("/api/schema", params={"schemas": "bs"})
        assert r.status_code == 200 and len(r.json()["schemas"]) == 1
        assert (await client.get("/api/schema", params={"schemas": "nosuch"})).json() == {
            "schemas": [{"name": "nosuch", "tables": []}]
        }
        assert (await client.get("/api/schema", params={"schemas": "Bad-Name"})).status_code == 400


async def test_models(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        assert (await client.get("/api/models")).json() == {
            "models": ["meta/muse-wonder-1.0", "meta/muse-spark-1.3"],
            "default": "meta/muse-wonder-1.0",
        }
    spark_app, _ = _app(ro_pool, tmp_path, _model(), env={"BOOTSTRAP_MODEL": "meta/muse-spark-1.3"})
    async with _client(spark_app) as client:
        assert (await client.get("/api/models")).json()["default"] == "meta/muse-spark-1.3"


# S12: export --force writes the draft's files into an existing directory (backup kept),
# and still refuses without it.
async def test_export_force(ro_pool, tmp_path):
    app, root = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        done, _ = await _drafted(client)
        edits = {"files": done["draft"]["files"], "comments": done["draft"]["comments"]}
        existing = root / "demo"
        existing.mkdir()
        (existing / "dataset.toml").write_text("old toml\n", encoding="utf-8")
        (existing / "scenarios.toml").write_text("keep me\n", encoding="utf-8")
        refused = await client.post("/api/export", json={**edits, "name": "demo", "target": "toml", "dismissed": []})
        assert refused.status_code == 422 and any("already exists" in r for r in refused.json()["reasons"])
        bad = await client.post("/api/export", json={**edits, "name": "demo", "target": "toml", "dismissed": [], "force": "yes"})
        assert bad.status_code == 400
        r = await client.post("/api/export", json={**edits, "name": "demo", "target": "toml", "dismissed": [], "force": True})
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["overwrote"] == ["dataset.toml"]
        assert body["backup"] is not None and body["path"] == "datasets/demo"
        assert (existing / "dataset.toml").read_text(encoding="utf-8") != "old toml\n"
        assert (existing / "scenarios.toml").read_text(encoding="utf-8") == "keep me\n"


# --- phase 3: Boards (S12, S13) ----------------------------------------------------------------

from test_boards_draft import EXPLORER as EXPLORER_BOARD, GRADE as GRADE_BOARD, good_set  # noqa: E402


def _model_then_boards(*boards_submits):
    steps = [
        tool_calls(("run_sql", {"sql": "SELECT count(*) AS n FROM bs.mixed", "purpose": "count rows"})),
        tool_calls(("submit_draft", valid_args())),
    ]
    steps += [tool_calls(("submit_boards", {"boards": b})) for b in boards_submits]
    return ScriptedCompletion(steps)


async def _boards(client):
    r = await client.post("/api/boards/draft", json={})
    assert r.status_code == 200, r.json()
    return await _wait(client, r.json()["run_id"])


# S12
async def test_boards_flow_over_http(ro_pool, tmp_path):
    app, root = _app(ro_pool, tmp_path, _model_then_boards(good_set()))
    async with _client(app) as client:
        # No Boards yet is an answer, not an error: a 404 showed in the browser console (M1, 2026-11-05).
        r = await client.get("/api/boards")
        assert (r.status_code, r.json()) == (200, None)
        assert (await client.post("/api/boards/draft", json={})).status_code == 404  # no draft yet
        done, _ = await _drafted(client)
        draft = done["draft"]
        done, events = await _boards(client)
        assert "error" not in done, done
        assert [e["type"] for e in events] == ["call"]
        state = done["boards"]
        assert [b["name"] for b in state["boards"]] == ["explorer", "grade"]
        assert state["problems"] == {"explorer": [], "grade": []} and state["set_problems"] == []
        assert state["preview"]["grade"]["key"]["source"] == "bs.mixed.grade"
        assert state["usage"]["totals"]["calls"] == 1
        assert isinstance(state["model"], str) and state["model"]  # Workspaces SPEC E9
        assert (await client.get("/api/boards")).json() == state

        # validate stores and re-checks edited text
        broken = [{**b, "toml": b["toml"].replace('"grade,amount"', '"grade,amont"')} if b["name"] == "explorer" else b
                  for b in state["boards"]]
        r = (await client.post("/api/boards/validate", json={"boards": broken})).json()
        assert "amont" in r["problems"]["explorer"][0] and "explorer" not in r["preview"]
        assert r["model"] == state["model"]  # an edit keeps the drafting model (E9)
        assert (await client.get("/api/boards")).json()["boards"][0]["toml"] == broken[0]["toml"]
        bad = await client.post("/api/boards/validate", json={"boards": [{"name": "explorer"}]})
        assert bad.status_code == 400 and bad.json()["reasons"]

        edits = {"files": draft["files"], "comments": draft["comments"], "dismissed": []}
        refused = await client.post("/api/export", json={**edits, "name": "bs_boards", "target": "toml"})
        assert refused.status_code == 422 and any("amont" in r for r in refused.json()["reasons"])
        assert (await client.post("/api/boards/validate", json={"boards": state["boards"]})).json()["problems"]["explorer"] == []
        ok = await client.post("/api/export", json={**edits, "name": "bs_boards", "target": "toml"})
        assert ok.status_code == 200, ok.json()
    assert sorted(load_dataset(root / "bs_boards").boards) == ["explorer", "grade"]


async def test_export_rechecks_boards_instead_of_trusting_stored_problems(ro_pool, tmp_path):
    app, root = _app(ro_pool, tmp_path, _model_then_boards(good_set()))
    async with _client(app) as client:
        draft = (await _drafted(client))[0]["draft"]
        await _boards(client)
        stored = app.app.state.bootstrap.boards  # _Guard wraps the Starlette app
        assert stored["problems"] == {"explorer": [], "grade": []}
        stored["boards"][1]["toml"] = GRADE_BOARD.replace("FROM bs.mixed WHERE grade = :g ORDER", "FROM bs.nope WHERE grade = :g ORDER")
        r = await client.post("/api/export", json={"files": draft["files"], "comments": draft["comments"], "dismissed": [],
                                                   "name": "stale", "target": "toml"})
        assert r.status_code == 422
        assert any("boards/grade.toml" in x and "nope" in x for x in r.json()["reasons"])
    assert not (root / "stale").exists()


async def test_boards_need_valid_files_and_one_run_at_a_time(ro_pool, tmp_path):
    gate = asyncio.Event()

    class Slow(ScriptedCompletion):
        async def __call__(self, **kw):
            if any(t["function"]["name"] == "submit_boards" for t in kw["tools"]):
                await gate.wait()
            return await super().__call__(**kw)

    model = Slow(_model_then_boards(good_set()).steps)
    app, _ = _app(ro_pool, tmp_path, model)
    async with _client(app) as client:
        draft = (await _drafted(client))[0]["draft"]
        bad = {"files": {**draft["files"], "dataset_toml": "name = "}, "comments": draft["comments"]}
        await client.post("/api/validate", json=bad)
        r = await client.post("/api/boards/draft", json={})
        assert r.status_code == 400 and any("not valid TOML" in x for x in r.json()["reasons"])
        await client.post("/api/validate", json={"files": draft["files"], "comments": draft["comments"]})
        run_id = (await client.post("/api/boards/draft", json={})).json()["run_id"]
        assert (await client.post("/api/boards/draft", json={})).status_code == 409
        assert (await client.post("/api/draft", json={})).status_code == 409
        assert (await client.post("/api/report", json={"schemas": ["bs"]})).status_code == 409
        gate.set()
        done, _ = await _wait(client, run_id)
        assert done["boards"]["boards"]


async def test_a_new_draft_clears_the_boards(ro_pool, tmp_path):
    model = _model_then_boards(good_set())
    model.steps += _model().steps
    app, _ = _app(ro_pool, tmp_path, model)
    async with _client(app) as client:
        await _drafted(client)
        await _boards(client)
        assert (await client.get("/api/boards")).status_code == 200
        run_id = (await client.post("/api/draft", json={})).json()["run_id"]
        await _wait(client, run_id)
        assert (await client.get("/api/boards")).json() is None


# S13
async def test_boards_estimate(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as client:
        assert (await client.post("/api/boards/estimate", json={})).status_code == 404
        await _drafted(client)
        draft_est = (await client.post("/api/estimate", json={})).json()
        est = (await client.post("/api/boards/estimate", json={"model": "meta/muse-spark-1.3"})).json()
        assert est["model"] == "meta/muse-spark-1.3"
        assert est["prompt_chars"] > draft_est["prompt_chars"]
        assert est["input_tokens_est"] == math.ceil(est["prompt_chars"] / 3)


async def test_overlapping_validates_keep_the_newest_text(ro_pool, tmp_path, monkeypatch):
    """Refuter round 1: an older validate finishing last must not overwrite a newer edit."""
    import explorer_bootstrap.server as srv

    real = srv.check_boards
    gate = asyncio.Event()

    async def gated(files, *a, **kw):
        if "# older" in files[0]["toml"]:
            await gate.wait()
        return await real(files, *a, **kw)

    app, _ = _app(ro_pool, tmp_path, _model_then_boards(good_set()))
    async with _client(app) as client:
        await _drafted(client)
        boards = (await _boards(client))[0]["boards"]["boards"]
        monkeypatch.setattr(srv, "check_boards", gated)
        older = [{**boards[0], "toml": boards[0]["toml"] + "# older\n"}, boards[1]]
        newer = [{**boards[0], "toml": boards[0]["toml"] + "# newer\n"}, boards[1]]
        first = asyncio.create_task(client.post("/api/boards/validate", json={"boards": older}))
        await asyncio.sleep(0.2)
        second = asyncio.create_task(client.post("/api/boards/validate", json={"boards": newer}))
        await asyncio.sleep(0.2)
        gate.set()
        await first
        await second
        assert (await client.get("/api/boards")).json()["boards"][0]["toml"].endswith("# newer\n")


async def test_boards_routes_refuse_while_a_dataset_draft_runs_and_404_without_boards(ro_pool, tmp_path):
    gate = asyncio.Event()

    async def slow(_kw):
        await gate.wait()
        return tool_calls(("submit_draft", valid_args()))

    app, _ = _app(ro_pool, tmp_path, ScriptedCompletion([tool_calls(("submit_draft", valid_args())), slow]))
    async with _client(app) as client:
        await _drafted(client)
        assert (await client.post("/api/boards/validate", json={"boards": good_set()})).status_code == 404
        run_id = (await client.post("/api/draft", json={})).json()["run_id"]
        assert (await client.post("/api/boards/draft", json={})).status_code == 409
        gate.set()
        await _wait(client, run_id)
