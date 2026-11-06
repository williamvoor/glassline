"""O4 (ADR 0048): run.sh has one mode. It takes no flags and refuses without EXPLORER_DATA_DIR,
before it starts anything. No process is started by these tests."""

import os
import subprocess
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[2] / "run.sh"


def run(args, env_file, **env):
    base = {k: v for k, v in os.environ.items() if k not in ("EXPLORER_DATA_DIR",)}
    return subprocess.run(["bash", str(RUN_SH), *args], env={**base, "EXPLORER_ENV_FILE": str(env_file), **env},
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("args", [["--real"], ["--stub"], ["x"]])
def test_run_sh_takes_no_flags(tmp_path, args):
    done = run(args, tmp_path / "none.env")
    assert done.returncode == 2 and "usage" in done.stderr


def test_run_sh_refuses_without_the_data_dir(tmp_path):
    env_file = tmp_path / "t.env"
    env_file.write_text("MODEL=x\n# EXPLORER_DATA_DIR=/commented/out\n", encoding="utf-8")
    done = run([], env_file)
    assert done.returncode == 1 and "EXPLORER_DATA_DIR is required" in done.stderr


def test_run_sh_refuses_without_an_env_file_or_data_dir(tmp_path):
    done = run([], tmp_path / "none.env")
    assert done.returncode == 1 and "EXPLORER_DATA_DIR is required" in done.stderr


# O1/O4 guard: nothing that builds or starts the app may read a mode switch again.
def test_no_mode_switch_in_the_client_launcher_or_deploy():
    repo = RUN_SH.parents[1]
    places = [RUN_SH, *(repo / "explorer" / "client" / "src").rglob("*.ts"), *(repo / "deploy").rglob("*")]
    hits = [str(p.relative_to(repo)) for p in places
            if p.is_file() and p.name != ".env" and "VITE_WORKSPACES" in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits == []
