"""Finds component references in a component, using the catalog schema.

A property refers to other components when its schema is the common `Child`
(v1) or `ComponentId` (v0.9) for one ID, or `ChildList` (a list of IDs, or a
template object). v1 refers to common types relatively (`common_types.json#/...`),
v0.9 by absolute URL (`https://a2ui.org/specification/v0_9/common_types.json#/...`). They can sit
nested, as in `Tabs.tabs[].child`, so the schema is walked alongside the value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Path = tuple[str | int, ...]


@dataclass(frozen=True)
class Ref:
    path: Path  # where the ID sits inside the component
    target: str
    template_path: str | None = None  # data path when the ref is a list template

    @property
    def slot(self) -> str:
        """The path with list indices dropped, e.g. `tabs[].child`."""
        return path_str(self.path, keep_index=False)


def path_str(path: Path, keep_index: bool = True) -> str:
    out = ""
    for seg in path:
        if isinstance(seg, int):
            out += f"[{seg}]" if keep_index else "[]"
        else:
            out += f".{seg}" if out else seg
    return out


_SINGLE_CHILD = {"Child", "ComponentId"}


def _def_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _is_common(ref: str) -> bool:
    return ref.split("#", 1)[0].rsplit("/", 1)[-1].startswith("common_types")


class RefIndex:
    def __init__(self, catalog_json: dict[str, Any]):
        self._components: dict[str, Any] = catalog_json.get("components", {})
        self._defs: dict[str, Any] = catalog_json.get("$defs", {})

    def component_types(self) -> list[str]:
        return sorted(self._components)

    def refs(self, component: dict[str, Any]) -> list[Ref]:
        schema = self._components.get(component.get("component"))
        out: list[Ref] = []
        if schema is not None:
            self._walk(component, schema, (), out, 0)
        # anyOf/oneOf branches can reach the same slot twice.
        return list({r.path: r for r in out}.values())

    def _walk(self, value: Any, schema: Any, path: Path, out: list[Ref], depth: int) -> None:
        if not isinstance(schema, dict) or depth > 64:
            return
        ref = schema.get("$ref")
        if isinstance(ref, str):
            name = _def_name(ref)
            if _is_common(ref) and name in _SINGLE_CHILD:
                if isinstance(value, str):
                    out.append(Ref(path, value))
                return
            if _is_common(ref) and name == "ChildList":
                if isinstance(value, list):
                    out.extend(Ref(path + (i,), v) for i, v in enumerate(value) if isinstance(v, str))
                elif isinstance(value, dict) and isinstance(value.get("componentId"), str):
                    out.append(Ref(path + ("componentId",), value["componentId"], value.get("path")))
                return
            if ref.startswith("#/$defs/"):
                self._walk(value, self._defs.get(name), path, out, depth + 1)
            # Other common types never hold component references.
        for key in ("allOf", "anyOf", "oneOf"):
            for sub in schema.get(key, []):
                self._walk(value, sub, path, out, depth + 1)
        if isinstance(value, dict):
            for key, sub in schema.get("properties", {}).items():
                if key in value:
                    self._walk(value[key], sub, path + (key,), out, depth + 1)
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                self._walk(item, schema["items"], path + (i,), out, depth + 1)
