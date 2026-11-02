"""Flags Express that reaches outside the active catalog: operators, unknown calls, unknown properties.

Express (`a2ui/specification/inference_formats/express/Express.g4`) has no operators at all, and
its calls are the catalog's components and functions plus four built-ins. A model that writes
`$x ? a : b`, `if(...)` or `$a >= 0` is inventing language. This check finds every such place
in a response, where the compiler stops at the first error, so its rate can be tracked per
checkpoint and per source, and the training rows can be linted for zero.

It reads the raw text with a lexer that follows the grammar's tokens. A character the grammar
cannot tokenise is an operator. `${...}` interpolations inside non-raw strings are read too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .sdk import ExpressSDK
from .synth.catalog_model import CatalogModel

BUILTINS = {
    "surface": {"sendDataModel"},
    "deleteSurface": set(),
    "Event": {"userMessage", "context"},
    "_template": set(),
}
RESERVED_KWARGS = {"id", "functionCallId", "sendDataModel", "update", "extensions", "catalogId"}
OPERATORS = ("==", "!=", "<=", ">=", "&&", "||", "?", "<", ">", "!", "+", "-", "*", "/", "%", "&", "|")

_TOKENS = re.compile(
    r"""
    (?P<skip>\s+|;|\#[^\r\n]*|//[^\r\n]*|/\*.*?\*/)
  | (?P<rawstr>[rR]\"\"\".*?\"\"\"|[rR]"[^\r\n"]*")
  | (?P<str>\"\"\"(?:\\.|[^\\])*?\"\"\"|"(?:\\.|[^\\"])*")
  | (?P<sqstr>'(?:\\.|[^\\'\r\n])*')
  | (?P<path>\$[a-zA-Z0-9_/]*)
  | (?P<check>\?[^\W\d]\w*)
  | (?P<num>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)
  | (?P<ident>[^\W\d]\w*)
  | (?P<punct>[()\[\]{},:=])
    """,
    re.VERBOSE | re.DOTALL,
)
_INTERP_TOKENS = re.compile(
    r"""
    (?P<skip>\s+)
  | (?P<str>'(?:\\.|[^\\'])*'|"(?:\\.|[^\\"])*")
  | (?P<interp>\$\{)
  | (?P<ident>[^\W\d]\w*)(?=\s*\()
  | (?P<op>==|!=|<=|>=|&&|\|\||[?<>!*%+])
  | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)
_VALUE_END = {"path", "num", "ident", "str", "rawstr", "sqstr", ")", "]", "}"}


@dataclass(frozen=True)
class Finding:
    kind: str  # operator | unknown_function | unknown_component | unknown_property
    detail: str
    offset: int


def kinds(findings: list[Finding]) -> list[str]:
    return sorted({f.kind for f in findings})


class CatalogCheck:
    def __init__(self, sdk: ExpressSDK):
        self.sdk = sdk
        model = CatalogModel(sdk)
        self.components = {n: {s.name for s in c.slots} for n, c in model.components.items()}
        self.functions = {n: {a.name for a in f.args} for n, f in model.functions.items()}

    def response(self, text: str | None) -> list[Finding]:
        """Findings in every `<a2ui>` block of a model response; prose outside blocks is not Express."""
        if not text:
            return []
        _, blocks = self.sdk.unwrap(text)
        return [f for b in blocks for f in self.findings(b.text)]

    def _unknown_call(self, name: str) -> str | None:
        if name in BUILTINS or name in self.components or name in self.functions:
            return None
        return "unknown_component" if name[:1].isupper() else "unknown_function"

    def _params(self, name: str) -> set[str] | None:
        """Keyword arguments `name(...)` accepts, or None when the callee itself is unknown."""
        own = BUILTINS.get(name, self.components.get(name, self.functions.get(name)))
        return None if own is None else own | RESERVED_KWARGS

    def findings(self, express: str) -> list[Finding]:
        out: list[Finding] = []
        toks: list[tuple[str, str, int]] = []
        pos = 0
        while pos < len(express):
            m = None if express.startswith("==", pos) else _TOKENS.match(express, pos)  # `=` is a token, `==` is not
            if m is None:
                op = next(o for o in (*OPERATORS, express[pos]) if express.startswith(o, pos))
                glued = op in ("/", "-") and 0 < pos and not express[pos - 1].isspace() and not express[pos + 1 : pos + 2].isspace()
                if op in OPERATORS and not glued:  # `$/a b/c`, `$/a[0]/b`, `$/a-b`: a broken path, not arithmetic
                    out.append(Finding("operator", op, pos))
                toks.append(("op", op, pos))
                pos += len(op)
                continue
            kind = m.lastgroup
            text = m.group()
            if kind == "punct":
                kind = text
            if kind == "num" and text[0] in "+-" and toks and toks[-1][0] in _VALUE_END:
                out.append(Finding("operator", text[0], pos))  # `$a -1`: a signed number after a value
            if kind == "str":
                out += self._interpolations(text, pos)
            if kind != "skip":
                toks.append((kind, text, pos))
            pos = m.end()
        out += self._calls(toks)
        return sorted(out, key=lambda f: f.offset)

    def _calls(self, toks: list[tuple[str, str, int]]) -> list[Finding]:
        out: list[Finding] = []
        stack: list[str | None] = []  # callee name for a call's "(", None for brackets and braces
        for i, (kind, text, pos) in enumerate(toks):
            nxt = toks[i + 1][0] if i + 1 < len(toks) else None
            if kind == "ident" and nxt == "(":
                if bad := self._unknown_call(text):
                    out.append(Finding(bad, text, pos))
                stack.append(text)
            elif kind == "check":
                if text[1:] not in self.functions:
                    out.append(Finding("unknown_function", text[1:], pos))
                if nxt == "(":
                    stack.append(text[1:])
            elif kind == "(":
                if not (i and toks[i - 1][0] in ("ident", "check")):
                    stack.append(None)
            elif kind in ("[", "{"):
                stack.append(None)
            elif kind in (")", "]", "}"):
                if stack:
                    stack.pop()
            elif kind == "ident" and nxt == "=" and stack and stack[-1] is not None:
                params = self._params(stack[-1])
                if params is not None and text not in params:
                    out.append(Finding("unknown_property", f"{stack[-1]}.{text}", pos))
        return out

    def _interpolations(self, string: str, base: int) -> list[Finding]:
        """Operators and unknown calls inside `${...}` in a (non-raw) string literal."""
        out: list[Finding] = []
        start = 0
        while (i := string.find("${", start)) != -1:
            depth, j = 1, i + 2
            while j < len(string) and depth:
                m = _INTERP_TOKENS.match(string, j)
                if m is None:
                    break
                if m.lastgroup == "interp":
                    depth += 1
                elif m.group() == "}":
                    depth -= 1
                elif m.lastgroup == "op":
                    out.append(Finding("operator", m.group(), base + j))
                elif m.lastgroup == "ident" and (bad := self._unknown_call(m.group())):
                    out.append(Finding(bad, m.group(), base + j))
                j = m.end()
            start = j
        return out
