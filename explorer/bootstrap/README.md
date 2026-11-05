# Dataset bootstrap

Bootstrap prepares a Postgres Dataset for Explorer: inspect the database, draft descriptions
and checkable claims, review them, optionally draft Boards, then export. It never writes to
the database. An administrator creates the read-only login/grants and applies exported comment
SQL if needed. The behavioral contract is [SPEC.md](SPEC.md).

The browser workflow lives in Explorer Settings. The Python server also offers a
[CLI](server/README.md) for reports and Dataset-plus-Boards export. It reuses the agent’s SQL
safety, Dataset loading, Macros and Board checks through a local editable dependency.

## Setup and browser workflow

Install Explorer’s [agent and client dependencies](../README.md#run-the-stub-application),
then the bootstrap server’s dependencies:

```bash
# From the repository root
cd explorer/bootstrap/server
uv sync --locked
```

Prepare a database with a read-only login. In the root `.env`, set `META_API_KEY`,
`EXPLORER_DATA_DIR` (an absolute path outside the repo), and a named login for each database:

```dotenv
EXPLORER_DATA_DIR=~/glassline-data
VICPROP_DATABASE_URL=postgresql://explorer_ro:<password>@localhost:5435/vicprop
```

Replace the password with the database’s read-only password, URL-encoded as needed. Existing
environment variables override `.env`; `EXPLORER_ENV_FILE` selects another file for the launcher.
From the repository root:

```bash
cd explorer
./run.sh --real
```

Open **http://127.0.0.1:5173/#/settings** and select **Add a Workspace**:

1. **Database:** choose a configured login and allowed schemas; inspect the report.
2. **Draft:** choose a model and confirm the model call.
3. **Review:** edit descriptions/comments and recheck or dismiss claims.
4. **Boards:** optionally draft Boards and review their query previews.
5. **Export:** write the Workspace and reload the agent.

Regenerate follows the same steps for an existing Workspace, keeping its database and name.
Reports and query previews use no model. Drafting makes live model calls and records cost.
Previews show query data and cold-render timings; review the rendered Boards in Explorer
after export.

The old standalone UI and standalone serving workflow are retired (ADRs 0041 and 0045).
Use `run.sh --real` in Workspace mode for the browser, or the [CLI](server/README.md).

## Review and export

Review factual claims and proposed comment rewrites. Failed, errored or unchecked claims block
export unless dismissed; invalid Dataset or Board files also block export. The Settings Draft
is held in memory and lost on a server restart. CLI `draft.json` and `boards.json` files provide
a durable review workflow.

Comment overlays replace selected database descriptions. Alternatively, CLI export can write
SQL comments for an object owner to apply manually. Export does not grant privileges or run
that SQL. Replaced files are backed up; replacing Boards replaces the whole Board set.

## Configuration and tests

Muse Wonder (`meta/muse-wonder-1.0`) is the default draft model; Muse Spark
(`meta/muse-spark-1.3`) is the alternative. Settings and limits are in
[the environment example](../.env.example); CLI options, defaults and report limitations
are in the [server reference](server/README.md).

```bash
# explorer/bootstrap/server/; database tests need Docker
uv run pytest

# explorer/client/; includes Settings tests
npm test
```

Tests stub model calls. The server binds to loopback, checks Host and POST content type,
and permits one model run at a time. Reports use planner estimates rather than exact counts;
min/max queries can time out. The tool does not run `ANALYZE` or repair missing grants.
