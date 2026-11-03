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


def test_frozen_prompt_matches(sdk):
    """T1: the committed frozen prompt is the prompt training uses (ADR 0015)."""
    from a2ui_eval.prompt import FROZEN_PROMPT

    assert FROZEN_PROMPT.read_text(encoding="utf-8") == build_system_prompt(sdk)


def test_export_refuses_unfrozen_prompt(sdk, tmp_path):
    """T2: a prompt that differs from the frozen file is refused; no file yet means no freeze."""
    import pytest

    from a2ui_eval.prompt import PromptError, checked_system_prompt

    frozen = tmp_path / "system_prompt.txt"
    assert checked_system_prompt(sdk, frozen) == build_system_prompt(sdk)
    frozen.write_text(build_system_prompt(sdk) + " ", encoding="utf-8")
    with pytest.raises(PromptError, match="frozen"):
        checked_system_prompt(sdk, frozen)
    frozen.write_text(build_system_prompt(sdk), encoding="utf-8")
    assert checked_system_prompt(sdk, frozen) == build_system_prompt(sdk)


def test_run_2_rule_follows_the_sdk_snippet(sdk):
    """Item 8: one line after the upstream snippet, which itself stays byte-identical."""
    from a2ui_eval.prompt import RUN_2_RULE

    assert RUN_2_RULE == "Express has no conditionals or operators. Values that vary per item belong in the data model."
    assert build_system_prompt(sdk) == f"{sdk.prompt_snippet}\n\n{RUN_2_RULE}\n\nReasoning strength: low."
