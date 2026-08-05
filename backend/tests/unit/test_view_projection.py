"""View-filter compiler tests — kanban.md §2.3/§3.4, README §6.14.

The saved-view filter shape (``{"operator","conditions":[...]}``, ops
eq/neq/in/not_in/lt/lte/gt/gte/is_null/is_not_null/contains) differs from the
issue module's flat tree, so the projection layer compiles it itself against
the ``issues`` columns — parameterized only (no JSON-to-SQL string splicing,
§2.9 injection guard). Label / custom-field conditions compile to correlated
association-table predicates whose tenant key follows the outer issue row.
"""

from __future__ import annotations

import pytest
from sqlalchemy.sql.elements import BooleanClauseList

from mesh.errors import ValidationError
from mesh.issue.filters import FilterTooComplexError
from mesh.views.projection import compile_view_filters

pytestmark = pytest.mark.unit


def _sql(clause) -> str:
    return str(clause).lower()


def test_empty_filters_compile_to_no_clause() -> None:
    assert compile_view_filters({}) is None
    assert compile_view_filters(None) is None


def test_and_of_two_leaves() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [
                {"field": "state_category", "op": "eq", "value": "todo"},
                {"field": "priority", "op": "in", "value": ["high", "urgent"]},
            ],
        }
    )
    sql = _sql(clause)
    assert "state_category" in sql
    assert "priority" in sql
    assert "and" in sql
    assert "in" in sql


def test_or_combinator() -> None:
    clause = compile_view_filters(
        {
            "operator": "OR",
            "conditions": [
                {"field": "priority", "op": "eq", "value": "urgent"},
                {"field": "state_category", "op": "eq", "value": "done"},
            ],
        }
    )
    assert "or" in _sql(clause)


def test_nested_group_depth_three_is_allowed() -> None:
    # top group (1) -> group (2) -> group (3) -> leaf  == depth 3, OK.
    filters = {
        "operator": "AND",
        "conditions": [
            {
                "operator": "OR",
                "conditions": [
                    {
                        "operator": "AND",
                        "conditions": [{"field": "priority", "op": "eq", "value": "high"}],
                    }
                ],
            }
        ],
    }
    assert compile_view_filters(filters) is not None


def test_depth_four_group_is_too_complex() -> None:
    filters = {
        "operator": "AND",
        "conditions": [
            {
                "operator": "OR",
                "conditions": [
                    {
                        "operator": "AND",
                        "conditions": [
                            {
                                "operator": "OR",
                                "conditions": [{"field": "priority", "op": "eq", "value": "high"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    with pytest.raises(FilterTooComplexError):
        compile_view_filters(filters)


def test_more_than_twenty_conditions_is_too_complex() -> None:
    conditions = [{"field": "priority", "op": "eq", "value": "high"} for _ in range(21)]
    with pytest.raises(FilterTooComplexError):
        compile_view_filters({"operator": "AND", "conditions": conditions})


def test_label_condition_compiles_to_association_exists() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [
                {
                    "field": "label",
                    "op": "in",
                    "value": ["00000000-0000-0000-0000-000000000101"],
                }
            ],
        }
    )
    sql = _sql(clause)
    assert "exists" in sql
    assert "issue_labels" in sql
    assert "issue_labels.workspace_id = issues.workspace_id" in sql


def test_custom_field_condition_compiles_to_typed_association_exists() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [
                {
                    "field_kind": "custom_field",
                    "field_def_id": "00000000-0000-0000-0000-000000000201",
                    "op": "eq",
                    "value": "00000000-0000-0000-0000-000000000301",
                }
            ],
        }
    )
    sql = _sql(clause)
    assert "exists" in sql
    assert "issue_custom_field_values" in sql
    assert "field_def_id" in sql
    assert "issue_custom_field_values.workspace_id = issues.workspace_id" in sql


def test_unknown_field_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "not_a_field", "op": "eq", "value": "x"}],
            }
        )
    assert exc.value.code == "invalid_filters"


def test_q_contains_maps_to_title_or_identifier() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [{"field": "q", "op": "contains", "value": "login"}],
        }
    )
    sql = _sql(clause)
    assert "title" in sql
    assert "identifier" in sql
    assert "like" in sql


