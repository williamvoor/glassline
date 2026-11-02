"""The SFT run: train, checkpoint, sample dev, score, promote (glimmer-tune SPEC "SFT run 1", ADR 0015).

Everything remote comes in through `Deps`, so tests run the whole flow with fakes.
"""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BASE_MODEL = "accounts/fireworks/models/muse-glimmer-30b"
MAX_CKPT_NAME = 17  # Fireworks checkpoint ids must fit in 63 chars once wrapped
RETRY_DELAYS = (2.0, 8.0)  # seconds before each retry of a failed sample
ERROR_LIMIT = 0.25  # above this share of error rows a checkpoint's score is noise: stop the run


@dataclass(frozen=True)
class Config:
    run_id: str
    rank: int = 16
    alpha: int = 32
    learning_rate: float = 5e-5
    batch_size: int = 16
    every: int = 25
    max_tokens: int = 3072
    max_seq_len: int = 8192
    seed: int = 0
    workers: int = 8
    top_k: int = 2
    base_model: str = BASE_MODEL

    def model_id(self, ckpt: str) -> str:
        return f"glimmer-a2ui-{self.run_id}-{ckpt}"


@dataclass
class Deps:
    """`api`: namespace with Datum, ModelInput, AdamParams, SamplingParams, get_text_content,
    LAST_ASSISTANT_TURN. `scorer(pred_path, out_path, full) -> scores`. `promote(session_name,
    ckpt, model_id) -> model name`."""

    api: Any
    renderer: Any
    tokenizer: Any
    service: Any
    scorer: Callable[[Path, Path, bool], dict[str, Any]]
    promote: Callable[[str, str, str], str]
    clock: Callable[[], float] = time.monotonic


class RunError(Exception):
    pass


def redact(text: str) -> str:
    """Error text goes to the manifest and predictions; the API key must not."""
    key = os.environ.get("FIREWORKS_API_KEY")
    return text.replace(key, "***") if key else text


def ckpt_name(step: int) -> str:
    return f"s{step:03d}"


def schedule(n_steps: int, every: int) -> list[int]:
    """Steps after which a sampler checkpoint is saved: 0 (base), every `every`, and the last."""
    steps = list(range(0, n_steps + 1, every))
    return steps if steps[-1] == n_steps else steps + [n_steps]


def batches(rows: list[Any], size: int, seed: int) -> list[list[Any]]:
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    return [[rows[i] for i in order[k : k + size]] for k in range(0, len(order), size)]


def batch_datums(api: Any, renderer: Any, rows: list[dict[str, Any]]) -> list[Any]:
    """Cookbook datums (inputs and targets shifted by one), with each assistant-token weight
    set to 1 / (response tokens in the batch): the summed loss is a per-token batch mean."""
    rendered = []
    for row in rows:
        model_input, weights = renderer.build_supervised_example(row["messages"], train_on_what=api.LAST_ASSISTANT_TURN)
        tokens = [int(t) for t in model_input.to_ints()]
        w = [float(x) for x in (weights.tolist() if hasattr(weights, "tolist") else weights)]
        rendered.append((tokens, w[1:]))
    total = sum(sum(w) for _, w in rendered)
    if total <= 0:
        raise RunError("batch has no assistant tokens")
    return [
        api.Datum(model_input=api.ModelInput.from_ints(tokens[:-1]), loss_fn_inputs={"target_tokens": tokens[1:], "weights": [x / total for x in w]})
        for tokens, w in rendered
    ]


def sample_one(api: Any, renderer: Any, sampler: Any, row: dict[str, Any], max_tokens: int, ckpt: str) -> dict[str, Any]:
    params = api.SamplingParams(max_tokens=max_tokens, temperature=0.0, stop=renderer.get_stop_sequences())
    last: Exception | None = None
    for attempt in range(1 + len(RETRY_DELAYS)):
        if attempt:
            time.sleep(RETRY_DELAYS[attempt - 1])
        try:
            res = sampler.sample(prompt=renderer.build_generation_prompt(row["messages"]), num_samples=1, sampling_params=params).result()
            seq = res.sequences[0]
            msg, _ = renderer.parse_response(list(seq.tokens))
            return {"id": row["id"], "checkpoint": ckpt, "text": api.get_text_content(msg), "finish": "length" if seq.stop_reason == "length" else "stop"}
        except Exception as e:  # noqa: BLE001 - recorded per row, scored as a failure
            last = e
    return {"id": row["id"], "checkpoint": ckpt, "text": None, "finish": "error", "error": redact(f"{type(last).__name__}: {last}")[:200]}


