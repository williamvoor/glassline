"""The Express GBNF used for the "+ grammar" eval setups. Checked through Lark (same rules)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from lark import Lark
from lark.exceptions import LarkError

from a2ui_eval.catalog_check import BUILTINS, CatalogCheck
from a2ui_eval.generate import wrap
from a2ui_eval.grammar import ANSWER_OPEN, REASONING_CLOSE, REASONING_OPEN, chat_gbnf, express_gbnf, to_lark

ROOT = Path(__file__).resolve().parents[1]


def _cases(rel: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / rel).read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def check(sdk_v0_9) -> CatalogCheck:
    return CatalogCheck(sdk_v0_9)


@pytest.fixture(scope="module")
def express(check) -> Lark:
    return Lark(to_lark(express_gbnf(check), "express"), parser="earley", lexer="dynamic")


@pytest.fixture(scope="module")
def chat(check) -> Lark:
    return Lark(to_lark(chat_gbnf(check), "root"), parser="earley", lexer="dynamic")


def accepts(parser: Lark, text: str) -> bool:
    try:
        parser.parse(text)
        return True
    except LarkError:
        return False


def test_every_dev_reference_is_accepted(express):
    cases = _cases("data/dev/cases.jsonl") + _cases("data/synth/dev/cases.jsonl")
    rejected = [c["id"] for c in cases if c.get("reference_express") and not accepts(express, wrap(c["reference_express"]))]
    assert rejected == []


def test_every_train_gallery_reference_is_accepted(express):
    cases = _cases("data/train/cases.jsonl")
    rejected = [c["id"] for c in cases if c.get("reference_express") and not accepts(express, wrap(c["reference_express"]))]
    assert rejected == []


@pytest.mark.parametrize(
    "body",
    [
        'root = Text(if($/change >= 0, "up", "down"))',  # operator, and `if` isn't a catalog function
        "root = Text($/a + $/b)",  # operator
        'root = Chart($/rows)',  # component outside the catalog
        'root = TextField($/x, "Name", checks=[?isPositive])',  # check that isn't a catalog function
        'root = Text($/ok ? "yes" : "no")',  # ternary
    ],
)
def test_rejects_language_outside_express_and_the_catalog(express, body):
    assert not accepts(express, wrap(body))


def test_accepts_comments_semicolons_raw_strings_and_trailing_commas(express):
    body = '# heading\nroot = Column([a, b,]); a = Text(r"C:\\path", "h1")\nb = Button(t, _, Event("go", {k: 1.5e3, "q": true,}))'
    assert accepts(express, wrap(body))


def test_needs_both_sentinel_tags(express):
    assert not accepts(express, 'root = Text("x")\n</a2ui>')
    assert not accepts(express, '<a2ui>\nroot = Text("x")\n')


def test_chat_framing_with_and_without_reasoning(chat):
    answer = wrap('root = Text("Hello")')
    assert accepts(chat, ANSWER_OPEN + answer)
    assert accepts(chat, REASONING_OPEN + "Need a Text. Use <a2ui> tags." + REASONING_CLOSE + ANSWER_OPEN + answer)
    assert not accepts(chat, answer)  # the parser would see no user-visible message
    assert not accepts(chat, ANSWER_OPEN + answer + " trailing prose")


def test_call_names_come_from_the_catalog(check):
    other = SimpleNamespace(components={"Gauge": set()}, functions={"isPositive": set()})
    for c in (check, other):
        lines = dict(l.split(" ::= ", 1) for l in express_gbnf(c).splitlines())
        names = lambda rule: {a.strip().strip('"') for a in lines[rule].split("|")}  # noqa: E731
        assert names("callname") == set(BUILTINS) | set(c.components) | set(c.functions)
        assert names("checkname") == set(c.functions)
    parser = Lark(to_lark(express_gbnf(other), "express"), parser="earley", lexer="dynamic")
    assert accepts(parser, wrap("root = Gauge($/v, checks=[?isPositive])"))
    assert not accepts(parser, wrap('root = Text("x")'))


def test_triple_quoted_strings(express):
    assert accepts(express, wrap('root = Text("""# Notes\n\nSay "hi" or ""twice"".""")'))
    assert accepts(express, wrap('root = Text(r"""raw \\d+ "q" """)'))
    assert not accepts(express, wrap('root = Text("""a""" b""")'))


def test_grammar_cli_writes_the_chat_grammar(tmp_path, check):
    from a2ui_eval.data_cli import main

    out = tmp_path / "g.gbnf"
    assert main(["grammar", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == chat_gbnf(check)
