"""The real backend: Fireworks training SDK sampler plus the cookbook's `muse_glimmer` renderer.

SDK imports happen only in `build()`, so tests can exercise `FireworksBackend` with fakes.
`sampler._submit` and `sampler.deployment_sampler` are private SDK surface, the same path as
glimmer-tune/trainer's `real.direct`. They are behind the exact `fireworks-ai` pin.
"""

from __future__ import annotations

import uuid
from typing import Any

from .app import SurfaceRequest

TOKENIZER = "meta-models/Muse-Glimmer-30B"
RENDERER = "muse_glimmer"
API_ROOT = "https://api.fireworks.ai"
USAGE_KEYS = ("prompt_tokens", "cached_prompt_tokens", "completion_tokens")


class FireworksBackend:
    """Holds one sampling client for the process. Thread-safe: `_submit` runs every request on the
    SDK's one event loop (researcher report, 2026-11-05)."""

    def __init__(self, renderer: Any, sampler: Any, get_text_content: Any):
        self.renderer, self.sampler, self.get_text_content = renderer, sampler, get_text_content

    def __call__(self, req: SurfaceRequest) -> dict[str, Any]:
        ids = [int(t) for t in self.renderer.build_generation_prompt(req.messages).to_ints()]
        extra: dict[str, Any] = {}
        if req.grammar is not None:
            extra["response_format"] = {"type": "grammar", "grammar": req.grammar}
        ds = self.sampler.deployment_sampler
        coro = ds.sample_with_prompt_tokens_result(
            ids, max_tokens=req.max_tokens, temperature=0.0, stop=self.renderer.get_stop_sequences(),
            prompt_cache_key=req.cache_key, logical_request_id=f"glimmer-{uuid.uuid4().hex}", **extra)
        result = self.sampler._submit(coro).result()
        c = result.completions[0]
        tokens = list(c.full_tokens[c.prompt_len:])
        msg, _ = self.renderer.parse_response(tokens)
        metrics = getattr(result, "server_metrics", None)
        usage = {k: _count(getattr(metrics, k, None)) for k in USAGE_KEYS}
        if usage["completion_tokens"] is None:  # serverless may send no metrics; this count is ours
            usage["completion_tokens"] = len(tokens)
        return {"text": self.get_text_content(msg), "finish": "length" if c.finish_reason == "length" else "stop",
                "tokens": len(tokens), "usage": usage}

    def close(self) -> None:
        self.sampler.close()


def _count(v: Any) -> int | None:
    """A count the server didn't report stays `None`, so a missing number is never shown as 0."""
    return None if v is None else int(v)


def build(api_key: str, checkpoint: str) -> FireworksBackend:
    """Loads the tokenizer and renderer and creates the one sampling client (binds the route)."""
    from fireworks.training.sdk import FiretitanServiceClient
    from training.renderer import get_renderer, get_text_content
    from training.renderer.tokenizer import get_tokenizer

    tokenizer = get_tokenizer(TOKENIZER)
    renderer = get_renderer(RENDERER, tokenizer)
    service = FiretitanServiceClient(api_key=api_key, base_url=f"{API_ROOT}/training/v1/serverless")
    sampler = service.create_sampling_client(model_path=checkpoint, tokenizer=tokenizer)
    return FireworksBackend(renderer, sampler, get_text_content)
