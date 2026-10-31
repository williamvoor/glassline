"""The switch to the v0.9 basic catalog (ADR 0008; SPEC "2b step 0")."""

import json
from pathlib import Path

import pytest

from a2ui_eval.convert import V0_9_BASIC_CATALOG_ID, ConversionError, v1_to_v0_9
from a2ui_eval.data_cli import DATA
from a2ui_eval.fingerprint import fingerprint
from a2ui_eval.generate import GenerationError, Template, generate_family, load_splits, read_jsonl, wrap
from a2ui_eval.prompt import build_system_prompt
from a2ui_eval.sdk import ExpressSDK
from a2ui_eval.seeds import load_seeds
from tests.conftest import CATALOG

UPSTREAM = Path(__file__).resolve().parents[2] / "a2ui" / "specification" / "v0_9_1" / "catalogs" / "basic" / "catalog.json"
SEEDS = load_seeds()
SPLITS = load_splits(SEEDS)
SINGLE_TURN = sorted(f for f in SEEDS if SPLITS[f] != "excluded")


def _components(messages):
    for m in messages:
        for body in m.values():
            if isinstance(body, dict):
                yield from body.get("components", [])


@pytest.mark.skipif(not UPSTREAM.exists(), reason="upstream A2UI clone (../a2ui) not present")
def test_vendored_catalog_matches_upstream():
    # Byte-for-byte upstream, plus one inserted line.
    added = '  "protocolVersion": "0.9",\n'
    vendored = CATALOG.read_text(encoding="utf-8")
    assert vendored.count(added) == 1
    assert vendored.replace(added, "") == UPSTREAM.read_text(encoding="utf-8")


def test_catalog_loads():
    assert ExpressSDK(CATALOG).catalog_json["catalogId"] == V0_9_BASIC_CATALOG_ID


@pytest.mark.parametrize("family", SINGLE_TURN)
def test_seeds_convert(sdk, family):
    messages = SEEDS[family].messages
    assert all(m["version"] == "v0.9" for m in messages)
    for c in _components(messages):
        assert sdk.component_errors(c) == [], (c["id"], c["component"])  # strict: unknown props fail
    assert sdk.sdk_check(wrap(sdk.decompile(messages)), None) is None


@pytest.mark.parametrize("family", SINGLE_TURN)
def test_fingerprint_version_independent(index, index_v1, family):
    seed = SEEDS[family]
    assert fingerprint(seed.messages, index) == fingerprint(seed.v1_messages, index_v1)


def test_convert_shapes():
    v1 = [{"version": "1.0", "createSurface": {
        "surfaceId": "s", "catalogId": "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json",
        "components": [{"id": "root", "component": "Text", "text": {"@call": "formatString", "args": {"value": {"@path": "/n"}}}}],
        "dataModel": {"n": 1},
    }}, {"version": "1.0", "updateDataModel": {"surfaceId": "s", "path": "/n", "value": 2}}]
    assert v1_to_v0_9(v1) == [
        {"version": "v0.9", "createSurface": {"surfaceId": "s", "catalogId": V0_9_BASIC_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": "s", "components": [
            {"id": "root", "component": "Text", "text": {"call": "formatString", "args": {"value": {"path": "/n"}}}}]}},
        {"version": "v0.9", "updateDataModel": {"surfaceId": "s", "path": "/", "value": {"n": 1}}},
        {"version": "v0.9", "updateDataModel": {"surfaceId": "s", "path": "/n", "value": 2}},
    ]
    assert v1[0]["createSurface"]["components"][0]["text"]["@call"] == "formatString"  # input untouched


@pytest.mark.parametrize("component,prop", [("TextField", "placeholder"), ("Video", "posterUrl"), ("Slider", "steps")])
def test_convert_rejects_v1_only_prop(component, prop):
    msg = {"version": "1.0", "updateComponents": {"surfaceId": "s", "components": [{"id": "x", "component": component, prop: "v"}]}}
    with pytest.raises(ConversionError, match=prop):
        v1_to_v0_9([msg])


@pytest.mark.parametrize("line", [
    'root = TextField("Name", $/name, placeholder="Ann")',
    'root = Video("https://example.com/v.mp4", posterUrl="https://example.com/p.png")',
    'root = Slider($/v, 0, 10, steps=5)',
])
def test_v1_only_props_rejected(sdk, index, line):
    template = Template(family="tiny", slots={"x": [1]}, seed_values={}, intents=["A thing."],
                        express='surface("s")\n' + line + '\n$/name = "a"\n$/v = 1\n')
    with pytest.raises(GenerationError, match="tiny"):
        generate_family(sdk, index, template, "train", 1)


def test_prompt_is_v0_9(sdk):
    prompt = build_system_prompt(sdk)
    # Signatures, not bare words: the Express rules mention "`_` placeholders".
    assert "TextField(label, value?, variant? (static), validationRegexp?" in prompt
    assert "placeholder?" not in prompt and "posterUrl?" not in prompt and "steps?" not in prompt


def test_real_data_is_v0_9(sdk):
    draft = read_jsonl(DATA / "intents_draft.jsonl")
    dev = read_jsonl(DATA / "dev" / "cases.jsonl")
    train = read_jsonl(DATA / "train" / "cases.jsonl")
    assert len(draft) == 29 and all(r["reviewed"] for r in draft)
    assert [r["id"] for r in dev] == [r["id"] for r in draft if r["split"] == "dev"] and len(dev) == 12
    for row in draft + dev + train:
        assert all(m["version"] == "v0.9" for m in row["reference"]), row["id"]
        assert row["reference"][0]["createSurface"]["catalogId"] == V0_9_BASIC_CATALOG_ID, row["id"]
        assert sdk.compile_express(row["reference_express"]) == row["reference"], row["id"]
