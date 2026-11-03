"""Fake Fireworks service, training client, sampler, renderer and SDK types.

Tokens are characters: a message renders as its characters' ord() values, so lengths and
weights are easy to reason about."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

SECRET = "fw_SECRET_DO_NOT_LEAK"


@dataclass(frozen=True)
class ModelInput:
    ids: tuple[int, ...]

    @classmethod
    def from_ints(cls, ids):
        return cls(tuple(ids))

    def to_ints(self):
        return list(self.ids)


@dataclass(frozen=True)
class Datum:
    model_input: ModelInput
    loss_fn_inputs: dict


@dataclass(frozen=True)
class AdamParams:
    learning_rate: float
    beta1: float
    beta2: float
    eps: float
    weight_decay: float
    grad_clip_norm: float


@dataclass(frozen=True)
class SamplingParams:
    max_tokens: int
    temperature: float
    stop: Any


API = SimpleNamespace(
    Datum=Datum, ModelInput=ModelInput, AdamParams=AdamParams, SamplingParams=SamplingParams,
    get_text_content=lambda msg: msg["content"], LAST_ASSISTANT_TURN="last_assistant_turn",
)


class Renderer:
    def __init__(self, prefix: str = ""):
        self.prefix = prefix  # e.g. "Current date: ..." to simulate a default system block

    def _enc(self, text):
        return [ord(c) for c in text]

    def build_supervised_example(self, messages, train_on_what=None):
        assert train_on_what == "last_assistant_turn"
        ids, w = self._enc(self.prefix), [0.0] * len(self.prefix)
        for m in messages:
            t = self._enc(m["content"])
            ids += t
            w += [1.0 if m["role"] == "assistant" else 0.0] * len(t)
        return ModelInput.from_ints(ids), w

    def build_generation_prompt(self, messages):
        return ModelInput.from_ints(self._enc(self.prefix + "".join(m["content"] for m in messages)))

    def get_stop_sequences(self):
        return [0]

    def parse_response(self, tokens):
        return {"content": "".join(chr(t) for t in tokens)}, "stop_sequence"


class Tokenizer:
    def decode(self, ids):
        return "".join(chr(i) for i in ids)


class Sampler:
    def __init__(self, service, path):
        self.service, self.path = service, path

    def sample(self, prompt, num_samples, sampling_params):
        self.service.calls.append(("sample", self.path))
        if self.service.fail_sample and self.path.endswith(self.service.fail_sample):
            raise RuntimeError("boom")
        # the "answer" names the checkpoint, so the scorer fake can rank checkpoints
        text = f"<a2ui>{self.path}</a2ui>"
        seq = SimpleNamespace(tokens=[ord(c) for c in text], stop_reason="stop")
        return SimpleNamespace(result=lambda: SimpleNamespace(sequences=[seq]))

    def close(self):
        self.service.calls.append(("close-sampler", self.path))


class TrainingClient:
    def __init__(self, service):
        self.service, self.run_id, self.steps = service, "run-abc", 0

    def forward_backward(self, data, loss_fn):
        self.service.calls.append(("fb", len(data)))
        if self.service.fail_at_step and self.steps + 1 == self.service.fail_at_step:
            raise RuntimeError(f"server error with key {SECRET}")
        total = sum(sum(d.loss_fn_inputs["weights"]) for d in data)
        self.service.weight_sums.append(total)
        self.service.batches.append(data)
        return SimpleNamespace(result=lambda: SimpleNamespace(metrics={"loss:sum": 0.5, "response_tokens": 10}))

    def optim_step(self, adam, emit_grad_norm_metrics=None):
        self.steps += 1
        self.service.calls.append(("optim", adam))
        return SimpleNamespace(result=lambda: SimpleNamespace(metrics={"grad_norm": 1.5}))

    def save_weights_for_sampler(self, name):
        self.service.calls.append(("save", name, self.steps))
        return SimpleNamespace(result=lambda: SimpleNamespace(path=f"acct/run-abc/{name}"))


class Service:
    def __init__(self, fail_at_step=None, fail_sample=None):
        self.calls, self.weight_sums, self.batches = [], [], []
        self.lock = threading.Lock()
        self.fail_at_step, self.fail_sample = fail_at_step, fail_sample
        self.training_session_id, self.training_session_name = "ts-1", "accounts/a/trainingSessions/ts-1"
        self.api_key = SECRET

    def create_lora_training_client(self, **kw):
        self.calls.append(("create", kw))
        return TrainingClient(self)

    def create_sampling_client(self, model_path, tokenizer):
        return Sampler(self, model_path)

    def delete_training_session(self):
        self.calls.append(("delete",))

    def close(self):
        self.calls.append(("close",))
