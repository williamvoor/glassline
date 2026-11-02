import json

import pytest
from fakes import API, SECRET, Renderer, Service, Tokenizer

from glimmer_trainer.loop import Config, Deps, Run, batch_datums, batches, ckpt_name, schedule

SUBSET = {"s000": 0.1, "s002": 0.5, "s004": 0.7, "s005": 0.6}
FULL = {"s000": 0.1, "s004": 0.7, "s005": 0.8}


def rows(n, prefix="t"):
    return [{"id": f"{prefix}{i}", "messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": "a" * (1 + i % 3)}]} for i in range(n)]


def dev_rows():
    out = []
    for i in range(10):
        out.append({"id": f"d{i}", "source": "gallery" if i < 5 else "synthetic", "subset": i % 2 == 0, "messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": f"q{i}"}]})
    return out


def fake_scorer(dev):
    seen = []

    def score(pred, out, full):
        preds = [json.loads(line) for line in pred.read_text().splitlines()]
        ckpt = preds[0]["checkpoint"]
        want = {r["id"] for r in dev if full or r["subset"]}
        assert {p["id"] for p in preds} == want
        seen.append((ckpt, full))
        result = {"checkpoint": ckpt, "selection": (FULL if full else SUBSET)[ckpt]}
        out.write_text(json.dumps(result))
        return result

    score.seen = seen
    return score


def make(tmp_path, service=None, every=2, n=70):
    service = service or Service()
    dev = dev_rows()
    promoted = []
    deps = Deps(api=API, renderer=Renderer(), tokenizer=Tokenizer(), service=service, scorer=fake_scorer(dev),
                promote=lambda s, c, m: promoted.append((s, c, m)) or f"accounts/a/models/{m}")
    cfg = Config(run_id="t1", every=every, workers=3)
    logs = []
    return Run(cfg, deps, rows(n), dev, tmp_path / "t1", log=logs.append), service, promoted, logs


def test_batches():
    """T6: seeded shuffle, size 16, last short batch kept, every row exactly once."""
    data = list(range(70))
    b = batches(data, 16, 0)
    assert [len(x) for x in b] == [16, 16, 16, 16, 6]
    assert sorted(sum(b, [])) == data
    assert b == batches(data, 16, 0) and b != batches(data, 16, 1)
    assert sum(b, []) != data  # shuffled


def test_datums_mean_weights():
    """T7: weights sum to 1 over the batch, sit only on assistant tokens, inputs/targets shifted."""
    batch = rows(3)
    datums = batch_datums(API, Renderer(), batch)
    assert sum(sum(d.loss_fn_inputs["weights"]) for d in datums) == pytest.approx(1.0)
    for d, row in zip(datums, batch):
        ids = [ord(c) for c in "".join(m["content"] for m in row["messages"])]
        assert d.model_input.to_ints() == ids[:-1]
        assert d.loss_fn_inputs["target_tokens"] == ids[1:]
        n_answer = len(row["messages"][2]["content"])
        w = d.loss_fn_inputs["weights"]
        assert all(x == 0 for x in w[: len(w) - n_answer]) and all(x > 0 for x in w[-n_answer:])


def test_checkpoint_schedule():
    """T8: s000, every N steps, the last step; names fit Fireworks' limit."""
    assert schedule(325, 25) == list(range(0, 326, 25))
    assert schedule(330, 25)[-2:] == [325, 330]
    assert [ckpt_name(s) for s in schedule(60, 25)] == ["s000", "s025", "s050", "s060"]
    assert all(len(ckpt_name(s)) <= 17 for s in schedule(5000, 25))


