import re
from pathlib import Path

import pytest

from explorer_agent.config import ConfigError, load_dataset, load_settings

EXPLORER_ROOT = Path(__file__).resolve().parents[2]
SECRET_DSN = "postgresql://explorer_ro:s3cret-pw@db.example:5434/austender"
BASE_ENV = {"DATABASE_URL": SECRET_DSN}


def write_dataset(root: Path, *, toml: str | None = None, notes: bool = True, glossary: bool = True) -> Path:
    d = root / "datasets" / "demo"
    d.mkdir(parents=True)
    (d / "dataset.toml").write_text(
        toml
        if toml is not None
        else 'name = "demo"\ndescription = "Demo"\nallowed_schemas = ["mart", "extra"]\n'
        'glossary = "glossary.md"\nnotes = "notes.md"\n[currency]\ncode = "EUR"\nsymbol = "€"\n'
    )
    if notes:
        (d / "notes.md").write_bytes(b"line one\r\nline two\r\n")
    if glossary:
        (d / "glossary.md").write_text("- term: meaning\n")
    return d


def env_for(tmp_path, **extra):
    return {**BASE_ENV, "DATASET_DIR": "datasets/demo", **extra}


# D1
def test_defaults(tmp_path):
    write_dataset(tmp_path)
    s = load_settings(env_for(tmp_path), tmp_path)
    assert (s.model, s.format, s.reasoning_effort) == ("meta/muse-spark-1.3", "express", "minimal")
    assert (s.sql_timeout_ms, s.sql_row_cap, s.rows_to_model, s.table_row_cap) == (15000, 5000, 20, 200)
    assert (s.max_tool_iterations, s.max_ui_retries) == (8, 2)
    assert (s.agent_host, s.agent_port, s.client_origin, s.log_level) == (
        "127.0.0.1", 10002, "http://127.0.0.1:5173", "INFO",
    )
    assert s.prompt_cache_key == "explorer-demo"
    assert s.dataset.allowed_schemas == ("extra", "mart")
    assert s.dataset.notes == "line one\nline two\n"
    assert (s.dataset.currency.code, s.dataset.currency.symbol) == ("EUR", "€")


def test_overrides(tmp_path):
    write_dataset(tmp_path)
    s = load_settings(
        env_for(tmp_path, MODEL="openrouter/x", FORMAT="direct_json", SQL_ROW_CAP="10", PROMPT_CACHE_KEY="k"),
        tmp_path,
    )
    assert (s.model, s.format, s.sql_row_cap, s.prompt_cache_key) == ("openrouter/x", "direct_json", 10, "k")


def test_empty_reasoning_effort_means_not_sent(tmp_path):
    write_dataset(tmp_path)
    assert load_settings(env_for(tmp_path, REASONING_EFFORT=""), tmp_path).reasoning_effort is None


@pytest.mark.parametrize(
    ("env", "names"),
    [
        ({"FORMAT": "yaml"}, "FORMAT"),
        ({"DATABASE_URL": ""}, "DATABASE_URL"),
        ({"SQL_ROW_CAP": "0"}, "SQL_ROW_CAP"),
        ({"SQL_TIMEOUT_MS": "-5"}, "SQL_TIMEOUT_MS"),
        ({"AGENT_PORT": "abc"}, "AGENT_PORT"),
    ],
)
def test_bad_env_is_a_config_error(tmp_path, env, names):
    write_dataset(tmp_path)
    with pytest.raises(ConfigError, match=names) as e:
        load_settings(env_for(tmp_path, **env), tmp_path)
    assert "s3cret" not in str(e.value)


