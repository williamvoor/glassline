"""Export checks and writes one Draft snapshot, without a database or model."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from explorer_bootstrap import server
from explorer_bootstrap.drafts import new_draft


@pytest.mark.parametrize("settings_mode", [False, True])
async def test_export_writes_the_snapshot_checked_before_a_concurrent_edit(tmp_path, monkeypatch, settings_mode):
    data = tmp_path / "data"
    data.mkdir()
    async def open_pool(_):
        return object(), {"host": "stub", "name": "stub"}
    app = server.build_app(object() if not settings_mode else None, database={},
                           env={"TEST_DATABASE_URL": "postgresql://stub:stub@invalid/stub"}, port=8765,
                           timeout_ms=1000, datasets_root=tmp_path, repo_root=tmp_path,
                           data_dir=data if settings_mode else None, open_pool=open_pool,
                           reload=AsyncMock(return_value=(200, {})))
    monkeypatch.setattr(server, "visible_schemas", AsyncMock(return_value=[{"name": "fixture", "objects": 0}]))
    monkeypatch.setattr(server, "SqlRunner", lambda *a: SimpleNamespace(check_privileges=AsyncMock()))
    started, resume = asyncio.Event(), asyncio.Event()
    checked = []
    async def check(files, dataset, runner, cap):
        checked.append(dataset.allowed_schemas)
        started.set()
        await resume.wait()
        return SimpleNamespace(to_dict=lambda: {"problems": {}, "set_problems": []})
    monkeypatch.setattr(server, "check_boards", check)
    monkeypatch.setattr(server, "boards_runner", lambda *a: None)
    files = {"dataset_toml": 'name = "demo"\ndescription = "Demo"\nallowed_schemas = ["fixture"]\n'
             'notes = "notes.md"\nglossary = "glossary.md"\nexamples = ["Show rows"]\n'
             '[currency]\ncode = "USD"\nsymbol = "$"\n', "notes_md": "Reviewed notes", "glossary_md": "Terms"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        if settings_mode:
            response = await client.post("/api/connect", json={"connection": "test"})
            assert response.status_code == 200
        state = app.app.state.bootstrap
        state.draft = new_draft({"schemas": ["fixture", "other"], "objects": []},
                                {"files": files, "comments": [], "claims": []}, model="stub", effort="low", usage={})
        state.boards = {"boards": [{"name": "explorer", "toml": 'title = "Explorer"'}],
                        "problems": {}, "set_problems": []}
        exporting = asyncio.create_task(client.post("/api/export", json={"name": "demo", "target": "toml",
                                        "files": files, "comments": [], "dismissed": []}))
        await asyncio.wait_for(started.wait(), 5)
        changed = copy.deepcopy(files)
        changed["dataset_toml"] = changed["dataset_toml"].replace('"fixture"', '"other"')
        changed["notes_md"] = "New notes"
        edited = await client.post("/api/validate", json={"files": changed, "comments": []})
        assert edited.status_code == 200 and edited.json()["errors"] == []
        resume.set()
        response = await exporting
        assert response.status_code == 200, response.text
    dest = data / "workspaces/demo/dataset" if settings_mode else tmp_path / "demo"
    assert checked == [("fixture",)]
    assert 'allowed_schemas = ["fixture"]' in (dest / "dataset.toml").read_text()
    assert (dest / "notes.md").read_text() == "Reviewed notes"
    assert state.draft["files"] == changed