def test_run_end_to_end_with_fakes(tmp_path):
    """T9: base saved before step 1, Adam params, subset sampled per checkpoint, top 2 get full dev,
    winner (never s000) promoted, session deleted last."""
    run, svc, promoted, _ = make(tmp_path)
    result = run.run()
    calls = svc.calls
    create = next(c for c in calls if c[0] == "create")[1]
    assert create["rank"] == 16 and create["alpha"] == 32 and create["base_model"].endswith("muse-glimmer-30b")
    saves = [c for c in calls if c[0] == "save"]
    assert [(n, s) for _, n, s in saves] == [("s000", 0), ("s002", 2), ("s004", 4), ("s005", 5)]
    first_fb = next(i for i, c in enumerate(calls) if c[0] == "fb")
    assert calls.index(saves[0]) < first_fb
    adam = next(c[1] for c in calls if c[0] == "optim")
    assert (adam.learning_rate, adam.beta2, adam.eps, adam.grad_clip_norm) == (5e-5, 0.999, 1e-8, 1.0)
    assert len(svc.weight_sums) == 5 and all(s == pytest.approx(1.0) for s in svc.weight_sums)
    assert sum(len(b) for b in svc.batches) == 70

    seen = run.deps.scorer.seen
    assert sorted(c for c, full in seen if not full) == ["s000", "s002", "s004", "s005"]
    assert sorted(c for c, full in seen if full) == ["s000", "s004", "s005"]  # base + top 2 by subset
    assert result["winner"] == "s005"  # full dev overrules the subset ranking
    assert promoted == [("accounts/a/trainingSessions/ts-1", "s005", "glimmer-a2ui-t1-s005")]
    assert calls[-1] == ("delete",)
    # the full files reuse the subset answers and sample only the rest
    samples = [c[1] for c in calls if c[0] == "sample"]
    assert samples.count("acct/run-abc/s002") == 5 and samples.count("acct/run-abc/s004") == 10

    d = tmp_path / "t1"
    assert len((d / "metrics.jsonl").read_text().splitlines()) == 5
    for name in ("s000", "s002", "s004", "s005"):
        assert (d / "predictions" / f"{name}.jsonl").exists()
    assert len((d / "predictions" / "s005.full.jsonl").read_text().splitlines()) == 10
    manifest = json.loads((d / "manifest.json").read_text())
    assert manifest["stage"] == "done" and manifest["promoted"] == "accounts/a/models/glimmer-a2ui-t1-s005"
    assert manifest["session_id"] == "ts-1" and set(manifest["checkpoints"]) == set(SUBSET)


def test_run_refuses_existing_run_dir(tmp_path):
    run, *_ = make(tmp_path)
    run.run()
    again, svc, *_ = make(tmp_path)
    with pytest.raises(Exception, match="exists"):
        again.run()
    assert svc.calls == []


@pytest.mark.parametrize("failure", [{"fail_at_step": 3}])
def test_failure_keeps_session(tmp_path, failure):
    """T10: an exception records the error and leaves the session (and its checkpoints) alone.
    A sampling failure is per row (scored as an error), so it must not abort the run."""
    run, svc, promoted, logs = make(tmp_path, Service(**failure))
    if "fail_at_step" in failure:
        with pytest.raises(RuntimeError):
            run.run()
        manifest = json.loads((tmp_path / "t1" / "manifest.json").read_text())
        assert manifest["stage"] == "failed" and "RuntimeError" in manifest["error"]
        assert ("delete",) not in svc.calls and promoted == []
        assert any("ts-1" in line for line in logs)
    else:
        run.run()
        preds = [json.loads(line) for line in (tmp_path / "t1" / "predictions" / "s004.jsonl").read_text().splitlines()]
        assert all(p["finish"] == "error" and p["text"] is None for p in preds)


def test_key_not_leaked(tmp_path, capsys, monkeypatch):
    """T13: the API key never reaches the run directory or stdout, even in an error message."""
    monkeypatch.setenv("FIREWORKS_API_KEY", SECRET)
    run, *_ = make(tmp_path, Service(fail_at_step=2))
    with pytest.raises(RuntimeError):
        run.run()
    blob = "".join(p.read_text() for p in (tmp_path / "t1").rglob("*") if p.is_file())
    assert SECRET not in blob and SECRET not in capsys.readouterr().out