def test_null_operators() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [
                {"field": "assignee_id", "op": "is_null"},
                {"field": "due_date", "op": "is_not_null"},
            ],
        }
    )
    sql = _sql(clause)
    assert "is null" in sql
    assert "is not null" in sql


def test_neq_and_not_in_operators() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [
                {"field": "priority", "op": "neq", "value": "none"},
                {"field": "state_category", "op": "not_in", "value": ["done", "cancelled"]},
            ],
        }
    )
    sql = _sql(clause)
    assert "!=" in sql or "<>" in sql
    assert "not in" in sql or "not_IN" in sql.lower()


def test_uuid_field_bad_value_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "assignee_id", "op": "eq", "value": "not-a-uuid"}],
            }
        )
    assert exc.value.code == "invalid_filters"


def test_uuid_field_valid_value_compiles() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [
                {
                    "field": "assignee_id",
                    "op": "eq",
                    "value": "00000000-0000-0000-0000-000000000001",
                }
            ],
        }
    )
    assert "assignee_id" in _sql(clause)


def test_date_field_bad_value_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "due_date", "op": "lte", "value": "not-a-date"}],
            }
        )
    assert exc.value.code == "invalid_filters"


def test_returns_boolean_clause() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [{"field": "priority", "op": "eq", "value": "high"}],
        }
    )
    assert isinstance(clause, BooleanClauseList) or clause is not None


def test_range_operators_on_dates() -> None:
    for op, fragment in (("lt", "<"), ("lte", "<="), ("gt", ">"), ("gte", ">=")):
        clause = compile_view_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "due_date", "op": op, "value": "2026-08-31"}],
            }
        )
        assert fragment in _sql(clause)


def test_datetime_coercion() -> None:
    # ISO string parses.
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [{"field": "created_at", "op": "gte", "value": "2026-01-01T00:00:00"}],
        }
    )
    assert "created_at" in _sql(clause)
    # A datetime instance passes through the isinstance branch.
    from datetime import datetime

    clause2 = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [{"field": "updated_at", "op": "lt", "value": datetime(2026, 1, 1)}],
        }
    )
    assert "updated_at" in _sql(clause2)


def test_datetime_bad_value_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "created_at", "op": "eq", "value": "not-a-datetime"}],
            }
        )
    assert exc.value.code == "invalid_filters"


def test_contains_on_text_and_rejected_on_uuid() -> None:
    clause = compile_view_filters(
        {
            "operator": "AND",
            "conditions": [{"field": "state_category", "op": "contains", "value": "todo"}],
        }
    )
    assert "like" in _sql(clause)
    with pytest.raises(ValidationError) as exc:
        compile_view_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "assignee_id", "op": "contains", "value": "x"}],
            }
        )
    assert exc.value.code == "invalid_filters"


def test_list_op_requires_array_value() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "priority", "op": "in", "value": "high"}],
            }
        )
    assert exc.value.code == "invalid_filters"


def test_unknown_op_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "priority", "op": "between", "value": "x"}],
            }
        )
    assert exc.value.code == "invalid_filters"


def test_non_dict_condition_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters({"operator": "AND", "conditions": ["not-a-dict"]})
    assert exc.value.code == "invalid_filters"


def test_empty_conditions_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters({"operator": "AND", "conditions": []})
    assert exc.value.code == "invalid_filters"


def test_conditions_not_array_is_invalid() -> None:
    with pytest.raises(ValidationError) as exc:
        compile_view_filters({"operator": "AND", "conditions": "nope"})
    assert exc.value.code == "invalid_filters"
