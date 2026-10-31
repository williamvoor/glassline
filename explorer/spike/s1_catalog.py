"""Spike risk 1: does a merged basic+VegaChart catalog work through macros and validation?

Run from explorer/agent:  uv run python ../spike/s1_catalog.py
"""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter

from a2ui.builder.core.base_node import ComponentBuilderNode
from a2ui.builder.v0_9.catalogs.basic import Column, Text
from a2ui.core import Catalog
from a2ui.core.basic_catalog import BasicCatalog
from a2ui.core.processing.message_processor import MessageProcessor, MessageProcessorOptions
from a2ui.core.validation.payload_validator import STRICT_VALIDATION
from a2ui.core.schema import AgentToRendererMessage
from a2ui.inference_formats.express import ExpressFormat
from a2ui.parser import A2uiPart
from a2ui.transformers.macros import MacroExpander, macro

HERE = Path(__file__).parent
MERGED_ID = "urn:explorer:catalog:basic-vegachart:v0_9"

ROWS = {"q1": [{"supplier": "Acme", "total": 120.5}, {"supplier": "Beta", "total": 80.0}]}


class VegaChart(ComponentBuilderNode):
    component: Literal["VegaChart"] = "VegaChart"
    spec: dict[str, Any]
    height: int | None = None


@macro
def BarChart(result_id: str, x: str, y: str, title: str) -> Column:
    """Bar chart of a stored query result.

    Args:
        result_id: ID of a stored query result.
        x: Category column.
        y: Numeric column.
        title: Chart title.
    """
    spec = {
        "mark": "bar",
        "data": {"values": ROWS[result_id]},
        "encoding": {
            "x": {"field": x, "type": "nominal"},
            "y": {"field": y, "type": "quantitative"},
        },
    }
    return Column(children=[Text(text=title, variant="h4"), VegaChart(spec=spec)])


def merged_catalog():
    basic = BasicCatalog("0.9.1")
    doc = basic.to_json()
    vega = json.loads((HERE / "vegachart_v0_9_catalog.json").read_text())
    doc["catalogId"] = MERGED_ID
    doc["$id"] = MERGED_ID
    doc["components"] = {**doc["components"], **vega["components"]}
    # Let the union be rebuilt from the components.
    doc.get("$defs", {}).pop("anyComponent", None)
    return Catalog.from_json(doc, protocol_version="0.9")


REPLY = """Here is the chart.
<a2ui>
root = Column([intro, chart])
intro = Text("Top suppliers")
chart = BarChart("q1", "supplier", "total", "Top suppliers by value")
</a2ui>"""


def main():
    cat = merged_catalog()
    print("merged id:", cat.id, "| has VegaChart:", cat.get_component("VegaChart") is not None,
          "| n components:", len(cat.components))

    expander = MacroExpander([BarChart])
    inference = expander.transform_to_inference_catalog(cat)
    fmt = ExpressFormat(catalogs=[inference], surface_id="main", version="v0.9.1")
    snippet = fmt.prompt_generator.generate()
    print("snippet chars:", len(snippet))

    parts = fmt.create_parser().parse_response(REPLY)
    msgs = [m for p in parts if isinstance(p, A2uiPart) for m in p.a2ui]
    typed = [TypeAdapter(AgentToRendererMessage).validate_python(m) for m in msgs]
    transport = expander.transform_to_transport(typed)
    dumped = [m.model_dump(by_alias=True, exclude_none=True) for m in transport]
    create = next(m for m in dumped if "createSurface" in m)
    comps = next(m for m in dumped if "updateComponents" in m)["updateComponents"]["components"]
    print("createSurface catalogId:", create["createSurface"]["catalogId"])
    print("component types:", sorted({c["component"] for c in comps}))

    # Validate the transport messages the way a renderer would, against the
    # merged catalog only (no macros).
    proc = MessageProcessor(catalogs=[cat], options=MessageProcessorOptions(validation_config=STRICT_VALIDATION))
    proc.process_messages(dumped)
    print("python MessageProcessor accepted transport messages (STRICT_VALIDATION)")

    (HERE / "s1_transport.json").write_text(json.dumps(dumped, indent=2))


if __name__ == "__main__":
    main()
