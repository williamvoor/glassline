"""One chat completion through LiteLLM, run in its own throwaway environment.

Standalone on purpose: litellm==1.106.6 needs huggingface-hub<2, which conflicts
with glimmer-tune's own deps, so vocab_writer runs this file with
`uv run --no-project --with litellm==1.106.6 ...`. Reads a JSON request on stdin
and prints {"content", "prompt_tokens", "cached_tokens"} on stdout. Keys are read
from the given .env and never printed.
"""

import json
import sys


def main() -> None:
    import litellm
    from dotenv import load_dotenv

    req = json.load(sys.stdin)
    load_dotenv(req["env_file"])
    # Optional keys (temperature, max_tokens) are sent only when the request has them, and a null
    # reasoning_effort is left out, so the vocabulary writer's calls are unchanged.
    extra = {k: req[k] for k in ("temperature", "top_p", "max_tokens", "allowed_openai_params") if req.get(k) is not None}
    if req.get("reasoning_effort") is not None:
        extra["reasoning_effort"] = req["reasoning_effort"]
    resp = litellm.completion(
        model=req["model"],
        messages=req["messages"],
        prompt_cache_key=req["cache_key"],
        **extra,
    )
    usage = getattr(resp, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None) if usage else None
    json.dump(
        {
            "content": resp.choices[0].message.content or "",
            "finish_reason": resp.choices[0].finish_reason,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "cached_tokens": getattr(details, "cached_tokens", None) if details else None,
            "reasoning_tokens": getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None) if usage else None,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
