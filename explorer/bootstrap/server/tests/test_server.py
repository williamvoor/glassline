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


async def test_serves_the_built_ui(ro_pool, tmp_path):
    ui = tmp_path / "dist"
    ui.mkdir()
    (ui / "index.html").write_text("<bootstrap-app></bootstrap-app>", encoding="utf-8")
    root = tmp_path / "datasets"
    root.mkdir()
    app = build_app(ro_pool, database=DB, env={}, port=PORT, timeout_ms=2000, datasets_root=root,
                    repo_root=tmp_path, ui_dir=ui)
    async with _client(app) as client:
        r = await client.get("/")
        assert r.status_code == 200 and "<bootstrap-app>" in r.text
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
