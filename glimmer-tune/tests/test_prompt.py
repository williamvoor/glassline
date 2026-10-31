from datetime import date

from a2ui_eval.prompt import build_system_prompt
from a2ui_eval.sdk import ExpressSDK
from a2ui_eval.seeds import load_seeds
from tests.conftest import CATALOG


def test_stable(sdk):
    assert build_system_prompt(sdk) == build_system_prompt(ExpressSDK(CATALOG))


def test_reasoning_low(sdk):
    prompt = build_system_prompt(sdk)
    assert prompt.endswith("\n\nReasoning strength: low.")
    assert prompt.lower().count("reasoning strength") == 1


def test_no_dates(sdk):
    assert date.today().isoformat() not in build_system_prompt(sdk)


def test_no_gallery_examples(sdk):
    prompt = build_system_prompt(sdk)
    for seed in load_seeds().values():
        assert seed.name not in prompt
        assert seed.messages[0]["createSurface"]["surfaceId"] not in prompt