def sample_rows(deps: Deps, path: str, rows: list[dict[str, Any]], ckpt: str, workers: int, max_tokens: int) -> list[dict[str, Any]]:
    """Greedy samples of `rows` from the sampler checkpoint at `path`, in row order. The sampler is
    always closed. Works after the run's session is gone (the checkpoint is addressed by path)."""
    sampler = deps.service.create_sampling_client(model_path=path, tokenizer=deps.tokenizer)
    try:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(lambda r: sample_one(deps.api, deps.renderer, sampler, r, max_tokens, ckpt), rows))
    finally:
        sampler.close()


def pick_checkpoint(rows: list[dict[str, Any]], ckpt: str) -> dict[str, Any]:
    """The newest promotable session checkpoint saved as `ckpt`. The SDK documents `name`
    (`.../checkpoints/run-<hex>-<ckpt>-<suffix>`); `checkpointName` is read when present."""
    def logical(r: dict[str, Any]) -> str:
        return str(r.get("checkpointName") or r.get("name", "")).rsplit("/", 1)[-1]

    hits = [r for r in rows if r.get("promotable") and f"-{ckpt}-" in logical(r)]
    if not hits:
        raise RunError(f"no promotable checkpoint named {ckpt}")
    return max(hits, key=lambda r: str(r.get("createTime", "")))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


@dataclass
class Manifest:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    def update(self, **kw: Any) -> None:
        self.data.update(kw)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, default=str) + "\n", encoding="utf-8")
        tmp.replace(self.path)


