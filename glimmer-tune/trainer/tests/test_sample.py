"""`sample`: one saved checkpoint of a finished run, sampled on a prompt export (the probe and the
one-time test evaluation). Fakes only; no network."""

import json

from fakes import API, SECRET, Renderer, Service, Tokenizer
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
                    promote=lambda *a: (_ for _ in ()).throw(AssertionError("sample must not promote")))

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
    assert {c[1] for c in service.calls if c[0] == "sample"} == {"acct/run-abc/s250-x"}
    assert ("close-sampler", "acct/run-abc/s250-x") in service.calls
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary == {"checkpoint": "s250", "rows": 10, "errors": 0, "length": 0, "out": str(out)}


def test_sample_limit_takes_the_first_rows(tmp_path):
    run, cases, service, remote, local, _ = setup(tmp_path)
    out = tmp_path / "probe.jsonl"
    assert main(["sample", "--run", str(run), "--ckpt", "s250", "--cases", str(cases), "--out", str(out), "--limit", "1"], local=local, remote=remote) == 0
    assert [p["id"] for p in read(out)] == ["d0"]
    assert sum(c[0] == "sample" for c in service.calls) == 1


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

    real = cli.sample_rows
    monkeypatch.setattr(cli, "sample_rows", lambda *a: seen.append(a[-1]) or real(*a))
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

    real = cli.sample_rows

    def racing(*a):
        out.write_text("other\n")
        return real(*a)

    monkeypatch.setattr(cli, "sample_rows", racing)
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