# D2
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"notes": False}, "notes"),
        ({"glossary": False}, "glossary"),
        ({"toml": 'name = "x"\n'}, "missing key"),
        ({"toml": 'name="x"\ndescription="d"\nallowed_schemas=[]\nnotes="notes.md"\nglossary="glossary.md"\n'
                  '[currency]\ncode="A"\nsymbol="$"\n'}, "allowed_schemas"),
        ({"toml": 'name="x"\ndescription="d"\nallowed_schemas=["Mart; drop"]\nnotes="notes.md"\n'
                  'glossary="glossary.md"\n[currency]\ncode="A"\nsymbol="$"\n'}, "invalid schema name"),
        ({"toml": 'name="x"\ndescription="d"\nallowed_schemas=["m"]\nnotes="notes.md"\nglossary="glossary.md"\n'},
         "currency"),
        ({"toml": "not = [valid"}, "not valid TOML"),
    ],
)
def test_bad_dataset_is_a_config_error(tmp_path, kwargs, expected):
    write_dataset(tmp_path, **kwargs)
    with pytest.raises(ConfigError, match=expected) as e:
        load_settings(env_for(tmp_path), tmp_path)
    assert "s3cret" not in str(e.value)


def test_missing_dataset_dir_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="dataset.toml"):
        load_settings(env_for(tmp_path, DATASET_DIR="datasets/nope"), tmp_path)


def test_dataset_dir_accepts_an_absolute_path(tmp_path):
    ds = write_dataset(tmp_path / "elsewhere")
    other_root = tmp_path / "root"
    other_root.mkdir()
    assert load_settings(env_for(tmp_path, DATASET_DIR=str(ds)), other_root).dataset_dir == ds


def test_dataset_dir_accepts_a_home_relative_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ds = write_dataset(tmp_path / "mine")
    other_root = tmp_path / "root"
    other_root.mkdir()
    assert load_settings(env_for(tmp_path, DATASET_DIR="~/mine/datasets/demo"), other_root).dataset_dir == ds


# Regression: an unset DATASET_DIR fell back to the AusTender dataset (code must be dataset-agnostic).
@pytest.mark.parametrize("value", [None, "", "  "])
def test_dataset_dir_is_required(tmp_path, value):
    env = dict(BASE_ENV) if value is None else {**BASE_ENV, "DATASET_DIR": value}
    with pytest.raises(ConfigError, match="DATASET_DIR is required"):
        load_settings(env, tmp_path)


# Deployment (specs/deployment): the agent sits behind a proxy, so the card's URL is configurable.
def test_agent_public_url_defaults_to_unset(tmp_path):
    write_dataset(tmp_path)
    assert load_settings(env_for(tmp_path), tmp_path).agent_public_url is None
    assert load_settings(env_for(tmp_path, AGENT_PUBLIC_URL="  "), tmp_path).agent_public_url is None


@pytest.mark.parametrize("value", ["https://34.207.118.134.sslip.io/agent/", "http://localhost:8080/"])
def test_agent_public_url_is_read(tmp_path, value):
    write_dataset(tmp_path)
    assert load_settings(env_for(tmp_path, AGENT_PUBLIC_URL=f" {value} "), tmp_path).agent_public_url == value


@pytest.mark.parametrize("value", ["example.com/agent/", "ftp://example.com/", "https://", "https://h/a?x=1",
                                   "https://h/a#f"])
def test_bad_agent_public_url_is_a_config_error(tmp_path, value):
    write_dataset(tmp_path)
    with pytest.raises(ConfigError, match="AGENT_PUBLIC_URL"):
        load_settings(env_for(tmp_path, AGENT_PUBLIC_URL=value), tmp_path)


def test_max_turns_per_day_defaults_to_no_cap_and_is_read(tmp_path):
    write_dataset(tmp_path)
    assert load_settings(env_for(tmp_path), tmp_path).max_turns_per_day == 0
    assert load_settings(env_for(tmp_path, MAX_TURNS_PER_DAY="200"), tmp_path).max_turns_per_day == 200


def test_turn_limit_contact_is_read(tmp_path):
    write_dataset(tmp_path)
    assert load_settings(env_for(tmp_path), tmp_path).turn_limit_contact is None
    s = load_settings(env_for(tmp_path, TURN_LIMIT_CONTACT=" Contact me on GitHub. "), tmp_path)
    assert s.turn_limit_contact == "Contact me on GitHub."


@pytest.mark.parametrize("value", ["-1", "lots"])
def test_bad_max_turns_per_day_is_a_config_error(tmp_path, value):
    write_dataset(tmp_path)
    with pytest.raises(ConfigError, match="MAX_TURNS_PER_DAY"):
        load_settings(env_for(tmp_path, MAX_TURNS_PER_DAY=value), tmp_path)


