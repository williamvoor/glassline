# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workspace layout

`genUI/` is the single git repo for this project.

| Directory | What it is | Rules |
|---|---|---|
| `a2ui/` | Clone of the upstream A2UI repo (Google), main branch, late Oct 2026. Git-ignored. | **Reference only. Never modify it**, and never make app code depend on paths inside it at runtime. It has its own `AGENTS.md` for upstream contributors. |
| `explorer/` | Our app: an A2UI data explorer for Postgres | Dataset-agnostic: nothing AusTender-specific in code. |
| `austender/` | Our AusTender database: ingestion from the AusTender OCDS API, schema, read-only role, test seed | All Python, no TypeScript. Has its own `SPEC.md`. |
| `docs/adr/` | Architecture decisions | |

`CONTEXT.md` (repo root) is the explorer's glossary. Use its terms.

## Status and source of truth

- `explorer/SPEC.md` is **approved** (2026-10-31) and is the source of truth over `explorer/BUILD_PROMPT.md` (the original brief). `explorer/spike/FINDINGS.md` holds the phase 1 evidence.
- `austender/SPEC.md` is the database's source of truth.
- **Speed is the most important requirement.** Prefer fewer model round trips and faster queries, as long as numbers stay correct.

## What the app is (big picture)

Plain-English question → LLM agent (LiteLLM `acompletion` with our own tool loop, no Google ADK) writes and runs SQL via tools (`list_tables`, `describe_table`, `run_sql`), with the whole schema (from database `COMMENT ON`s) in its cached prompt → replies in A2UI Express → server compiles Express to A2UI v0.9 JSON and expands macros (`BarChart`, `LineChart`, `DataTable`, `Metric`, `Cite`) into components using stored Results → sent over A2A to a Vite + Lit client (`@a2ui/lit`, `@a2ui/web_core`, vendored VegaChart, custom `DataGrid` and `SqlDetails`).

The central design idea (ADR 0001): the model never retypes data. It references a Result ID in a macro call; the macro builds the chart, table or sentence from the stored rows. Every number on screen comes from the database, and the SQL behind each Visual is shown.

## Constraints that are easy to violate

- **Model:** use only `meta/muse-spark-1.3` for any live call. No other models, no `-contributor` variants. Tests must never call a real model or the network (stub model with canned tool calls/Express).
- **Secrets:** `explorer/.env` holds `META_API_KEY`, `MODEL`, `DATABASE_URL`. Never print, log or commit them. New settings go in a committed `.env.example` with placeholders.
- **Database:** the explorer connects only with the dataset's read-only role. Every query: `READ ONLY` transaction, `statement_timeout`, row cap with truncation reported, schema allowlist from config. The `fti` project (`../fti`) is not used; don't modify it. Its docs are a source of lessons about the AusTender API only.
- **One merged catalog** with its own ID on both agent and client (ADR 0002). Components outside the surface's catalog render as nothing.
- **Prompt caching:** prompt order is A2UI rules/catalog → instructions → dataset context → conversation/tool results. No per-request values (dates, IDs, timestamps) in the stable prefix. The prefix must be byte-identical across requests (needs a test). Send a fixed `prompt_cache_key`; log prompt and cached tokens per call.
- **No AusTender hard-coding in `explorer/`:** dataset description, notes and allowed schemas are config in `explorer/datasets/<name>/`.
- **LiteLLM:** pinned exactly (`1.106.6`); the `mcp` package is a required dep to work around a `tools=` import bug. Keep `uv.lock` committed.
- **Express gotchas:** wrap output in `<a2ui>…</a2ui>` (code fences aren't recognised); Express doesn't stream.
- **Commits:** Conventional Commits, template in `.gitmessage`.

## Commands

Agent (`explorer/agent/`, Python 3.13, `uv`):

```bash
uv sync                          # install deps
uv run pytest                    # all tests (db tests need Docker)
uv run pytest path/to/test.py::test_name   # single test
```

Client (`explorer/client/`) and database (`austender/`) commands are in their READMEs once built.

## Where to look when unsure about A2UI APIs

- Installed SDK source: `explorer/agent/.venv/lib/python3.13/site-packages/a2ui/`
- Macros: `a2ui/python/a2ui_agent/src/a2ui/transformers/macros/README.md`, working example `a2ui/samples/community/macros/` (`macro_runtime.py`)
- VegaChart: `a2ui/typescript/catalogs/vegachart` (not on npm — vendor it, keep the Apache-2.0 header), sample `a2ui/samples/vegachart/`
- Lit shell: `a2ui/samples/client/lit/shell`
