"""The Boards step with a scripted model: never the network (SPEC.md phase 3: D9-D12)."""

import json

import explorer_agent  # noqa: F401  (first: keeps LiteLLM from fetching its price map)
import pytest
from explorer_agent.boards import BOARD_FILE_RULES
from explorer_agent.testing.stub_model import ScriptedCompletion, text, tool_calls

from explorer_bootstrap.boards import (
    TEMPLATE_FILES,
    TOOLS,
    boards_runner,
    boards_state,
    build_boards_prompt,
    check_boards,
    express_rules,
    run_boards,
    shape_problems,
)
from explorer_bootstrap.draft import DraftError
from explorer_bootstrap.drafts import DraftFileError, draft_dataset, new_draft, schema_section
from explorer_bootstrap.settings import load_draft_settings

from conftest import valid_args

EXPLORER = '''title = "Explorer"
question = "Where do the amounts go?"
layout = """root = Column([total, grades])
total = Metric("totals", "amount", "Total amount", 0, "int")
grades = DataTable("by_grade", "By grade", "grade,amount", "amount:int")"""

[[queries]]
name = "totals"
sql = "SELECT count(*) AS n, sum(amount) AS amount FROM bs.mixed"

[[queries]]
name = "by_grade"
sql = "SELECT grade, sum(amount) AS amount FROM bs.mixed GROUP BY grade ORDER BY grade"
'''
GRADE = '''title = "Grade"
scope = "Every row with this grade, not only the clicked answer's."
key = "g"
key_type = "int"
key_columns = ["grade"]
layout = """root = Column([total, top])
total = Metric("totals", "amount", "Amount", 0, "int")
top = DataTable("rows", "Rows", "id,amount", "amount:int")"""

[[queries]]
name = "totals"
sql = "SELECT sum(amount) AS amount FROM bs.mixed WHERE grade = :g"

[[queries]]
name = "rows"
sql = "SELECT id, amount FROM bs.mixed WHERE grade = :g ORDER BY amount DESC LIMIT 10"
'''


def board(name, toml, why="Because."):
    return {"name": name, "toml": toml, "why": why}


def good_set():
    return [board("explorer", EXPLORER, "The Dataset at a glance."), board("grade", GRADE, "Grades key many rows.")]


@pytest.fixture
def settings():
    return load_draft_settings({"BOOTSTRAP_MAX_TOOL_ITERATIONS": "4"})


@pytest.fixture
def draft(bs_report):
    return new_draft(bs_report, valid_args(), model="m", effort="low", usage={})


@pytest.fixture
def dataset(draft):
    return draft_dataset(draft)


@pytest.fixture
def runner(ro_pool, dataset):
    return boards_runner(ro_pool, dataset, 2000, 50)


def _tool_messages(call):
    return [json.loads(m["content"]) for m in call["messages"] if m["role"] == "tool"]


# --- D9: the prompt ------------------------------------------------------------------------------


@pytest.mark.db
def test_prompt_holds_the_parts_in_order_and_is_stable(draft, dataset):
    section = schema_section(draft, dataset)
    prompt = build_boards_prompt(dataset, section, 3)
    assert prompt == build_boards_prompt(draft_dataset(draft), schema_section(draft, dataset), 3)
    assert prompt != build_boards_prompt(dataset, section, 2)
    assert prompt.startswith(express_rules())
    marks = ["## Instructions\n", f"## Board file rules\n{BOARD_FILE_RULES}\n", "## Templates\n",
             *(f"### {n}\n" for n in TEMPLATE_FILES), "## Dataset: bs\n", "### Schema\n",
             "## Limit\nDraft at most 3 Entity Boards."]
    at = [prompt.index(m) for m in marks]
    assert at == sorted(at)
    assert "bs.mixed" in prompt and "OVERLAY" not in prompt


@pytest.mark.db
def test_a_draft_whose_files_dont_validate_has_no_dataset(draft):
    draft["files"]["dataset_toml"] = "name = "
    with pytest.raises(DraftFileError, match="not valid TOML"):
        draft_dataset(draft)


# --- D10: the loop and the checks on submit ------------------------------------------------------


def test_submit_boards_schema_follows_adr_0023():
    params = TOOLS[1]["function"]["parameters"]

    def walk(node):
        assert "oneOf" not in node and "anyOf" not in node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(params)
    assert [t["function"]["name"] for t in TOOLS] == ["run_sql", "submit_boards"]


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({}, "'boards' is a required property"),
        ({"boards": []}, "non-empty list"),
        ({"boards": [{"name": "explorer", "toml": ""}]}, "'why' is a required property"),
        ({"boards": [{"name": "explorer", "toml": 1, "why": "x"}]}, "boards[0].toml: must be string"),
        ({"boards": [{"name": "explorer", "toml": "", "why": "two\nlines"}]}, "why must be one line"),
    ],
)
def test_shape_problems(args, problem):
    assert any(problem in p for p in shape_problems(args))


