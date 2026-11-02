"""Draft arguments, comment kinds and file validation (SPEC.md phase 2b: D6, D7, V1)."""

import copy

import pytest

from explorer_agent.config import load_dataset
from explorer_agent.prompt import dataset_context
from explorer_agent.schema import SchemaCatalog, overlay_problems, render_schema_section

from explorer_bootstrap.draft_schema import MAX_PROBLEMS, validate_draft_args
from explorer_bootstrap.drafts import new_draft, validate_files
from explorer_bootstrap.export import export_dataset

from conftest import valid_args

pytestmark = pytest.mark.db


def _draft(report, args=None, **changes):
    d = new_draft(report, args or valid_args(), model="m", effort="low", usage={})
    for c in d["claims"]:
        c["result"] = {"state": "verified", "message": "", "actual": None}
    d.update(changes)
    return d


def _mut(fn):
    a = valid_args()
    fn(a)
    return a


# D6
@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda a: a.update(extra=1), "unknown key extra"),
        (lambda a: a.pop("files"), "files must be an object"),
        (lambda a: a["files"].update(notes_md="  "), "files.notes_md must be a non-blank string"),
        (lambda a: a["files"].update(dataset_toml=a["files"]["dataset_toml"] + '[comments]\noverlay = "c.toml"\n'),
         "must not have a [comments] table"),
        (lambda a: a["comments"].append({"object": "bs.nope", "column": "", "comment": "x"}), "not a table or view"),
        (lambda a: a["comments"].append({"object": "bs.mixed", "column": "nope", "comment": "x"}), "not a column"),
        (lambda a: a["comments"].append(dict(a["comments"][0])), "repeats bs.mixed.name"),
        (lambda a: a["comments"][0].update(comment=" "), "comments[0].comment must be a non-blank"),
        (lambda a: a["comments"][0].pop("column"), "must have exactly object, column, comment"),
        (lambda a: a["claims"][0].update(file="README"), "claims[0].file must be one of"),
        (lambda a: a["claims"][0].update(text=""), "claims[0].text must be a non-blank"),
        (lambda a: a["claims"][0]["expect"].pop("rel_tol"), "claims[0].expect must be scalar"),
        (lambda a: a["claims"][0]["expect"].update(kind="range"), "claims[0].expect must be scalar"),
        (lambda a: a["claims"][0]["expect"].update(abs_tol=-1), "tolerances must be >= 0"),
        (lambda a: a["claims"][0]["expect"].update(value=True), "must be finite numbers"),
        (lambda a: a["claims"][0]["expect"].update(value=float("nan")), "must be finite numbers"),
        (lambda a: a["claims"][0].update(sql=" "), "sql is required for a scalar"),
        (lambda a: a["claims"][1]["expect"].update(values=["1", "1"]), "has duplicates"),
        (lambda a: a["claims"][1]["expect"].update(values=[1]), "array of strings"),
        (lambda a: a["claims"][0]["expect"].update(kind=["scalar"]), "claims[0].expect must be scalar"),
        (lambda a: a["claims"][0].update(expect={"kind": {"x": 1}}), "claims[0].expect must be scalar"),
        (lambda a: a["claims"][0].update(file=["notes.md"]), "claims[0].file must be one of"),
    ],
)
async def test_validate_draft_args(bs_report, change, problem):
    assert validate_draft_args(valid_args(), bs_report) == []
    problems = validate_draft_args(_mut(change), bs_report)
    assert any(problem in p for p in problems), problems


async def test_validate_draft_args_caps_problems(bs_report):
    a = valid_args()
    a["comments"] = [{"object": f"bs.t{i}", "column": "", "comment": "x"} for i in range(50)]
    assert len(validate_draft_args(a, bs_report)) == MAX_PROBLEMS
    assert validate_draft_args([], bs_report) == ["arguments must be an object"]


# D7
async def test_comment_kinds(bs_report):
    d = new_draft(bs_report, valid_args(), model="m", effort="low", usage={})
    kinds = {(c["object"], c["column"]): (c["kind"], c["accepted"]) for c in d["comments"]}
    assert kinds[("bs.mixed", "amount")] == ("rewrite", False)
    assert kinds[("bs.mixed", "name")] == ("new", True)
    assert kinds[("bs.fresh", "")] == ("new", True)
    assert all(c["dismissed"] is False and c["result"] is None for c in d["claims"])


# V1
async def test_validate_files(bs_report, ro_pool, tmp_path):
    d = _draft(bs_report)
    v = validate_files(d)
    assert v["errors"] == []
    # The same figure the explorer would compute over the exported overlay dataset.
    out = export_dataset(d, "bs_export", "toml", tmp_path)
    ds = load_dataset(out)
    objects = await SchemaCatalog(ro_pool, ds.allowed_schemas, ds.comment_overlay).objects()
    assert overlay_problems(objects, ds.comment_overlay) == []
    assert v["context_chars"] == len(dataset_context(ds, render_schema_section(objects)))
    # an accepted rewrite changes the context
    d2 = copy.deepcopy(d)
    next(c for c in d2["comments"] if c["kind"] == "rewrite")["accepted"] = True
    assert validate_files(d2)["context_chars"] != v["context_chars"]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda c: c[0].update(comment="  "), "comments: bs.mixed.name has a blank comment"),
        (lambda c: c.append({"object": "bs.ghost", "column": "", "comment": "x", "kind": "new", "accepted": True}),
         "comments: bs.ghost is not a table or view in the report"),
        (lambda c: c.append({"object": "bs.mixed", "column": "nope", "comment": "x", "kind": "new",
                             "accepted": True}), "comments: bs.mixed.nope is not a column in the report"),
        (lambda c: c.append(dict(c[0])), "comments: bs.mixed.name is accepted twice"),
        (lambda c: c[0].update(object=["bs.mixed"]), "is not a table or view in the report"),
    ],
)
async def test_validate_files_checks_accepted_comments(bs_report, change, error):
    d = _draft(bs_report)
    change(d["comments"])
    v = validate_files(d)
    assert any(error in e for e in v["errors"]), v["errors"]
    assert v["context_chars"] is None
    for c in d["comments"]:
        c["accepted"] = False  # only accepted comments are checked
    assert validate_files(d)["errors"] == []


@pytest.mark.parametrize(
    ("toml", "error"),
    [
        ("name = ", "not valid TOML"),
        ('name = "bs"\n', "missing key"),
        (None, "[comments]"),
        (None, "allowed_schemas must be among the reported schemas"),
        (None, 'notes must be "notes.md"'),
    ],
)
async def test_validate_files_errors(bs_report, toml, error):
    d = _draft(bs_report)
    base = d["files"]["dataset_toml"]
    if toml is None and error == "[comments]":
        toml = base + '\n[comments]\noverlay = "comments.toml"\n'
    elif toml is None and "allowed_schemas" in error:
        toml = base.replace('["bs"]', '["bs", "mart"]')
    elif toml is None:
        toml = base.replace('notes = "notes.md"', 'notes = "n.md"')
    d["files"]["dataset_toml"] = toml if toml != 'name = "bs"\n' else toml + 'notes = "notes.md"\nglossary = "glossary.md"\n'
    v = validate_files(d)
    assert v["context_chars"] is None
    assert any(error in e for e in v["errors"]), v["errors"]
    assert not any("bootstrap-validate" in e or "/tmp" in e or "/var/" in e for e in v["errors"])
