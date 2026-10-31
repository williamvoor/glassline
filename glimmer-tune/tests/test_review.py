import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from a2ui_eval.review import DRAFT, ReviewError, expected_values, make_handler, read_rows, update_row, view_rows


def _row(i, **extra):
    return {"id": f"dev/fam/{i:04d}", "family": "fam", "split": "dev", "intent": f"intent {i}",
            "reference": [], "reference_express": "x", "fingerprint": "f", "reviewed": False, **extra}


@pytest.fixture
def draft(tmp_path):
    path = tmp_path / "intents_draft.jsonl"
    path.write_text("".join(json.dumps(_row(i)) + "\n" for i in range(3)))
    return path


def test_update_changes_only_intent_and_reviewed(draft):
    before = read_rows(draft)
    update_row(draft, "dev/fam/0001", "edited", True)
    after = read_rows(draft)
    assert [r["id"] for r in after] == [r["id"] for r in before]  # order kept
    assert after[0] == before[0] and after[2] == before[2]
    assert after[1] == {**before[1], "intent": "edited", "reviewed": True}


@pytest.mark.parametrize("row_id,intent,reviewed", [
    ("dev/fam/9999", "x", True),   # unknown id
    ("dev/fam/0001", "  ", True),  # blank intent
    ("dev/fam/0001", "x", "yes"),  # non-boolean reviewed
])
def test_update_rejects_bad_input_and_leaves_file_alone(draft, row_id, intent, reviewed):
    before = draft.read_text()
    with pytest.raises(ReviewError):
        update_row(draft, row_id, intent, reviewed)
    assert draft.read_text() == before


@pytest.mark.parametrize("call", ["call", "@call"])  # v0.9, v1
def test_expected_values_cover_data_text_format_strings_and_events(call):
    reference = [{"createSurface": {
        "surfaceId": "s",
        "dataModel": {"name": "Ad Spend", "pct": 40, "flag": True},
        "components": [
            {"id": "t", "component": "Text", "text": "## Terms of service"},
            {"id": "f", "component": "Text", "text": {call: "formatString", "args": {"value": "+${/pct}% from last week"}}},
            {"id": "b", "component": "Button", "child": "t", "action": {"event": {"name": "showTerms"}}},
            {"id": "p", "component": "Text", "text": {"@path": "/name"}},
        ],
    }}]
    assert expected_values(reference) == ["Ad Spend", "40", "Terms of service", "+40% from last week", "showTerms"]


def test_real_draft_rows_compile_to_v0_9():
    rows = view_rows(DRAFT)
    assert rows and all(r["v09"] and "render_error" not in r for r in rows)
    assert all(m["version"] == "v0.9" for r in rows for m in r["v09"])


def test_http_round_trip(draft, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(draft, tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/api/rows"
    try:
        ok = urllib.request.urlopen(urllib.request.Request(
            url, data=json.dumps({"id": "dev/fam/0002", "intent": "new", "reviewed": True}).encode(), method="POST"))
        assert json.loads(ok.read()) == {"id": "dev/fam/0002", "intent": "new", "reviewed": True}
        with pytest.raises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(urllib.request.Request(url, data=b'{"id": "nope", "intent": "x", "reviewed": true}', method="POST"))
        assert bad.value.code == 400
    finally:
        server.shutdown()
    assert read_rows(draft)[2]["reviewed"] is True


def test_expected_values_on_compiled_v0_9(sdk):
    # The real v0.9 shape: createSurface, then separate updateComponents and updateDataModel.
    reference = sdk.compile_express(
        'surface("s")\n'
        'root = Column([t, f, b])\n'
        't = Text("## Terms of service")\n'
        'f = Text(formatString("+${/pct}% from last week"))\n'
        'b = Button(bl, _, Event("showTerms", {}))\n'
        'bl = Text($/name)\n'
        '$/name = "Ad Spend"\n'
        '$/pct = 40\n'
    )
    assert {next(iter(k for k in m if k != "version")) for m in reference} == {"createSurface", "updateComponents", "updateDataModel"}
    assert expected_values(reference) == ["Ad Spend", "40", "Terms of service", "+40% from last week", "showTerms"]
