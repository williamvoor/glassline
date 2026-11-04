"""`sample`: one saved checkpoint of a finished run, sampled on a prompt export (the probe and the
one-time test evaluation). Fakes only; no network."""

import json

from fakes import API, SECRET, Renderer, Service, Tokenizer, direct
from test_loop import dev_rows

from glimmer_trainer.__main__ import main
from glimmer_trainer.loop import Deps


def setup(tmp_path, service=None):
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"checkpoints": {"s000": "acct/run-abc/s000", "s250": "acct/run-abc/s250-x"}}))
    cases = tmp_path / "cases.jsonl"
    cases.write_text("".join(json.dumps(r) + "\n" for r in dev_rows()))
    service = service or Service()
    made = []

    def remote(_cases):
        made.append(_cases)
        return Deps(api=API, renderer=Renderer(), tokenizer=Tokenizer(), service=service,
                    scorer=lambda *a: (_ for _ in ()).throw(AssertionError("sample must not score")),
                    promote=lambda *a: (_ for _ in ()).throw(AssertionError("sample must not promote")), direct=direct)

    def local():
        raise AssertionError("sample must not run the local train checks")

    return run, cases, service, remote, local, made


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_sample_reads_the_path_from_the_manifest_and_writes_predictions(tmp_path, capsys):
    run, cases, service, remote, local, _ = setup(tmp_path)
    out = tmp_path / "out" / "s250.jsonl"
    argv = ["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), "--workers", "2"]
    assert main(argv, local=local, remote=remote) == 0
    preds = read(out)
    assert [p["id"] for p in preds] == [r["id"] for r in dev_rows()]
    assert {p["checkpoint"] for p in preds} == {"s250"}
    assert {p["finish"] for p in preds} == {"stop"}
    assert preds[0]["text"] == "<a2ui>acct/run-abc/s250-x</a2ui>"
    assert {c[1] for c in service.calls if c[0] == "direct"} == {"acct/run-abc/s250-x"}
    assert all(p["latency_s"] >= 0 for p in preds)
    assert ("close-sampler", "acct/run-abc/s250-x") in service.calls
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert {k: summary[k] for k in ("checkpoint", "rows", "errors", "length", "out", "grammar")} == {
        "checkpoint": "s250", "rows": 10, "errors": 0, "length": 0, "out": str(out), "grammar": None}
    assert summary["usage"]["prompt_tokens"] == 10_000 and summary["usage"]["cached_prompt_tokens"] == 4_000


def test_sample_limit_takes_the_first_rows(tmp_path):
    run, cases, service, remote, local, _ = setup(tmp_path)
    out = tmp_path / "probe.jsonl"
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), "--limit", "1"], local=local, remote=remote) == 0
    assert [p["id"] for p in read(out)] == ["d0"]
    assert sum(c[0] == "direct" for c in service.calls) == 1


def test_sample_refuses_an_existing_output_and_an_unknown_checkpoint(tmp_path, capsys):
    """Test predictions are made once: never overwritten. Both checks happen before any client exists."""
    run, cases, _, _, local, _ = setup(tmp_path)

    def never(_c):
        raise AssertionError("a Fireworks client was created")

    out = tmp_path / "s250.jsonl"
    out.write_text("old\n")
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out)], local=local, remote=never) == 1
    assert "exists" in capsys.readouterr().err and out.read_text() == "old\n"
    assert main(["sample", "--run", str(run), "--ckpt", "s999", "--cases", str(cases), "--out", str(tmp_path / "n.jsonl")], local=local, remote=never) == 1
    assert "s999" in capsys.readouterr().err


def test_sample_errors_exit_nonzero_and_are_redacted(tmp_path, capsys, monkeypatch, ):
    """A missing checkpoint shows up as per-row errors: the file is still written, the exit is 1."""
    monkeypatch.setenv("FIREWORKS_API_KEY", SECRET)
    monkeypatch.setattr("glimmer_trainer.loop.RETRY_DELAYS", (0.0, 0.0))
    run, cases, service, remote, local, _ = setup(tmp_path, Service(fail_sample="s250-x"))
    out = tmp_path / "s250.jsonl"
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), "--limit", "2"], local=local, remote=remote) == 1
    preds = read(out)
    assert {p["finish"] for p in preds} == {"error"}
    captured = capsys.readouterr()
    assert SECRET not in out.read_text() + captured.out + captured.err
    assert json.loads(captured.out.strip().splitlines()[-1])["errors"] == 2


