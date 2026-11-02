"""UiPipeline: the model's final reply → filled prose + validated A2UI v0.9 transport messages.

Parse (Express or Direct JSON), fill prose, expand macros, rewrite the surface ID, reject props
the merged catalog doesn't define, then STRICT_VALIDATION. Repairs live in the tool loop
(`llm.py`); this module only reports what is wrong.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from typing import Any

from a2ui.core.processing.message_processor import MessageProcessor, MessageProcessorOptions
from a2ui.core.schema import AgentToRendererMessage
from a2ui.core.validation.payload_validator import STRICT_VALIDATION
from a2ui.core.schema.v0_9.common_types import Checkable, ComponentCommon
from a2ui.core import Catalog
from a2ui.core.exceptions import A2uiError
from a2ui.parser import A2uiPart, TextPart
from pydantic import TypeAdapter

from explorer_agent.catalog import explorer_catalog
from explorer_agent.macros import (
    DEFAULT_CURRENCY_SYMBOL,
    FilledProse,
    fill_prose,
    fill_surface_text,
    macro_context,
    macro_expander,
)
from explorer_agent.prompt import make_format
from explorer_agent.results import ResultStore

log = logging.getLogger(__name__)

_MESSAGE = TypeAdapter(AgentToRendererMessage)
# The effort check harness parses this line (ADR 0012).
BARE_NUMBERS_LOG = "reply has numbers typed by the model: "


class UiError(Exception):
    """The reply can't become a valid UI. `messages` are meant for the model."""

    def __init__(self, messages: list[str], prose: str | None = None):
        super().__init__("; ".join(messages))
        self.messages = messages
        # The reply's filled prose when the prose itself was fine (only the UI failed), so a
        # Turn that runs out of Repairs can still show it.
        self.prose = prose


@dataclass
class UiResult:
    prose: str
    messages: list[dict[str, Any]]
    bare_numbers: list[str]


# `$ref`s in a component schema that the merged catalog doesn't carry in its own $defs.
_SDK_REFS = {"ComponentCommon": ComponentCommon, "Checkable": Checkable}


def _ref_props(name: str, catalog: Catalog) -> set[str]:
    if name in _SDK_REFS:
        model = _SDK_REFS[name]
        return {f.alias or n for n, f in model.model_fields.items()}
    if name in catalog.defs:
        return _schema_props(catalog.defs[name], catalog)
    raise RuntimeError(f"cannot resolve component schema reference {name!r}")


def _schema_props(schema: dict[str, Any], catalog: Catalog) -> set[str]:
    """Top-level property names of a component schema, following allOf and $defs refs."""
    props = set(schema.get("properties", {}))
    for part in schema.get("allOf", ()):
        props |= _schema_props(part, catalog)
    ref = schema.get("$ref")
    if ref:
        props |= _ref_props(ref.rsplit("/", 1)[-1], catalog)
    return props


@functools.cache
def known_props(catalog: Catalog) -> dict[str, frozenset[str]]:
    """Allowed property names per component type. Fails loudly on a schema it can't read."""
    return {
        name: frozenset(_schema_props(catalog.resolved_component_schema(name), catalog))
        for name in catalog.components
    }


def check_known_props(messages: list[dict[str, Any]], catalog: Catalog) -> None:
    """Raise UiError for any component property the catalog doesn't define (ADR 0005).

    STRICT_VALIDATION accepts some props the v0.9 catalog lacks, and Lit ignores unknown props
    silently, so without this a Surface could lose content unseen.
    """
    allowed = known_props(catalog)
    problems = []
    for message in messages:
        for comp in (message.get("updateComponents") or {}).get("components", []):
            kind = comp.get("component")
            if kind not in allowed:
                continue  # unknown component types are STRICT_VALIDATION's job
            for prop in sorted(set(comp) - allowed[kind]):
                problems.append(f"{kind} {comp.get('id')!r} has no property {prop!r}")
    if problems:
        raise UiError(problems)


def validate(messages: list[dict[str, Any]]) -> None:
    """Strict validation against the merged catalog; raises `UiError`."""
    processor = MessageProcessor(
        catalogs=[explorer_catalog()],
        options=MessageProcessorOptions(validation_config=STRICT_VALIDATION),
    )
    try:
        processor.process_messages(messages)
    except Exception as e:  # the SDK raises several validation exception types
        raise UiError([f"invalid UI: {e}"]) from e


def _components(message: Any) -> list[dict[str, Any]]:
    """The component dicts of a parsed message, whether it is a dict or an SDK model."""
    if isinstance(message, dict):
        body = message.get("updateComponents") or {}
        return body.get("components", [])
    body = getattr(message, "update_components", None)
    return list(getattr(body, "components", None) or [])


def _rewrite_surface(message: dict[str, Any], surface_id: str) -> dict[str, Any]:
    out = dict(message)
    for key, body in message.items():
        if isinstance(body, dict) and "surfaceId" in body:
            out[key] = {**body, "surfaceId": surface_id}
    return out


class UiPipeline:
    def __init__(self, fmt_name: str = "express", currency_symbol: str = DEFAULT_CURRENCY_SYMBOL):
        self._currency_symbol = currency_symbol
        self._expander = macro_expander()
        self.inference_catalog = self._expander.transform_to_inference_catalog(explorer_catalog())
        self.format = make_format(fmt_name, self.inference_catalog)

    def build(self, reply: str, store: ResultStore, surface_id: str) -> UiResult:
        # Any SDK error here comes from what the model wrote (syntax, dangling references, bad
        # macro arguments, duplicate surfaces), so it goes back to the model as a UiError.
        try:
            parts = self.format.create_parser().parse_response(reply)
        except A2uiError as e:
            raise UiError([f"could not parse the <a2ui> block: {e}"]) from e

        prose_raw = "\n\n".join(p.text.strip() for p in parts if isinstance(p, TextPart) and p.text.strip())
        typed = [m for p in parts if isinstance(p, A2uiPart) for m in p.a2ui]

        prose: FilledProse | None = None
        try:
            with macro_context(store, currency_symbol=self._currency_symbol) as ctx:
                prose = fill_prose(prose_raw)
                prose_ok = not ctx.errors
                bare = prose.bare_numbers + fill_surface_text(c for m in typed for c in _components(m))
                try:
                    expanded = self._expander.transform_to_transport(
                        [_MESSAGE.validate_python(m) if isinstance(m, dict) else m for m in typed]
                    )
                except A2uiError as e:
                    raise UiError(list(ctx.errors) or [f"invalid UI: {e}"]) from e
                if ctx.errors:
                    raise UiError(list(ctx.errors))

            if bare:
                log.warning("%s%s", BARE_NUMBERS_LOG, ", ".join(bare))

            messages = [
                _rewrite_surface(m.model_dump(by_alias=True, exclude_none=True), surface_id) for m in expanded
            ]
            if messages:  # a prose-only reply is valid; the processor rejects an empty list
                check_known_props(messages, explorer_catalog())
                validate(messages)
            return UiResult(prose=prose.text, messages=messages, bare_numbers=bare)
        except UiError as e:
            if prose is not None and prose_ok and prose.text:
                e.prose = prose.text
            raise
