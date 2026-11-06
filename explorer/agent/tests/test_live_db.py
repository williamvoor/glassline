"""Checks against the loaded dataset database. Not run by default: `uv run pytest -m live_db`.

Reads the repo root's `.env`, like `__main__`, and checks the Workspace named by `LIVE_WORKSPACE`
(in `EXPLORER_DATA_DIR`). Local database only: no model, no network.
"""

import logging
import os
import statistics
from pathlib import Path

import pytest
from dotenv import dotenv_values

from explorer_agent.__main__ import ENV_FILE
from explorer_agent.config import named_workspace_settings
from explorer_agent.schema import SchemaCatalog, render_schema_section
from explorer_agent.sql import SqlOk, SqlRunner, open_pool

from .test_tools import ACCEPTANCE_SQL

pytestmark = pytest.mark.live_db
log = logging.getLogger(__name__)

EXPLORER_ROOT = Path(__file__).resolve().parents[2]
TARGET_MS = 500
FAIL_MS = 2000
SCHEMA_SECTION_LIMIT = 12_000


@pytest.fixture
async def live():
    env_file = ENV_FILE
    if not env_file.is_file():
        pytest.fail(f"live_db checks need {env_file} with EXPLORER_DATA_DIR (see .env.example)")
    env = {**{k: v for k, v in dotenv_values(env_file).items() if v is not None}, **os.environ}
    name = env.get("LIVE_WORKSPACE", "").strip()
    if not name:
        pytest.fail("live_db checks need LIVE_WORKSPACE, the name of a Workspace in EXPLORER_DATA_DIR")
    settings = named_workspace_settings(env, name, EXPLORER_ROOT.parent, EXPLORER_ROOT)
    pool = open_pool(settings.database_url)
    await pool.open(wait=True)
    try:
        yield settings, pool
    finally:
        await pool.close()


# L1
async def test_privileges(live):
    settings, pool = live
    s = settings.dataset
    await SqlRunner(pool, s.allowed_schemas, settings.sql_timeout_ms, settings.sql_row_cap).check_privileges()


# L2
async def test_schema_section(live):
    settings, pool = live
    objects = await SchemaCatalog(pool, settings.dataset.allowed_schemas).objects()
    section = render_schema_section(objects)
    print(f"\nschema section: {len(objects)} objects, {len(section)} chars")
    assert objects
    assert len(section) < SCHEMA_SECTION_LIMIT


# L3
async def test_acceptance_query_speed(live):
    settings, pool = live
    runner = SqlRunner(pool, settings.dataset.allowed_schemas, settings.sql_timeout_ms, settings.sql_row_cap)
    times = []
    for _ in range(5):
        out = await runner.run(ACCEPTANCE_SQL)
        assert isinstance(out, SqlOk), out
        times.append(out.elapsed_ms)
    median = statistics.median(times)
    verdict = "meets" if median <= TARGET_MS else "MISSES"
    print(f"\nacceptance query: median {median} ms over 5 runs {times}; {verdict} the {TARGET_MS} ms target")
    assert median <= FAIL_MS, f"acceptance query median {median} ms is over {FAIL_MS} ms"
