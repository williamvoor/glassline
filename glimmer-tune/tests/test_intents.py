"""Phase 2 intent pipeline (SPEC "Approach v2 Phase 2"). No model or network: calls are stubbed."""

from __future__ import annotations

import asyncio
import json
import random
from collections import Counter

import pytest

from a2ui_eval.intents import checks, content as C, pipeline as P, wording
from a2ui_eval.intents.bank import BankError, extract_json, parse_domain, parse_entity


PATIENTS = [  # Patient, Ward, Admitted, Weight, Insured, Status
    ("Ana Ruiz", "4B", "3 Nov 2026", "61 kg", "Yes", "Stable"),
    ("Ben Cole", "5A", "1 Nov 2026", "75 kg", "No", "Critical"),
    ("Cy Hart", "ICU", "30 Oct 2026", "82 kg", "Yes", "Critical"),
    ("Di Lane", "4B", "28 Oct 2026", "58 kg", "Yes", "Discharged"),
    ("Ed Moss", "2C", "2 Nov 2026", "90 kg", "No", "Stable"),
    ("Flo Park", "3D", "29 Oct 2026", "67 kg", "Yes", "Stable"),
    ("Gus Bell", "1A", "31 Oct 2026", "71 kg", "No", "Discharged"),
    ("Hal Kim", "5A", "27 Oct 2026", "88 kg", "Yes", "Critical"),
    ("Ida Fox", "2C", "26 Oct 2026", "54 kg", "Yes", "Stable"),
    ("Jo Webb", "ICU", "25 Oct 2026", "79 kg", "No", "Critical"),
    ("Kai Lund", "3D", "24 Oct 2026", "93 kg", "Yes", "Discharged"),
    ("Lea Ross", "1A", "23 Oct 2026", "60 kg", "No", "Stable"),
    ("Max Ortiz", "4B", "22 Oct 2026", "84 kg", "Yes", "Stable"),
    ("Nia Shaw", "5A", "21 Oct 2026", "66 kg", "Yes", "Critical"),
]


def entity_raw(name="patient", status=True):
    cols = [
        {"label": "Patient", "type": "text"},
        {"label": "Ward", "type": "text"},
        {"label": "Admitted", "type": "date"},
        {"label": "Weight", "type": "number"},
        {"label": "Insured", "type": "bool", "options": ["Yes", "No"]},
    ]
    if status:
        cols.append({"label": "Status", "type": "status", "options": ["Stable", "Critical", "Discharged", "Transferred"]})
    labels = [c["label"] for c in cols]
    return {
        "name": name, "plural": name + "s", "columns": cols,
        "records": [dict(zip(labels, rec)) for rec in PATIENTS],
        "metrics": [{"label": lab, "samples": [f"{i}{suffix}" for i in range(10, 16)]}
                    for lab, suffix in (("Beds occupied", " of 50"), ("Average stay", " days"), ("Admissions today", ""))],
        "actions": ["Discharge", "Transfer", "Add note"],
        "create_action": "Admit patient",
    }


def domain_raw(name="Hospital ward", status=True):
    return {"name": name, "entities": [entity_raw("patient", status), entity_raw("nurse", status)]}


@pytest.fixture
def domains():
    return [parse_domain(domain_raw("Hospital ward"), "model:x"), parse_domain(domain_raw("Bike shop"), "model:x")]


# ---------------------------------------------------------------- bank

def test_bank_parses_a_valid_domain():
    d = parse_domain(domain_raw(), "model:x")
    assert d.slug == "hospital-ward" and len(d.entities) == 2
    e = d.entities[0]
    assert e.columns[0].label == "Patient" and [c.label for c in e.statuses] == ["Status"]


def _drop_column(e, label):
    e["columns"] = [c for c in e["columns"] if c["label"] != label]
    for r in e["records"]:
        r.pop(label, None)


