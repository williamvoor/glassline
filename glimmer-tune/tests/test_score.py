"""End-to-end scoring of hand-written Express responses (compiled by the SDK, no network)."""

import pytest

from a2ui_eval.score import close_stopped_block, score_response

# One form per catalog. Positional slots differ: v1 TextField is (label, value,
# placeholder, ...), v0.9 is (label, value, variant, validationRegexp, ...) (ADR 0008).
# Each entry: (express, (text, text with two free-string slots swapped)).
_FORM = '''surface("signup")
root = Column([title, email, send])
title = Text("Sign up")
email = {textfield}, checks=[?required])
send = Button(sendLabel, "primary", Event("signup", {{email: $/form/email}}))
sendLabel = Text("Send")
'''
_FIELDS = {
    "v0_9": ('TextField("Email", $/form/email, _, "^.+@.+$"', 'TextField("^.+@.+$", $/form/email, _, "Email"'),
    "v1": ('TextField("Email", $/form/email, "you@example.com"', 'TextField("you@example.com", $/form/email, "Email"'),
}
FORM_REFS = {v: (_FORM.format(textfield=a), (a, b)) for v, (a, b) in _FIELDS.items()}

EXISTING = '''surface("profile")
root = Column([name, bio])
name = Text($/user/name)
bio = Text("Hello")
$/user/name = "Ann"
'''


@pytest.fixture(scope="module", params=["v0_9", "v1"])
def version(request):
    return request.param


@pytest.fixture(scope="module")
def sdk(version, request):
    """Every scorer test runs under both catalogs (overrides conftest's v0.9 `sdk`)."""
    return request.getfixturevalue("sdk_v1" if version == "v1" else "sdk_v0_9")


@pytest.fixture(scope="module")
def index(version, request):
    return request.getfixturevalue("index_v1" if version == "v1" else "index_v0_9")


@pytest.fixture(scope="module")
def FORM_REF(version):
    return FORM_REFS[version][0]


@pytest.fixture(scope="module")
def form_case(sdk, FORM_REF):
    return {"id": "form", "expect_ui": True, "reference": sdk.compile_express(FORM_REF)}


def wrap(express):
    return f"Here you go.\n<a2ui>\n{express}</a2ui>"


def test_reference_scores_perfectly_with_other_ids(sdk, index, form_case, FORM_REF):
    pred = FORM_REF.replace("title", "heading").replace("sendLabel", "lbl").replace('surface("signup")', 'surface("s1")')
    r = score_response(sdk, index, form_case, wrap(pred), "stop")
    assert r["extracted"] and r["compiles"] and r["sdk_valid"] and r["schema_valid"]
    assert r["structure_valid"] and r["bindings_valid"]
    assert r["slot"]["f1"] == 1.0
    assert r["exact_match"]
    assert set(r["components_used"]) == {"Column", "Text", "TextField", "Button"}


def test_swapped_positional_args(sdk, index, form_case, FORM_REF, version):
    pred = FORM_REF.replace(*FORM_REFS[version][1])
    assert pred != FORM_REF
    r = score_response(sdk, index, form_case, wrap(pred), "stop")
    assert r["compiles"] and r["sdk_valid"]
    assert r["slot"]["positional_errors"] == 2
    assert r["slot"]["f1"] < 1.0
    assert not r["exact_match"]


def test_stop_sequence_close_tag_is_restored(sdk, index, form_case, FORM_REF):
    stripped = f"<a2ui>\n{FORM_REF}"  # what a provider returns with stop=["</a2ui>"]
    assert close_stopped_block(stripped, "stop", sdk).endswith("</a2ui>")
    r = score_response(sdk, index, form_case, stripped, "stop")
    assert not r["truncated"] and r["exact_match"]


def test_cut_off_response_is_truncated_and_not_closed(sdk, index, form_case, FORM_REF):
    cut = "<a2ui>\n" + FORM_REF.split("send =")[0]
    assert close_stopped_block(cut, "length", sdk) == cut
    r = score_response(sdk, index, form_case, cut, "length")
    assert r["truncated"]
    assert not r["exact_match"]


def test_dangling_child_fails_sdk_and_structure(sdk, index, form_case, FORM_REF):
    pred = FORM_REF.replace("Column([title, email, send])", "Column([title, email, send, ghost])")
    r = score_response(sdk, index, form_case, wrap(pred), "stop")
    assert r["compiles"]
    assert not r["sdk_valid"]
    assert not r["structure_valid"]
    assert r["structure"]["signup"]["undefined_refs"] == [["root", "ghost"]]


def test_compile_error_is_recorded(sdk, index, form_case):
    r = score_response(sdk, index, form_case, wrap('surface("x")\nroot = Text("a" +)\n'), "stop")
    assert r["extracted"] and not r["compiles"]
    assert r["compile_errors"][0]["kind"].startswith("A2uiCompilation")
    assert r["slot"]["recall"] == 0.0


