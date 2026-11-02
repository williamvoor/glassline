"""Export to a new dataset directory, both comment targets (SPEC.md phase 2b: E1-E4)."""

import os
import socket
import subprocess
import sys
import time
import tomllib

import psycopg
import pytest

from explorer_agent.config import load_dataset, load_overlay
from explorer_agent.schema import SchemaCatalog, apply_overlay

from explorer_bootstrap.drafts import new_draft
from explorer_bootstrap.export import ExportError, export_dataset, render_comments_sql, render_comments_toml

from conftest import valid_args

pytestmark = pytest.mark.db


def _draft(report, args=None):
    d = new_draft(report, args or valid_args(), model="m", effort="low", usage={})
    for c in d["claims"]:
        c["result"] = {"state": "verified", "message": "", "actual": None}
    return d


def _sql_body(sql: str) -> str:
    """The script without its BEGIN/COMMIT, so a test can run it and roll back."""
    assert sql.startswith("BEGIN;\n") and sql.endswith("\nCOMMIT;\n")
    return sql[len("BEGIN;\n"):-len("COMMIT;\n")]


COMMENTS_SQL = """
SELECT c.relname, '', obj_description(c.oid, 'pg_class')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'bs' AND c.relkind IN ('r', 'v')
UNION ALL
SELECT c.relname, a.attname, col_description(c.oid, a.attnum)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'bs' AND c.relkind IN ('r', 'v')
"""


def _comments(objects):
    out = {}
    for t in objects:
        out[(t.qualified_name, "")] = t.comment
        out.update({(t.qualified_name, c.name): c.comment for c in t.columns})
    return out


# E1
async def test_export_refusals(bs_report, tmp_path):
    d = _draft(bs_report)
    (tmp_path / "taken").mkdir()
    d["claims"][0]["result"]["state"] = "failed"
    d["files"]["dataset_toml"] += '\n[comments]\noverlay = "x.toml"\n'
    with pytest.raises(ExportError) as e:
        export_dataset(d, "taken", "toml", tmp_path)
    reasons = " | ".join(e.value.reasons)
    assert "already exists" in reasons and "[comments]" in reasons and "claim 1 is failed" in reasons
    for name, target, text in [("Bad-Name", "toml", "name must be"), ("ok", "csv", "target must be")]:
        with pytest.raises(ExportError, match=text):
            export_dataset(_draft(bs_report), name, target, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["taken"]  # nothing written, no temp dirs left
    d = _draft(bs_report)
    d["claims"][0]["result"]["state"] = "error"
    d["claims"][0]["dismissed"] = True
    assert export_dataset(d, "ok", "sql", tmp_path).is_dir()  # a dismissed claim no longer blocks


def test_export_is_world_readable_and_cleans_up(bs_report_sync, tmp_path, monkeypatch):
    import stat

    from explorer_bootstrap import export as export_mod

    out = export_dataset(_draft(bs_report_sync), "ok", "toml", tmp_path)
    assert stat.S_IMODE(out.stat().st_mode) & 0o055 == 0o055  # group/other can read and enter

    def boom(_draft):
        raise OSError("disk full")

    monkeypatch.setattr(export_mod, "render_comments_sql", boom)
    with pytest.raises(OSError):
        export_dataset(_draft(bs_report_sync), "broken", "sql", tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ok"]


# E2
async def test_both_targets_same_comments(bs_report, ro_pool, pg, tmp_path):
    d = _draft(bs_report)
    by_toml = export_dataset(d, "as_toml", "toml", tmp_path)
    by_sql = export_dataset(d, "as_sql", "sql", tmp_path)
    assert sorted(p.name for p in by_toml.iterdir()) == ["comments.toml", "dataset.toml", "glossary.md", "notes.md"]
    assert sorted(p.name for p in by_sql.iterdir()) == ["comments.sql", "dataset.toml", "glossary.md", "notes.md"]
    ds = load_dataset(by_toml)
    assert ds.comment_overlay is not None
    assert "comments" not in tomllib.loads((by_sql / "dataset.toml").read_text())
    load_dataset(by_sql)  # valid without an overlay
    raw = await SchemaCatalog(ro_pool, ["bs"]).objects()
    via_overlay = _comments(apply_overlay(raw, ds.comment_overlay))
    with psycopg.connect(pg.superuser_dsn) as conn:  # the objects' owner; rolled back below
        conn.execute(_sql_body((by_sql / "comments.sql").read_text()))
        rows = conn.execute(COMMENTS_SQL).fetchall()
        conn.rollback()
    via_sql = {(f"bs.{t}", col): text for t, col, text in rows}
    assert via_sql == via_overlay
    assert via_overlay[("bs.mixed", "amount")] == "Amount in dollars."  # unaccepted rewrite not applied
    assert via_overlay[("bs.fresh", "")] == "A small table that was never analysed."


# E3
def test_explorer_starts_with_overlay_export(bs_report_sync, pg, tmp_path):
    out = export_dataset(_draft(bs_report_sync), "bs_overlay", "toml", tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MODEL", "META_API_KEY")}
    env.update({"EXPLORER_ENV_FILE": "/nonexistent/.env", "DATABASE_URL": pg.ro_dsn, "AGENT_PORT": str(port),
                "DATASET_DIR": str(out)})
    proc = subprocess.Popen([sys.executable, "-m", "explorer_agent"], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        deadline, line = time.monotonic() + 60, ""
        while time.monotonic() < deadline and "listening" not in line:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                pytest.fail(f"explorer exited: {proc.stderr.read()}")
        assert "listening" in line
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# E4
TRICKY = "It's a \"quote\", a back\\slash, a\nnewline, a tab\t, DEL \x7f, café and 🦘."


def test_sql_and_toml_quoting(pg):
    report = {
        "schemas": ["bs"],
        "objects": [{
            "schema": "bs", "name": 'we"ird T', "kind": "table", "comment": None,
            "columns": [{"name": 'Col "x"', "comment": None}],
        }],
    }
    draft = {"report": report, "comments": [
        {"object": 'bs.we"ird T', "column": "", "comment": TRICKY, "accepted": True},
        {"object": 'bs.we"ird T', "column": 'Col "x"', "comment": TRICKY[::-1], "accepted": True},
    ]}
    parsed = tomllib.loads(render_comments_toml(draft))
    assert parsed['bs.we"ird T']["comment"] == TRICKY
    assert parsed['bs.we"ird T']["columns"]['Col "x"'] == TRICKY[::-1]
    with psycopg.connect(pg.superuser_dsn) as conn:
        conn.execute('CREATE TABLE bs."we""ird T" ("Col ""x""" int)')
        conn.execute(_sql_body(render_comments_sql(draft)))
        obj, col = conn.execute(
            """SELECT obj_description('bs."we""ird T"'::regclass, 'pg_class'),
                      col_description('bs."we""ird T"'::regclass, 1)"""
        ).fetchone()
        conn.rollback()
    assert (obj, col) == (TRICKY, TRICKY[::-1])


def test_overlay_file_loads_with_tricky_text(tmp_path):
    draft = {"report": {"objects": [{"schema": "bs", "name": "t", "kind": "view", "comment": None,
                                     "columns": [{"name": "c", "comment": None}]}]},
             "comments": [{"object": "bs.t", "column": "c", "comment": TRICKY, "accepted": True}]}
    path = tmp_path / "comments.toml"
    path.write_text(render_comments_toml(draft), encoding="utf-8")
    assert load_overlay(path, ("bs",)).entries["bs.t"].columns["c"] == TRICKY
    assert "COMMENT ON COLUMN" in render_comments_sql(draft)