def test_settings_repr_hides_the_dsn(tmp_path):
    write_dataset(tmp_path)
    assert "s3cret" not in repr(load_settings(env_for(tmp_path), tmp_path))


# D3
def test_committed_dataset_loads():
    s = load_settings({**BASE_ENV, "DATASET_DIR": "agent/tests/fixtures/austender"}, EXPLORER_ROOT)
    assert s.dataset.name == "austender"
    assert s.dataset.allowed_schemas == ("mart",)
    assert (s.dataset.currency.code, s.dataset.currency.symbol) == ("AUD", "$")
    assert s.prompt_cache_key == "explorer-austender"
    # Raised from 2500 when bootstrap regenerated the notes (2026-11-04); keeps the prefix in check.
    assert len(s.dataset.notes) + len(s.dataset.glossary) < 5500


def _drill_hints(notes: str) -> str:
    """The notes' "Drill-downs" bullet with its sub-bullets (SPEC §17, round 2)."""
    m = re.search(r"^- Drill-downs.*?(?=^- |\Z)", notes, re.MULTILINE | re.DOTALL)
    assert m, "the notes have a Drill-downs bullet"
    return m[0]


# SPEC §17 D8: the Dataset hints name only tables and columns that exist
@pytest.mark.db
async def test_committed_drill_hints_name_real_tables_and_columns(ro_pool):
    from explorer_agent.schema import SchemaCatalog

    s = load_settings({**BASE_ENV, "DATASET_DIR": "agent/tests/fixtures/austender"}, EXPLORER_ROOT)
    hints = _drill_hints(s.dataset.notes)
    tables = {f"{t.schema}.{t.name}": {c.name for c in t.columns} for t in await SchemaCatalog(ro_pool, ["mart"]).objects()}
    names = re.findall(r"`([^`]+)`", hints)
    named_tables = [n for n in names if "." in n]
    assert named_tables and all(t in tables for t in named_tables), named_tables
    columns = set().union(*(tables[t] for t in named_tables))
    assert [n for n in names if "." not in n and n not in columns] == []


# Regression (refuter): `$` let a trailing newline through.
def test_schema_name_with_trailing_newline_is_rejected(tmp_path):
    write_dataset(tmp_path, toml='name="x"\ndescription="d"\nallowed_schemas=["mart\\n"]\nnotes="notes.md"\n'
                  'glossary="glossary.md"\n[currency]\ncode="A"\nsymbol="$"\n')
    with pytest.raises(ConfigError, match="invalid schema name"):
        load_settings(env_for(tmp_path), tmp_path)


# Regression (refuter): a non-UTF-8 file is a ConfigError, not a UnicodeDecodeError.
def test_non_utf8_notes_is_a_config_error(tmp_path):
    d = write_dataset(tmp_path)
    (d / "notes.md").write_bytes(b"\xff\xfe bad")
    with pytest.raises(ConfigError, match="UTF-8"):
        load_settings(env_for(tmp_path), tmp_path)


# Regression (refuter): a malformed DATABASE_URL is rejected at load, without the secret.
def test_malformed_database_url_is_a_config_error(tmp_path):
    write_dataset(tmp_path)
    with pytest.raises(ConfigError, match="DATABASE_URL") as e:
        load_settings(env_for(tmp_path, DATABASE_URL="explorer_ro:s3cret@localhost/db"), tmp_path)
    assert "s3cret" not in str(e.value)



# Regression: TABLE_ROW_CAP was read but never reached the macros, so tables always showed 200 rows.
def test_table_row_cap_reaches_the_tables(tmp_path):
    from explorer_agent.results import ResultStore
    from explorer_agent.ui import UiPipeline

    write_dataset(tmp_path)
    settings = load_settings(env_for(tmp_path, TABLE_ROW_CAP="10"), tmp_path)
    store = ResultStore()
    store.add(sql="s", purpose="p", columns=["n"], column_types=["integer"],
              rows=[{"n": i} for i in range(50)], truncated=False)
    ui = UiPipeline.from_settings(settings).build(
        '<a2ui>\nroot = Column([t])\nt = DataTable("r1", "T")\n</a2ui>', store, "turn-1")
    grid = next(c for m in ui.messages if "updateComponents" in m
                for c in m["updateComponents"]["components"] if c["component"] == "DataGrid")
    assert len(grid["rows"]) == 10


