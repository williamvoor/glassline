"""A GBNF grammar for A2UI Express answers, for constrained decoding (the "+ grammar" eval setups).

It follows `Express.g4` (upstream, SDK pin), with three narrowings:
- Call names are the active catalog's components and functions plus the Express built-ins, and
  check names (`?name`) are its functions. These come from the catalog JSON (via `CatalogCheck`),
  never from a hard-coded list. Named-argument names stay free.
- No block comments: no reference uses them. Triple-quoted strings can't use lazy matching in
  GBNF, so their body is "no three quotes in a row", and the body can't end with a quote.
- Identifiers are ASCII (`[A-Za-z_][A-Za-z0-9_]*`), a subset of UAX 31.

The answer is `<a2ui>` statements `</a2ui>`. `chat_gbnf` adds Muse Glimmer's channel framing, which the
grammar has to produce because it constrains the whole completion: an optional reasoning segment, then
the user-visible message (renderer `muse_glimmer`: ` to=self<|message|>…<|eom|><|start|>assistant`
then ` to=user<|message|>`).

`to_lark` turns the same rules into a Lark grammar, so tests can check which texts the grammar accepts.
"""

from __future__ import annotations

import json
import re

from .catalog_check import BUILTINS, CatalogCheck

REASONING_OPEN = " to=self<|message|>"
REASONING_CLOSE = "<|eom|><|start|>assistant"
ANSWER_OPEN = " to=user<|message|>"

# GBNF rules. `{calls}` and `{checks}` are filled from the catalog.
_EXPRESS = r"""
express ::= "<a2ui>" ws (statement ws)* "</a2ui>"
ws ::= ([ \t\r\n;] | comment)*
comment ::= ("#" | "//") [^\r\n]* [\r\n]
statement ::= assignment | expr
assignment ::= (ident | path) ws "=" ws expr
expr ::= array | map | path | check | call | ident | literal
array ::= "[" ws (expr ws ("," ws expr ws)* ("," ws)?)? "]"
map ::= "{" ws (entry ws ("," ws entry ws)* ("," ws)?)? "}"
entry ::= (ident | stdstr) ws ":" ws expr
path ::= "$" [a-zA-Z0-9_/]*
check ::= "?" checkname (ws "(" ws (expr ws ("," ws expr ws)* ("," ws)?)? ")")?
call ::= callname ws "(" ws (arg ws ("," ws arg ws)* ("," ws)?)? ")"
arg ::= ident ws "=" ws expr | expr
literal ::= triplestr | rawtriplestr | stdstr | rawstr | number
triplestr ::= "\"\"\"" ([^"\\] | "\\" [^\x00] | "\"" [^"\\] | "\"\"" [^"\\])* "\"\"\""
rawtriplestr ::= [rR] "\"\"\"" ([^"] | "\"" [^"] | "\"\"" [^"])* "\"\"\""
stdstr ::= "\"" ([^"\\] | "\\" [^\r\n])* "\""
rawstr ::= [rR] "\"" [^\r\n"]* "\""
number ::= [+-]? ([0-9]+ ("." [0-9]*)? | "." [0-9]+) ([eE] [+-]? [0-9]+)?
ident ::= [a-zA-Z_] [a-zA-Z0-9_]*
callname ::= {calls}
checkname ::= {checks}
"""

_CHAT = r"""
root ::= reasoning? answeropen express
reasoning ::= reasoningopen ([^<] | "<" [^|])* reasoningclose
reasoningopen ::= {ropen}
reasoningclose ::= {rclose}
answeropen ::= {aopen}
"""
# `true`, `false`, `null` and `_` are identifiers here: Express.g4 lexes them as BOOLEAN/'null'/'_',
# which an `ident` alternative already covers, so the language accepted is the same.


def _alts(names: list[str]) -> str:
    return " | ".join(_lit(n) for n in sorted(names, key=lambda n: (-len(n), n)))


def _lit(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def express_gbnf(check: CatalogCheck) -> str:
    calls = list(BUILTINS) + list(check.components) + list(check.functions)
    return _EXPRESS.replace("{calls}", _alts(calls)).replace("{checks}", _alts(list(check.functions))).strip() + "\n"


def chat_gbnf(check: CatalogCheck) -> str:
    """The grammar sent with each request: Glimmer's channel framing around `express`."""
    chat = _CHAT.replace("{ropen}", _lit(REASONING_OPEN)).replace("{rclose}", _lit(REASONING_CLOSE)).replace("{aopen}", _lit(ANSWER_OPEN))
    return chat.strip() + "\n" + express_gbnf(check)


_RULE = re.compile(r"^(\w+) ::= (.*)$")
_PIECE = re.compile(r'"(?:\\.|[^"\\])*"|\[(?:\\.|[^\]\\])*\]|[()|*+?]|\w+|\s+')


def _rules(gbnf: str) -> dict[str, list[str]]:
    rules = {}
    for line in gbnf.strip().splitlines():
        m = _RULE.match(line)
        if not m:
            raise ValueError(f"unsupported GBNF line: {line!r}")
        name, body = m.groups()
        pieces = _PIECE.findall(body)
        if "".join(pieces) != body:
            raise ValueError(f"unsupported GBNF in rule {name}")
        rules[name] = pieces
    return rules


def _lexical(rules: dict[str, list[str]]) -> set[str]:
    """Rules that use only literals, classes and other lexical rules (no recursion): these become regexes."""
    lexical: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, pieces in rules.items():
            refs = {p for p in pieces if re.fullmatch(r"\w+", p)}
            if name not in lexical and refs <= lexical:
                lexical.add(name)
                changed = True
    return lexical


def _regex(name: str, rules: dict[str, list[str]]) -> str:
    out = []
    for p in rules[name]:
        if p.startswith('"'):
            out.append(re.escape(json.loads(p)))
        elif p.startswith("["):
            out.append(p)
        elif re.fullmatch(r"\w+", p):
            out.append("(?:" + _regex(p, rules) + ")")
        elif p.isspace():
            continue
        else:
            out.append(p.replace("(", "(?:"))
    return "".join(out)


def to_lark(gbnf: str, start: str) -> str:
    """The same rules as a Lark grammar, for tests. Lexical rules become regex terminals (fast);
    the others stay rules. A lexical rule that can match nothing must have the form `(X)*`: Lark has
    no empty terminals, so it becomes an optional terminal `(X)+`. Only the GBNF subset written above
    is supported."""
    rules = _rules(gbnf)
    lexical = _lexical(rules)
    out = [f"start: {start}"]
    for name, pieces in rules.items():
        if name in lexical:
            regex = _regex(name, rules)
            term = name.upper()
            if re.fullmatch(regex, ""):
                if not (regex.startswith("(?:") and regex.endswith(")*")):
                    raise ValueError(f"lexical rule {name} can be empty but isn't (X)*")
                regex = regex[:-1] + "+"
                out.append(f"{name}: {term}?")
            else:
                out.append(f"{name}: {term}")
            out.append(f"{term}: /{regex.replace('/', chr(92) + '/')}/")
            continue
        lark = "".join("/" + p.replace("/", "\\/") + "/" if p.startswith("[") else p for p in pieces)
        out.append(f"{name}: {lark}")
    return "\n".join(out) + "\n"