def test_sample_defaults_to_the_runs_max_tokens(tmp_path, monkeypatch):
    """Refuter: "the run's settings" means the manifest's max_tokens unless overridden."""
    run, cases, _, remote, local, _ = setup(tmp_path)
    m = json.loads((run / "manifest.json").read_text())
    (run / "manifest.json").write_text(json.dumps({**m, "config": {"max_tokens": 1234}}))
    seen = []
    import glimmer_trainer.__main__ as cli

    real = cli.sample_rows_direct
    monkeypatch.setattr(cli, "sample_rows_direct", lambda *a: seen.append(a[5]) or real(*a))
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(tmp_path / "a.jsonl"), "--limit", "1"], local=local, remote=remote) == 0
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(tmp_path / "b.jsonl"), "--limit", "1", "--max-tokens", "99"], local=local, remote=remote) == 0
    assert seen == [1234, 99]


def test_sample_rejects_bad_limit_and_empty_cases_before_any_client(tmp_path, capsys):
    run, cases, _, _, local, _ = setup(tmp_path)

    def never(_c):
        raise AssertionError("a Fireworks client was created")

    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    for extra, c in ((["--limit", "0"], cases), (["--limit", "-1"], cases), ([], empty)):
        out = tmp_path / "x.jsonl"
        assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(c), "--out", str(out), *extra], local=local, remote=never) == 1
        assert not out.exists()
        err = capsys.readouterr().err
        assert "AssertionError" not in err and ("--limit" in err or "no rows" in err)


def test_sample_never_overwrites_a_file_created_while_sampling(tmp_path, monkeypatch, capsys):
    """Exclusive create: a file that appears after the up-front check is kept."""
    run, cases, _, remote, local, _ = setup(tmp_path)
    out = tmp_path / "s250.jsonl"
    import glimmer_trainer.__main__ as cli

    real = cli.sample_rows_direct

    def racing(*a):
        out.write_text("other\n")
        return real(*a)

    monkeypatch.setattr(cli, "sample_rows_direct", racing)
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), "--limit", "1"], local=local, remote=remote) == 1
    assert out.read_text() == "other\n"
    assert "exists" in capsys.readouterr().err


def test_sample_redacts_a_client_creation_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FIREWORKS_API_KEY", SECRET)
    run, cases, _, _, local, _ = setup(tmp_path)

    def explode(_c):
        raise RuntimeError(f"401 for key {SECRET}")

    out = tmp_path / "s250.jsonl"
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out)], local=local, remote=explode) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err and "401" in captured.err
    assert not out.exists()


def test_sample_sends_the_grammar_as_response_format(tmp_path, capsys):
    run, cases, service, remote, local, _ = setup(tmp_path)
    grammar = tmp_path / "g.gbnf"
    grammar.write_text('root ::= "x"\n')
    out = tmp_path / "g.jsonl"
    argv = ["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), "--limit", "2", "--grammar", str(grammar)]
    assert main(argv, local=local, remote=remote) == 0
    sent = [c[2] for c in service.calls if c[0] == "direct"]
    assert sent == [{"response_format": {"type": "grammar", "grammar": 'root ::= "x"\n'}}] * 2
    assert read(out)[0]["text"] == "<a2ui>acct/run-abc/s250-x+g</a2ui>"
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["grammar"] == str(grammar)


def test_sample_without_grammar_sends_no_extra_fields(tmp_path):
    run, cases, service, remote, local, _ = setup(tmp_path)
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(tmp_path / "p.jsonl"), "--limit", "1"], local=local, remote=remote) == 0
    assert [c[2] for c in service.calls if c[0] == "direct"] == [{}]


