"""Vendored A2UI gallery examples (v1.0 JSON on disk): one seed per family.

`Seed.messages` is converted to v0.9 (ADR 0008); `Seed.v1_messages` is the file as-is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .convert import v1_to_v0_9

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "seeds" / "gallery"


@dataclass(frozen=True)
class Seed:
    stem: str
    name: str
    description: str
    messages: list[dict[str, Any]]  # v0.9
    v1_messages: list[dict[str, Any]]


def load_seeds(seed_dir: Path = SEED_DIR) -> dict[str, Seed]:
    seeds = {}
    for path in sorted(seed_dir.glob("*.json")):
        d = json.loads(path.read_text())
        seeds[path.stem] = Seed(path.stem, d["name"], d["description"], v1_to_v0_9(d["messages"]), d["messages"])
    return seeds
