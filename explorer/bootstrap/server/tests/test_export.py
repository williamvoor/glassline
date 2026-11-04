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
    assert export_dataset(d, "ok", "sql", tmp_path).dest.is_dir()  # a dismissed claim no longer blocks


def test_export_is_world_readable_and_cleans_up(bs_report_sync, tmp_path, monkeypatch):
    import stat

    from explorer_bootstrap import export as export_mod

    out = export_dataset(_draft(bs_report_sync), "ok", "toml", tmp_path).dest
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
    by_toml = export_dataset(d, "as_toml", "toml", tmp_path).dest
    by_sql = export_dataset(d, "as_sql", "sql", tmp_path).dest
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
    out = export_dataset(_draft(bs_report_sync), "bs_overlay", "toml", tmp_path).dest
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


# E4: force replaces only the draft's files in an existing directory, keeping a backup.
def test_export_force_replaces_only_the_drafts_files(bs_report_sync, tmp_path):
    dest = tmp_path / "lived"
    dest.mkdir()
    (dest / "dataset.toml").write_text("old toml\n", encoding="utf-8")
    (dest / "scenarios.toml").write_text("keep me\n", encoding="utf-8")
    result = export_dataset(_draft(bs_report_sync), "lived", "toml", tmp_path, force=True)
    assert result.dest == dest
    assert result.overwrote == ["dataset.toml"]
    assert (dest / "dataset.toml").read_text(encoding="utf-8") != "old toml\n"
    assert (dest / "scenarios.toml").read_text(encoding="utf-8") == "keep me\n"  # untouched
    assert sorted(p.name for p in dest.iterdir()) == [
        "comments.toml", "dataset.toml", "glossary.md", "notes.md", "scenarios.toml"]
    assert result.backup is not None and result.backup.parent == tmp_path
    assert (result.backup / "dataset.toml").read_text(encoding="utf-8") == "old toml\n"
    assert result.backup.name.startswith(".lived.bak-")  # dot-prefixed: the dataset picker ignores it


def test_export_force_into_an_empty_dir_keeps_no_backup(bs_report_sync, tmp_path):
    (tmp_path / "empty").mkdir()
    result = export_dataset(_draft(bs_report_sync), "empty", "sql", tmp_path, force=True)
    assert result.overwrote == [] and result.backup is None
    assert (tmp_path / "empty" / "comments.sql").is_file()


def test_export_force_still_refuses_a_file(bs_report_sync, tmp_path):
    (tmp_path / "afile").write_text("x", encoding="utf-8")
    with pytest.raises(ExportError, match="is not a directory"):
        export_dataset(_draft(bs_report_sync), "afile", "toml", tmp_path, force=True)


# D8: drafted examples land in the exported dataset and load.
def test_export_carries_examples(bs_report_sync, tmp_path):
    out = export_dataset(_draft(bs_report_sync), "bs_examples", "toml", tmp_path).dest
    assert load_dataset(out).examples == (
        "How many rows does bs.mixed have?",
        "What grades appear in bs.mixed?",
    )


# E6 (phase 3): Boards are written to boards/, refused with problems, and replaced whole under force.
from test_boards_draft import EXPLORER as EXPLORER_BOARD, GRADE as GRADE_BOARD  # noqa: E402

BOARDS = [{"name": "explorer", "toml": EXPLORER_BOARD}, {"name": "grade", "toml": GRADE_BOARD}]


def test_export_writes_boards_that_the_agent_loads(bs_report_sync, tmp_path):
    out = export_dataset(_draft(bs_report_sync), "with_boards", "toml", tmp_path, boards=BOARDS).dest
    assert sorted(p.name for p in (out / "boards").iterdir()) == ["explorer.toml", "grade.toml"]
    assert (out / "boards" / "grade.toml").read_text(encoding="utf-8") == GRADE_BOARD
    dataset = load_dataset(out)
    assert sorted(dataset.boards) == ["explorer", "grade"] and dataset.boards["grade"].key == "g"


def test_export_refuses_boards_with_problems(bs_report_sync, tmp_path):
    with pytest.raises(ExportError) as e:
        export_dataset(_draft(bs_report_sync), "bad_boards", "toml", tmp_path, boards=BOARDS,
                       board_problems=["boards/grade.toml: layout: nope"])
    assert e.value.reasons == ["boards/grade.toml: layout: nope"]
    assert not (tmp_path / "bad_boards").exists()


def test_export_force_replaces_boards_whole_and_backs_them_up(bs_report_sync, tmp_path):
    dest = tmp_path / "lived"
    (dest / "boards").mkdir(parents=True)
    (dest / "boards" / "explorer.toml").write_text("old explorer\n", encoding="utf-8")
    (dest / "boards" / "stale.toml").write_text("left out of the new set\n", encoding="utf-8")
    result = export_dataset(_draft(bs_report_sync), "lived", "toml", tmp_path, force=True, boards=BOARDS[:1])
    assert sorted(p.name for p in (dest / "boards").iterdir()) == ["explorer.toml"]
    assert "boards/" in result.overwrote
    assert result.backup is not None
    assert sorted(p.name for p in (result.backup / "boards").iterdir()) == ["explorer.toml", "stale.toml"]
    assert (result.backup / "boards" / "explorer.toml").read_text(encoding="utf-8") == "old explorer\n"
    assert not [p for p in dest.iterdir() if p.name.startswith(".")]  # no temp leftovers


def test_export_without_boards_leaves_an_existing_boards_dir_alone(bs_report_sync, tmp_path):
    dest = tmp_path / "lived"
    (dest / "boards").mkdir(parents=True)
    (dest / "boards" / "explorer.toml").write_text("theirs\n", encoding="utf-8")
    result = export_dataset(_draft(bs_report_sync), "lived", "toml", tmp_path, force=True)
    assert (dest / "boards" / "explorer.toml").read_text(encoding="utf-8") == "theirs\n"
    assert "boards/" not in result.overwrote


def test_export_refuses_a_board_name_that_would_leave_boards_dir(bs_report_sync, tmp_path):
    with pytest.raises(ExportError, match="is not a Board file name"):
        export_dataset(_draft(bs_report_sync), "escape", "toml", tmp_path, boards=[{"name": "../x", "toml": "t"}])
    assert not (tmp_path / "escape").exists() and not (tmp_path / "x.toml").exists()
