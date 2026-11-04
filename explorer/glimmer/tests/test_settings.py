import pytest

from glimmer_server.settings import SettingsError, checkpoint_label, load

BASE = {"FIREWORKS_API_KEY": "fw-secret-123", "GLIMMER_CHECKPOINT": "a/b/s225-x"}


def _no_file(tmp_path):
    return {"EXPLORER_ENV_FILE": str(tmp_path / "missing.env")}


def test_defaults(tmp_path):
    s = load({**BASE, **_no_file(tmp_path)})
    assert s.port == 8765 and s.checkpoint == "a/b/s225-x"
    assert "fw-secret-123" not in repr(s) and "fw-secret-123" not in str(s)


def test_environment_wins_over_env_file(tmp_path):
    f = tmp_path / "x.env"
    f.write_text("FIREWORKS_API_KEY=from-file\nGLIMMER_CHECKPOINT=a/b/file\nGLIMMER_URL=http://localhost:9000\n")
    s = load({"EXPLORER_ENV_FILE": str(f), "GLIMMER_CHECKPOINT": "a/b/env"})
    assert (s.api_key, s.checkpoint, s.port) == ("from-file", "a/b/env", 9000)


@pytest.mark.parametrize("drop, match", [("FIREWORKS_API_KEY", "FIREWORKS_API_KEY"), ("GLIMMER_CHECKPOINT", "GLIMMER_CHECKPOINT")])
def test_missing_settings_are_refused(tmp_path, drop, match):
    env = {k: v for k, v in BASE.items() if k != drop}
    with pytest.raises(SettingsError, match=match):
        load({**env, **_no_file(tmp_path)})


@pytest.mark.parametrize("url", ["http://0.0.0.0:8765", "http://192.168.1.5:8765", "http://example.com"])
def test_non_loopback_host_is_refused(tmp_path, url):
    with pytest.raises(SettingsError, match="local only"):
        load({**BASE, "GLIMMER_URL": url, **_no_file(tmp_path)})


def test_checkpoint_label():
    assert checkpoint_label("williamvoor/run-1/s225-2939ff3b") == "s225-2939ff3b"
    assert checkpoint_label("a/b/s000-x/") == "s000-x"