def test_missing_block_when_ui_expected(sdk, index, form_case):
    r = score_response(sdk, index, form_case, "Sure, here is a form.", "stop")
    assert not r["extracted"] and not r["compiles"]
    assert r["slot"]["n_ref"] > 0 and r["slot"]["f1"] == 0.0


def test_text_only_case(sdk, index):
    case = {"id": "chat", "expect_ui": False, "reference": []}
    good = score_response(sdk, index, case, "Paris is the capital of France.", "stop")
    assert good["extracted"] and good["exact_match"]
    bad = score_response(sdk, index, case, wrap('surface("x")\nroot = Text("Paris")\n'), "stop")
    assert not bad["extracted"] and not bad["exact_match"]


def test_unresolved_binding(sdk, index):
    ref = sdk.compile_express('surface("s")\nroot = Text($/user/name)\n$/user/name = "Ann"\n')
    case = {"id": "b", "expect_ui": True, "reference": ref}
    r = score_response(sdk, index, case, wrap('surface("s")\nroot = Text($/user/nmae)\n$/user/name = "Ann"\n'), "stop")
    assert r["sdk_valid"]  # the SDK does not check bindings
    assert r["unresolved_bindings"] == [["s", "root", "/user/nmae"]]


def test_update_turn_scored_against_existing_surface(sdk, index):
    existing = sdk.compile_express(EXISTING)
    ref = sdk.compile_express('surface("profile")\nbio = Text("Updated bio")\n')
    case = {"id": "upd", "expect_ui": True, "existing_surface": existing, "reference": ref}

    right = score_response(sdk, index, case, wrap('surface("profile")\nbio = Text("Updated bio")\n'), "stop")
    assert right["sdk_valid"] and right["structure_valid"] and right["exact_match"]

    # Same text, but a new ID: an orphan on the existing surface, and the anchor is missed.
    renamed = score_response(sdk, index, case, wrap('surface("profile")\nbio2 = Text("Updated bio")\n'), "stop")
    assert not renamed["sdk_valid"]
    assert renamed["structure"]["profile"]["orphans"] == ["bio2"]
    assert not renamed["exact_match"]

    wrong_surface = score_response(sdk, index, case, wrap('surface("other")\nbio = Text("Updated bio")\n'), "stop")
    assert not wrong_surface["sdk_valid"]
    assert wrong_surface["surface_id_errors"]


def test_update_scope_with_root(sdk, index):
    existing = sdk.compile_express(EXISTING)
    ref = sdk.compile_express('surface("profile", update=true)\nroot = Row([name, bio])\n')
    case = {"id": "upd-root", "expect_ui": True, "existing_surface": existing, "reference": ref}
    right = score_response(sdk, index, case, wrap('surface("profile", update=true)\nroot = Row([name, bio])\n'), "stop")
    assert right["exact_match"]
    # Without update=true the scope recreates the surface, dropping name and bio.
    recreate = score_response(sdk, index, case, wrap('surface("profile")\nroot = Row([name, bio])\n'), "stop")
    assert not recreate["exact_match"]


def test_data_model_only_update(sdk, index):
    existing = sdk.compile_express(EXISTING)
    ref = sdk.compile_express('surface("profile")\n$/user/name = "Bea"\n')
    case = {"id": "data", "expect_ui": True, "existing_surface": existing, "reference": ref}
    right = score_response(sdk, index, case, wrap('surface("profile")\n$/user/name = "Bea"\n'), "stop")
    assert right["exact_match"] and right["slot"]["f1"] == 1.0
    wrong = score_response(sdk, index, case, wrap('surface("profile")\n$/user/name = "Bee"\n'), "stop")
    assert not wrong["exact_match"] and wrong["slot"]["f1"] < 1.0


def test_delete_surface(sdk, index):
    existing = sdk.compile_express(EXISTING)
    case = {"id": "del", "expect_ui": True, "existing_surface": existing, "reference": sdk.compile_express('deleteSurface("profile")\n')}
    assert score_response(sdk, index, case, wrap('deleteSurface("profile")\n'), "stop")["exact_match"]
    assert not score_response(sdk, index, case, wrap('deleteSurface("other")\n'), "stop")["exact_match"]


def test_list_template_with_relative_binding(sdk, index):
    src = 'surface("l")\nroot = List(_template($/rows, row))\nrow = Text($name)\n$/rows = [{name: "a"}]\n'
    case = {"id": "list", "expect_ui": True, "reference": sdk.compile_express(src)}
    r = score_response(sdk, index, case, wrap(src), "stop")
    assert r["bindings_valid"] and r["exact_match"]
    typo = score_response(sdk, index, case, wrap(src.replace("$name", "$nmae")), "stop")
    assert typo["unresolved_bindings"] == [["l", "row", "nmae"]]


