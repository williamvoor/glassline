import datetime
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from explorer_agent.config import Currency, Dataset
from explorer_agent.macros import BarChart, Cite, DataTable, LineChart, Metric
from explorer_agent.prompt import build_system_prompt, dataset_context, instructions, make_format
from explorer_agent.schema import SchemaCatalog, render_schema_section
from explorer_agent.ui import UiPipeline

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
FORMATS = ("express", "direct_json")

DEMO = Dataset(
    name="demo",
    description="Demo data  \r\n",
    allowed_schemas=("s",),
    notes="- note one   \n",
    glossary="- term: meaning\n",
    currency=Currency("EUR", "€"),
)
DEMO_SCHEMA = "### s.things (table)\nThings.\n- id integer not null: Key.\n"

# Builds the prompt the way __main__ will, against the DSN in EXPLORER_TEST_DSN, and prints it.
BUILD_SCRIPT = """
import asyncio, os, sys
from explorer_agent.config import Currency, Dataset
from explorer_agent.macros import BarChart, Cite, DataTable, LineChart, Metric
from explorer_agent.prompt import build_system_prompt, dataset_context
from explorer_agent.schema import SchemaCatalog, render_schema_section
from explorer_agent.sql import open_pool
from explorer_agent.ui import UiPipeline

async def main():
    pool = open_pool(os.environ["EXPLORER_TEST_DSN"])
    await pool.open(wait=True)
    try:
        section = render_schema_section(await SchemaCatalog(pool, ["mart"]).objects())
    finally:
        await pool.close()
    ds = Dataset("demo", "Demo", ("mart",), "notes", "glossary", Currency("AUD", "$"))
    fmt = UiPipeline(sys.argv[1]).format
    sys.stdout.write(build_system_prompt(fmt, dataset_context(ds, section)))

asyncio.run(main())
"""


async def seed_prompt(pool, fmt_name: str) -> str:
    section = render_schema_section(await SchemaCatalog(pool, ["mart"]).objects())
    ds = Dataset("demo", "Demo", ("mart",), "notes", "glossary", Currency("AUD", "$"))
    return build_system_prompt(UiPipeline(fmt_name).format, dataset_context(ds, section))


# P1
@pytest.mark.db
@pytest.mark.parametrize("fmt_name", FORMATS)
def test_prompt_is_identical_across_processes(pg, fmt_name):
    outputs = []
    for seed in ("0", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "EXPLORER_TEST_DSN": pg.ro_dsn}
        done = subprocess.run(
            [sys.executable, "-c", BUILD_SCRIPT, fmt_name], env=env, capture_output=True, check=True, timeout=120
        )
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1]
    assert len(outputs[0]) > 10_000


# Builds the prompt from a dataset directory with a Comment overlay, the way __main__ does.
OVERLAY_BUILD_SCRIPT = """
import asyncio, os, sys
from pathlib import Path
from explorer_agent.config import load_dataset
from explorer_agent.prompt import build_system_prompt, dataset_context
from explorer_agent.schema import SchemaCatalog, render_schema_section
from explorer_agent.sql import open_pool
from explorer_agent.ui import UiPipeline

async def main():
    ds = load_dataset(Path(sys.argv[2]))
    pool = open_pool(os.environ["EXPLORER_TEST_DSN"])
    await pool.open(wait=True)
    try:
        section = render_schema_section(await SchemaCatalog(pool, ds.allowed_schemas, ds.comment_overlay).objects())
    finally:
        await pool.close()
    sys.stdout.write(build_system_prompt(UiPipeline(sys.argv[1]).format, dataset_context(ds, section)))

asyncio.run(main())
"""

OVERLAY_FILE = (
    '["mart.supplier"]\ncomment = "OVERLAY supplier"\n'
    + '\n["mart.contract".columns]\nnotice_id = "OVERLAY notice"\n'
    + '\n["mart.agency"]\ncomment = "OVERLAY agency"\n'
)


# O7
@pytest.mark.db
def test_overlay_prompt_is_identical_across_processes(pg, tmp_path):
    (tmp_path / "dataset.toml").write_text(
        'name = "demo"\ndescription = "Demo"\nallowed_schemas = ["mart"]\nglossary = "g.md"\nnotes = "n.md"\n'
        '[comments]\noverlay = "comments.toml"\n[currency]\ncode = "AUD"\nsymbol = "$"\n'
    )
    (tmp_path / "g.md").write_text("glossary\n")
    (tmp_path / "n.md").write_text("notes\n")
    (tmp_path / "comments.toml").write_text(OVERLAY_FILE)
    outputs = []
    for seed in ("0", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "EXPLORER_TEST_DSN": pg.ro_dsn}
        done = subprocess.run(
            [sys.executable, "-c", OVERLAY_BUILD_SCRIPT, "express", str(tmp_path)],
            env=env, capture_output=True, check=True, timeout=120,
        )
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1]
    text = outputs[0].decode()
    assert "OVERLAY supplier" in text and "OVERLAY notice" in text and "OVERLAY agency" in text


