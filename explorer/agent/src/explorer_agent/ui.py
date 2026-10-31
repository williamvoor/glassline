"""UiPipeline: the model's final reply → filled prose + validated A2UI v0.9 transport messages.

Thin-slice version (specs/thin-slice/SPEC.md): parse, fill prose, expand macros, rewrite the
surface ID, validate. Repair retries come with the tool loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from a2ui.core.processing.message_processor import MessageProcessor, MessageProcessorOptions
from a2ui.core.schema import AgentToRendererMessage
from a2ui.core.validation.payload_validator import STRICT_VALIDATION
from a2ui.inference_formats.express import ExpressFormat
from a2ui.core.exceptions import A2uiError
from a2ui.parser import A2uiPart, TextPart
from pydantic import TypeAdapter

from explorer_agent.catalog import explorer_catalog
from explorer_agent.macros import DEFAULT_CURRENCY_SYMBOL, fill_prose, macro_context, macro_expander
from explorer_agent.results import ResultStore

log = logging.getLogger(__name__)

MODEL_SURFACE_ID = "main"  # what the model always writes; rewritten per turn (SPEC §6)
_MESSAGE = TypeAdapter(AgentToRendererMessage)


class UiError(Exception):
    """The reply can't become a valid UI. `messages` are meant for the model."""

    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


@dataclass
class UiResult:
    prose: str
    messages: list[dict[str, Any]]
    bare_numbers: list[str]


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


def _rewrite_surface(message: dict[str, Any], surface_id: str) -> dict[str, Any]:
    out = dict(message)
    for key, body in message.items():
        if isinstance(body, dict) and "surfaceId" in body:
            out[key] = {**body, "surfaceId": surface_id}
    return out


class UiPipeline:
    def __init__(self, currency_symbol: str = DEFAULT_CURRENCY_SYMBOL):
        self._currency_symbol = currency_symbol
        self._expander = macro_expander()
        inference = self._expander.transform_to_inference_catalog(explorer_catalog())
        self._format = ExpressFormat(catalogs=[inference], surface_id=MODEL_SURFACE_ID)

    def build(self, reply: str, store: ResultStore, surface_id: str) -> UiResult:
        # Any SDK error here comes from what the model wrote (syntax, dangling references, bad
        # macro arguments, duplicate surfaces), so it goes back to the model as a UiError.
        try:
            parts = self._format.create_parser().parse_response(reply)
        except A2uiError as e:
            raise UiError([f"could not parse the <a2ui> block: {e}"]) from e

        prose_raw = "\n\n".join(p.text.strip() for p in parts if isinstance(p, TextPart) and p.text.strip())
        typed = [m for p in parts if isinstance(p, A2uiPart) for m in p.a2ui]

        with macro_context(store, currency_symbol=self._currency_symbol) as ctx:
            prose = fill_prose(prose_raw)
            try:
                expanded = self._expander.transform_to_transport(
                    [_MESSAGE.validate_python(m) if isinstance(m, dict) else m for m in typed]
                )
            except A2uiError as e:
                raise UiError(list(ctx.errors) or [f"invalid UI: {e}"]) from e
            if ctx.errors:
                raise UiError(list(ctx.errors))

        if prose.bare_numbers:
            log.warning("prose has numbers typed by the model: %s", ", ".join(prose.bare_numbers))

        messages = [
            _rewrite_surface(m.model_dump(by_alias=True, exclude_none=True), surface_id) for m in expanded
        ]
        if messages:  # a prose-only reply is valid; the processor rejects an empty list
            validate(messages)
        return UiResult(prose=prose.text, messages=messages, bare_numbers=prose.bare_numbers)
