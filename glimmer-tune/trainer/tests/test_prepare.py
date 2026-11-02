import pytest
from fakes import API, Renderer, Tokenizer
from test_loop import dev_rows, rows

from glimmer_trainer.loop import Config
from glimmer_trainer.prepare import PrepareError, check

CFG = Config(run_id="p", every=2)


def run(train=None, dev=None, frozen="SYS", renderer=None, cfg=CFG):
    return check(cfg, train if train is not None else rows(70), dev if dev is not None else dev_rows(), frozen, renderer or Renderer(), Tokenizer().decode, API.LAST_ASSISTANT_TURN)


def test_report():
    r = run()
    assert (r["train_rows"], r["dev_rows"], r["dev_subset"], r["steps"]) == (70, 10, 5, 5)
    assert r["checkpoints"] == ["s000", "s002", "s004", "s005"]
    assert r["samples_estimate"] == 5 * 4 + 5 * 3


def test_unfrozen_prompt_fails():
    """T11a: a train or dev row whose system text is not the frozen prompt."""
    with pytest.raises(PrepareError, match="frozen"):
        run(frozen="OTHER")
    dev = dev_rows()
    dev[3]["messages"][0]["content"] = "SYS changed"
    with pytest.raises(PrepareError, match="1 dev rows"):
        run(dev=dev)


def test_current_date_fails():
    """T11b: the renderer slipped a dated default system block in."""
    with pytest.raises(PrepareError, match="Current date"):
        run(renderer=Renderer(prefix="Current date: 2026-11-02.\n"))


def test_over_length_fails():
    """T11c: a row over max_seq_len aborts instead of being dropped silently."""
    train = rows(5)
    train[2]["messages"][2]["content"] = "x" * 200
    with pytest.raises(PrepareError, match="train row 2 has"):
        run(train=train, cfg=Config(run_id="p", max_seq_len=100))