@pytest.mark.parametrize("mutate, message", [
    (lambda e: e.update(records=e["records"][:11]), "records, need"),
    (lambda e: e["records"][2].update(Patient="Ana Ruiz"), "naming column repeats"),
    (lambda e: e["records"][1].pop("Ward"), "missing Ward"),
    (lambda e: e["records"][1].update(Ward="  "), "missing Ward"),
    (lambda e: e["records"][1].update(Status="Asleep"), "not in its options"),
    (lambda e: e["columns"][5].update(options=["Stable"]), "options"),
    (lambda e: e["columns"][1].update(type="enum"), "not in"),
    (lambda e: e["columns"].insert(0, e["columns"].pop(2)), "must name the record"),
    (lambda e: e["columns"][2].update(label="ward"), "duplicate column labels"),
    (lambda e: e.update(metrics=e["metrics"][:2]), "metrics, need"),
    (lambda e: e.update(actions=[]), "actions"),
    (lambda e: e.pop("create_action"), "create_action"),
    (lambda e: [_drop_column(e, x) for x in ("Weight", "Insured", "Status")], "columns, need"),
])
def test_bank_rejects_bad_entities(mutate, message):
    e = entity_raw()
    mutate(e)
    with pytest.raises(BankError, match=message):
        parse_entity(e, "d")


def test_extract_json_ignores_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"a": [1]}\n```\nDone') == {"a": [1]}
    assert extract_json('["x", "y"] trailing') == ["x", "y"]
    with pytest.raises(BankError):
        extract_json("no json here")


def stub(replies):
    """A Complete that answers from `replies(request)` and records every request."""
    seen = []

    async def complete(req):
        seen.append(req)
        out = replies(req)
        if isinstance(out, Exception):
            raise out
        return {"content": out, "prompt_tokens": 100, "completion_tokens": 50, "finish_reason": "stop"}

    complete.seen = seen
    return complete


def test_draft_bank_keeps_valid_domains_and_logs_rejects():
    def replies(req):
        text = req["messages"][-1]["content"]
        if text.startswith("List "):
            return json.dumps(["Hospital ward", "Bike shop", "Broken"])
        if "Bike shop" in text:
            return json.dumps({**domain_raw("Cycle store"), "name": "Cycle store"})  # reworded name
        if "Broken" in text:
            return '{"name": "Broken", "entities": [{"name": "x"}]}'
        return json.dumps(domain_raw("Hospital ward"))

    complete = stub(replies)
    domains, rejects, cost = asyncio.run(P.draft_bank(complete, 3))
    assert [d.slug for d in domains] == ["hospital-ward", "bike-shop"]  # the listed name wins
    assert [r["name"] for r in rejects] == ["Broken"]
    assert all(d.provenance == P.MODEL_TAG for d in domains)
    assert cost > 0 and all(r["reasoning_effort"] == "low" for r in complete.seen)


# ---------------------------------------------------------------- content

def _bank_strings(domains):
    out = set()
    for d in domains:
        for e in d.entities:
            for c in e.columns:
                out |= {c.label, *c.samples, *c.options}
            for m in e.metrics:
                out |= {m.label, *m.samples}
            out |= set(e.actions) | {e.create_action}
    return out


def test_content_is_deterministic(domains):
    assert P.build_content(7, 60, domains) == P.build_content(7, 60, domains)
    assert P.build_content(7, 60, domains) != P.build_content(8, 60, domains)


def test_content_only_uses_bank_text(domains):
    """The script picks bank text and never writes words of its own (provenance, ADR 0033)."""
    bank = _bank_strings(domains)
    for row in P.build_content(1, 200, domains):
        assert set(C.required_strings(row["content"])) <= bank, row


def test_content_follows_the_mix(domains):
    rows = P.build_content(3, 500, domains)
    kinds = Counter(r["content"]["kind"] for r in rows)
    assert set(kinds) == set(C.KIND_MIX)
    for k, w in C.KIND_MIX.items():
        assert abs(kinds[k] - 5 * w) <= 2, (k, kinds[k])


def test_update_rows_point_at_earlier_parent_rows(domains):
    rows = P.build_content(5, 200, domains)
    position = {r["id"]: i for i, r in enumerate(rows)}
    updates = [r for r in rows if r["content"]["kind"] == "update"]
    assert updates
    for r in updates:
        parent = r["content"]["parent"]
        assert position[parent] < position[r["id"]]
        assert rows[position[parent]]["content"]["kind"] in C.PARENT_KINDS


def test_kinds_needing_a_status_column_skip_entities_without_one():
    d = parse_domain(domain_raw(status=False), "model:x")
    rng = random.Random(0)
    assert C.build("filter", rng, d, d.entities[0]) is None
    assert C.build("state", rng, d, d.entities[0]) is None


def test_state_highlight_value_is_one_of_its_rows(domains):
    rng = random.Random(0)
    for _ in range(50):
        c = C.build("state", rng, domains[0], domains[0].entities[0])
        if c:
            col = c["columns"].index(c["highlight"]["column"])
            assert c["highlight"]["value"] in {r[col] for r in c["rows"]}


def test_plan_hits_the_total_exactly():
    for total in (1, 7, 99, 1000):
        assert len(C.plan(random.Random(0), total)) == total


# ---------------------------------------------------------------- checks

@pytest.fixture(scope="module")
def vocab(sdk):
    return checks.catalog_jargon(sdk.catalog_json)


@pytest.mark.parametrize("text, hit", [
    ("Put the names in a Column on the left", "Column"),
    ("Spread them out with spaceBetween", "spaceBetween"),
    ("Make the title an h1", "h1"),
    ("Please justify it to the right", "justify"),
    ("Bind the list to /patients", "/patients"),
    ("Use `name` for the label", "`"),
    ("Show a calendarToday icon", "calendarToday"),
])
def test_register_flags_catalog_words(vocab, text, hit):
    assert hit in checks.register(text, [], *vocab)


