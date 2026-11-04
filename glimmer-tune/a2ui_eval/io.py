"""Shared JSONL, split and Express-wrapping helpers."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from .sdk import CLOSE_TAG, OPEN_TAG
from .seeds import ROOT

SPLITS_PATH = ROOT / "data" / "splits.yaml"
SPLITS = ("train", "dev", "test", "excluded")
# Non-family keys in the same file: the run 2 synthetic dev skeletons (history) and the
# archetypes `lint.banned_matches` refuses outside their split.
NON_FAMILY_KEYS = ("skeletons", "banned_archetypes")


class SplitError(Exception):
    pass


def load_splits(known: Iterable[str], path: Path = SPLITS_PATH) -> dict[str, str]:
    """`family -> split`. Every known family must appear exactly once."""
    d = yaml.safe_load(path.read_text()) or {}
    unknown_splits = set(d) - set(SPLITS) - set(NON_FAMILY_KEYS)
    if unknown_splits:
        raise SplitError(f"unknown splits: {sorted(unknown_splits)}")
    out: dict[str, str] = {}
    for split in SPLITS:
        for family in d.get(split) or []:
            if family in out:
                raise SplitError(f"{family} is in both {out[family]} and {split}")
            out[family] = split
    known = set(known)
    if set(out) - known:
        raise SplitError(f"unknown families: {sorted(set(out) - known)}")
    if known - set(out):
        raise SplitError(f"families without a split: {sorted(known - set(out))}")
    return out


def wrap(express: str) -> str:
    return f"{OPEN_TAG}\n{express.strip()}\n{CLOSE_TAG}"


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Atomic: written to a temp file, then renamed, so a failure never leaves a
    half-written file (the draft holds hand edits)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    n = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                n += 1
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