def test_failed_request(sdk, index, form_case):
    r = score_response(sdk, index, form_case, None)
    assert r["compile_errors"][0]["kind"] == "NoResponse"


def test_failed_request_scores_zero_slot_f1(sdk, index, form_case):
    """Regression: a missing response kept SlotScore() with n_ref=0, so its F1 read as 1.0."""
    r = score_response(sdk, index, form_case, None)
    assert r["slot"]["n_ref"] > 0
    assert r["slot"]["f1"] == 0.0
    assert r["slot_blind"] == r["slot"]  # N5


# --- name-blind slot score (SPEC "Name-blind slot score", ADR 0021) -------------------------

_LIST = 'surface("l")\nroot = Column([head, items])\nhead = Text("Rows: ${/count}")\nitems = List(_template($/rows, row))\nrow = Text($name)\n'


def _both(sdk, index, ref_src, pred_src):
    case = {"id": "b", "expect_ui": True, "reference": sdk.compile_express(ref_src)}
    r = score_response(sdk, index, case, wrap(pred_src), "stop")
    return r["slot"]["f1"], r["slot_blind"]["f1"], r


@pytest.mark.parametrize(
    "old,new",
    [
        ("$/form/email", "$/contact/mail"),  # absolute binding (in the field and the event context)
        ("$name", "$label"),  # relative binding in a template
        ("$/rows", "$/entries"),  # a template's list path
        ("${/count}", "${/total}"),  # interpolation
    ],
)
def test_blind_ignores_path_names(sdk, index, FORM_REF, old, new):
    """N1: renaming a path costs literal F1, not name-blind F1."""
    src = FORM_REF if old == "$/form/email" else _LIST
    lit, blind, r = _both(sdk, index, src, src.replace(old, new))
    assert lit < 1.0 and blind == 1.0
    assert r["slot_blind"]["n_ref"] == r["slot"]["n_ref"] and r["slot_blind"]["n_pred"] == r["slot"]["n_pred"]


def test_blind_ignores_event_names(sdk, index, FORM_REF):
    """N2: event name and context key."""
    for old, new in (('Event("signup"', 'Event("register"'), ("{email: $/form/email}", "{address: $/form/email}")):
        lit, blind, _ = _both(sdk, index, FORM_REF, FORM_REF.replace(old, new))
        assert lit < 1.0 and blind == 1.0, old


def test_blind_data_model_keeps_values(sdk, index):
    """N3: the pointer is blinded, the value is not."""
    ref = 'surface("d")\nroot = Text($/user/name)\n$/user/name = "Ann"\n'
    lit, blind, _ = _both(sdk, index, ref, ref.replace("/user/name", "/person/first"))
    assert lit < 1.0 and blind == 1.0
    _, blind, _ = _both(sdk, index, ref, ref.replace('"Ann"', '"Bea"'))
    assert blind < 1.0


def test_blind_still_scores_text_and_binding_kind(sdk, index, FORM_REF):
    """N4: literal text, and a literal where a binding belongs, still cost name-blind F1."""
    _, blind, _ = _both(sdk, index, FORM_REF, FORM_REF.replace('Text("Sign up")', 'Text("Register")'))
    assert blind < 1.0
    ref = 'surface("d")\nroot = Text($/user/name)\n'
    _, blind, _ = _both(sdk, index, ref, ref.replace("$/user/name", '"user name"'))
    assert blind < 1.0


def test_blind_parity_on_every_early_return_and_missing_surface(sdk, index, form_case):
    """Refuter: slot_blind equals slot (as an equal, separate dict) wherever no blinding applies."""
    cases = [
        (form_case, wrap("root = = broken\n")),  # does not compile
        (form_case, "no block at all"),  # nothing extracted
        ({"id": "t", "expect_ui": False, "reference": []}, "Just text."),  # text-only, as expected
    ]
    for case, text in cases:
        r = score_response(sdk, index, case, text, "stop")
        assert r["slot_blind"] == r["slot"] and r["slot_blind"] is not r["slot"], text
    two = sdk.compile_express('surface("a")\nroot = Text("A")\n') + sdk.compile_express('surface("b")\nroot = Text($/x)\n$/x = "B"\n')
    r = score_response(sdk, index, {"id": "m", "expect_ui": True, "reference": two}, wrap('surface("a")\nroot = Text("A")\n'), "stop")
    assert r["slot_blind"]["n_ref"] == r["slot"]["n_ref"] and r["slot_blind"]["tp"] == r["slot"]["tp"]
    assert r["slot"]["recall"] < 1.0


def test_blind_event_rule_needs_a_key_boundary():
    from a2ui_eval.align import EVENT_MARK, blind_leaf

    assert blind_leaf("action.event.name", "go") == ("action.event.name", EVENT_MARK)
    assert blind_leaf("prevent.name", "x") == ("prevent.name", "x")
