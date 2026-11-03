"""The entry point, run as a real subprocess (no model is ever called)."""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest


def run_main(env_extra, *, timeout=60):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MODEL", "META_API_KEY")}
    env.update({"EXPLORER_ENV_FILE": "/nonexistent/.env", "DATASET_DIR": "datasets/austender", **env_extra})
    return subprocess.run([sys.executable, "-m", "explorer_agent"], env=env, capture_output=True, text=True,
                          timeout=timeout)


def assert_one_line_failure(done, *must_contain, secret=None):
    assert done.returncode == 1
    lines = done.stderr.strip().splitlines()
    assert lines and lines[-1].startswith("explorer: "), done.stderr
    assert "Traceback" not in done.stderr
    for text in must_contain:
        assert text in lines[-1]
    if secret:
        assert secret not in done.stderr and secret not in done.stdout


# M1
def test_missing_database_url_fails_cleanly():
    assert_one_line_failure(run_main({}), "DATABASE_URL is required")


@pytest.mark.db
def test_loader_login_is_refused(pg):
    done = run_main({"DATABASE_URL": pg.loader_dsn})
    assert_one_line_failure(done, "can write to allowed tables", secret="austender_loader@")


def test_unreachable_database_fails_cleanly():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # nothing listens here once the socket closes
    done = run_main({"DATABASE_URL": f"postgresql://u:s3cret@127.0.0.1:{port}/db"})
    assert_one_line_failure(done, "cannot connect to the database", secret="s3cret")


# M2
@pytest.mark.db
def test_starts(pg):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL",)}
    env.update({"EXPLORER_ENV_FILE": "/nonexistent/.env", "DATABASE_URL": pg.ro_dsn, "AGENT_PORT": str(port),
                "DATASET_DIR": "datasets/austender"})
    proc = subprocess.Popen([sys.executable, "-m", "explorer_agent"], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        line = ""
        while time.monotonic() < deadline and "listening" not in line:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                pytest.fail(f"agent exited: {proc.stderr.read()}")
        assert line.strip() == f"explorer: listening on http://127.0.0.1:{port}"
        card = httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=10).json()
        assert card["name"] == "Explorer"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


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


# O8
@pytest.mark.db
def test_overlay_drift_fails_startup(pg, tmp_path):
    (tmp_path / "dataset.toml").write_text(
        'name = "demo"\ndescription = "Demo"\nallowed_schemas = ["mart"]\nglossary = "g.md"\nnotes = "n.md"\n'
        '[comments]\noverlay = "comments.toml"\n[currency]\ncode = "AUD"\nsymbol = "$"\n'
    )
    (tmp_path / "g.md").write_text("glossary\n")
    (tmp_path / "n.md").write_text("notes\n")
    (tmp_path / "comments.toml").write_text(
        '["mart.gone"]\ncomment = "x"\n["mart.contract".columns]\nnotice_id = "ok"\nno_such_col = "x"\n'
    )
    done = run_main({"DATABASE_URL": pg.ro_dsn, "DATASET_DIR": str(tmp_path)})
    assert_one_line_failure(
        done, "comment overlay", "unknown table mart.gone", "unknown column mart.contract.no_such_col",
        secret="explorer_ro:",
    )
