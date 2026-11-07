"""Load the committed workbook into mart through dataload (load.toml + transforms/).

The workbook goes into staging.houses_raw as it is; transforms/10_mart.sql reshapes it into
mart.suburb_price and mart.suburb_trend in the same transaction. Loading twice gives identical
tables, and readers keep seeing the old data until the load commits.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import psycopg
from dataload import Manifest, load_manifest, run

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "load.toml"


def manifest(xlsx: Path | None = None) -> Manifest:
    """vicprop's manifest, optionally reading another workbook than the committed one."""
    m = load_manifest(MANIFEST)
    if xlsx is None:
        return m
    [source] = m.sources
    return dataclasses.replace(m, sources=(dataclasses.replace(source, file=str(Path(xlsx).resolve())),))


def load(conn: psycopg.Connection, xlsx: Path | None = None, *, role: str | None = None) -> dict[str, int]:
    """Refill mart. `role`: load as that role (a superuser loading for vicprop_loader, as tests do)."""
    run(conn, manifest(xlsx), role=role)
    prices = conn.execute("SELECT count(*) FROM mart.suburb_price").fetchone()
    localities = conn.execute("SELECT count(*) FROM mart.suburb_trend").fetchone()
    assert prices is not None and localities is not None
    return {"price_rows": prices[0], "localities": localities[0]}
