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


def test_settings_repr_hides_the_dsn(tmp_path):
    write_dataset(tmp_path)
    assert "s3cret" not in repr(load_settings(env_for(tmp_path), tmp_path))


# D3
def test_committed_dataset_loads():
    s = load_settings(BASE_ENV, EXPLORER_ROOT)
    assert s.dataset.name == "austender"
    assert s.dataset.allowed_schemas == ("mart",)
    assert (s.dataset.currency.code, s.dataset.currency.symbol) == ("AUD", "$")
    assert s.prompt_cache_key == "explorer-austender"
    assert len(s.dataset.notes) + len(s.dataset.glossary) < 2500


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