# P2
@pytest.mark.parametrize("fmt_name", FORMATS)
def test_layer_order(fmt_name):
    fmt = UiPipeline(fmt_name).format
    prompt = build_system_prompt(fmt, dataset_context(DEMO, DEMO_SCHEMA))
    rules = fmt.prompt_generator.generate().strip()
    i_rules = prompt.index(rules[:200])  # the format rules come first
    i_instr = prompt.index("\n## Instructions\n")
    i_ds = prompt.index("\n## Dataset: demo\n")
    i_notes, i_gloss, i_schema = (prompt.index(h) for h in ("### Notes", "### Glossary", "### Schema"))
    assert i_rules < i_instr < i_ds < i_notes < i_gloss < i_schema
    assert prompt.rstrip("\n").endswith("- id integer not null: Key.")
    assert prompt.endswith("\n") and not prompt.endswith("\n\n")


# P3
def test_prompt_has_no_per_request_values():
    a = build_system_prompt(UiPipeline().format, dataset_context(DEMO, DEMO_SCHEMA))
    b = build_system_prompt(UiPipeline().format, dataset_context(DEMO, DEMO_SCHEMA))
    assert a == b
    assert "turn-" not in a
    now = datetime.datetime.now()
    # The SDK's rules contain a fixed example date; what must never appear is the current one.
    assert now.date().isoformat() not in a
    assert now.strftime("%H:%M") not in a
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", a)  # no UUIDs


# Regression (effort check 2026-11-02): a share computed as a fraction (0.066) was shown with
# `pct` as "0.1%". Every macro that takes a format must say which value each percent format expects.
@pytest.mark.parametrize("fmt_name", FORMATS)
def test_prompt_explains_percent_formats(fmt_name):
    prompt = build_system_prompt(UiPipeline(fmt_name).format, dataset_context(DEMO, DEMO_SCHEMA))
    for macro in ("BarChart", "LineChart", "Metric", "Cite"):
        assert macro in prompt
    assert prompt.count("pct = value already in percent (6.6 -> 6.6%)") >= 4
    assert prompt.count("fraction_pct = value is a fraction (0.066 -> 6.6%)") >= 4


# P4: changes to the prompt are deliberate. Rewrite with UPDATE_SNAPSHOTS=1.
@pytest.mark.db
@pytest.mark.parametrize("fmt_name", FORMATS)
async def test_prompt_snapshot(ro_pool, fmt_name):
    digest = hashlib.sha256((await seed_prompt(ro_pool, fmt_name)).encode()).hexdigest()
    path = SNAPSHOTS / f"system_prompt_{fmt_name}.sha256"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(digest + "\n")
    assert path.read_text().strip() == digest, "the system prompt changed; rerun with UPDATE_SNAPSHOTS=1 if deliberate"


@pytest.mark.db
async def test_formats_give_different_prompts(ro_pool):
    assert await seed_prompt(ro_pool, "express") != await seed_prompt(ro_pool, "direct_json")


# P5
def test_dataset_context_is_pure():
    ctx = dataset_context(DEMO, DEMO_SCHEMA)
    assert ctx == (
        "## Dataset: demo\nDemo data\n\n"
        "### Notes\n- note one\n\n"
        "### Glossary\n- term: meaning\n\n"
        "### Schema\n### s.things (table)\nThings.\n- id integer not null: Key."
    )


# P6
def test_instructions_are_generic():
    text = instructions()
    assert len(text) < 2600  # raised from 2,000 for the Clicks section (SPEC §15)
    for word in ("austender", "mart", "supplier", "agency", "contract"):
        assert word not in text.lower()


def test_make_format_rejects_unknown():
    with pytest.raises(ValueError):
        make_format("yaml", None)


# Regression (refuter final): the instructions and the Repair text must not name a UI tag that the
# active format doesn't use (Direct JSON writes <a2ui-json>, Express writes <a2ui>).
def test_instructions_and_repair_text_are_format_neutral():
    from explorer_agent.llm import REPAIR_MESSAGE

    for text in (instructions(), REPAIR_MESSAGE):
        assert "<a2ui" not in text, text


# explorer/specs/names-periods-currency
@pytest.mark.parametrize("fmt_name", FORMATS)
def test_prompt_lists_currency_compact(fmt_name):
    prompt = build_system_prompt(UiPipeline(fmt_name).format, dataset_context(DEMO, DEMO_SCHEMA))
    # The macro docstrings are what the prompt shows for each macro (test_prompt_explains_percent_formats).
    for fn in (BarChart, LineChart, DataTable, Metric, Cite):
        assert "currency_compact" in (fn.__doc__ or ""), fn.__name__
    assert prompt.count("currency_compact") >= 6  # five macros plus the instructions' example


def test_prompt_has_name_and_period_rules():
    text = instructions()
    assert "{r1[0].value|currency_compact}" in text
    assert "Don't run a separate query to look up keys or IDs first" in text
    assert "string_agg(DISTINCT name, '; ')" in text
    assert "Don't convert it into a date range yourself" in text