def test_sample_stops_at_the_budget(tmp_path, capsys):
    """Each fake row costs (600 x 1.96 + 400 x 0.39 + ~31 x 4.88) / 1e6, about $0.0015. With one worker
    and a budget of $0.002, the second row pushes the spend over, and the rest are refused."""
    run, cases, service, remote, local, _ = setup(tmp_path)
    out = tmp_path / "b.jsonl"
    argv = ["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), "--workers", "1", "--budget", "0.002"]
    assert main(argv, local=local, remote=remote) == 1
    preds = read(out)
    assert [p["finish"] for p in preds[:2]] == ["stop", "stop"]
    assert {p.get("error") for p in preds[2:]} == {"budget reached"}
    assert sum(c[0] == "direct" for c in service.calls) == 2
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert 0.002 <= summary["cost_usd"] < 0.004


def test_sample_rejects_a_missing_grammar_and_a_bad_budget_before_any_client(tmp_path, capsys):
    run, cases, _, _, local, _ = setup(tmp_path)

    def never(_c):
        raise AssertionError("a Fireworks client was created")

    for extra in (["--grammar", str(tmp_path / "nope.gbnf")], ["--budget", "0"]):
        out = tmp_path / "x.jsonl"
        assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), *extra], local=local, remote=never) == 1
        assert not out.exists()
        err = capsys.readouterr().err
        assert "AssertionError" not in err and ("grammar" in err or "budget" in err)


def test_usage_cost_uses_the_listed_prices():
    from glimmer_trainer.loop import usage_cost

    assert usage_cost({"prompt_tokens": 1_000_000, "cached_prompt_tokens": 0, "completion_tokens": 0}) == 1.96
    assert usage_cost({"prompt_tokens": 1_000_000, "cached_prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}) == 0.39 + 4.88


def test_sample_n_draws_n_samples_per_row_with_the_sampling_params(tmp_path, capsys):
    """Best-of-n (distill Phase 1): each row is sampled `n` times, rows stay adjacent, each
    prediction carries its sample index, and the sampling params reach the request."""
    run, cases, service, remote, local, _ = setup(tmp_path)
    out = tmp_path / "n.jsonl"
    argv = ["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), "--limit", "2",
            "--n", "3", "--temperature", "1.0", "--top-p", "0.95", "--top-k", "64"]
    assert main(argv, local=local, remote=remote) == 0
    preds = read(out)
    ids = [r["id"] for r in dev_rows()[:2]]
    assert [(p["id"], p["sample"]) for p in preds] == [(i, k) for i in ids for k in range(3)]
    sent = [c[2] for c in service.calls if c[0] == "direct"]
    assert sent == [{"temperature": 1.0, "top_p": 0.95, "top_k": 64}] * 6
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["rows"] == 6


def test_sample_n_needs_a_positive_temperature(tmp_path, capsys):
    run, cases, service, remote, local, _ = setup(tmp_path)
    for extra in ([], ["--temperature", "0"]):
        argv = ["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(tmp_path / "x.jsonl"), "--n", "2", *extra]
        assert main(argv, local=local, remote=remote) == 1
        assert "temperature" in capsys.readouterr().err
    assert not service.calls


def test_real_direct_defaults_to_greedy_and_forwards_sampling_params():
    """`real.direct` keeps temperature 0.0 unless the extra fields set it."""
    import asyncio
    from types import SimpleNamespace

    from glimmer_trainer import real

    seen = []

    class DS:
        async def sample_with_prompt_tokens(self, ids, **kw):
            seen.append(kw)
            return [SimpleNamespace(full_tokens=[*ids, 5], prompt_len=len(ids), finish_reason="stop")]

        def drain_metrics(self):
            return []

    sampler = SimpleNamespace(deployment_sampler=DS(), _submit=lambda c: SimpleNamespace(result=lambda: asyncio.run(c)))
    real.direct(sampler, [1, 2], 10, [0], {})
    real.direct(sampler, [1, 2], 10, [0], {"temperature": 1.0, "top_k": 64})
    assert seen[0]["temperature"] == 0.0 and "top_k" not in seen[0]
    assert seen[1]["temperature"] == 1.0 and seen[1]["top_k"] == 64 and seen[1]["n"] == 1