def test_pick_checkpoint_matches_sdk_rows():
    """Regression: SDK rows document only `name`; matching on `checkpointName` alone found nothing."""
    from glimmer_trainer.loop import RunError, pick_checkpoint

    rows = [
        {"name": "accounts/a/trainingSessions/ts-1/checkpoints/run-abc-s025-1a2b3c4d", "promotable": True, "createTime": "1"},
        {"name": "accounts/a/trainingSessions/ts-1/checkpoints/run-abc-s250-9f9f9f9f", "promotable": True, "createTime": "2"},
        {"name": "accounts/a/trainingSessions/ts-1/checkpoints/run-abc-s025-ffff0000", "promotable": False, "createTime": "3"},
    ]
    assert pick_checkpoint(rows, "s025")["name"].endswith("1a2b3c4d")
    assert pick_checkpoint([{"checkpointName": "run-abc-s250-00", "name": "x", "promotable": True}], "s250")["name"] == "x"
    with pytest.raises(RunError, match="no promotable"):
        pick_checkpoint(rows, "s050")


def run_with(tmp_path, subset, full):
    run, svc, promoted, _ = make(tmp_path)
    dev = dev_rows()

    def score(pred, out, is_full):
        ckpt = json.loads(pred.read_text().splitlines()[0])["checkpoint"]
        return {"checkpoint": ckpt, "selection": (full if is_full else subset)[ckpt]}

    run.deps.scorer = score
    return run.run(), promoted


def test_winner_is_never_base(tmp_path):
    result, promoted = run_with(tmp_path, {"s000": 0.9, "s002": 0.3, "s004": 0.5, "s005": 0.4}, {"s000": 0.9, "s004": 0.5, "s005": 0.4})
    assert result["finalists"] == ["s000", "s004", "s005"] and result["winner"] == "s004"
    assert promoted[0][1] == "s004"


def test_ties_go_to_the_earlier_step(tmp_path):
    result, _ = run_with(tmp_path, {"s000": 0.5, "s002": 0.5, "s004": 0.5, "s005": 0.5}, {"s000": 0.5, "s002": 0.4, "s004": 0.4})
    assert result["finalists"] == ["s000", "s002", "s004"] and result["winner"] == "s002"


def test_length_stop_reason_is_recorded(tmp_path):
    from types import SimpleNamespace

    from glimmer_trainer.loop import sample_one

    seq = SimpleNamespace(tokens=[ord("x")], stop_reason="length")
    sampler = SimpleNamespace(sample=lambda **_: SimpleNamespace(result=lambda: SimpleNamespace(sequences=[seq])))
    assert sample_one(API, Renderer(), sampler, dev_rows()[0], 10, "s001")["finish"] == "length"


def test_sampling_errors_are_counted_and_abort_above_threshold(tmp_path, monkeypatch):
    """Errors score 0, so their rate is recorded per checkpoint; above 25% the run stops (session kept)."""
    monkeypatch.setattr("glimmer_trainer.loop.RETRY_DELAYS", (0, 0))
    run, svc, promoted, _ = make(tmp_path, Service(fail_sample="s004"))
    with pytest.raises(Exception, match="s004"):
        run.run()
    manifest = json.loads((tmp_path / "t1" / "manifest.json").read_text())
    assert manifest["sample_errors"]["s004"] == 5 and manifest["sample_errors"]["s002"] == 0
    assert manifest["stage"] == "failed" and ("delete",) not in svc.calls and promoted == []


def test_failed_delete_is_recorded(tmp_path):
    svc = Service()

    def boom():
        raise RuntimeError("delete failed")

    svc.delete_training_session = boom
    run, *_ = make(tmp_path, svc)
    result = run.run()
    assert result["promoted"] and result["session_deleted"] is False and "delete failed" in result["delete_error"]


def test_existing_nonempty_run_dir_is_refused(tmp_path):
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "metrics.jsonl").write_text("{}\n")
    run, svc, *_ = make(tmp_path)
    with pytest.raises(Exception, match="exists"):
        run.run()
    assert svc.calls == []
