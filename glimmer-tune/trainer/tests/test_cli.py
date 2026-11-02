import json

from fakes import API, Renderer, Tokenizer
from test_loop import dev_rows, rows

from glimmer_trainer.__main__ import main


def files(tmp_path):
    train, dev, prompt = tmp_path / "train.jsonl", tmp_path / "dev.jsonl", tmp_path / "prompt.txt"
    train.write_text("".join(json.dumps(r) + "\n" for r in rows(40)))
    dev.write_text("".join(json.dumps(r) + "\n" for r in dev_rows()))
    prompt.write_text("SYS")
    return ["--train", str(train), "--dev", str(dev), "--prompt", str(prompt), "--runs", str(tmp_path / "runs")]


def local():
    return API, Renderer(), Tokenizer()


def never(_dev):
    raise AssertionError("a Fireworks client was created without --launch")


def test_train_without_launch_is_local(tmp_path, capsys):
    """T12: `train` without --launch runs the checks and creates no Fireworks client."""
    assert main(["train", "--run-id", "x1", *files(tmp_path)], local=local, remote=never) == 0
    out = capsys.readouterr().out
    assert '"steps": 3' in out and "not launched" in out
    assert not (tmp_path / "runs").exists()
    assert main(["prepare", *files(tmp_path)], local=local, remote=never) == 0


def test_bad_run_id_and_failed_check_exit_nonzero(tmp_path, capsys):
    assert main(["train", "--run-id", "Bad_ID", *files(tmp_path)], local=local, remote=never) == 1
    args = files(tmp_path)
    (tmp_path / "prompt.txt").write_text("OTHER")
    assert main(["prepare", *args], local=local, remote=never) == 1
    assert "frozen" in capsys.readouterr().err


def test_launch_needs_explicit_run_id(tmp_path, capsys):
    assert main(["train", "--launch", *files(tmp_path)], local=local, remote=never) == 1
    assert "--run-id" in capsys.readouterr().err


def test_unexpected_errors_are_redacted(tmp_path, capsys, monkeypatch):
    from fakes import SECRET

    monkeypatch.setenv("FIREWORKS_API_KEY", SECRET)

    def explode(_dev):
        raise ValueError(f"auth failed for {SECRET}")

    assert main(["train", "--launch", "--run-id", "r1", *files(tmp_path)], local=local, remote=explode) == 1
    err = capsys.readouterr().err
    assert SECRET not in err and "auth failed" in err