@pytest.mark.parametrize("text, required", [
    ("List my patients with their ward.", []),
    ("Text me a summary: no wait, show it on screen.", []),
    ("A table with \"Weight\" and \"Status\" columns", ["Weight", "Status"]),
    ("Link to https://example.com/patients please", []),
    ("Admitted 03/11/2026, a table of them side by side", []),
])
def test_register_passes_everyday_wording(vocab, text, required):
    assert checks.register(text, required, *vocab) == []


def test_missing_is_case_and_space_insensitive():
    assert checks.missing("Show  ana ruiz in 4b", ["Ana Ruiz", "4B", "ICU"]) == ["ICU"]


def test_near_dup_rejects_references_and_repeats():
    d = checks.NearDup(["Show my calendar for Monday with the gym and the dentist"], t=0.35)
    assert d.check("Show my calendar for Monday with the gym and the dentist please")[0] == "reference"
    assert d.check("A table of patients by ward with their admission date") is None
    assert d.check("A table of patients by ward with their admission dates")[0] == "set"
    assert d.check("Sales figures for the bike shop this week") is None


# ---------------------------------------------------------------- wording

def test_parse_takes_exactly_one_intent_block():
    assert wording.parse("<think>x</think><intent> Show it </intent>") == "Show it"
    assert wording.parse("no block") is None
    assert wording.parse("<intent>a</intent><intent>b</intent>") is None
    assert wording.parse("<intent>  </intent>") is None


def test_messages_carry_style_and_hide_bookkeeping(domains):
    row = P.build_content(0, 30, domains)[0]
    msgs = wording.messages(row["content"], "terse")
    assert msgs[0]["role"] == "system" and msgs[1]["content"].endswith(": terse.")
    assert '"domain"' not in msgs[1]["content"] and '"kind"' not in msgs[1]["content"]


def _echo_reply(content_rows):
    """A stub wording model that states every required string (a perfect R2 answer)."""
    by_spec = {}
    for r in content_rows:
        by_spec[json.dumps(wording.spec_for_model(r["content"]), ensure_ascii=False, indent=1)] = r

    def replies(req):
        user = req["messages"][-1]["content"]
        spec = user.split("Spec:\n", 1)[1].rsplit("\n\nStyle of the message", 1)[0]
        r = by_spec[spec]
        return "<intent>I'd like " + "; ".join(f'"{s}"' for s in C.required_strings(r["content"])) + f" ({r['id']})</intent>"

    return replies


