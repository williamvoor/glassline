"""`uv run python -m glimmer_server`: load settings, build the backend, serve on 127.0.0.1."""

from __future__ import annotations

import logging
import sys

from .app import Server, redact
from .settings import SettingsError, checkpoint_label, load


def configure_logging() -> None:
    """Root at WARNING: the SDK logs the sampler's model route at INFO, and the checkpoint path
    must not reach the log. Only our own request lines log at INFO."""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("glimmer_server").setLevel(logging.INFO)


def main() -> int:
    configure_logging()
    try:
        s = load()
    except SettingsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    label = checkpoint_label(s.checkpoint)
    secrets = [s.api_key, s.checkpoint]
    try:
        from .fireworks import build

        backend = build(s.api_key, s.checkpoint)
    except Exception as e:  # noqa: BLE001 - startup failure: report without the key, then exit
        print(f"error: could not start the sampler for {label}: {redact(f'{type(e).__name__}: {e}', secrets)}", file=sys.stderr)
        return 1
    try:
        server = Server(s.port, backend, label, secrets)
    except OSError as e:
        print(f"error: could not listen on 127.0.0.1:{s.port}: {e.strerror or e}", file=sys.stderr)
        backend.close()
        return 1
    logging.getLogger("glimmer_server").info("serving %s on http://127.0.0.1:%d", label, s.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
