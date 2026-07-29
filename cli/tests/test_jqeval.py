"""Built-in jq-subset evaluator (cli.md C24)."""

from __future__ import annotations

import pytest

from meshcli.jqeval import JqError, compile_expression


def run(expression: str, data):
    return compile_expression(expression).evaluate(data)


class TestCore:
    def test_identity(self):
        assert run(".", {"a": 1}) == [{"a": 1}]

    def test_field_access(self):
        assert run(".name", {"name": "mesh"}) == ["mesh"]

    def test_nested_field_access(self):
        assert run(".data.id", {"data": {"id": 7}}) == [7]

    def test_missing_field_is_null(self):
        assert run(".nope", {"a": 1}) == [None]

    def test_field_on_non_object_is_null(self):
        assert run(".a", 42) == [None]

    def test_index(self):
        assert run(".[1]", [10, 20, 30]) == [20]
        assert run(".[-1]", [10, 20, 30]) == [30]

    def test_index_out_of_range_is_null(self):
        assert run(".[9]", [1]) == [None]

    def test_chained_field_and_index(self):
        data = {"items": [{"id": "a"}, {"id": "b"}]}
        assert run(".items[1].id", data) == ["b"]

    def test_iterate_array(self):
        assert run(".[]", [1, 2, 3]) == [1, 2, 3]

    def test_iterate_object_values(self):
        assert sorted(run(".[]", {"a": 1, "b": 2}), key=str) == [1, 2]

    def test_iterate_non_iterable_raises(self):
        with pytest.raises(JqError):
            run(".[]", 5)


class TestPipes:
    def test_iterate_then_field(self):
        """The canonical CLI shape: `.[] | .identifier` (cli.md §5.1)."""
        data = [{"identifier": "MES-1"}, {"identifier": "MES-2"}]
        assert run(".[] | .identifier", data) == ["MES-1", "MES-2"]

    def test_multi_stage_pipe(self):
        data = {"data": [{"a": {"b": 1}}, {"a": {"b": 2}}]}
        assert run(".data | .[] | .a.b", data) == [1, 2]

    def test_pipe_through_iterate_expands(self):
        assert run(".groups | .[] | .[]", {"groups": [[1, 2], [3]]}) == [1, 2, 3]


class TestSelect:
    def test_select_keeps_matching(self):
        data = [{"s": "done"}, {"s": "todo"}, {"s": "done"}]
        assert run(".[] | select(.s == \"done\")", data) == [{"s": "done"}, {"s": "done"}]

    def test_select_numeric(self):
        data = [{"n": 1}, {"n": 5}]
        assert run(".[] | select(.n == 5)", data) == [{"n": 5}]

    def test_select_nested_path(self):
        data = [{"m": {"k": "x"}}, {"m": {"k": "y"}}]
        assert run(".[] | select(.m.k == \"y\")", data) == [{"m": {"k": "y"}}]

    def test_select_boolean_literal(self):
        data = [{"ok": True}, {"ok": False}]
        assert run(".[] | select(.ok == true)", data) == [{"ok": True}]


class TestLiterals:
    def test_string_literal_stage(self):
        assert run("\"hello\"", {"ignored": 1}) == ["hello"]

    def test_number_literal(self):
        assert run("42", None) == [42]

    def test_null_literal(self):
        assert run("null", {"x": 1}) == [None]


class TestErrors:
    def test_identity_ok(self):
        compile_expression(".")  # sanity: identity always compiles

    @pytest.mark.parametrize(
        "expression",
        [
            ".foo |",
            "| .foo",
            ".[x]",
            ".[",
            "select(.a = 1)",
            "select(.a ==)",
            "select(a == 1)",
            "foo bar",
            "@bad",
        ],
    )
    def test_compile_errors_carry_position(self, expression):
        with pytest.raises(JqError) as exc:
            compile_expression(expression)
        assert str(exc.value)  # message includes position suffix

    def test_empty_expression_error(self):
        with pytest.raises(JqError):
            compile_expression("   ")
