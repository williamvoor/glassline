"""Tool/edited-claim boundary cases run in the fast suite, with no database."""

import pytest

from explorer_bootstrap.boards import shape_problems
from explorer_bootstrap.draft_schema import claim_problems, validate_draft_args

REPORT = {"objects": [{"schema": "fixture", "name": "t", "comment": None,
                       "columns": [{"name": "id", "comment": None}]}]}


def valid():
    return {"files": {"dataset_toml": 'examples = ["Show rows"]', "notes_md": "Notes", "glossary_md": "Terms"},
            "comments": [{"object": "fixture.t", "column": "id", "comment": "Identifier"}],
            "claims": [{"text": "Count", "file": "notes.md", "sql": "SELECT 0",
                        "expect": {"kind": "scalar", "value": 0, "abs_tol": 0, "rel_tol": 0}}]}


@pytest.mark.parametrize("path,value", [
    (("files",), None), (("files",), []), (("files", "notes_md"), False),
    (("files", "unexpected"), "extra"), (("files", "notes_md"), " "),
    (("comments",), {}), (("comments", 0), None), (("comments", 0, "column"), 1),
    (("comments", 0, "extra"), "extra"), (("comments", 0, "object"), "missing"),
    (("comments", 0, "column"), "missing"), (("comments", 0, "comment"), ""),
    (("claims",), {}), (("claims", 0), []), (("claims", 0, "text"), ""),
    (("claims", 0, "file"), "wrong.md"), (("claims", 0, "sql"), None),
    (("claims", 0, "sql"), ""), (("claims", 0, "extra"), True),
    (("claims", 0, "expect"), None), (("claims", 0, "expect"), []),
    (("claims", 0, "expect", "extra"), 1), (("claims", 0, "expect", "kind"), []),
    (("claims", 0, "expect", "kind"), "invalid"), (("claims", 0, "expect", "value"), True),
    (("claims", 0, "expect", "value"), "0"), (("claims", 0, "expect", "value"), float("nan")),
    (("claims", 0, "expect", "value"), float("inf")), (("claims", 0, "expect", "value"), -float("inf")),
    (("claims", 0, "expect", "abs_tol"), -1), (("claims", 0, "expect", "rel_tol"), -1),
])
def test_draft_rejects_invalid_boundary_values(path, value):
    args = valid()
    node = args
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert validate_draft_args(args, REPORT)


@pytest.mark.parametrize("path", [
    ("files",), ("comments",), ("claims",), ("files", "dataset_toml"),
    ("comments", 0, "comment"), ("claims", 0, "expect"), ("claims", 0, "expect", "abs_tol"),
])
def test_draft_requires_declared_fields(path):
    args = valid()
    node = args
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    assert validate_draft_args(args, REPORT)


@pytest.mark.parametrize("expect,sql,accepted", [
    ({"kind": "none"}, "", True), ({"kind": "none", "value": 0}, "", False),
    ({"kind": "set", "values": []}, "SELECT 1", True),
    ({"kind": "set", "values": ["", "0"]}, "SELECT 1", True),
    ({"kind": "set", "values": ["a", "a"]}, "SELECT 1", False),
    ({"kind": "set", "values": [1]}, "SELECT 1", False),
    ({"kind": "set", "values": None}, "SELECT 1", False),
    ({"kind": "set", "values": []}, "", False),
])
def test_expectation_variants(expect, sql, accepted):
    args = valid()
    args["claims"][0].update(expect=expect, sql=sql)
    assert (not validate_draft_args(args, REPORT)) is accepted


def test_valid_zero_values_and_empty_optional_lists():
    args = valid()
    assert validate_draft_args(args, REPORT) == []
    args.update(comments=[], claims=[])
    assert validate_draft_args(args, REPORT) == []


def test_edited_claims_have_their_own_optional_fields():
    claims = valid()["claims"]
    assert claim_problems(claims, extra_keys=frozenset({"dismissed", "result"})) == []
    claims[0].update(dismissed=True, result={"state": "verified"})
    assert claim_problems(claims)
    assert claim_problems(claims, extra_keys=frozenset({"dismissed", "result"})) == []
    claims[0]["unexpected"] = 1
    assert claim_problems(claims, extra_keys=frozenset({"dismissed", "result"}))


def test_errors_are_bounded_and_never_echo_input_values():
    secret = "fake-secret-that-must-not-be-in-diagnostics"
    args = valid()
    args["comments"] = [{"object": {"secret": secret}, "column": False, "comment": []}] * 100
    problems = validate_draft_args(args, REPORT)
    assert len(problems) == 20 and all(len(p) <= 240 for p in problems)
    assert secret not in str(problems)
    assert problems[0].startswith("comments[0].object:")


@pytest.mark.parametrize("args", [[], {"boards": None}, {"boards": [None]},
                                  {"boards": [{"name": "x", "toml": "x", "why": "x", "extra": 1}]}])
def test_board_submission_shapes(args):
    assert shape_problems(args)
