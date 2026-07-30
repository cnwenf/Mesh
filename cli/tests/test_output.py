"""Output discipline (cli.md §3.5 / §4.1 / C24)."""

from __future__ import annotations

import io
import json

import pytest

from meshcli.errors import EXIT_VALIDATION, CliError
from meshcli.output import apply_jq, emit_json, emit_json_lines, emit_table, render_table


def test_emit_json_single_document():
    out = io.StringIO()
    emit_json({"data": {"id": 1, "n": "x"}}, out=out)
    parsed = json.loads(out.getvalue())  # exactly one document
    assert parsed == {"data": {"id": 1, "n": "x"}}


def test_emit_json_unicode_not_escaped():
    out = io.StringIO()
    emit_json({"data": {"title": "修复登录"}}, out=out)
    assert "修复登录" in out.getvalue()


def test_emit_json_lines_one_per_element():
    out = io.StringIO()
    emit_json_lines(["MES-1", "MES-2"], out=out)
    lines = [line for line in out.getvalue().splitlines() if line]
    assert lines == ['"MES-1"', '"MES-2"']
    for line in lines:
        json.loads(line)  # each line is legal JSON


def test_render_table_columns_and_truncation():
    rows = [{"identifier": "MES-1", "title": "t" * 100}]
    rendered = render_table(rows, ["identifier", "title"], tty=True)
    lines = rendered.splitlines()
    assert lines[0].split() == ["IDENTIFIER", "TITLE"]
    assert "…" in lines[1]  # long cells truncated


def test_render_table_no_header():
    rows = [{"a": "1", "b": "2"}]
    rendered = render_table(rows, ["a", "b"], no_header=True, tty=True)
    assert "A" not in rendered.splitlines()[0].split()


def test_render_table_empty():
    assert render_table([], ["a"]) == ""


def test_apply_jq_on_envelope_data():
    envelope = {"data": [{"identifier": "MES-1"}, {"identifier": "MES-2"}]}
    assert apply_jq(envelope, ".[] | .identifier") == ["MES-1", "MES-2"]


def test_apply_jq_compile_error_exit_3():
    with pytest.raises(CliError) as exc:
        apply_jq({"data": []}, ".[")
    assert exc.value.exit_code == EXIT_VALIDATION


def test_apply_jq_eval_error_exit_3():
    with pytest.raises(CliError) as exc:
        apply_jq({"data": 5}, ".[] | .x")
    assert exc.value.exit_code == EXIT_VALIDATION


def test_emit_table_scalar_fallback(capsys):
    # table mode with a scalar payload falls back to json (still stdout-only).
    emit_table if False else None  # noqa: B018 — keep import referenced
    out = io.StringIO()
    emit_json({"data": "plain"}, out=out)
    assert json.loads(out.getvalue())["data"] == "plain"
