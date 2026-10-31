"""Vendored A2UI gallery examples: one seed per family."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "seeds" / "gallery"


@dataclass(frozen=True)
class Seed:
    stem: str
    name: str
    description: str
    messages: list[dict[str, Any]]


def load_seeds(seed_dir: Path = SEED_DIR) -> dict[str, Seed]:
    seeds = {}
    for path in sorted(seed_dir.glob("*.json")):
        d = json.loads(path.read_text())
        seeds[path.stem] = Seed(path.stem, d["name"], d["description"], d["messages"])
    return seeds
