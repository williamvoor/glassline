# AGENTS.md

Guidance for coding agents working in `dataload/`. Read `SPEC.md` (source of truth) and
`README.md` first.

## What this is

A CLI and small library that loads CSV, Excel and Parquet files into any Postgres as they are
(one table per file or sheet), runs SQL transforms, and grants a read-only role. vicprop uses it
(`../vicprop/load.toml`). The explorer never imports it.

| Path | Role |
|---|---|
| `src/dataload/manifest.py` | `load.toml` parsing and checks; the owner login (`Owner.conninfo`) |
| `src/dataload/readers.py` | CSV, Excel and Parquet readers: header, trimmed rows, read twice |
| `src/dataload/names.py` | Column and table names |
| `src/dataload/infer.py` | Type inference and COPY text |
| `src/dataload/load.py` | `plan` (no database) and `run` (one transaction) |
| `src/dataload/grants.py` | `dataload grant`: plan, checks, apply |
| `src/dataload/cli.py` | `dataload load | file | grant` |

## Hard rules

- **Python only** (3.13, `uv`): stdlib `csv`, `openpyxl`, `psycopg` 3; `pyarrow` only through the
  `parquet` extra. Keep `uv.lock` committed.
- **Secrets:** logins come only from the environment. Never print, log or put in an error message a
  login, a conninfo string or a password. A psycopg connect error can name the user: replace it.
- **One transaction per load.** Inference happens before it; nothing is written until everything
  has been read.
- **Grants are read-only.** Never add a privilege beyond CONNECT, USAGE and SELECT.
- **Tests never touch the network.** Database tests use Testcontainers Postgres 18 and are marked `db`.
- **No timestamps in comments:** table and column comments feed the explorer's cached prompt.
