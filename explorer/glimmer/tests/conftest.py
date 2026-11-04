import json
import threading
import urllib.error
import urllib.request

import pytest

from glimmer_server.app import Server

KEY = "fw-secret-key-123"
CKPT = "acct/run-abc/s225-2939ff3b"


class StubBackend:
    def __init__(self, reply=None, error=None, delay=0.0):
        self.calls, self.reply, self.error, self.delay = [], reply, error, delay

    def __call__(self, req):
        import time

        self.calls.append(req)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.reply or {"text": "<a2ui>root = Text(\"hi\")</a2ui>", "finish": "stop", "tokens": 7,
                              "usage": {"prompt_tokens": 100, "cached_prompt_tokens": 80, "completion_tokens": 7}}


@pytest.fixture
def serve():
    servers = []

    def start(backend):
        s = Server(0, backend, "s225-2939ff3b", [KEY, CKPT])
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers.append(s)
        return f"http://127.0.0.1:{s.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def call(url, path, body=None, method=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def body(**over):
    return {"messages": [{"role": "system", "content": "rules\n\nReasoning strength: low."}, {"role": "user", "content": "brief"}],
            "cache_key": "explorer-glimmer", **over}
