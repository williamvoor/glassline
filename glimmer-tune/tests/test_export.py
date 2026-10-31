import pytest

from a2ui_eval.export import TEMPLATE_OVERHEAD, ExportError, export_sft
from a2ui_eval.generate import generate_family, load_templates
from a2ui_eval.prompt import build_system_prompt

SPLITS = {"09_login-form": "train", "34_child-list-template": "train", "27_stats-card": "dev"}


def words(text: str) -> int:
    return len(text.split())


@pytest.fixture(scope="module")
def train(sdk, index):
    t = load_templates()
    return generate_family(sdk, index, t["09_login-form"], "train", 4) + generate_family(
        sdk, index, t["34_child-list-template"], "train", 3
    )


@pytest.fixture(scope="module")
def dev(sdk, index):
    return generate_family(sdk, index, load_templates()["27_stats-card"], "dev", 2)


def run(sdk, train, dev, splits=SPLITS, max_seq_len=100_000):
    return export_sft(train, dev, splits, build_system_prompt(sdk), words, max_seq_len)


def test_row_shape(sdk, train, dev):
    rows, report = run(sdk, train, dev)
    assert report.rows == len(rows) == len(train)
    for row, case in zip(rows, train):
        system, user, assistant = row["messages"]
        assert [m["role"] for m in row["messages"]] == ["system", "user", "assistant"]
        assert system["content"] == build_system_prompt(sdk)
        assert user["content"] == case["intent"]
        assert assistant["content"] == f"<a2ui>\n{case['reference_express'].strip()}\n</a2ui>"


def test_no_reasoning(sdk, train, dev):
    rows, _ = run(sdk, train, dev)
    assert all(set(m) == {"role", "content"} for row in rows for m in row["messages"])


def test_target_round_trips(sdk, train, dev):
    rows, _ = run(sdk, train, dev)
    for row, case in zip(rows, train):
        _, blocks = sdk.unwrap(row["messages"][-1]["content"])
        assert len(blocks) == 1 and blocks[0].is_final
        assert sdk.compile(blocks[0]) == case["reference"]


def test_rejects_non_train_family(sdk, train, dev):
    with pytest.raises(ExportError, match="34_child-list-template"):
        run(sdk, train, dev, splits={**SPLITS, "34_child-list-template": "test"})


def test_drops_fingerprint_overlap(sdk, train, dev):
    leaked = [{**dev[0], "fingerprint": train[0]["fingerprint"]}]
    rows, report = run(sdk, train, leaked)
    same = sum(c["fingerprint"] == train[0]["fingerprint"] for c in train)
    assert report.rows == len(rows) == len(train) - same
    assert report.dropped["09_login-form"]["fingerprint"] == same


def test_drops_intent_overlap(sdk, train, dev):
    leaked = [{**dev[0], "intent": train[-1]["intent"] + "  "}]
    rows, report = run(sdk, train, leaked)
    assert len(rows) == len(train) - 1
    assert report.dropped["34_child-list-template"]["intent"] == 1


def test_over_length_fails(sdk, train, dev):
    system = words(build_system_prompt(sdk))
    with pytest.raises(ExportError, match=r"train/09_login-form/0000"):
        run(sdk, train, dev, max_seq_len=system + TEMPLATE_OVERHEAD + 10)


def test_length_report(sdk, train, dev):
    _, report = run(sdk, train, dev)
    system = words(build_system_prompt(sdk))
    lengths = sorted(
        system + words(c["intent"]) + words(f"<a2ui>\n{c['reference_express'].strip()}\n</a2ui>") + TEMPLATE_OVERHEAD
        for c in train
    )
    assert report.tokens == {"max": lengths[-1], "p50": lengths[len(lengths) // 2], "p95": lengths[int(0.95 * len(lengths))]}


def test_overhead_and_default_pinned():
    from a2ui_eval import data_cli

    assert TEMPLATE_OVERHEAD == 64
    args = data_cli.build_parser().parse_args(["export"])
    assert args.max_seq_len == 8192


def test_drops_extra_fingerprints(sdk, train, dev):
    rows, report = export_sft(train, [], SPLITS, build_system_prompt(sdk), words, 100_000, extra_fingerprints={train[0]["fingerprint"]})
    assert len(rows) < len(train) and report.dropped["09_login-form"]["fingerprint"] >= 1


def test_cli_export_guards_against_drafts_and_seeds(sdk, index, train, tmp_path, monkeypatch):
    import shutil

    from a2ui_eval import data_cli
    from a2ui_eval.fingerprint import fingerprint
    from a2ui_eval.generate import SPLITS_PATH, read_jsonl, write_jsonl
    from a2ui_eval.seeds import load_seeds

    shutil.copy(SPLITS_PATH, tmp_path / "splits.yaml")
    login = [c for c in train if c["family"] == "09_login-form"]
    lists = [c for c in train if c["family"] == "34_child-list-template"]
    write_jsonl(tmp_path / "train" / "cases.jsonl", train)
    # A draft row shares the login fingerprint; nothing in dev/test files does.
    write_jsonl(tmp_path / "intents_draft.jsonl", [{"id": "d", "split": "dev", "intent": "z", "fingerprint": login[0]["fingerprint"], "reviewed": False}])
    monkeypatch.setattr(data_cli, "load_tokenizer_counter", lambda: words)
    out = tmp_path / "out.jsonl"
    assert data_cli.main(["--data", str(tmp_path), "export", "--out", str(out)]) == 0
    rows = read_jsonl(out)
    kept = {r["messages"][1]["content"] for r in rows}
    assert not kept & {c["intent"] for c in login if c["fingerprint"] == login[0]["fingerprint"]}
    assert {c["intent"] for c in lists} <= kept
    # Dev/test seed fingerprints are guarded even with no cases for those families.
    seeds = load_seeds()
    seed_prints = {fingerprint(s.messages, index) for f, s in seeds.items() if f in ("27_stats-card", "36_modal")}
    assert seed_prints <= data_cli.eval_seed_fingerprints(index)
