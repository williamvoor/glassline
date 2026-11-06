"""The entry point, run as a real subprocess (no model is ever called)."""

import os
import socket
import subprocess
import sys
import time

from pathlib import Path

import httpx
import pytest


REPO = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "austender"
CLEAN = ("DATABASE_URL", "DATASET_DIR", "EXPLORER_DATA_DIR", "MODEL", "META_API_KEY", "AGENT_PUBLIC_URL")


def base_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env.update({"EXPLORER_ENV_FILE": "/nonexistent/.env", **extra})
    return env


def run_main(env_extra, *args, timeout=60):
    return subprocess.run([sys.executable, "-m", "explorer_agent", *args], env=base_env(**env_extra),
                          capture_output=True, text=True, timeout=timeout)


def workspace_data(tmp_path, dataset=FIXTURE, name="aus"):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "workspaces.toml").write_text(
        f'[[workspace]]\nname = "{name}"\ndataset_dir = "{dataset}"\ndsn_env = "A_DATABASE_URL"\n', encoding="utf-8")
    return str(data)


def assert_one_line_failure(done, *must_contain, secret=None):
    assert done.returncode == 1
    lines = done.stderr.strip().splitlines()
    assert lines and lines[-1].startswith("explorer: "), done.stderr
    assert "Traceback" not in done.stderr
    for text in must_contain:
        assert text in lines[-1]
    if secret:
        assert secret not in done.stderr and secret not in done.stdout


# O2 (ADR 0048): one mode. Without EXPLORER_DATA_DIR the agent does not start, even with the old
# single-Dataset settings, so a deploy can't come up without Workspaces and Settings.
def test_missing_data_dir_fails_cleanly():
    assert_one_line_failure(run_main({}), "EXPLORER_DATA_DIR is required")


def test_single_dataset_settings_do_not_start_an_agent():
    done = run_main({"DATABASE_URL": "postgresql://u:s3cret@127.0.0.1:1/db", "DATASET_DIR": str(FIXTURE)})
    assert_one_line_failure(done, "EXPLORER_DATA_DIR is required", secret="s3cret")


