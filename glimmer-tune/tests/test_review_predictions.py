"""Item 5: prediction review mode, the gallery dev rows worst-first with the model's answer beside the reference."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from a2ui_eval.checkpoints import dev_cases
from a2ui_eval.generate import read_jsonl, wrap, write_jsonl
from a2ui_eval.review import PREDICTION_VERDICTS, PredictionReview, ReviewError, make_handler, prediction_views, record_prediction_verdict


@pytest.fixture
def review(tmp_path):
    gallery = sorted((c for c in dev_cases() if not c["id"].startswith("synth/")), key=lambda c: c["id"])[:3]
    synth = next(c for c in dev_cases() if c["id"].startswith("synth/"))
    a, b, c = gallery
    preds = [
        {"id": a["id"], "checkpoint": "s250", "finish": "stop", "text": wrap(a["reference_express"])},
        {"id": b["id"], "checkpoint": "s250", "finish": "stop", "text": '<a2ui>\nroot = Icon($done ? "check" : "close")\n</a2ui>'},
        {"id": c["id"], "checkpoint": "s250", "finish": "error", "text": None},
        {"id": synth["id"], "checkpoint": "s250", "finish": "stop", "text": wrap(synth["reference_express"])},
    ]
    rows = [
        {"id": a["id"], "source": "gallery", "slot_f1": 1.0, "slot_f1_blind": 1.0, "compiles": True, "outside_catalog_kinds": []},
        {"id": b["id"], "source": "gallery", "slot_f1": 0.0, "slot_f1_blind": 0.0, "compiles": False, "outside_catalog_kinds": ["operator"]},
        {"id": c["id"], "source": "gallery", "slot_f1": 0.0, "slot_f1_blind": 0.2, "compiles": False, "outside_catalog_kinds": []},
        {"id": synth["id"], "source": "synthetic", "slot_f1": 0.0, "slot_f1_blind": 0.0, "compiles": True, "outside_catalog_kinds": []},
    ]
    p, s = tmp_path / "p.jsonl", tmp_path / "s.json"
    write_jsonl(p, preds)
    s.write_text(json.dumps({"checkpoint": "s250", "rows": rows}))
    return PredictionReview(p, s, verdicts=tmp_path / "verdicts.jsonl"), (a, b, c)


def test_gallery_rows_worst_first(review):
    r, (a, b, c) = review
    views = prediction_views(r)
    assert [v["id"] for v in views] == [b["id"], c["id"], a["id"]]  # blind F1 0.0, 0.2, 1.0; no synthetic row
    worst = views[0]
    assert worst["prediction_mode"] is True and worst["outside_catalog_kinds"] == ["operator"]
    assert worst["prediction_v09"] == [] and worst["prediction_error"]  # shown, not crashed
    assert views[1]["prediction_text"] is None and views[1]["prediction_error"] == "no response (error)"
    assert views[2]["prediction_v09"] and "prediction_error" not in views[2]
    assert views[2]["v09"] and views[2]["intent"] == a["intent"]
    assert worst["verdict"] is None and worst["reviewed"] is False


def test_verdicts_are_validated_and_upserted(review):
    r, (a, b, _) = review
    with pytest.raises(ReviewError, match="verdict"):
        record_prediction_verdict(r, b["id"], "approve", "")
    with pytest.raises(ReviewError, match="unknown row"):
        record_prediction_verdict(r, "dev/nope/0000", "ok", "")
    record_prediction_verdict(r, b["id"], "model", "invents a ternary")
    record_prediction_verdict(r, b["id"], "reference", "  under-specified  ")
    assert read_jsonl(r.verdicts) == [{"id": b["id"], "verdict": "reference", "note": "under-specified"}]
    assert {v["id"]: v["verdict"] for v in prediction_views(r)}[b["id"]] == "reference"
    assert set(PREDICTION_VERDICTS) == {"ok", "model", "reference", "scorer"}


def test_handler_serves_rows_and_records_verdicts(review, tmp_path):
    r, (_, b, _) = review
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path / "unused.jsonl", tmp_path, predictions=r))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        rows = json.loads(urllib.request.urlopen(f"{base}/api/rows").read())
        assert rows[0]["id"] == b["id"]
        req = urllib.request.Request(f"{base}/api/verdict", data=json.dumps({"id": b["id"], "verdict": "scorer", "note": "x"}).encode(), method="POST")
        assert json.loads(urllib.request.urlopen(req).read())["verdict"] == "scorer"
    finally:
        server.shutdown()
