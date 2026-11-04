"""struct_hash: the near-duplicate key for validator layer 5."""

import copy

import pytest

from a2ui_eval.catalog_model import CatalogModel
from a2ui_eval.io import read_jsonl
from a2ui_eval.seeds import ROOT
from a2ui_eval.structhash import components_of, struct_hash

DEV = read_jsonl(ROOT / "data" / "dev" / "cases.jsonl")


@pytest.fixture(scope="module")
def model(sdk_v0_9):
    return CatalogModel(sdk_v0_9)


def _with_text(messages, text):
    out = copy.deepcopy(messages)
    for c in components_of(out):
        if c.get("component") == "Text" and isinstance(c.get("text"), str):
            c["text"] = text
    return out


def test_text_changes_keep_the_hash(index, model):
    ref = DEV[0]["reference"]
    assert struct_hash(ref, index, model) == struct_hash(_with_text(ref, "Something else"), index, model)


def test_enum_prop_changes_the_hash(index, model):
    case = next(c for c in DEV if any(x.get("component") == "Text" and "variant" in x for x in components_of(c["reference"])))
    ref = copy.deepcopy(case["reference"])
    t = next(x for x in components_of(ref) if x.get("component") == "Text" and "variant" in x)
    t["variant"] = "caption" if t["variant"] != "caption" else "body"
    assert struct_hash(ref, index, model) != struct_hash(case["reference"], index, model)


def test_dev_families_hash_apart(index, model):
    by_family = {}
    for c in DEV:
        by_family.setdefault(c["family"], set()).add(struct_hash(c["reference"], index, model))
    families = list(by_family)
    for i, a in enumerate(families):
        for b in families[i + 1:]:
            assert not by_family[a] & by_family[b], (a, b)
