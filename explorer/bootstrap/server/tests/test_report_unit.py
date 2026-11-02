import pytest

from explorer_bootstrap.report import ColumnReport, ObjectReport, Report, Summary, render_text
from explorer_bootstrap.stats import base_type, distinct_estimate, is_range_type


@pytest.mark.parametrize(
    ("n_distinct", "est_rows", "expected"),
    [
        (None, 100, None),
        (2.0, 100, 2),
        (0.0, 100, 0),
        (-1.0, 100, 100),  # every row distinct
        (-0.25, 1000, 250),
        (-0.5, None, None),  # a fraction of an unknown row count
    ],
)
def test_distinct_estimate(n_distinct, est_rows, expected):
    assert distinct_estimate(n_distinct, est_rows) == expected


@pytest.mark.parametrize(
    ("type_name", "is_range"),
    [
        ("integer", True),
        ("bigint", True),
        ("smallint", True),
        ("numeric(18,2)", True),
        ("numeric", True),
        ("double precision", True),
        ("real", True),
        ("date", True),
        ("timestamp with time zone", True),
        ("timestamp(3) without time zone", True),
        ("text", False),
        ("character varying(20)", False),
        ("boolean", False),
        ("integer[]", False),
        ("jsonb", False),
        ("time without time zone", False),
    ],
)
def test_range_types(type_name, is_range):
    assert is_range_type(type_name) is is_range


def test_base_type_strips_typmods():
    assert base_type("timestamp(3) with time zone") == "timestamp with time zone"
    assert base_type("numeric(18,2)") == "numeric"


def _report(context_chars: int, scope: str, comment: str | None) -> Report:
    col = ColumnReport("x", "integer", True, None, None, None, None, None, [], None, None, "skipped", None)
    obj = ObjectReport("s", "t", "table", comment, "database" if comment else None, None, None, False, False, [col])
    return Report(
        report_version=1,
        database={"host": "h", "name": "d"},
        dataset=None,
        comment_overlay=None,
        schemas=["s"],
        empty_schemas=["other"],
        summary=Summary(1, 1, 1 if comment else 0, 0, 50.0 if comment else 0.0, False, 0, 0, context_chars, 20_000, scope),
        objects=[obj],
    )


def test_render_text_lists_problems():
    text = render_text(_report(25_000, "schema_only", None))
    assert "Missing comments (2):" in text
    assert "  s.t\n" in text and "  s.t.x\n" in text
    assert "OVER TARGET" in text
    assert "schema section only" in text
    assert "Never analysed" in text
    assert "s.t.x: skipped" in text
    assert "Schemas with no visible objects: other" in text


def test_render_text_full_scope_within_target():
    text = render_text(_report(1_000, "full", "has one"))
    assert "OVER TARGET" not in text
    assert "schema section only" not in text
    assert "Missing comments (1):" in text
