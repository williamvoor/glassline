"""The real Fireworks, cookbook and scorer wiring. Imported only by the CLI, never by tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .loop import BASE_MODEL, Deps, RunError, pick_checkpoint

TOKENIZER = "meta-models/Muse-Glimmer-30B"
RENDERER = "muse_glimmer"
API_ROOT = "https://api.fireworks.ai"
GLIMMER_TUNE = Path(__file__).resolve().parents[2]


def renderer_and_tokenizer() -> tuple[Any, Any, Any]:
    """(api namespace, renderer, tokenizer). Local apart from the tokenizer download."""
    from fireworks.training import sdk
    from training.renderer import TrainOnWhat, get_renderer, get_text_content
    from training.renderer.tokenizer import get_tokenizer

    tokenizer = get_tokenizer(TOKENIZER)
    api = SimpleNamespace(
        Datum=sdk.Datum,
        ModelInput=sdk.ModelInput,
        AdamParams=sdk.AdamParams,
        SamplingParams=sdk.SamplingParams,
        get_text_content=get_text_content,
        LAST_ASSISTANT_TURN=TrainOnWhat.LAST_ASSISTANT_TURN,
    )
    return api, get_renderer(RENDERER, tokenizer), tokenizer


def _key() -> str:
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key:
        raise SystemExit("FIREWORKS_API_KEY is not set")
    return key


def scorer(dev: Path) -> Any:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "FIREWORKS_API_KEY")}

    def score(pred: Path, out: Path, full: bool) -> dict[str, Any]:
        cmd = ["uv", "run", "--project", str(GLIMMER_TUNE), "python", "-m", "a2ui_eval.checkpoints", "score", str(pred), "--out", str(out), "--dev", str(dev)]
        done = subprocess.run(cmd + (["--full"] if full else []), cwd=GLIMMER_TUNE, env=env, capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(f"scoring {pred.name} failed: {done.stderr.strip()[-500:]}")
        return json.loads(out.read_text(encoding="utf-8"))

    return score


def direct(sampler: Any, ids: list[int], max_tokens: int, stop: list[int], extra: dict[str, Any]) -> dict[str, Any]:
    """One greedy completion through the sampler's `DeploymentSampler`, which forwards extra fields
    (here `response_format`) into the `/inference/v1/completions` payload. Runs on the sampler's own
    event loop (`_submit`, private to the SDK's FiretitanSamplingClient). Probed 2026-11-04: the
    serverless pool honours a GBNF `response_format`."""
    ds = sampler.deployment_sampler

    async def one():
        out = await ds.sample_with_prompt_tokens(ids, n=1, max_tokens=max_tokens, temperature=0.0, stop=stop, logprobs=True, **extra)
        return out[0]

    c = sampler._submit(one()).result()
    usage = {"prompt_tokens": 0, "cached_prompt_tokens": 0, "completion_tokens": 0}
    for m in ds.drain_metrics():  # every request finished since the last drain, from any thread
        for k in usage:
            usage[k] += int(getattr(m, k, 0) or 0)
    return {"tokens": list(c.full_tokens[c.prompt_len :]), "finish": c.finish_reason, "usage": usage}


def deps(dev: Path) -> Deps:
    from fireworks.training.sdk import FireworksClient, FiretitanServiceClient

    key = _key()
    api, renderer, tokenizer = renderer_and_tokenizer()
    service = FiretitanServiceClient(api_key=key, base_url=f"{API_ROOT}/training/v1/serverless")
    try:  # the cookbook's retry on 409s from the shared pool, when present
        from training.utils.serverless import enable_serverless_supervised_409_retry

        enable_serverless_supervised_409_retry(service)
    except ImportError:
        pass
    fw = FireworksClient(api_key=key, base_url=API_ROOT)

    def promote(session_name: str, ckpt: str, model_id: str) -> str:
        if not session_name:
            raise RunError("the training session name is unknown; promote by hand from the manifest")
        target = pick_checkpoint(fw.list_training_session_checkpoints(session_name), ckpt)
        out = fw.promote_session_checkpoint(name=target["name"], output_model_id=model_id, base_model=BASE_MODEL)
        return str(out.get("name") or model_id)

    return Deps(api=api, renderer=renderer, tokenizer=tokenizer, service=service, scorer=scorer(dev), promote=promote, direct=direct)