# Comment overlay (SPEC §13)
OVERLAY_TOML = (
    'name = "demo"\ndescription = "Demo"\nallowed_schemas = ["mart"]\nglossary = "glossary.md"\nnotes = "notes.md"\n'
    '[comments]\noverlay = "comments.toml"\n[currency]\ncode = "EUR"\nsymbol = "€"\n'
)
GOOD_OVERLAY = (
    '["mart.contract"]\ncomment = "One row per contract."\n'
    '["mart.contract".columns]\nnotice_id = "Notice number."\n'
    '["mart.agency".columns]\nagency_key = "Agency key."\n'
)


def write_overlay_dataset(tmp_path, overlay, toml=OVERLAY_TOML):
    d = write_dataset(tmp_path, toml=toml)
    if overlay is not None:
        (d / "comments.toml").write_text(overlay, encoding="utf-8")
    return d


# O1
def test_no_overlay_by_default(tmp_path):
    write_dataset(tmp_path)
    assert load_settings(env_for(tmp_path), tmp_path).dataset.comment_overlay is None


# O2
def test_overlay_loads(tmp_path):
    d = write_overlay_dataset(tmp_path, GOOD_OVERLAY)
    overlay = load_dataset(d).comment_overlay
    assert overlay is not None and overlay.path == d / "comments.toml"
    assert set(overlay.entries) == {"mart.contract", "mart.agency"}
    assert overlay.entries["mart.contract"].comment == "One row per contract."
    assert dict(overlay.entries["mart.contract"].columns) == {"notice_id": "Notice number."}
    assert overlay.entries["mart.agency"].comment is None