def test_word_rows_is_resumable_and_retries_errors(tmp_path, domains):
    rows = P.build_content(0, 12, domains)
    out = tmp_path / "worded.jsonl"
    calls = {"n": 0}
    echo = _echo_reply(rows)

    def flaky(req):
        calls["n"] += 1
        return RuntimeError("boom") if calls["n"] == 1 else echo(req)

    s1 = asyncio.run(P.word_rows(rows, out, stub(flaky), seed=0, workers=1))
    assert s1["asked"] == 12 and s1["errors"] == 1
    s2 = asyncio.run(P.word_rows(rows, out, stub(echo), seed=0))
    assert s2["asked"] == 1 and s2["errors"] == 0
    s3 = asyncio.run(P.word_rows(rows, out, stub(echo), seed=0))
    assert s3["asked"] == 0


def test_styles_are_seeded_per_row(tmp_path, domains):
    rows = P.build_content(0, 20, domains)
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    asyncio.run(P.word_rows(rows, a, stub(_echo_reply(rows)), seed=0))
    asyncio.run(P.word_rows(rows, b, stub(_echo_reply(rows)), seed=0))
    assert [r["style"] for r in P.read_jsonl(a)] == [r["style"] for r in P.read_jsonl(b)]
    assert len({r["style"] for r in P.read_jsonl(a)}) > 1


# ---------------------------------------------------------------- checks and assembly

def _worded(rows, text_for):
    return [{"id": r["id"], "intent": text_for(r), "style": "terse", "finish": "stop"} for r in rows]


def _perfect(r):
    return "I'd like " + "; ".join(f'"{s}"' for s in C.required_strings(r["content"])) + f" ({r['id']})"


def test_check_rows_reports_each_failure(sdk, domains):
    rows = P.build_content(0, 40, domains)[:5]
    texts = {
        rows[0]["id"]: _perfect(rows[0]),
        rows[1]["id"]: "Show me something",  # R2
        rows[2]["id"]: _perfect(rows[2]) + " in a Column, please",  # register
        rows[3]["id"]: None,  # unparsed
        rows[4]["id"]: _perfect(rows[0]),  # near-duplicate of row 0
    }
    verdicts = P.check_rows(rows, _worded(rows, lambda r: texts[r["id"]]), [], sdk.catalog_json)
    got = [v["verdict"] for v in verdicts]
    assert got[:4] == ["ok", "r2_missing", "register", "unparsed"]
    assert got[4] in ("near_dup_set", "r2_missing")  # rows 0 and 4 may differ in required strings
    ref = P.check_rows(rows[:1], _worded(rows[:1], _perfect), [_perfect(rows[0])], sdk.catalog_json)
    assert ref[0]["verdict"] == "near_dup_reference"


def test_assemble_splits_and_records_provenance(sdk, domains):
    rows = P.build_content(2, 400, domains)
    worded = _worded(rows, _perfect)
    verdicts = P.check_rows(rows, worded, [], sdk.catalog_json, t=1.01)
    assert {v["verdict"] for v in verdicts} == {"ok"}
    out = P.assemble(rows, worded, verdicts, seed=0, content_tag="script:x@abc", tune_size=60)
    tune = [r for r in out if r["split"] == "tune"]
    assert 50 <= len(tune) <= 70
    assert not any(r["kind"] == "update" for r in tune)
    parents = {r["parent"] for r in out if r["parent"]}
    assert not any(r["id"] in parents for r in tune)
    for r in out:
        assert r["provenance"] == {"intent": P.MODEL_TAG, "content": "script:x@abc", "schema": "model:x"}
        assert r["expect_ui"] == (r["kind"] != "text_only")


def test_assemble_drops_updates_whose_parent_failed(sdk, domains):
    rows = P.build_content(5, 200, domains)
    upd = next(r for r in rows if r["content"]["kind"] == "update")
    parent = upd["content"]["parent"]
    worded = _worded(rows, lambda r: "nope" if r["id"] == parent else _perfect(r))
    verdicts = P.check_rows(rows, worded, [], sdk.catalog_json, t=1.01)
    ids = {r["id"] for r in P.assemble(rows, worded, verdicts, 0, "script:x")}
    assert parent not in ids and upd["id"] not in ids


def test_train_domains_hold_out_the_slice(domains):
    assert [d.slug for d in P.train_domains(domains, ["bike-shop"])] == ["hospital-ward"]
    assert P.pick_slice_domains(domains, 1, 0) == P.pick_slice_domains(domains, 1, 0)