@pytest.mark.db
async def test_loop_returns_tool_errors_naming_the_board_then_ends_on_a_clean_submit(draft, runner, settings):
    bad_column = EXPLORER.replace('"grade,amount"', '"grade,amont"')
    other_schema = GRADE.replace("FROM bs.mixed WHERE grade = :g ORDER", "FROM mart.contract WHERE grade = :g ORDER")
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT count(DISTINCT grade) AS n FROM bs.mixed", "purpose": "grades"})),
        tool_calls(("submit_boards", {"boards": [board("explorer", bad_column), board("grade", other_schema),
                                                  board("Bad-Name", GRADE)]})),
        tool_calls(("submit_boards", {"boards": good_set()})),
    ])
    events = []
    result = await run_boards(draft, runner, settings, completion=model, on_event=events.append)
    assert [b["name"] for b in result.files] == ["explorer", "grade"]
    assert result.check.ok
    first = model.calls[0]
    assert first["prompt_cache_key"] == "bootstrap-boards-bs"
    assert [t["function"]["name"] for t in first["tools"]] == ["run_sql", "submit_boards"]
    assert _tool_messages(model.calls[1])[0]["rows"] == [{"n": 7}]
    error = _tool_messages(model.calls[2])[-1]["error"]
    assert "call submit_boards again" in error["message"]
    problems = "\n".join(error["problems"])
    assert "boards/explorer.toml: layout:" in problems and "amont" in problems
    assert "boards/grade.toml: queries.rows.sql" in problems
    assert "boards/Bad-Name.toml: a Board file name" in problems
    assert [e["type"] for e in events] == ["call", "sql", "call", "invalid_boards", "call"]
    assert result.usage["totals"]["calls"] == 3


@pytest.mark.db
async def test_the_set_rules_are_checked(dataset, runner):
    no_explorer = await check_boards([board("grade", GRADE)], dataset, runner, 3)
    assert any("explorer.toml is required" in p for p in no_explorer.set_problems)
    keyed = await check_boards([board("explorer", GRADE)], dataset, runner, 3)
    assert any("can't have a key" in p for p in keyed.set_problems)
    twice = await check_boards([*good_set(), board("grade", GRADE)], dataset, runner, 3)
    assert "boards: grade appears twice" in twice.set_problems


@pytest.mark.db
async def test_a_board_that_does_not_parse_does_not_report_a_missing_explorer(dataset, runner):
    check = await check_boards([board("explorer", "title = "), board("grade", GRADE)], dataset, runner, 3)
    assert check.set_problems == []
    assert "not valid TOML" in check.problems["explorer"][0]
    assert check.problems["grade"] == [] and list(check.boards) == ["grade"]


@pytest.mark.db
async def test_the_cap_is_a_draft_error(draft, runner, settings):
    model = ScriptedCompletion([tool_calls(("submit_boards", {"boards": [board("grade", GRADE)]}))] * 4)
    with pytest.raises(DraftError, match="no valid Boards after 4 model calls"):
        await run_boards(draft, runner, settings, completion=model)


@pytest.mark.db
async def test_no_tool_call_twice_is_a_draft_error(draft, runner, settings):
    model = ScriptedCompletion([text("Done."), text("Done.")])
    with pytest.raises(DraftError, match="without calling submit_boards"):
        await run_boards(draft, runner, settings, completion=model)


# --- D11: at most BOOTSTRAP_MAX_ENTITY_BOARDS Entity Boards --------------------------------------


@pytest.mark.db
async def test_one_entity_board_too_many_is_a_problem(dataset, runner):
    by_id = GRADE.replace('key_columns = ["grade"]', 'key_columns = ["id"]').replace('title = "Grade"', 'title = "Row"')
    files = [*good_set(), board("row", by_id)]
    assert (await check_boards(files, dataset, runner, 2)).ok
    check = await check_boards(files, dataset, runner, 1)
    assert check.set_problems == ["boards: at most 1 Entity Boards; this set has 2 (grade, row)"]


# --- D12: the preview ----------------------------------------------------------------------------


@pytest.mark.db
async def test_preview_reports_queries_visuals_and_the_cold_verdict(draft, dataset, runner, settings):
    empty = EXPLORER.replace("FROM bs.mixed\"", "FROM bs.mixed WHERE grade > 100\"").replace(
        "SELECT count(*) AS n, sum(amount) AS amount FROM bs.mixed WHERE grade > 100",
        "SELECT count(*) AS n, sum(amount) AS amount FROM bs.mixed WHERE grade > 100 GROUP BY grade")
    failing = GRADE.replace("SELECT sum(amount) AS amount", "SELECT sum(amount) / 0 AS amount")
    no_key = GRADE.replace('key_columns = ["grade"]', 'key_columns = ["mixed_grade"]').replace('title = "Grade"', 'title = "Other"')
    files = [board("explorer", empty), board("grade", failing), board("other", no_key)]
    check = await check_boards(files, dataset, runner, 3)
    assert check.ok, check.flat()
    state = await boards_state(files, check, dataset, runner, draft["report"], settings, usage=None)
    preview = state["preview"]
    assert list(preview) == ["explorer", "grade", "other"]

    explorer = preview["explorer"]
    assert explorer["key"] is None and explorer["verdict"] in ("ok", "slow") and explorer["target_s"] == 1.5
    totals, by_grade = explorer["queries"]["totals"], explorer["queries"]["by_grade"]
    assert totals["rows"] == 0 and totals["columns"] == ["n", "amount"]
    assert by_grade["rows"] == 7 and by_grade["truncated"] is False and isinstance(by_grade["ms"], int)
    assert by_grade["first_rows"][0] == ["1", "697.5"] and len(by_grade["first_rows"]) == settings.board_preview_rows
    assert {v["id"]: (v["macro"], v["query"], v["verdict"]) for v in explorer["visuals"]} == {
        "total": ("Metric", "totals", "no_rows"), "grades": ("DataTable", "by_grade", "drawn")}

    grade = preview["grade"]
    assert grade["key"] == {"value": "1", "source": "bs.mixed.grade"}
    assert "22012" in grade["queries"]["totals"]["error"]
    assert grade["queries"]["rows"]["rows"] == 10
    assert {v["id"]: v["verdict"] for v in grade["visuals"]} == {"total": "failed", "top": "drawn"}

    assert preview["other"]["skipped"].startswith("no sample key")
    assert state["problems"] == {"explorer": [], "grade": [], "other": []} and state["boards_version"] == 1
