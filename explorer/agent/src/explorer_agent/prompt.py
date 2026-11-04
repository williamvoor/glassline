"""The system prompt (parent spec §6): format rules + catalog, then instructions, then the
Dataset context. It is built once at startup and must be byte-identical across requests, so
nothing per request goes in (no dates, IDs or surface IDs; the model's surface is always `main`).
"""

from __future__ import annotations

from pathlib import Path

from a2ui.inference_formats.direct_json import DirectJsonFormat
from a2ui.inference_formats.express import ExpressFormat

from explorer_agent.config import Dataset

MODEL_SURFACE_ID = "main"
INSTRUCTIONS_PATH = Path(__file__).resolve().parent / "prompts" / "instructions.md"


def make_format(name: str, inference_catalog):
    """One format object builds both the prompt (layer 1) and the parser, so they always match."""
    if name == "express":
        return ExpressFormat(catalogs=[inference_catalog], surface_id=MODEL_SURFACE_ID)
    if name == "direct_json":
        return DirectJsonFormat(catalogs=[inference_catalog])
    raise ValueError(f"unknown format {name!r}")


def _clean(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))


def instructions() -> str:
    return _clean(INSTRUCTIONS_PATH.read_text(encoding="utf-8"))


def _columns(names: tuple[str, ...]) -> str:
    quoted = [f"`{n}`" for n in names]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def board_lines(dataset: Dataset) -> list[str]:
    """One stable line per Entity Board, in file name order (SPEC §18 slice 3)."""
    return [
        f"- Rows with a {_columns(b.key_columns)} column open the {b.title} Board."
        for _name, b in sorted(dataset.boards.items()) if b.key
    ]


def dataset_context(dataset: Dataset, schema_section: str) -> str:
    """Layer 3. Pure: a made-up schema string works as well as one rendered from a database."""
    parts = [
        f"## Dataset: {dataset.name}\n{_clean(dataset.description)}",
        f"### Notes\n{_clean(dataset.notes)}",
        f"### Glossary\n{_clean(dataset.glossary)}",
    ]
    lines = board_lines(dataset)
    if lines:
        parts.append("### Boards\n" + "\n".join(lines))
    parts.append(f"### Schema\n{_clean(schema_section)}")
    return "\n\n".join(parts)


def build_system_prompt(fmt, context: str) -> str:
    rules = _clean(fmt.prompt_generator.generate())
    return f"{rules}\n\n## Instructions\n{instructions()}\n\n{context}\n"
