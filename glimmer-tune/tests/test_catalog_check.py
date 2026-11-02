"""Outside-the-catalog check: operators, and calls or keyword arguments the active catalog lacks."""

import pytest

from a2ui_eval.catalog_check import CatalogCheck, kinds
from a2ui_eval.checkpoints import CATALOG, dev_cases
from a2ui_eval.sdk import ExpressSDK


@pytest.fixture(scope="module")
def check():
    return CatalogCheck(ExpressSDK(CATALOG))


def _kinds(check, src):
    return kinds(check.findings(src))


@pytest.mark.parametrize(
    "src",
    [
        'root = Text("Revenue > target, a ? b : c && d")',  # operators inside a plain string are text
        'f = TextField("Zip", $/zip, "shortText", r"^\\d{5}$", ?regex("^\\+?[0-9]{5}$", "5 digits"))',
        "b = Button(t, _, Event(\"go\", {a: $/a}), ?and([required($/a), not(required($/b))]))",
        'surface("s-1", sendDataModel=true)\n$/items = [{icon: "check", n: -1, x: +2.5}]\nroot = Column(_template($/items, row))\nrow = Icon($icon)',
        'root = Text("x", variant="h1", id="t")  # a > b in a comment\n// c ? d\n/* e >= f */',
        "x = 'single quoted > text'",
        'root = Text(formatString("${/a} of ${formatNumber(value: ${/b}, decimals: 2)}"))',
        'e = Event("go", context={a: $/a}, userMessage="hi")',
        'deleteSurface("s-1")',
        "root = Text($)",
    ],
)
def test_legal_express_has_no_findings(check, src):
    assert check.findings(src) == []


def test_ternary_icon_from_section_7(check):
    found = check.findings('row = Icon($completed ? "check" : "arrowForward")')
    assert kinds(found) == ["operator"]
    assert found[0].detail == "?"


def test_if_with_a_comparison_from_section_7(check):
    found = check.findings('c = Text($change, if($change >= 0, "body", "caption"))')
    assert kinds(found) == ["operator", "unknown_function"]
    assert {f.detail for f in found} == {">=", "if"}


@pytest.mark.parametrize(
    "src,op",
    [
        ("t = Text($/a == $/b)", "=="),
        ("t = Text($/a != 1)", "!="),
        ("t = Text($/a < 1)", "<"),
        ("t = Text($/a && $/b)", "&&"),
        ("t = Text($/a || $/b)", "||"),
        ("t = Text(!$/a)", "!"),
        ("t = Text($/a + 1)", "+"),
        ("t = Text($/a * 2)", "*"),
        ("t = Text($/a % 2)", "%"),
        ("t = Text($/a -1)", "-"),  # a signed number right after a value is subtraction
        ("t = Text($/a - $/b)", "-"),
        ('t = Text("${/a > 1}")', ">"),  # inside an interpolation
        ('t = Text("${/done ? \'yes\' : \'no\'}")', "?"),
    ],
)
def test_operators(check, src, op):
    found = check.findings(src)
    assert kinds(found) == ["operator"]
    assert found[0].detail == op


def test_unknown_names(check):
    assert _kinds(check, 'root = Badge("x")') == ["unknown_component"]
    assert _kinds(check, 'root = Text(upper("x"))') == ["unknown_function"]
    assert _kinds(check, 'root = Text("x", colour="red")') == ["unknown_property"]
    assert _kinds(check, "f = TextField(\"a\", $/a, ?positive)") == ["unknown_function"]
    assert _kinds(check, 'root = Text("${map(/xs)}")') == ["unknown_function"]
    assert _kinds(check, 'root = Text(formatNumber($/a, places=2))') == ["unknown_property"]


def test_every_dev_reference_is_clean(check):
    """The references were written in Express that compiles: the check must find nothing in them."""
    dirty = [c["id"] for c in dev_cases() if check.findings(c["reference_express"])]
    assert dirty == []


@pytest.mark.parametrize(
    "src",
    [
        '$/Term Summary/Term = "Fall"',  # base model: a path with a space is broken syntax, not division
        "t = Text($/courses[0]/name)",
        "t = Text($/first-name)",
    ],
)
def test_broken_paths_are_not_operators(check, src):
    """Regression (base rescore): `/` or `-` glued to a name on both sides is a malformed path."""
    assert check.findings(src) == []


def test_spaced_division_is_still_an_operator(check):
    assert [f.detail for f in check.findings("t = Text($/a / 2)")] == ["/"]