# O8, per Workspace: comment overlay drift makes the Workspace unavailable, naming the problems.
@pytest.mark.db
def test_overlay_drift_makes_the_workspace_unavailable(pg, tmp_path):
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "dataset.toml").write_text(
        'name = "demo"\ndescription = "Demo"\nallowed_schemas = ["mart"]\nglossary = "g.md"\nnotes = "n.md"\n'
        '[comments]\noverlay = "comments.toml"\n[currency]\ncode = "AUD"\nsymbol = "$"\n'
    )
    (ds / "g.md").write_text("glossary\n")
    (ds / "n.md").write_text("notes\n")
    (ds / "comments.toml").write_text(
        '["mart.gone"]\ncomment = "x"\n["mart.contract".columns]\nnotice_id = "ok"\nno_such_col = "x"\n'
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = base_env(EXPLORER_DATA_DIR=workspace_data(tmp_path, ds), A_DATABASE_URL=pg.ro_dsn, AGENT_PORT=str(port))
    proc = subprocess.Popen([sys.executable, "-m", "explorer_agent"], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        line = ""
        while time.monotonic() < deadline and "listening" not in line:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                pytest.fail(f"agent exited: {proc.stderr.read()}")
        [ws] = httpx.get(f"http://127.0.0.1:{port}/workspaces", timeout=10).json()
        assert ws["status"] == "unavailable" and ws["reason"].startswith("dataset invalid: comment overlay")
        assert "unknown table mart.gone" in ws["reason"] and "unknown column mart.contract.no_such_col" in ws["reason"]
    finally:
        proc.terminate()
        _, err = proc.communicate(timeout=10)
    assert pg.ro_dsn not in err


# Regression (refuter final): at LOG_LEVEL=DEBUG the openai SDK logs request bodies (prompts and
# history). Every library logger that can do that stays at WARNING.
def test_library_loggers_are_quiet_at_debug():
    import logging

    from explorer_agent.__main__ import _configure_logging

    _configure_logging("DEBUG")
    logging.getLogger().setLevel(logging.DEBUG)  # basicConfig is a no-op under pytest's handlers
    try:
        for name in ("openai", "openai._base_client", "httpx", "httpcore", "LiteLLM", "urllib3", "aiohttp"):
            assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name
    finally:
        logging.getLogger().setLevel(logging.WARNING)


# --- `boards` (SPEC §18, B19) ---------------------------------------------------------------------

def _dataset_with_board(tmp_path, totals_sql):
    import shutil

    from tests.test_boards import SEED_BOARD

    for name in ("dataset.toml", "notes.md", "glossary.md"):
        shutil.copy(FIXTURE / name, tmp_path / name)
    (tmp_path / "boards").mkdir()
    board = SEED_BOARD.replace("SELECT sum(total_value) AS total_value FROM mart.agency", totals_sql)
    (tmp_path / "boards" / "explorer.toml").write_text(board, encoding="utf-8")
    return str(tmp_path)


def run_boards(tmp_path, dsn, dataset, *names):
    data = workspace_data(tmp_path, dataset)
    return run_main({"EXPLORER_DATA_DIR": data, "A_DATABASE_URL": dsn}, "boards", *(names or ("aus",)), timeout=120)


@pytest.mark.db
def test_boards_check_renders_each_board_cold(pg, tmp_path):
    dataset = _dataset_with_board(tmp_path, "SELECT sum(total_value) AS total_value FROM mart.agency")
    done = run_boards(tmp_path, pg.ro_dsn, dataset)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("ok explorer: ")


@pytest.mark.db
def test_boards_check_fails_a_board_whose_query_fails_when_rendered(pg, tmp_path):
    # Passes the LIMIT 0 check (nothing is evaluated), fails when rows are produced.
    sql = "SELECT sum(total_value) / (count(*) - count(*)) AS total_value FROM mart.agency"
    done = run_boards(tmp_path, pg.ro_dsn, _dataset_with_board(tmp_path, sql))
    assert done.returncode == 1
    assert "FAIL explorer" in done.stdout and "failed queries: totals" in done.stdout


@pytest.mark.parametrize("args", [("nope",), ("boards",), ("boards", "a", "b")])
def test_main_rejects_unknown_arguments(args):
    done = run_main({}, *args)
    assert done.returncode == 2 and "usage" in done.stderr


# O3: the boards check names a Workspace in EXPLORER_DATA_DIR.
def test_boards_check_fails_an_unknown_workspace(tmp_path):
    done = run_boards(tmp_path, "postgresql://u:s3cret@127.0.0.1:1/db", FIXTURE, "nope")
    assert_one_line_failure(done, "no Workspace named 'nope'", secret="s3cret")


def test_boards_check_needs_the_data_dir():
    assert_one_line_failure(run_main({}, "boards", "aus"), "EXPLORER_DATA_DIR is required")


@pytest.mark.db
async def test_boards_check_fails_a_board_over_the_cold_target(pg, tmp_path, capsys):
    """B19: the time comparison itself (a target of 0 s, which every render misses)."""
    from explorer_agent.__main__ import _boards_check
    from explorer_agent.config import load_settings

    dataset = _dataset_with_board(tmp_path, "SELECT sum(total_value) AS total_value FROM mart.agency")
    settings = load_settings({}, Path(dataset), pg.ro_dsn)
    assert await _boards_check(settings, target_s=0.0) == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL explorer: ") and "s cold (target < 0.0 s)" in out
    assert "failed queries" not in out  # it failed on time alone
    assert await _boards_check(settings, target_s=60.0) == 0


AGENCY_BOARD = '''title = "Agency"
key = "agency_name"
key_type = "text"
key_columns = ["agency_name"]
layout = """root = Column([total])
total = Metric("totals", "total_value", "Total", 0, "currency_compact")"""

[[queries]]
name = "totals"
sql = "SELECT sum(total_value) AS total_value FROM mart.agency WHERE agency_name = :agency_name"
'''


@pytest.mark.db
async def test_boards_check_skips_entity_boards_it_cannot_render_without_a_key(pg, tmp_path, capsys):
    """Refuter F4: a keyed Board can't be timed cold without a key value; it's checked, not timed."""
    from explorer_agent.__main__ import _boards_check
    from explorer_agent.config import load_settings

    dataset = _dataset_with_board(tmp_path, "SELECT sum(total_value) AS total_value FROM mart.agency")
    (tmp_path / "boards" / "agency.toml").write_text(AGENCY_BOARD, encoding="utf-8")
    settings = load_settings({}, Path(dataset), pg.ro_dsn)
    assert await _boards_check(settings, target_s=60.0) == 0
    out = capsys.readouterr().out
    assert "skip agency: an Entity Board renders per row; not timed" in out
    assert "ok agency" not in out and "ok explorer: " in out
