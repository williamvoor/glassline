"""The merged explorer catalog: basic v0.9 + VegaChart + DataGrid + SqlDetails (ADR 0002)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Literal

from a2ui.builder.core.base_node import ComponentBuilderNode
from a2ui.core import Catalog
from a2ui.core.basic_catalog import BasicCatalog

EXPLORER_CATALOG_ID = "urn:explorer:catalog:v1"
CUSTOM_COMPONENTS_PATH = Path(__file__).resolve().parents[3] / "catalog" / "explorer_custom_components.json"


def custom_components() -> dict[str, Any]:
    return json.loads(CUSTOM_COMPONENTS_PATH.read_text(encoding="utf-8"))["components"]


@cache
def explorer_catalog() -> Catalog:
    doc = BasicCatalog("0.9.1").to_json()
    doc["catalogId"] = EXPLORER_CATALOG_ID
    doc["$id"] = EXPLORER_CATALOG_ID
    doc["components"] = {**doc["components"], **custom_components()}
    # Let the component union be rebuilt from the merged components.
    doc.get("$defs", {}).pop("anyComponent", None)
    return Catalog.from_json(doc, protocol_version="0.9")


class VegaChart(ComponentBuilderNode):
    component: Literal["VegaChart"] = "VegaChart"
    spec: dict[str, Any]
    height: int | None = None
    action: dict[str, Any] | None = None  # {source, rows, drillable}: clickable marks (SPEC §15)


class DataGrid(ComponentBuilderNode):
    component: Literal["DataGrid"] = "DataGrid"
    columns: list[dict[str, str]]
    rows: list[list[str]]
    caption: str | None = None
    action: dict[str, Any] | None = None  # {source, rows, drillable}: clickable rows (SPEC §15)


class SqlDetails(ComponentBuilderNode):
    component: Literal["SqlDetails"] = "SqlDetails"
    sql: str
    summary: str | None = None