def test_bank_accepts_repeated_values_outside_the_naming_column():
    """Regression (2026-11-04 bank draft): 30 of 48 domains were rejected because one column had
    4-5 distinct values (a clinic with 4 vets). Only the naming column must be distinct."""
    e = entity_raw()
    for r in e["records"]:
        r["Ward"] = "4B"
    assert parse_entity(e, "d").column("Ward").samples == ("4B",)


def test_draft_bank_keeps_raw_replies_of_rejects():
    def replies(req):
        text = req["messages"][-1]["content"]
        if text.startswith("List "):
            return json.dumps(["Broken"])
        return '{"name": "Broken", "entities": [{"name": "x"}]}'

    _, rejects, _ = asyncio.run(P.draft_bank(stub(replies), 1))
    assert rejects[0]["raw"] == '{"name": "Broken", "entities": [{"name": "x"}]}'


# ---------------------------------------------------------------- 2026-11-04 sample review fixes

def _record_rows(domains):
    return {(d.slug, e.name): {r for r in e.records} for d in domains for e in d.entities}


def _project(entity, labels, record):
    return [record[entity.columns.index(entity.column(lab))] for lab in labels]


def test_content_rows_are_whole_bank_records(domains):
    """Fix 1: columns sampled independently gave "The Great Gatsby | J.R.R. Tolkien | 1813".
    Every row (and a detail's record) is one bank record projected onto the chosen columns."""
    ents = {(d.slug, e.name): e for d in domains for e in d.entities}
    for row in P.build_content(4, 300, domains):
        c = row["content"]
        e = ents[(c["domain"], c["entity"])]
        projections = [_project(e, c["columns"], r) for r in e.records] if c.get("columns") else []
        for r in c.get("rows") or []:
            assert r in projections, (row["id"], r)
        if c.get("rows"):
            assert len({r[0] for r in c["rows"]}) == len(c["rows"])
        if c["kind"] == "detail":
            labels = [lab for lab, _ in c["record"]]
            assert [v for _, v in c["record"]] in [_project(e, labels, r) for r in e.records]


def test_update_values_come_from_the_parents_records(domains):
    ents = {(d.slug, e.name): e for d in domains for e in d.entities}
    rows = P.build_content(6, 600, domains)
    by_id = {r["id"]: r["content"] for r in rows}
    seen = Counter()
    for row in rows:
        c = row["content"]
        if c["kind"] != "update":
            continue
        e, parent = ents[(c["domain"], c["entity"])], by_id[c["parent"]]
        seen[c["op"]] += 1
        if c["op"] == "add_column":
            rec = {r[0]: r for r in e.records}
            assert c["values"] == [_project(e, [c["column"]], rec[pr[0]])[0] for pr in parent["rows"]]
        if c["op"] == "add_row":
            assert c["row"] in [_project(e, parent["columns"], r) for r in e.records]
            assert c["row"][0] not in {pr[0] for pr in parent["rows"]}
    assert seen["add_column"] and seen["add_row"]


def test_form_submit_is_the_create_action(domains):
    """Fix 2: a new-record form got a per-record action ("Cancel Booking") as its button."""
    ents = {(d.slug, e.name): e for d in domains for e in d.entities}
    forms = [r["content"] for r in P.build_content(0, 300, domains) if r["content"]["kind"] == "form"]
    assert forms and any(f["submit"] for f in forms)
    for f in forms:
        assert f["submit"] in (None, ents[(f["domain"], f["entity"])].create_action)


def test_prompt_names_records_naturally_and_labels_the_style(domains):
    """Fix 3: Spark Open parroted the spec keys ("all my 'members' where each one is a 'member'")
    and took the style ("a short intro line") as screen content."""
    for row in P.build_content(0, 60, domains):
        user = wording.messages(row["content"], "a short intro line")[1]["content"]
        assert '"entity"' not in user and '"plural"' not in user, row["id"]
        assert row["content"]["plural"] in user.split("Spec:")[0]
        assert "Style of the message itself" in user


