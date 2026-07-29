"""Built-in jq-subset evaluator (cli.md C24 — no external jq process).

Supported grammar (enough for real scripting workflows):

    program  := pipeline
    pipeline := stage ("|" stage)*
    stage    := accessor+                    (.foo.bar[0][])
              | select(path "==" literal)    select(.status == "done")
              | literal                      "x" / 3 / true / false / null
              | "."                          identity
    accessor := "." ident | "[" int "]" | "[]"
    literal  := string | number | true | false | null

Semantics follow jq: ``.[]`` is a generator (one output per element), pipes
thread every output through the next stage, missing keys yield ``null``,
``select`` keeps or drops its input. Compile/evaluation failures raise
``JqError`` carrying the offending position (exit 3 + stderr, cli.md C24).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

TOKEN_PATTERN = re.compile(
    r"""
      (?P<pipe>\|)
    | (?P<select>select)
    | (?P<dot>\.)
    | (?P<lbracket>\[)
    | (?P<rbracket>\])
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<eq>==)
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<number>-?\d+(?:\.\d+)?)
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<ws>\s+)
    """,
    re.VERBOSE,
)


class JqError(Exception):
    """A compile- or evaluation-time jq failure with a position hint."""

    def __init__(self, message: str, *, position: int | None = None) -> None:
        self.position = position
        suffix = f" (at position {position})" if position is not None else ""
        super().__init__(f"{message}{suffix}")


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    pos: int


def _tokenize(expression: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    while index < len(expression):
        match = TOKEN_PATTERN.match(expression, index)
        if match is None:
            raise JqError(f"unexpected character {expression[index]!r}", position=index)
        index = match.end()
        if match.lastgroup == "ws":
            continue
        tokens.append(_Token(match.lastgroup or "", match.group(), match.start()))
    return tokens


# --- AST -------------------------------------------------------------------------

_OP_IDENTITY = "identity"
_OP_FIELD = "field"
_OP_INDEX = "index"
_OP_ITERATE = "iterate"
_OP_SELECT = "select"
_OP_LITERAL = "literal"


@dataclass(frozen=True)
class _Stage:
    ops: tuple  # sequence of (op, arg) tuples


class _Parser:
    def __init__(self, tokens: list[_Token], expression: str) -> None:
        self._tokens = tokens
        self._expression = expression
        self._cursor = 0

    def _peek(self) -> _Token | None:
        return self._tokens[self._cursor] if self._cursor < len(self._tokens) else None

    def _advance(self) -> _Token:
        token = self._peek()
        if token is None:
            raise JqError("unexpected end of expression", position=len(self._expression))
        self._cursor += 1
        return token

    def parse(self) -> list[_Stage]:
        stages = [self._parse_stage()]
        while (token := self._peek()) is not None:
            if token.kind != "pipe":
                raise JqError(f"expected '|' but found {token.value!r}", position=token.pos)
            self._advance()
            stages.append(self._parse_stage())
        if not stages:
            raise JqError("empty expression", position=0)
        return stages

    def _parse_stage(self) -> _Stage:
        token = self._peek()
        if token is None:
            raise JqError("expected a filter after '|'", position=len(self._expression))
        if token.kind == "select":
            return _Stage(ops=(self._parse_select(),))
        # A bare literal stage: string/number, or a true/false/null keyword.
        if token.kind in ("string", "number") or (
            token.kind == "ident" and token.value in ("true", "false", "null")
        ):
            return _Stage(ops=(self._parse_literal(),))
        ops: list[tuple] = []
        while True:
            token = self._peek()
            if token is None or token.kind == "pipe":
                break
            if token.kind == "dot":
                self._advance()
                nxt = self._peek()
                if nxt is not None and nxt.kind == "ident":
                    self._advance()
                    ops.append((_OP_FIELD, nxt.value))
                elif nxt is not None and nxt.kind == "lbracket":
                    ops.append(self._parse_bracket())
                else:
                    ops.append((_OP_IDENTITY, None))
            elif token.kind == "lbracket":
                ops.append(self._parse_bracket())
            else:
                raise JqError(f"unexpected {token.value!r} in accessor", position=token.pos)
        if not ops:
            raise JqError("empty filter stage", position=token.pos if token else 0)
        return _Stage(ops=tuple(ops))

    def _parse_bracket(self) -> tuple:
        self._advance()  # [
        token = self._peek()
        if token is None:
            raise JqError("unterminated '['", position=len(self._expression))
        if token.kind == "rbracket":
            self._advance()
            return (_OP_ITERATE, None)
        if token.kind == "number":
            self._advance()
            closing = self._peek()
            if closing is None or closing.kind != "rbracket":
                raise JqError("expected ']' after index", position=token.pos)
            self._advance()
            value = token.value
            return (_OP_INDEX, int(value) if "." not in value else float(value))
        raise JqError(f"unsupported index {token.value!r}", position=token.pos)

    def _parse_select(self) -> tuple:
        start = self._advance()  # select
        opening = self._peek()
        if opening is None or opening.kind != "lparen":
            raise JqError("expected '(' after select", position=start.pos)
        self._advance()
        # Left-hand path: dotted field access from root.
        dot = self._peek()
        if dot is None or dot.kind != "dot":
            raise JqError("select condition must start with a '.' path", position=start.pos)
        path: list[str] = []
        self._advance()
        while True:
            ident = self._peek()
            if ident is not None and ident.kind == "ident":
                path.append(ident.value)
                self._advance()
                nxt = self._peek()
                if nxt is not None and nxt.kind == "dot":
                    self._advance()
                    continue
            break
        eq = self._peek()
        if eq is None or eq.kind != "eq":
            raise JqError("select supports only '==' comparisons", position=start.pos)
        self._advance()
        literal = self._parse_literal()
        closing = self._peek()
        if closing is None or closing.kind != "rparen":
            raise JqError("expected ')' to close select", position=start.pos)
        self._advance()
        return (_OP_SELECT, (tuple(path), literal[1]))

    def _parse_literal(self) -> tuple:
        token = self._advance()
        if token.kind == "string":
            body = token.value[1:-1]
            return (_OP_LITERAL, re.sub(r'\\(.)', r"\1", body))
        if token.kind == "number":
            value: Any = int(token.value) if "." not in token.value else float(token.value)
            return (_OP_LITERAL, value)
        if token.kind == "ident" and token.value in ("true", "false", "null"):
            return (_OP_LITERAL, {"true": True, "false": False, "null": None}[token.value])
        raise JqError(f"expected a literal value, found {token.value!r}", position=token.pos)


# --- evaluation -------------------------------------------------------------------


def _apply_op(value: Any, op: str, arg: Any) -> list[Any]:
    if op == _OP_IDENTITY:
        return [value]
    if op == _OP_FIELD:
        if isinstance(value, dict):
            return [value.get(arg)]
        return [None]  # jq: field access on non-object yields null
    if op == _OP_INDEX:
        if isinstance(value, list):
            index = int(arg)
            if -len(value) <= index < len(value):
                return [value[index]]
            return [None]
        return [None]
    if op == _OP_ITERATE:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            return list(value.values())
        raise JqError(f"cannot iterate over {type(value).__name__}")
    if op == _OP_SELECT:
        path, expected = arg
        current: Any = value
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = None
                break
        return [value] if current == expected else []
    if op == _OP_LITERAL:
        return [arg]
    raise JqError(f"unsupported operation {op!r}")  # pragma: no cover


@dataclass(frozen=True)
class Program:
    """A compiled jq-subset program; evaluate() yields zero or more outputs."""

    stages: tuple[_Stage, ...]

    def evaluate(self, data: Any) -> list[Any]:
        outputs = [data]
        for stage in self.stages:
            next_outputs: list[Any] = []
            for value in outputs:
                current = [value]
                for op, arg in stage.ops:
                    expanded: list[Any] = []
                    for item in current:
                        expanded.extend(_apply_op(item, op, arg))
                    current = expanded
                next_outputs.extend(current)
            outputs = next_outputs
        return outputs


def compile_expression(expression: str) -> Program:
    """Compile a jq-subset expression; raises JqError with a position."""
    if not expression.strip():
        raise JqError("empty --jq expression", position=0)
    tokens = _tokenize(expression)
    stages = _Parser(tokens, expression).parse()
    return Program(stages=tuple(stages))