class Run:
    def __init__(self, cfg: Config, deps: Deps, train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]], run_dir: Path, log: Callable[[str], None] = print):
        self.cfg, self.deps, self.train, self.dev, self.dir, self.log = cfg, deps, train_rows, dev_rows, run_dir, log
        self.subset = [r for r in dev_rows if r["subset"]]
        self.rest = [r for r in dev_rows if not r["subset"]]
        self.preds = run_dir / "predictions"
        self.manifest = Manifest(run_dir / "manifest.json")
        self.checkpoints: dict[str, str] = {}
        self.sample_errors: dict[str, int] = {}

    # sampling -------------------------------------------------------------
    def _sample(self, ckpt: str, path: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sample_rows(self.deps, path, rows, ckpt, self.cfg.workers, self.cfg.max_tokens)

    def _check_errors(self, ckpt: str, preds: list[dict[str, Any]], total: int) -> None:
        """Main thread only. Error rows score 0, so their count is recorded and capped."""
        self.manifest.update(sample_errors=dict(self.sample_errors))
        n = sum(p["finish"] == "error" for p in preds)
        if n:
            self.log(f"{ckpt}: {n}/{total} sample errors")
        if n > ERROR_LIMIT * total:
            raise RunError(f"{ckpt}: {n}/{total} sample errors (limit {ERROR_LIMIT:.0%}); see predictions/{ckpt}*.jsonl")

    def _sample_subset(self, ckpt: str, path: str) -> Path:
        out = self.preds / f"{ckpt}.jsonl"
        preds = self._sample(ckpt, path, self.subset)
        self.sample_errors[ckpt] = sum(p["finish"] == "error" for p in preds)  # one key per thread
        write_jsonl(out, preds)
        return out

    # stages ---------------------------------------------------------------
    def _save(self, step: int, pool: ThreadPoolExecutor, pending: dict[str, Future]) -> None:
        name = ckpt_name(step)
        path = self.tc.save_weights_for_sampler(name).result().path
        self.checkpoints[name] = path
        self.manifest.update(checkpoints=dict(self.checkpoints))
        pending[name] = pool.submit(self._sample_subset, name, path)
        self.log(f"saved {name} at step {step}")

    def _score(self, ckpt: str, pred: Path, full: bool) -> dict[str, Any]:
        out = self.dir / "scores" / (f"{ckpt}.full.json" if full else f"{ckpt}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        return self.deps.scorer(pred, out, full)

    def run(self) -> dict[str, Any]:
        cfg, d = self.cfg, self.deps
        if self.dir.exists() and any(self.dir.iterdir()):
            raise RunError(f"{self.dir} exists; pick a new run id")
        self.dir.mkdir(parents=True, exist_ok=True)
        steps = batches(self.train, cfg.batch_size, cfg.seed)
        plan = schedule(len(steps), cfg.every)
        if any(len(ckpt_name(s)) > MAX_CKPT_NAME for s in plan):
            raise RunError("checkpoint names too long")
        self.manifest.update(config=asdict(cfg), steps=len(steps), schedule=plan, stage="opening")
        tc = d.service.create_lora_training_client(
            base_model=cfg.base_model, rank=cfg.rank, alpha=cfg.alpha, train_mlp=True, train_attn=True, train_unembed=True
        )
        self.tc = tc
        self.manifest.update(session_id=getattr(d.service, "training_session_id", None), session_name=getattr(d.service, "training_session_name", None), run_id=getattr(tc, "run_id", None), stage="training")
        try:
            result = self._run(tc, steps, plan)
        except BaseException as e:
            self.manifest.update(stage="failed", error=redact(f"{type(e).__name__}: {e}")[:500])
            self.log(f"run failed; session {self.manifest.data.get('session_id')} kept so checkpoints can be promoted by hand")
            raise
        try:
            d.service.delete_training_session()
            self.manifest.update(stage="done", session_deleted=True)
        except Exception as e:  # noqa: BLE001 - the promotion already happened; say so and keep it
            self.manifest.update(stage="done", session_deleted=False, delete_error=redact(f"{type(e).__name__}: {e}")[:500])
            self.log(f"promoted, but deleting session {self.manifest.data.get('session_id')} failed; delete it by hand")
        return self.manifest.data

    def _run(self, tc: Any, steps: list[list[dict[str, Any]]], plan: list[int]) -> dict[str, Any]:
        cfg, d = self.cfg, self.deps
        adam = d.api.AdamParams(learning_rate=cfg.learning_rate, beta1=0.9, beta2=0.999, eps=1e-8, weight_decay=0.0, grad_clip_norm=1.0)
        pending: dict[str, Future] = {}
        metrics = self.dir / "metrics.jsonl"
        pool = ThreadPoolExecutor(2)
        try:
            self._save(0, pool, pending)
            with metrics.open("a", encoding="utf-8") as mlog:
                for step, batch in enumerate(steps, 1):
                    t0 = d.clock()
                    datums = batch_datums(d.api, d.renderer, batch)
                    fb = tc.forward_backward(datums, "cross_entropy").result()
                    opt = tc.optim_step(adam, emit_grad_norm_metrics=True).result()
                    m = dict(getattr(fb, "metrics", {}) or {})
                    row = {"step": step, "loss": m.get("loss:sum"), "response_tokens": m.get("response_tokens"), "rows": len(batch), "seconds": round(d.clock() - t0, 3)}
                    row.update({k: v for k, v in (getattr(opt, "metrics", {}) or {}).items() if "grad" in k})
                    mlog.write(json.dumps(row) + "\n")
                    mlog.flush()
                    if step in plan:
                        self._save(step, pool, pending)
            subset_preds = {name: f.result() for name, f in pending.items()}  # re-raises a sampling crash
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)  # record the failure now, not after billed sampling ends
            raise
        pool.shutdown()
        for name in subset_preds:
            self._check_errors(name, [p for p in map(json.loads, subset_preds[name].read_text(encoding="utf-8").splitlines()) if p], len(self.subset))

        self.manifest.update(stage="scoring")
        subset_scores = {name: self._score(name, p, full=False) for name, p in subset_preds.items()}
        self.manifest.update(subset_scores={n: s["selection"] for n, s in subset_scores.items()})
        ranked = sorted((n for n in subset_scores if n != ckpt_name(0)), key=lambda n: (-subset_scores[n]["selection"], n))
        finalists = [ckpt_name(0)] + ranked[: cfg.top_k]

        self.manifest.update(stage="full-dev", finalists=finalists)
        full_scores = {}
        for name in finalists:
            have = [json.loads(line) for line in subset_preds[name].read_text(encoding="utf-8").splitlines() if line]
            full = self.preds / f"{name}.full.jsonl"
            rest = self._sample(name, self.checkpoints[name], self.rest)
            self.sample_errors[f"{name}.full"] = sum(p["finish"] == "error" for p in rest)
            write_jsonl(full, have + rest)
            self._check_errors(f"{name}.full", rest, len(self.rest))
            full_scores[name] = self._score(name, full, full=True)
        self.manifest.update(full_scores={n: s["selection"] for n, s in full_scores.items()})
        winner = max(finalists[1:], key=lambda n: (full_scores[n]["selection"], -int(n[1:])))

        self.manifest.update(stage="promoting", winner=winner)
        model = d.promote(self.manifest.data.get("session_name") or "", winner, cfg.model_id(winner))
        self.manifest.update(promoted=model)
        self.log(f"winner {winner}: full-dev selection {full_scores[winner]['selection']:.4f}, base {full_scores[ckpt_name(0)]['selection']:.4f}; promoted {model}")
        return self.manifest.data
