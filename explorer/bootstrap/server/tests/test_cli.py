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


# Phase 2b: L1, L2
@pytest.mark.db
def test_draft_verify_export(pg, monkeypatch, capsys, tmp_path):
    from explorer_agent.testing.stub_model import ScriptedCompletion, tool_calls

    from conftest import valid_args

    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    args = valid_args()
    args["claims"][0]["expect"]["value"] = 99  # will fail
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT count(*) AS n FROM bs.mixed", "purpose": "count rows"})),
        tool_calls(("submit_draft", args)),
    ])
    monkeypatch.setattr(cli, "COMPLETION", model)
    out_file = tmp_path / "draft.json"
    code, out, err = _run(capsys, "draft", "--schemas", "bs", "--out", str(out_file), "--timeout-ms", "1000")
    assert code == 0, err
    assert "run_sql (count rows)" in out and "[failed]" in out and "[verified]" in out and "Files validate" in out
    text = out_file.read_text(encoding="utf-8")
    data = json.loads(text, parse_constant=lambda c: pytest.fail(f"invalid JSON constant {c}"))
    assert text == json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    for secret in (RO_PASSWORD, "explorer_ro"):
        assert secret not in text and secret not in out
    assert str(tmp_path) not in text  # no local paths in the draft (the terminal names the file it wrote)
    assert [c["result"]["state"] for c in data["claims"]] == ["failed", "verified", "needs_review"]
    assert data["usage"]["totals"]["calls"] == 2

    root = tmp_path / "datasets"
    root.mkdir()
    code, out, _ = _run(capsys, "export", "--draft", str(out_file), "--name", "bs", "--target", "toml",
                        "--root", str(root))
    assert code == 1 and "claim 1 is failed" in out and not (root / "bs").exists()

    code, out, _ = _run(capsys, "verify", "--draft", str(out_file))
    assert code == 1 and "block export" in out
    data["claims"][0]["expect"]["value"] = 100  # the person fixes the claim
    out_file.write_text(json.dumps(data), encoding="utf-8")
    code, out, _ = _run(capsys, "verify", "--draft", str(out_file))
    assert code == 0 and "[failed]" not in out

    code, out, _ = _run(capsys, "export", "--draft", str(out_file), "--name", "bs", "--target", "sql",
                        "--root", str(root))
    assert code == 0 and "Apply comments.sql" in out
    assert (root / "bs" / "comments.sql").is_file()


