import pytest

from austender.config import Settings


def test_request_spacing_defaults_to_one_second():
    assert Settings.from_env({}).min_interval_s == 1.0


@pytest.mark.parametrize(("ms", "expected"), [("0", 1.0), ("100", 1.0), ("999", 1.0), ("1500", 1.5)])
def test_request_spacing_never_goes_below_one_second(ms, expected):
    assert Settings.from_env({"INGEST_MIN_INTERVAL_MS": ms}).min_interval_s == expected