def test_layout_hint_is_an_instruction_not_required_text(domains):
    """Fix 4: every list came out as a table. List rows always carry a layout instruction; it is
    a direction to the wording model, never a string the request must contain."""
    rows = [r["content"] for r in P.build_content(0, 400, domains)]
    lists = [c for c in rows if c["kind"] == "list"]
    assert lists and all(c.get("layout") for c in lists)
    assert any(c.get("layout") for c in rows if c["kind"] in ("detail", "stats", "dashboard"))
    for c in rows:
        if c.get("layout"):
            assert c["layout"] not in C.required_strings(c)
            assert "Layout: " + c["layout"] in wording.messages(c, "terse")[1]["content"]
            assert '"layout"' not in wording.messages(c, "terse")[1]["content"]


# ---------------------------------------------------------------- 2026-11-04 second sample fixes

def _with_category(e):
    e["columns"].insert(2, {"label": "Specialty", "type": "category", "options": ["Cardiology", "Oncology", "General"]})
    for r, spec in zip(e["records"], ["Cardiology", "Oncology", "General"] * 5):
        r["Specialty"] = spec
    return e


def test_highlight_uses_status_columns_and_filter_may_use_categories():
    """Second sample: a highlight picked "Service Type" (fixed values, not a state). Only status
    columns are highlighted; a filter may use a status or a category column."""
    d = parse_domain({"name": "Ward", "entities": [_with_category(entity_raw())]}, "model:x")
    e = d.entities[0]
    assert [c.label for c in e.statuses] == ["Status"]
    assert {c.label for c in e.choices} == {"Status", "Specialty"}
    rng = random.Random(0)
    filters = set()
    for _ in range(200):
        if c := C.build("state", rng, d, e):
            assert c["highlight"]["column"] == "Status"
        if c := C.build("filter", rng, d, e):
            filters.add(c["filter"]["label"])
    assert filters == {"Status", "Specialty"}


def test_hint_names_the_domain(domains):
    """Second sample: a fitness studio's members became a "book club" with the domain hidden."""
    row = P.build_content(0, 10, domains)[0]["content"]
    name = next(d.name for d in domains if d.slug == row["domain"])
    row = {**row, "domain_name": name}
    assert name in wording.messages(row, "terse")[1]["content"].split("Spec:")[0]


def test_content_carries_the_domain_name(domains):
    names = {d.slug: d.name for d in domains}
    for r in P.build_content(0, 40, domains):
        assert r["content"]["domain_name"] == names[r["content"]["domain"]]


@pytest.mark.parametrize("text, copied", [
    ("I want it as a stacked list with the name on its own line and the other values under it", True),
    ("Show each one stacked: name on top, details below it", False),
])
def test_layout_copy_check(text, copied):
    """Second sample: Spark Open pasted the layout instruction word for word."""
    layout = "a stacked list with the name on its own line and the other values under it; not a table"
    assert bool(checks.copied(text, layout)) is copied


def test_check_rows_rejects_a_copied_layout(sdk, domains):
    rows = [r for r in P.build_content(0, 200, domains) if r["content"].get("layout")][:1]
    text = _perfect(rows[0]) + " As " + rows[0]["content"]["layout"]
    (v,) = P.check_rows(rows, _worded(rows, lambda r: text), [], sdk.catalog_json)
    assert v["verdict"] == "layout_copied"


def test_register_allows_everyday_inflections_of_short_jargon(vocab):
    """500-row run: "where it fits" was rejected as the prop `fit`. Inflections count only for
    longer words ("stretched"), not for short everyday ones."""
    assert checks.register("And where it fits put two fields side by side", [], *vocab) == []
    assert "stretch" in checks.register("Make the button stretched across", [], *vocab)
    assert "fit" in checks.register("Set the image fit to cover", [], *vocab)


def test_openers_are_seeded_and_varied(tmp_path, domains):
    """E1: 253 of 532 rows opened with "Hey" or "Hi". Each prompt now carries a seeded opener."""
    rows = P.build_content(0, 40, domains)
    out = tmp_path / "w.jsonl"
    complete = stub(_echo_reply(rows))
    asyncio.run(P.word_rows(rows, out, complete, seed=0))
    openers = [next(o for o in wording.OPENERS if f"Open the message by {o}" in r["messages"][-1]["content"])
               for r in complete.seen]
    assert len(set(openers)) > 2
    again = stub(_echo_reply(rows))
    asyncio.run(P.word_rows(rows, tmp_path / "w2.jsonl", again, seed=0))
    assert [r["messages"][-1]["content"] for r in again.seen] == [r["messages"][-1]["content"] for r in complete.seen]


