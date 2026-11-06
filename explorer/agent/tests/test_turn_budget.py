"""MAX_TURNS_PER_DAY (specs/deployment): a hard daily cap on model runs across all users, for a
public deploy whose model key has no provider-side spend cap. Drill-downs and Boards use no model
and are never capped."""

from datetime import date

import httpx
import pytest

from explorer_agent.__main__ import EXPLORER_ROOT, build_workspace
from explorer_agent.budget import TURN_LIMIT_PROSE, TurnBudget
from explorer_agent.config import load_settings
from explorer_agent.drill import Prefetcher
from explorer_agent.executor import ACTION_FAILED_KEY, LEVEL_KEY, Conversations, DrillDowns
from explorer_agent.server import build_app
from explorer_agent.testing.stub_model import ScriptedCompletion, text
from tests.test_executor import PLAIN_REPLY, REPLY, Answers, Runner, act, chart_action, meta, prose, send_action
from tests.test_server import ORIGIN, URL, stream


class Clock:
    def __init__(self, day: date):
        self.day = day

    def __call__(self) -> date:
        return self.day


def test_budget_allows_the_limit_then_refuses_until_the_next_day():
    clock = Clock(date(2026, 11, 5))
    budget = TurnBudget(2, today=clock)
    assert [budget.take(), budget.take(), budget.take()] == [True, True, False]
    assert budget.take() is False
    clock.day = date(2026, 11, 6)
    assert [budget.take(), budget.take(), budget.take()] == [True, True, False]


def make(limit: int):
    answers = Answers(REPLY)  # its chart has drill_sql
    runner = Runner()
    drills = DrillDowns(runner, answers.pipeline, Prefetcher(runner, rows=0, timeout_ms=3000, max_held=30),
                        rows_to_model=20)
    budget = TurnBudget(limit, today=Clock(date(2026, 11, 5)))
    app = build_app(answers, url=URL, client_origin=ORIGIN, conversations=Conversations(), drills=drills,
                    budget=budget)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL), answers, runner


async def test_a_question_over_the_limit_is_refused_without_the_model():
    client, answers, _ = make(1)
    async with client:
        first = await stream(client)
        second = await stream(client, "and by year?", context_id=first[-1]["contextId"])
        other = await stream(client, "a new conversation")  # the cap is for everyone, not per Conversation
    assert LEVEL_KEY in meta(first)
    assert len(answers.texts) == 1  # only the first reached the model
    for events in (second, other):
        assert prose(events) == TURN_LIMIT_PROSE
        assert LEVEL_KEY not in meta(events)  # no Turn, no Level, no tab


async def test_an_ask_over_the_limit_fails_as_an_action_and_a_drill_down_still_works():
    client, answers, runner = make(1)
    async with client:
        first = await stream(client)
        ctx = first[-1]["contextId"]
        surface, _ = chart_action(first)
        ask = await send_action(client, act(surface, "ask", source="v1", row=0), ctx)
        drill = await send_action(client, act(surface, source="v1", row=0), ctx)
    assert len(answers.texts) == 1
    assert meta(ask) == {ACTION_FAILED_KEY: True}
    assert prose(ask) == TURN_LIMIT_PROSE
    assert LEVEL_KEY in meta(drill)  # database only: not counted, not capped
    assert runner.calls


async def test_without_a_budget_there_is_no_cap():
    answers = Answers(PLAIN_REPLY)
    app = build_app(answers, url=URL, client_origin=ORIGIN, conversations=Conversations())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as client:
        for _ in range(3):
            await stream(client)
    assert len(answers.texts) == 3


# Wiring: MAX_TURNS_PER_DAY from the environment reaches the running agent (startup builds the budget).
@pytest.mark.db
async def test_max_turns_per_day_caps_the_started_agent(pg):
    settings = load_settings({"DATABASE_URL": pg.ro_dsn, "DATASET_DIR": "agent/tests/fixtures/austender",
                              "MAX_TURNS_PER_DAY": "1"}, EXPLORER_ROOT)
    model = ScriptedCompletion([text("One."), text("Two.")])
    running = await build_workspace(settings, URL, completion=model)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=running.app), base_url=URL) as client:
            first = await stream(client)
            second = await stream(client)
    finally:
        await running.close()
    assert prose(first) == "One."
    assert prose(second) == TURN_LIMIT_PROSE


def test_the_limit_message_ends_with_the_configured_contact():
    assert TurnBudget(1).message == TURN_LIMIT_PROSE
    contact = "To reset it, contact the owner on GitHub: https://github.com/example"
    assert TurnBudget(1, contact=f"  {contact} ").message == f"{TURN_LIMIT_PROSE} {contact}"


async def test_a_refused_question_shows_the_contact():
    answers = Answers(PLAIN_REPLY)
    budget = TurnBudget(1, contact="Ask on GitHub.", today=Clock(date(2026, 11, 5)))
    app = build_app(answers, url=URL, client_origin=ORIGIN, conversations=Conversations(), budget=budget)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as client:
        await stream(client)
        refused = await stream(client)
    assert prose(refused) == f"{TURN_LIMIT_PROSE} Ask on GitHub."
