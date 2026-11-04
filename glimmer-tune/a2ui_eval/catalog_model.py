"""What the generator knows about the catalog, read from the SDK's own tables.

Slot order, required props, enums and checkability come from
`build_catalog_helpers` (the table the Express compiler uses), so positional order
is never derived from our own walk of the JSON. Function roles come from each
function's `returnType` in the catalog JSON. Nothing about specific components is
hard-coded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from a2ui.inference_formats._shared.catalogs import build_catalog_helpers
from a2ui.inference_formats._shared.schema_helper import CatalogSchemaHelper, is_action_slot

from a2ui_eval.sdk import ExpressSDK

SlotKind = Literal[
    "text",  # DynamicString: literal text or a binding
    "dynamic",  # other Dynamic* types: number, boolean, string list, any
    "static-enum",  # literal from an enum
    "static-literal",  # plain JSON literal (number, boolean, string)
    "child",
    "children",
    "action",
    "checks",
    "map-list",  # array of objects; literal-only (the compiler rejects $path inside)
]
ValueType = Literal["string", "number", "boolean", "string-list", "any"]
FunctionRole = Literal["check", "value", "action"]

_DYNAMIC_TYPES: dict[str, ValueType] = {
    "DynamicString": "string",
    "DynamicNumber": "number",
    "DynamicBoolean": "boolean",
    "DynamicStringList": "string-list",
    "DynamicValue": "any",
}
_ROLES: dict[str, FunctionRole] = {"boolean": "check", "string": "value", "void": "action"}


@dataclass(frozen=True)
class Slot:
    name: str
    required: bool
    kind: SlotKind
    value_type: ValueType | None = None
    enum: tuple[str, ...] = ()
    bindable: bool = False
    item_fields: tuple[str, ...] = ()  # map-list item keys, schema order
    item_required: tuple[str, ...] = ()


@dataclass(frozen=True)
class Component:
    name: str
    slots: tuple[Slot, ...]  # SDK positional order
    checkable: bool

    def slot(self, name: str) -> Slot:
        return next(s for s in self.slots if s.name == name)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.slots if s.required)

    @property
    def optional(self) -> tuple[str, ...]:
        """Optional slots that make up a slot pattern (weight and checks excluded)."""
        return tuple(s.name for s in self.slots if not s.required and s.name not in ("weight", "checks"))


@dataclass(frozen=True)
class Arg:
    name: str
    required: bool
    value_type: ValueType | None  # None for a plain (non-dynamic) literal
    json_type: str | None  # "integer", "number", "string", "array" ... for plain literals
    minimum: float | None = None


@dataclass(frozen=True)
class Function:
    name: str
    role: FunctionRole
    args: tuple[Arg, ...]  # SDK order
    one_of_required: tuple[tuple[str, ...], ...] = ()  # e.g. length: (("min",), ("max",))


def _refs(schema: Any) -> list[str]:
    """Every common-type def name a schema references, through combinators."""
    out: list[str] = []
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            out.append(ref.rsplit("/", 1)[-1])
        for k in ("oneOf", "anyOf", "allOf"):
            for sub in schema.get(k, []) or []:
                out.extend(_refs(sub))
    return out


def _value_type(schema: Any) -> ValueType | None:
    for r in _refs(schema):
        if r in _DYNAMIC_TYPES:
            return _DYNAMIC_TYPES[r]
    return None


def _slot(h: CatalogSchemaHelper, comp: str, prop: str, required: bool) -> Slot:
    schema = h.get_property_schema(comp, prop) or {}
    if prop == "checks" and h.is_checkable(comp):
        return Slot(prop, required, "checks")
    ptype = h.get_property_type(comp, prop)
    if ptype == "ChildList":
        return Slot(prop, required, "children")
    if ptype == "Child":
        return Slot(prop, required, "child")
    if is_action_slot(schema):
        return Slot(prop, required, "action")
    items = schema.get("items")
    if schema.get("type") == "array" and isinstance(items, dict) and items.get("type") == "object":
        return Slot(
            prop, required, "map-list",
            item_fields=tuple(items.get("properties", {})),
            item_required=tuple(items.get("required", [])),
        )
    refs = _refs(schema)
    bindable = any(r in _DYNAMIC_TYPES or r == "DataBinding" for r in refs)
    enum = h.get_property_enum(comp, prop)
    if enum:
        return Slot(prop, required, "static-enum", value_type="string", enum=tuple(enum), bindable=bindable)
    vt = _value_type(schema)
    if vt == "string":
        return Slot(prop, required, "text", value_type="string", bindable=True)
    if vt is not None:
        return Slot(prop, required, "dynamic", value_type=vt, bindable=True)
    jt = schema.get("type")
    return Slot(prop, required, "static-literal", value_type=jt if jt in ("string", "number", "boolean") else "any")


class CatalogModel:
    def __init__(self, sdk: ExpressSDK):
        self.helper = next(iter(build_catalog_helpers([sdk.catalog]).values()))
        h = self.helper
        self.components: dict[str, Component] = {}
        for name in h.components:
            req = set(h.get_component_required(name))
            slots = tuple(_slot(h, name, p, p in req) for p in h.get_component_properties(name))
            self.components[name] = Component(name, slots, h.is_checkable(name))
        self.functions: dict[str, Function] = {}
        for name, schema in sdk.catalog_json.get("functions", {}).items():
            props = schema.get("properties", {})
            ret = (props.get("returnType") or {}).get("const")
            if ret not in _ROLES:
                raise ValueError(f"function {name}: unknown returnType {ret!r}")
            args_schema = props.get("args", {})
            req = set(h.get_function_required(name))
            args = []
            for a in h.get_function_properties(name):
                s = h.get_function_property_schema(name, a) or {}
                args.append(Arg(a, a in req, _value_type(s), s.get("type"), s.get("minimum")))
            one_of = tuple(
                tuple(alt.get("required", [])) for alt in args_schema.get("anyOf", []) if alt.get("required")
            )
            self.functions[name] = Function(name, _ROLES[ret], tuple(args), one_of)

    @property
    def boolean_inputs(self) -> frozenset[str]:
        """Checkable components whose `value` is a boolean. `required` passes on `false`
        (web_core validateRequired), so it can never block one of these."""
        return frozenset(
            c.name for c in self.components.values()
            if c.checkable and any(s.name == "value" and s.value_type == "boolean" for s in c.slots)
        )

    def enum(self, component: str, prop: str) -> tuple[str, ...]:
        return self.components[component].slot(prop).enum

    def functions_by_role(self, role: FunctionRole) -> list[Function]:
        return [f for f in self.functions.values() if f.role == role]
