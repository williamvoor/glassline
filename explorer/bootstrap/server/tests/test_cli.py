import json

import pytest

import explorer_bootstrap.__main__ as cli
from explorer_bootstrap.__main__ import main

from conftest import AUSTENDER_DATASET, RO_PASSWORD, write_overlay_dataset


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ENV_FILE", "/nonexistent/.env")
    monkeypatch.delenv("BOOTSTRAP_DATABASE_URL", raising=False)


def _run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.db
def test_json_output(pg, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    out_file = tmp_path / "report.json"
    code, out, _ = _run(capsys, "report", "--schemas", "bs", "--json", str(out_file))
    assert code == 0
    assert "JSON written to" in out
    text = out_file.read_text(encoding="utf-8")
    data = json.loads(text, parse_constant=lambda c: pytest.fail(f"invalid JSON constant {c}"))
    assert text == json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert data["report_version"] == 1
    assert set(data["database"]) == {"host", "name"}
    assert data["summary"]["context_scope"] == "schema_only"
    mixed = next(o for o in data["objects"] if o["name"] == "mixed")
    created = next(c for c in mixed["columns"] if c["name"] == "created")
    assert created["min"] == "2024-01-02"
    assert RO_PASSWORD not in text and "explorer_ro" not in text


@pytest.mark.db
def test_no_secrets_in_output(pg, monkeypatch, capsys):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    code, out, err = _run(capsys, "report", "--dataset-dir", str(AUSTENDER_DATASET))
    assert code == 0
    for secret in (RO_PASSWORD, "explorer_ro"):
        assert secret not in out and secret not in err
    assert "Context size:" in out and "schema section only" not in out


def test_bad_url_message(monkeypatch, capsys):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", "postgresql://zqxuser:sekrit-pw@[bad")
    code, _, err = _run(capsys, "report", "--schemas", "bs")
    assert code == 2
    assert "malformed" in err
    assert "sekrit-pw" not in err and "zqxuser" not in err


def test_unreachable_database(monkeypatch, capsys):
    monkeypatch.setattr(cli, "CONNECT_TIMEOUT_S", 1.0)
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", "postgresql://zqxuser:sekrit-pw@127.0.0.1:1/db")
    code, out, err = _run(capsys, "report", "--schemas", "bs")
    assert code == 2
    assert "cannot connect" in err
    assert "sekrit-pw" not in out + err and "zqxuser" not in out + err


@pytest.mark.parametrize(
    "argv",
    [
        ["report"],
        ["report", "--schemas", "bs", "--dataset-dir", "x"],
    ],
)
def test_selector_usage_errors(capsys, argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2


def test_usage_errors(monkeypatch, capsys, tmp_path):
    code, _, err = _run(capsys, "report", "--schemas", "bs")
    assert code == 2 and "BOOTSTRAP_DATABASE_URL is not set" in err
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    code, _, err = _run(capsys, "report", "--schemas", "Bad-Name")
    assert code == 2 and "invalid schema name" in err
    code, _, err = _run(capsys, "report", "--dataset-dir", str(tmp_path))
    assert code == 2 and "dataset file not found" in err


@pytest.mark.db
def test_all_schemas_empty(pg, monkeypatch, capsys):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    code, _, err = _run(capsys, "report", "--schemas", "nope")
    assert code == 2 and "no tables or views" in err


@pytest.mark.db
def test_runtime_database_error_exits_2(pg, monkeypatch, capsys):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    code, _, err = _run(capsys, "report", "--schemas", "bs", "--timeout-ms", "1")
    assert code == 2
    assert "timed out" in err and "--timeout-ms" in err
    assert RO_PASSWORD not in err and "explorer_ro" not in err


@pytest.mark.db
def test_unwritable_json_exits_2(pg, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    code, _, err = _run(capsys, "report", "--schemas", "bs", "--no-minmax", "--json", str(tmp_path / "no" / "x.json"))
    assert code == 2 and "cannot write" in err


@pytest.mark.db
def test_check_exit_codes(pg, monkeypatch, capsys):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    code, _, _ = _run(capsys, "report", "--dataset-dir", str(AUSTENDER_DATASET), "--check", "--no-minmax")
    assert code == 0
    code, _, _ = _run(capsys, "report", "--schemas", "bs", "--check", "--no-minmax")
    assert code == 1  # bs has missing comments
    code, _, _ = _run(capsys, "report", "--schemas", "bs", "--no-minmax")
    assert code == 0  # without --check, missing comments aren't a failure
    monkeypatch.setattr("explorer_bootstrap.report.CONTEXT_TARGET", 100)
    code, out, _ = _run(capsys, "report", "--dataset-dir", str(AUSTENDER_DATASET), "--check", "--no-minmax")
    assert code == 1 and "OVER TARGET" in out  # full coverage, but the context is over target


# B4
@pytest.mark.db
def test_overlay_drift_exits_2(pg, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    d = write_overlay_dataset(tmp_path, '["bs.gone"]\ncomment = "x"\n["bs.mixed".columns]\nnope = "x"\n')
    code, out, err = _run(capsys, "report", "--dataset-dir", str(d))
    assert code == 2
    lines = err.strip().splitlines()
    assert len(lines) == 1 and "Traceback" not in err
    assert "comment overlay" in lines[0]
    assert "unknown table bs.gone" in lines[0] and "unknown column bs.mixed.nope" in lines[0]
    assert RO_PASSWORD not in err + out


# B5
@pytest.mark.db
def test_overlay_text_line(pg, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    out_file = tmp_path / "r.json"
    d = write_overlay_dataset(tmp_path)
    code, out, _ = _run(capsys, "report", "--dataset-dir", str(d), "--no-minmax", "--json", str(out_file))
    assert code == 0
    assert "Comment overlay comments.toml: 5 comments (2 replace a database comment)" in out
    text = out_file.read_text(encoding="utf-8")
    assert json.loads(text)["comment_overlay"] == "comments.toml"
    assert str(tmp_path) not in text and str(tmp_path.resolve()) not in text
    for argv in (["--schemas", "bs"], ["--dataset-dir", str(AUSTENDER_DATASET)]):
        code, out, _ = _run(capsys, "report", *argv, "--no-minmax")
        assert code == 0
        assert not [line for line in out.splitlines() if line.startswith("Comment overlay")]
