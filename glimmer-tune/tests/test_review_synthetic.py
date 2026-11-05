"""S13: synthetic review mode (SPEC 2c-1, build B)."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from a2ui_eval.io import write_jsonl
from a2ui_eval.review import (
    ReviewError,
    SyntheticReview,
    make_handler,
    read_rows,
    record_verdict,
    sample_rows,
    synthetic_views,
)
from a2ui_eval.io import read_jsonl
from a2ui_eval.seeds import ROOT


@pytest.fixture(scope="module")
def generated():
    """14 gallery dev rows: the review mode takes any cases.jsonl with `reference` messages."""
    return read_jsonl(ROOT / "data" / "dev" / "cases.jsonl")[:14]


@pytest.fixture
def review(tmp_path, generated):
    cases = tmp_path / "cases.jsonl"
    write_jsonl(cases, generated)
    return SyntheticReview(cases, sample=6, seed=3, verdicts=tmp_path / "review.jsonl")


def test_synthetic_sample(review, generated):
    views = synthetic_views(review)
    assert len(views) == 6
    ids = [v["id"] for v in views]
    assert ids == sorted(ids) and len(set(ids)) == 6
    assert set(ids) <= {r["id"] for r in generated}
    # Deterministic for a seed, and independent of the file's row order; a new seed gives a new sample.
    assert [v["id"] for v in synthetic_views(review)] == ids
    assert [r["id"] for r in sample_rows(list(reversed(generated)), 6, 3)] == ids
    assert [r["id"] for r in sample_rows(generated, 6, 4)] != ids
    # Rendered as v0.9, with what a reviewer needs.
    for v in views:
        assert "render_error" not in v and v["v09"] and all(m["version"] == "v0.9" for m in v["v09"])
        assert v["synthetic"] is True and v["lint"] == [] and v["verdict"] is None and v["reviewed"] is False
        assert v["expected"]


def test_sample_larger_than_file_returns_all(generated):
    assert len(sample_rows(generated, 500, 0)) == len(generated)


def test_verdict_recorded(review):
    row_id = synthetic_views(review)[0]["id"]
    assert record_verdict(review, row_id, "approve", "") == {"id": row_id, "verdict": "approve", "reason": ""}
    assert read_rows(review.verdicts) == [{"id": row_id, "verdict": "approve", "reason": ""}]
    shown = synthetic_views(review)[0]
    assert shown["verdict"] == "approve" and shown["reviewed"] is True


def test_synthetic_verdict_requires_reason(review):
    row_id = synthetic_views(review)[0]["id"]
    for reason in ("", "   ", None):
        with pytest.raises(ReviewError, match="reason"):
            record_verdict(review, row_id, "reject", reason)
    assert not review.verdicts.exists()
    assert record_verdict(review, row_id, "reject", " odd layout ")["reason"] == "odd layout"


def test_verdict_rejects_bad_input(review, generated):
    ids = [v["id"] for v in synthetic_views(review)]
    outside = next(r["id"] for r in generated if r["id"] not in ids)
    record_verdict(review, ids[0], "approve", "")
    before = review.verdicts.read_text()
    for row_id, verdict in ((ids[1], "maybe"), (ids[1], None), ("nope", "approve"), (outside, "approve")):
        with pytest.raises(ReviewError):
            record_verdict(review, row_id, verdict, "x")
    assert review.verdicts.read_text() == before


def test_verdict_upsert(review):
    a, b = (v["id"] for v in synthetic_views(review)[:2])
    record_verdict(review, a, "approve", "")
    record_verdict(review, b, "approve", "")
    record_verdict(review, a, "reject", "changed my mind")
    assert read_rows(review.verdicts) == [
        {"id": a, "verdict": "reject", "reason": "changed my mind"},
        {"id": b, "verdict": "approve", "reason": ""},
    ]


def test_lint_failures_are_shown(review, generated, tmp_path):
    bad = {**generated[0], "reference": [m for m in generated[0]["reference"]]}
    comps = next(m for m in bad["reference"] if "updateComponents" in m)["updateComponents"]["components"]
    comps.append({"id": "extra_btn", "component": "Button", "child": comps[0]["id"]})  # no action
    cases = tmp_path / "one.jsonl"
    write_jsonl(cases, [bad])
    (view,) = synthetic_views(SyntheticReview(cases, 1, 0, tmp_path / "v.jsonl"))
    assert "button_action" in view["lint"]


def test_http_verdict_round_trip(review, tmp_path):
    draft = tmp_path / "draft.jsonl"
    draft.write_text("")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(draft, tmp_path, review))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/api"

    def post(path, body):
        return urllib.request.urlopen(urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST"))

    try:
        views = json.loads(urllib.request.urlopen(base + "/rows").read())
        assert len(views) == 6 and views[0]["synthetic"] is True
        ok = post("/verdict", {"id": views[0]["id"], "verdict": "reject", "reason": "wrong"})
        assert json.loads(ok.read()) == {"id": views[0]["id"], "verdict": "reject", "reason": "wrong"}
        with pytest.raises(urllib.error.HTTPError) as bad:
            post("/verdict", {"id": views[1]["id"], "verdict": "reject", "reason": ""})
        assert bad.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as wrong_path:
            post("/rows", {"id": views[1]["id"], "intent": "x", "reviewed": True})  # draft endpoint is off in this mode
        assert wrong_path.value.code == 404
        assert json.loads(urllib.request.urlopen(base + "/rows").read())[0]["verdict"] == "reject"
    finally:
        server.shutdown()
    assert [r["id"] for r in read_rows(review.verdicts)] == [views[0]["id"]]