def test_word_rows_keeps_replies_when_the_run_dies(tmp_path, domains):
    """A 2468-row run writes nothing until it ends, so a crash would lose every paid reply.
    Replies are appended as they arrive and picked up by the next run."""
    rows = P.build_content(0, 10, domains)
    out = tmp_path / "w.jsonl"
    echo = _echo_reply(rows)
    calls = {"n": 0}

    def dies(req):
        calls["n"] += 1
        if calls["n"] > 4:
            raise KeyboardInterrupt
        return echo(req)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(P.word_rows(rows, out, stub(dies), seed=0, workers=1))
    again = stub(echo)
    s = asyncio.run(P.word_rows(rows, out, again, seed=0))
    assert s["asked"] == 6 and len(P.read_jsonl(out)) == 10


# ---------------------------------------------------------------- refuter round 1 (2026-11-04)

def test_word_rows_rewords_a_row_whose_content_changed(tmp_path, domains):
    """Refuter MAJOR: wording was keyed on id only, so regenerated content kept stale intents."""
    rows = P.build_content(0, 20, domains[:1])
    out = tmp_path / "w.jsonl"
    asyncio.run(P.word_rows(rows, out, stub(_echo_reply(rows)), seed=0))
    changed = P.build_content(0, 20, domains)
    differ = sum(a["content"] != b["content"] for a, b in zip(rows, changed))
    assert differ
    s = asyncio.run(P.word_rows(changed, out, stub(_echo_reply(changed)), seed=0))
    assert s["asked"] == differ


def test_word_rows_survives_a_truncated_partial_line(tmp_path, domains):
    rows = P.build_content(0, 5, domains)
    out = tmp_path / "w.jsonl"
    P._partial(out).write_text('{"id": "x", "fin')
    s = asyncio.run(P.word_rows(rows, out, stub(_echo_reply(rows)), seed=0))
    assert s["asked"] == 5


@pytest.mark.parametrize("text, required, hit", [
    ("Make it an h1", ["1"], "h1"),
    ("Bind it to /patients", ["Patients"], "/patients"),
])
def test_register_masks_required_strings_only_as_whole_words(vocab, text, required, hit):
    """Refuter MINOR: blanking required substrings hid `h1` and `/patients`."""
    assert hit in checks.register(text, required, *vocab)


def test_missing_matches_short_values_as_whole_words():
    """Refuter MINOR: "No" matched inside "know", "5" inside "15"."""
    assert checks.missing("I know about 15 items", ["No", "5", "15"]) == ["No", "5"]
    assert checks.missing("Paid: No, total 5 and 1,200", ["No", "5", "1,200"]) == []


def test_assemble_records_bank_and_wording_provenance(sdk, domains):
    """Refuter MINOR: provenance was hard-coded; it comes from the domain and the worded record."""
    d = parse_domain(domain_raw("Bike shop"), "person:william")
    rows = P.build_content(0, 30, [d])
    worded = [{**w, "model": "model:other"} for w in _worded(rows, _perfect)]
    verdicts = P.check_rows(rows, worded, [], sdk.catalog_json, t=1.01)
    out = P.assemble(rows, worded, verdicts, 0, "script:x")
    assert {r["provenance"]["schema"] for r in out} == {"person:william"}
    assert {r["provenance"]["intent"] for r in out} == {"model:other"}


def test_tune_is_filled_without_update_parents(sdk, domains):
    """Refuter MINOR: parents were demoted after sampling, so tune shrank below its size."""
    rows = P.build_content(2, 600, domains)
    worded = _worded(rows, _perfect)
    verdicts = P.check_rows(rows, worded, [], sdk.catalog_json, t=1.01)
    out = P.assemble(rows, worded, verdicts, 0, "script:x", tune_size=150)
    assert sum(r["split"] == "tune" for r in out) >= 145
