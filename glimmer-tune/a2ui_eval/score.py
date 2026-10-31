"""Scores one model response against a case's reference."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .align import SlotScore, score_components
from .refs import RefIndex
from .sdk import CLOSE_TAG, CompileError, ExpressSDK
from .structure import bound_paths, check_structure, unresolved_bindings
from .surfaces import CREATE, DELETE, SurfaceTurn, collect, fold

Messages = list[dict[str, Any]]


def close_stopped_block(text: str, finish_reason: str | None, sdk: ExpressSDK) -> str:
    """Puts back the `</a2ui>` that a stop sequence removed.

    Without it the SDK treats the block as cut off and skips its tree checks.
    A response cut off by the token limit stays as it is.
    """
    if finish_reason != "stop":
        return text
    _, blocks = sdk.unwrap(text)
    if blocks and not blocks[-1].is_final:
        return text + ("" if text.endswith("\n") else "\n") + CLOSE_TAG
    return text


def _data_triples(turn: SurfaceTurn, name: str) -> Counter:
    out: Counter = Counter()
    out[("$op", turn.op, name)] += 1

    def flatten(prefix: str, value: Any) -> None:
        if isinstance(value, dict) and value:
            for k, v in value.items():
                flatten(f"{prefix.rstrip('/')}/{k}", v)
        elif isinstance(value, list) and value:
            for i, v in enumerate(value):
                flatten(f"{prefix.rstrip('/')}/{i}", v)
        else:
            out[("$data", prefix or "/", json.dumps(value, sort_keys=True))] += 1

    for path, value in turn.data_ops:
        flatten(path, value)
    return out


def pair_surfaces(pred: dict[str, SurfaceTurn], ref: dict[str, SurfaceTurn], existing_ids: set[str]) -> dict[str, str]:
    """{pred_surface_id: ref_surface_id}. Existing surfaces must be named exactly;
    a new surface's ID is a free choice, so new ones pair in order."""
    mapping = {sid: sid for sid in pred if sid in ref}
    free_p = [s for s in pred if s not in mapping and s not in existing_ids and pred[s].op == CREATE]
    free_r = [s for s in ref if s not in mapping.values() and s not in existing_ids and ref[s].op == CREATE]
    mapping.update(zip(free_p, free_r))
    return mapping


def _empty_result(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case["id"],
        "expect_ui": case.get("expect_ui", True),
        "extracted": False,
        "truncated": False,
        "compiles": False,
        "compile_errors": [],
        "sdk_valid": False,
        "sdk_error": None,
        "schema_valid": False,
        "schema_errors": [],
        "structure_valid": False,
        "structure": {},
        "structure_error_count": 0,
        "bindings_valid": False,
        "unresolved_bindings": [],
        "slot": SlotScore().to_dict(),
        "slot_by_type": {},
        "exact_match": False,
        "surface_id_errors": [],
        "components_used": [],
    }


