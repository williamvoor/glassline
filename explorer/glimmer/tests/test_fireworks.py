"""The real backend with fake renderer and sampler objects. The SDK is never imported."""

import sys
from concurrent.futures import Future
from types import SimpleNamespace

from glimmer_server.app import SurfaceRequest
from glimmer_server.fireworks import FireworksBackend


class FakeRenderer:
    def __init__(self):
        self.seen = None

    def build_generation_prompt(self, messages):
        self.seen = messages
        return SimpleNamespace(to_ints=lambda: [1, 2, 3])

    def get_stop_sequences(self):
        return [99]

    def parse_response(self, tokens):
        return {"tokens": tokens}, True


class FakeSampler:
    def __init__(self, finish="stop", metrics=None):
        self.kw, self.finish, self.metrics = None, finish, metrics
        outer = self

        class DS:
            def sample_with_prompt_tokens_result(self, ids, **kw):
                outer.kw = {"ids": ids, **kw}
                return "coro"

        self.deployment_sampler = DS()

    def _submit(self, coro):
        assert coro == "coro"
        f = Future()
        c = SimpleNamespace(full_tokens=[1, 2, 3, 40, 41], prompt_len=3, finish_reason=self.finish)
        f.set_result(SimpleNamespace(completions=[c], server_metrics=self.metrics))
        return f


def _req(grammar=None):
    return SurfaceRequest([{"role": "user", "content": "brief"}], 900, grammar, "explorer-glimmer")


def test_result_becomes_the_reply_and_request_fields_are_forwarded():
    r, s = FakeRenderer(), FakeSampler(metrics=SimpleNamespace(prompt_tokens=120, cached_prompt_tokens=100, completion_tokens=2))
    out = FireworksBackend(r, s, lambda msg: f"text{msg['tokens']}")(_req(grammar="root ::= x"))
    assert out == {"text": "text[40, 41]", "finish": "stop", "tokens": 2,
                   "usage": {"prompt_tokens": 120, "cached_prompt_tokens": 100, "completion_tokens": 2}}
    assert r.seen == [{"role": "user", "content": "brief"}]
    kw = s.kw
    assert kw["ids"] == [1, 2, 3] and kw["max_tokens"] == 900 and kw["temperature"] == 0.0 and kw["stop"] == [99]
    assert kw["prompt_cache_key"] == "explorer-glimmer" and kw["logical_request_id"].startswith("glimmer-")
    assert kw["response_format"] == {"type": "grammar", "grammar": "root ::= x"}


def test_length_finish_no_grammar_and_missing_usage_is_null():
    s = FakeSampler(finish="length", metrics=SimpleNamespace(prompt_tokens=None, cached_prompt_tokens=None, completion_tokens=5))
    out = FireworksBackend(FakeRenderer(), s, lambda m: "")(_req())
    assert out["finish"] == "length" and "response_format" not in s.kw
    assert out["usage"] == {"prompt_tokens": None, "cached_prompt_tokens": None, "completion_tokens": 5}
    out = FireworksBackend(FakeRenderer(), FakeSampler(metrics=None), lambda m: "")(_req())
    # completion tokens are known locally even when the server sends no metrics (serverless)
    assert out["usage"] == {"prompt_tokens": None, "cached_prompt_tokens": None, "completion_tokens": 2}


def test_the_sdk_is_not_imported():
    assert not any(m == "fireworks" or m.startswith(("fireworks.", "training.")) for m in sys.modules)
