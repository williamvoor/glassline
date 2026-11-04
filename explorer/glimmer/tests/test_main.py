"""Startup: failures exit 1 with no key in the message; logging keeps third-party INFO quiet."""

import logging
import socket
import types

import pytest

from glimmer_server import __main__ as M

KEY = "fw-secret-123"
CKPT = "acct/run-x/s225-abc"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("EXPLORER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("FIREWORKS_API_KEY", KEY)
    monkeypatch.setenv("GLIMMER_CHECKPOINT", CKPT)
    return monkeypatch


def _fake_build(monkeypatch, fn):
    import sys

    mod = types.ModuleType("glimmer_server.fireworks")
    mod.build = fn
    monkeypatch.setitem(sys.modules, "glimmer_server.fireworks", mod)


def test_missing_setting_exits_1(env, capsys):
    env.delenv("GLIMMER_CHECKPOINT")
    assert M.main() == 1 and "GLIMMER_CHECKPOINT" in capsys.readouterr().err


def test_failed_backend_build_exits_1_without_the_key(env, capsys):
    def boom(key, ckpt):
        raise RuntimeError(f"401 bad key {key} for {ckpt}")

    _fake_build(env, boom)
    assert M.main() == 1
    err = capsys.readouterr().err
    assert "s225-abc" in err and KEY not in err and CKPT not in err


def test_port_in_use_exits_1_and_closes_the_backend(env, capsys):
    closed = []
    _fake_build(env, lambda k, c: types.SimpleNamespace(close=lambda: closed.append(True)))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        env.setenv("GLIMMER_URL", f"http://127.0.0.1:{s.getsockname()[1]}")
        assert M.main() == 1
    assert "could not listen" in capsys.readouterr().err and closed == [True]


def test_logging_keeps_third_party_info_quiet():
    """The SDK logs the sampler's model route at INFO; only our logger may log at INFO."""
    M.configure_logging()
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("glimmer_server").getEffectiveLevel() == logging.INFO
    assert not logging.getLogger("fireworks.training.sdk").isEnabledFor(logging.INFO)