def score_response(
    sdk: ExpressSDK,
    index: RefIndex,
    case: dict[str, Any],
    text: str | None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    result = _empty_result(case)
    expect_ui = case.get("expect_ui", True)
    reference: Messages = case.get("reference") or []
    existing: Messages = case.get("existing_surface") or []

    if text is None:  # request failed
        result["compile_errors"] = [{"kind": "NoResponse", "message": "no response"}]
        return result

    text = close_stopped_block(text, finish_reason, sdk)
    _, blocks = sdk.unwrap(text)
    result["truncated"] = bool(blocks) and not blocks[-1].is_final

    if not expect_ui:
        result["extracted"] = not blocks
        if not blocks:
            ok = {k: True for k in ("compiles", "sdk_valid", "schema_valid", "structure_valid", "bindings_valid", "exact_match")}
            result.update(ok, slot=SlotScore().to_dict())
            return result
    else:
        result["extracted"] = bool(blocks)
        if not blocks:
            result["slot"] = SlotScore(n_ref=_ref_triple_count(index, reference)).to_dict()
            return result

    predicted: Messages = []
    for block in blocks:
        try:
            predicted.extend(sdk.compile(block))
        except CompileError as e:
            result["compile_errors"].append({"kind": e.kind, "message": e.message})
    result["compiles"] = not result["compile_errors"]
    if not result["compiles"]:
        result["slot"] = SlotScore(n_ref=_ref_triple_count(index, reference)).to_dict()
        return result

    result["sdk_error"] = sdk.sdk_check(text, existing)
    result["sdk_valid"] = result["sdk_error"] is None

    pred_turns, ref_turns = collect(predicted), collect(reference)
    before = fold(existing)
    after_pred, after_ref = fold(predicted, before), fold(reference, before)

    schema_errors = []
    used: set[str] = set()
    for turn in pred_turns.values():
        for cid, comp in turn.components.items():
            used.add(str(comp.get("component")))
            schema_errors += [f"{turn.surface_id}/{cid}: {msg}" for msg in sdk.component_errors(comp)]
    result["schema_errors"] = schema_errors
    result["schema_valid"] = not schema_errors
    result["components_used"] = sorted(used)

    existing_ids = set(before)
    surface_map = pair_surfaces(pred_turns, ref_turns, existing_ids)

    structure: dict[str, Any] = {}
    unresolved: list[list[str]] = []
    for sid, turn in pred_turns.items():
        if turn.op == DELETE or sid not in after_pred:
            continue
        merged = after_pred[sid].components
        report = check_structure(merged, index)
        if report.error_count:
            structure[sid] = report.to_dict()
            result["structure_error_count"] += report.error_count
        ref_state = after_ref.get(surface_map.get(sid, ""))
        allowed = bound_paths(ref_state.components, index) if ref_state else set()
        models = [after_pred[sid].data] + ([ref_state.data] if ref_state else [])
        unresolved += [[sid, c, p] for c, p in unresolved_bindings(turn.components, merged, index, models, allowed)]
    result["structure"] = structure
    result["structure_valid"] = not structure
    result["unresolved_bindings"] = unresolved
    result["bindings_valid"] = not unresolved

    result["surface_id_errors"] = (
        [f"unexpected surface {s}" for s in pred_turns if s not in surface_map]
        + [f"missing surface {s}" for s in ref_turns if s not in surface_map.values()]
    )

    total = SlotScore()
    by_type: dict[str, SlotScore] = {}
    exact = not result["surface_id_errors"]
    for sid, turn in pred_turns.items():
        rsid = surface_map.get(sid)
        rturn = ref_turns.get(rsid) if rsid else None
        anchors = frozenset(before[rsid].components) if rsid in before else frozenset()
        aligned = score_components(turn.components, rturn.components if rturn else {}, index, anchors)
        total.add(aligned.score)
        for t, s in aligned.per_type.items():
            by_type.setdefault(t, SlotScore()).add(s)
        pd = _data_triples(turn, rsid or f"?{sid}")
        rd = _data_triples(rturn, rsid) if rturn else Counter()
        total.add(SlotScore(tp=sum((pd & rd).values()), n_pred=sum(pd.values()), n_ref=sum(rd.values())))
        exact = exact and aligned.exact and pd == rd
    for rsid, rturn in ref_turns.items():
        if rsid in surface_map.values():
            continue
        missing = score_components({}, rturn.components, index)
        total.add(missing.score)
        total.n_ref += sum(_data_triples(rturn, rsid).values())
        for t, s in missing.per_type.items():
            by_type.setdefault(t, SlotScore()).add(s)

    result["slot"] = total.to_dict()
    result["slot_by_type"] = {t: s.to_dict() for t, s in sorted(by_type.items())}
    result["exact_match"] = exact and result["sdk_valid"]
    return result


def _ref_triple_count(index: RefIndex, reference: Messages) -> int:
    total = 0
    for sid, turn in collect(reference).items():
        total += score_components({}, turn.components, index).score.n_ref
        total += sum(_data_triples(turn, sid).values())
    return total
