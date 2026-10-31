"""v1.0 gallery JSON → v0.9 JSON, at the JSON level (ADR 0008).

Never through Express: positional slots follow each catalog's prop order, so the
same Express compiles to different props under v1 and v0.9 (v1 `TextField`'s 4th
slot is `variant`, v0.9's is `validationRegexp`).
"""

from __future__ import annotations

import copy
from typing import Any

V0_9_VERSION = "v0.9"
V0_9_BASIC_CATALOG_ID = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
_V1_CATALOG_MARK = "/v1_0/"

# Props the v1 basic catalog has and v0.9's lacks: {component: {prop}}.
V1_ONLY_PROPS = {"TextField": {"placeholder"}, "Video": {"posterUrl"}, "Slider": {"steps"}}


class ConversionError(Exception):
    pass


def _rename_bindings(value: Any) -> Any:
    if isinstance(value, dict):
        return {("path" if k == "@path" else "call" if k == "@call" else k): _rename_bindings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename_bindings(v) for v in value]
    return value


def _check_components(components: list[dict[str, Any]]) -> None:
    for c in components:
        bad = V1_ONLY_PROPS.get(c.get("component"), set()) & c.keys()
        if bad:
            raise ConversionError(f"component {c.get('id')!r} ({c['component']}) uses v1-only props {sorted(bad)}")


def v1_to_v0_9(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        m = _rename_bindings(copy.deepcopy(message))
        m.pop("version", None)
        (kind, body), *rest = m.items()
        if rest:
            raise ConversionError(f"expected one operation per message, got {list(m)}")
        if kind == "createSurface":
            if _V1_CATALOG_MARK in body.get("catalogId", ""):
                body["catalogId"] = V0_9_BASIC_CATALOG_ID
            components = body.pop("components", None)
            data = body.pop("dataModel", None)
            out.append({"version": V0_9_VERSION, "createSurface": body})
            if components is not None:
                _check_components(components)
                out.append({"version": V0_9_VERSION, "updateComponents": {"surfaceId": body["surfaceId"], "components": components}})
            if data is not None:
                out.append({"version": V0_9_VERSION, "updateDataModel": {"surfaceId": body["surfaceId"], "path": "/", "value": data}})
            continue
        if kind == "updateComponents":
            _check_components(body.get("components", []))
        out.append({"version": V0_9_VERSION, kind: body})
    return out