def test_model_allowlist(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    code, _, err = _run(capsys, "draft", "--schemas", "bs", "--out", str(tmp_path / "d.json"), "--model", "openai/gpt")
    assert code == 2 and "model must be one of" in err
    monkeypatch.setenv("BOOTSTRAP_MODEL", "meta/muse-spark-1.3-contributor")
    code, _, err = _run(capsys, "draft", "--schemas", "bs", "--out", str(tmp_path / "d.json"))
    assert code == 2 and "model must be one of" in err


def test_bad_draft_file(capsys, tmp_path):
    bad = tmp_path / "d.json"
    bad.write_text("{}", encoding="utf-8")
    code, _, err = _run(capsys, "export", "--draft", str(bad), "--name", "x", "--target", "toml")
    assert code == 2 and "not a version 1 draft" in err


# Phase 3: L5
@pytest.mark.db
def test_boards_then_export_with_boards(pg, monkeypatch, capsys, tmp_path):
    from explorer_agent.config import load_dataset
    from explorer_agent.testing.stub_model import ScriptedCompletion, tool_calls

    from conftest import valid_args
    from test_boards_draft import EXPLORER, GRADE, good_set

    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", pg.ro_dsn)
    draft_file, boards_file = tmp_path / "draft.json", tmp_path / "boards.json"
    broken = [{**good_set()[0], "toml": EXPLORER.replace('"grade,amount"', '"grade,amont"')}, good_set()[1]]
    model = ScriptedCompletion([
        tool_calls(("submit_draft", valid_args())),
        tool_calls(("submit_boards", {"boards": broken})),
        tool_calls(("submit_boards", {"boards": good_set()})),
    ])
    monkeypatch.setattr(cli, "COMPLETION", model)
    code, _, err = _run(capsys, "draft", "--schemas", "bs", "--out", str(draft_file))
    assert code == 0, err
    code, out, err = _run(capsys, "boards", "--draft", str(draft_file), "--out", str(boards_file))
    assert code == 0, err
    assert "Boards rejected (1 problems)" in out and "grade (key from bs.mixed.grade)" in out
    assert "by_grade: 7 rows" in out and "Boards written to" in out
    text = boards_file.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["boards_version"] == 1 and [b["name"] for b in data["boards"]] == ["explorer", "grade"]
    assert data["problems"] == {"explorer": [], "grade": []} and data["usage"]["totals"]["calls"] == 2
    for secret in (RO_PASSWORD, "explorer_ro"):
        assert secret not in text and secret not in out

    root = tmp_path / "datasets"
    root.mkdir()
    stale = {**data, "boards": [data["boards"][0], {**data["boards"][1], "toml": GRADE.replace("bs.mixed", "bs.nope")}]}
    stale_file = tmp_path / "stale.json"
    stale_file.write_text(json.dumps(stale), encoding="utf-8")
    code, out, _ = _run(capsys, "export", "--draft", str(draft_file), "--boards", str(stale_file), "--name", "bs",
                        "--target", "toml", "--root", str(root))
    assert code == 1 and "boards/grade.toml" in out and not (root / "bs").exists()
    code, out, err = _run(capsys, "export", "--draft", str(draft_file), "--boards", str(boards_file), "--name", "bs",
                          "--target", "toml", "--root", str(root))
    assert code == 0, err
    assert sorted(load_dataset(root / "bs").boards) == ["explorer", "grade"]


def test_bad_boards_file(capsys, tmp_path):
    bad = tmp_path / "b.json"
    bad.write_text('{"boards_version": 2}', encoding="utf-8")
    draft = tmp_path / "d.json"
    draft.write_text(json.dumps({"draft_version": 1, "report": {}, "files": {}, "comments": [], "claims": []}),
                     encoding="utf-8")
    code, _, err = _run(capsys, "export", "--draft", str(draft), "--boards", str(bad), "--name", "x", "--target", "toml")
    assert code == 2 and "not a version 1 boards file" in err


# M1 (2026-11-05): a Settings draft ran in `serve` and nothing reached the log, so its cost was
# unknown. `serve` had no logging set up, and Python's fallback handler drops INFO.
def test_serve_logs_model_calls_at_info(capsys):
    import logging

    handlers_before = list(logging.getLogger("explorer_bootstrap").handlers)
    try:
        cli._serve_logging()
        logging.getLogger("explorer_bootstrap.draft").info("model call: cost_usd=%.4f", 0.1234)
        logging.getLogger("litellm").info("library chatter")
        err = capsys.readouterr().err
        assert "INFO explorer_bootstrap.draft: model call: cost_usd=0.1234" in err
        assert "library chatter" not in err
    finally:
        logging.getLogger("explorer_bootstrap").handlers = handlers_before
        logging.getLogger("explorer_bootstrap").setLevel(logging.NOTSET)


def test_serve_sets_up_its_logging_once(monkeypatch, tmp_path, capsys):
    # The helper alone isn't enough: `serve` must call it (refuter, 2026-11-05).
    import logging

    import uvicorn

    import explorer_bootstrap.server as server_mod

    class FakeServer:
        def __init__(self, config):
            pass

        async def serve(self):
            return None

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: None)
    monkeypatch.setattr(server_mod, "build_app", lambda *a, **k: None)
    monkeypatch.setenv("EXPLORER_DATA_DIR", str(tmp_path / "data"))
    logger = logging.getLogger("explorer_bootstrap")
    handlers_before = list(logger.handlers)
    try:
        assert main(["serve"]) == 0
        assert main(["serve"]) == 0
        added = [h for h in logger.handlers if h not in handlers_before]
        assert len(added) == 1 and logger.level == logging.INFO
    finally:
        logger.handlers = handlers_before
        logger.setLevel(logging.NOTSET)
