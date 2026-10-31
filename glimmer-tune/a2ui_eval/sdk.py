"""The only module that imports the A2UI SDK. Everything else works on plain dicts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a2ui.core.validation.payload_validator import PayloadValidator, ValidationConfig
from a2ui.inference_formats.express import ExpressFormatFactory
from a2ui.parser import RawA2uiPart, TextPart
from a2ui.processor.catalog_config import CatalogConfig
from a2ui.processor.processor import A2uiRequestProcessor

OPEN_TAG = "<a2ui>"
CLOSE_TAG = "</a2ui>"

# Component-level checks only: references and reachability are scored separately
# (structure.py), so the schema stage must not fail on them.
_SCHEMA_ONLY = ValidationConfig(
    allow_orphan_components=True,
    allow_dangling_references=True,
    allow_missing_root=True,
)


@dataclass(frozen=True)
class Block:
    text: str
    is_final: bool  # False when the closing tag never arrived


class CompileError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _dump(messages: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        m if isinstance(m, dict) else m.model_dump(mode="json", exclude_none=True, by_alias=True)
        for m in messages
    ]


def write_basic_v0_9_catalog(path: Path) -> None:
    """Writes the SDK's own v0.9.1 basic catalog, for rendering with `@a2ui/lit` (ADR 0005)."""
    from a2ui.core.basic_catalog import BasicCatalog

    doc = BasicCatalog("0.9.1").to_json()
    doc["protocolVersion"] = "0.9"  # not declared in the SDK's copy; the loader requires it
    path.write_text(json.dumps(doc), encoding="utf-8")


class ExpressSDK:
    def __init__(self, catalog_path: str | Path, examples: Sequence[Sequence[dict]] | None = None):
        self.catalog_path = Path(catalog_path)
        self.catalog_json: dict[str, Any] = json.loads(self.catalog_path.read_text())
        self.catalog = CatalogConfig.from_path(str(self.catalog_path)).transformed_catalog
        self.processor = A2uiRequestProcessor(
            [self.catalog],
            examples=[list(t) for t in examples] if examples else None,
            format_factory=ExpressFormatFactory(),
        )
        self._validator = PayloadValidator(self.catalog, _SCHEMA_ONLY)

    @property
    def prompt_snippet(self) -> str:
        return self.processor.prompt_snippet

    def unwrap(self, text: str) -> tuple[list[str], list[Block]]:
        texts, blocks = [], []
        for raw in self.processor.parser.unwrap(text):
            if isinstance(raw.part, RawA2uiPart):
                blocks.append(Block(raw.part.a2ui_raw, raw.is_final))
            elif isinstance(raw.part, TextPart):
                texts.append(raw.part.text)
        return texts, blocks

    def compile(self, block: Block) -> list[dict[str, Any]]:
        try:
            return _dump(self.processor.parser.compile(block.text, is_final=block.is_final))
        except Exception as e:  # the SDK raises several error families; all are compile failures
            raise CompileError(type(e).__name__, str(e)) from e

    def compile_express(self, express: str) -> list[dict[str, Any]]:
        """Compiles one unwrapped Express source. Used for references and fixtures."""
        return self.compile(Block(express, True))

    def decompile(self, messages: Sequence[dict[str, Any]]) -> str:
        return self.processor.parser.decompile(messages)

    def sdk_check(self, text: str, existing: Sequence[dict[str, Any]] | None) -> str | None:
        """Full SDK verdict (compile + schema + topology) against the existing surface.

        Returns None when accepted, else the error message.
        """
        state = self.processor.create_state()
        try:
            if existing:
                state.apply(list(existing))
            self.processor.parse_response(text, state=state)
        except Exception as e:
            return f"{type(e).__name__}: {e}"
        return None

    def component_errors(self, component: dict[str, Any]) -> list[str]:
        try:
            self._validator.validate_component(component)
        except Exception as e:
            details = getattr(e, "details", None) or []
            return [d.message for d in details] or [str(e)]
        return []