# O3
@pytest.mark.parametrize(
    ("toml", "overlay", "message"),
    [
        (OVERLAY_TOML.replace('[comments]\noverlay = "comments.toml"\n', 'comments = "x"\n'), None,
         "[comments] must be a table"),
        (OVERLAY_TOML.replace('overlay = "comments.toml"', 'overlay = "comments.toml"\nfile = "y"'), None,
         "unknown key file"),
        (OVERLAY_TOML.replace('overlay = "comments.toml"', "overlay = 3"), None, "comments.overlay must be"),
        (OVERLAY_TOML.replace('overlay = "comments.toml"\n', ''), None, "missing key comments.overlay"),
        (OVERLAY_TOML, None, "comment overlay file not found"),
        (OVERLAY_TOML, '["mart.contract"\ncomment = "x"\n', "is not valid TOML"),
        (OVERLAY_TOML, '[mart.contract]\ncomment = "x"\n', 'quoted "schema.table"'),
        (OVERLAY_TOML, '["raw.release"]\ncomment = "x"\n', "not in an allowed schema"),
        (OVERLAY_TOML, '"mart.contract" = "x"\n', "must be a table"),
        (OVERLAY_TOML, '["mart.contract"]\ndescription = "x"\n', "unknown key 'description'"),
        (OVERLAY_TOML, '["mart.contract"]\n', "neither comment nor columns"),
        (OVERLAY_TOML, '["mart.contract"]\ncomment = "  "\n', "comment must be a non-empty string"),
        (OVERLAY_TOML, '["mart.contract"]\ncomment = 3\n', "comment must be a non-empty string"),
        (OVERLAY_TOML, '["mart.contract"]\ncolumns = "x"\n', "columns must be a table"),
        (OVERLAY_TOML, '["mart.contract".columns]\nnotice_id = ""\n', "column 'notice_id'"),
        (OVERLAY_TOML, '["mart.contract".columns]\nnotice_id = 1\n', "column 'notice_id'"),
        (OVERLAY_TOML, '["mart.contract"]\ncolumns = {}\n', "neither comment nor columns"),
        (OVERLAY_TOML, '["mart.contract".columns]\n', "neither comment nor columns"),
        (OVERLAY_TOML.replace('overlay = "comments.toml"', 'overlay = " "'), None, "comments.overlay must be"),
        (OVERLAY_TOML, b'["mart.contract"]\ncomment = "\xff"\n', "not valid UTF-8"),
    ],
)
def test_overlay_errors(tmp_path, toml, overlay, message):
    d = write_overlay_dataset(tmp_path, None, toml=toml)
    if isinstance(overlay, bytes):
        (d / "comments.toml").write_bytes(overlay)
    elif overlay is not None:
        (d / "comments.toml").write_text(overlay, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_dataset(d)
    assert message in str(e.value)
    # errors in the overlay file name it; errors in [comments] name dataset.toml
    expected = "comments.toml" if overlay is not None or "file not found" in message else "dataset.toml"
    assert expected in str(e.value)


def test_overlay_error_never_quotes_comment_text(tmp_path):
    d = write_overlay_dataset(tmp_path, '["mart.contract"]\ncomment = "SECRET-ish text"\ncolumns = 3\n')
    with pytest.raises(ConfigError) as e:
        load_dataset(d)
    assert "SECRET-ish" not in str(e.value)


# Example questions for the client's empty state (optional, shown in the agent card).
def test_examples_default_to_none(tmp_path):
    write_dataset(tmp_path)
    assert load_settings(env_for(tmp_path), tmp_path).dataset.examples == ()


def test_examples_load(tmp_path):
    base = 'name = "demo"\ndescription = "Demo"\nallowed_schemas = ["mart"]\nglossary = "glossary.md"\nnotes = "notes.md"\n'
    write_dataset(tmp_path, toml=base + 'examples = ["One?", "Two?"]\n[currency]\ncode = "EUR"\nsymbol = "€"\n')
    assert load_settings(env_for(tmp_path), tmp_path).dataset.examples == ("One?", "Two?")


@pytest.mark.parametrize("value", ['"one"', "[1]", '[""]', '["  "]'])
def test_bad_examples_are_a_config_error(tmp_path, value):
    base = 'name = "demo"\ndescription = "Demo"\nallowed_schemas = ["mart"]\nglossary = "glossary.md"\nnotes = "notes.md"\n'
    write_dataset(tmp_path, toml=base + f"examples = {value}\n[currency]\ncode = \"EUR\"\nsymbol = \"€\"\n")
    with pytest.raises(ConfigError, match="examples"):
        load_settings(env_for(tmp_path), tmp_path)


# SPEC §15: Actions and drill-down settings
def test_drill_settings_defaults_and_overrides(tmp_path):
    write_dataset(tmp_path)
    s = load_settings(env_for(tmp_path), tmp_path)
    assert (s.drill_prefetch_rows, s.drill_prefetch_timeout_ms, s.drill_check_timeout_ms) == (3, 3000, 2000)
    assert (s.max_drills_per_surface, s.max_action_sources, s.max_prefetched, s.max_pending_pairs) == (2, 50, 30, 5)
    s = load_settings(env_for(tmp_path, DRILL_PREFETCH_ROWS="0", MAX_DRILLS_PER_SURFACE="1"), tmp_path)
    assert (s.drill_prefetch_rows, s.max_drills_per_surface) == (0, 1)


@pytest.mark.parametrize(("key", "value"), [("DRILL_PREFETCH_ROWS", "-1"), ("MAX_PENDING_PAIRS", "0"), ("DRILL_CHECK_TIMEOUT_MS", "x")])
def test_bad_drill_settings(tmp_path, key, value):
    write_dataset(tmp_path)
    with pytest.raises(ConfigError, match=key):
        load_settings(env_for(tmp_path, **{key: value}), tmp_path)


def test_process_settings_read_the_public_url_and_the_daily_cap():
    from explorer_agent.config import process_settings

    p = process_settings({"AGENT_PUBLIC_URL": "https://h/agent/", "MAX_TURNS_PER_DAY": "7", "TURN_LIMIT_CONTACT": " c "})
    assert (p.agent_public_url, p.max_turns_per_day, p.turn_limit_contact) == ("https://h/agent/", 7, "c")
    p = process_settings({})
    assert (p.agent_public_url, p.max_turns_per_day, p.turn_limit_contact) == (None, 0, None)
