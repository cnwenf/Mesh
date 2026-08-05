"""View config validator tests — kanban.md §2.3/§3.4, README §6.14.

Pure-function coverage of the whitelist validators that guard every JSONB
config write path: filters (field/op matrices, depth and condition limits),
sort rules, board_settings (columns / card_fields / wip), group_by and the
layout/visibility enums. Named error codes per kanban §3.3: 400
validation_error family + 400 filter_too_complex.
"""

from __future__ import annotations

import pytest

from mesh.errors import ValidationError
from mesh.views.config import (
    FILTER_MAX_CONDITIONS,
    FILTER_MAX_DEPTH,
    PRIORITY_KEYS,
    STATE_CATEGORY_KEYS,
    validate_board_settings,
    validate_display_fields,
    validate_filters,
    validate_group_axes,
    validate_group_by,
    validate_layout,
    validate_name,
    validate_sort,
    validate_visibility,
)

pytestmark = pytest.mark.unit


def _error_code(excinfo) -> str:
    return excinfo.value.code


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------


def test_filters_empty_object_is_no_filter() -> None:
    assert validate_filters({}) == {}


def test_filters_valid_flat_conditions() -> None:
    value = {
        "operator": "AND",
        "conditions": [
            {"field": "state_category", "op": "in", "value": ["todo", "in_progress"]},
            {"field": "priority", "op": "in", "value": ["high", "urgent"]},
            {"field": "assignee_id", "op": "eq", "value": "11111111-1111-1111-1111-111111111111"},
            {"field": "due_date", "op": "lte", "value": "2026-08-31"},
            {"field": "label", "op": "in", "value": ["22222222-2222-2222-2222-222222222222"]},
        ],
    }
    assert validate_filters(value) == value


def test_filters_nested_group_allowed_to_depth_three() -> None:
    value = {
        "operator": "AND",
        "conditions": [
            {
                "operator": "OR",
                "conditions": [
                    {
                        "operator": "AND",
                        "conditions": [
                            {"field": "priority", "op": "eq", "value": "urgent"},
                        ],
                    }
                ],
            }
        ],
    }
    assert validate_filters(value) == value


def test_filters_depth_four_is_too_complex() -> None:
    value = {
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
                                "conditions": [{"field": "priority", "op": "eq", "value": "urgent"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(value)
    assert _error_code(excinfo) == "filter_too_complex"
    assert excinfo.value.details["depth"] == FILTER_MAX_DEPTH + 1


def test_filters_condition_count_over_limit_is_too_complex() -> None:
    condition = {"field": "priority", "op": "eq", "value": "urgent"}
    value = {"operator": "AND", "conditions": [condition] * (FILTER_MAX_CONDITIONS + 1)}
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(value)
    assert _error_code(excinfo) == "filter_too_complex"
    assert excinfo.value.details["conditions"] == FILTER_MAX_CONDITIONS + 1


def test_filters_condition_count_at_limit_passes() -> None:
    condition = {"field": "priority", "op": "eq", "value": "urgent"}
    value = {"operator": "AND", "conditions": [condition] * FILTER_MAX_CONDITIONS}
    assert validate_filters(value) == value


@pytest.mark.parametrize("bad", [None, [], "AND", {"operator": "AND"}, {"conditions": []}])
def test_filters_shape_violations(bad) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(bad)
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_invalid_operator() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters({"operator": "XOR", "conditions": []})
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_empty_conditions_list_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters({"operator": "AND", "conditions": []})
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters({"operator": "AND", "conditions": [{"field": "evil", "op": "eq", "value": "x"}]})
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_unknown_op_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(
            {"operator": "AND", "conditions": [{"field": "priority", "op": "regex", "value": "x"}]}
        )
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_label_only_in_not_in() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters({"operator": "AND", "conditions": [{"field": "label", "op": "eq", "value": "x"}]})
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_q_only_contains() -> None:
    assert validate_filters(
        {"operator": "AND", "conditions": [{"field": "q", "op": "contains", "value": "web"}]}
    )
    with pytest.raises(ValidationError) as excinfo:
        validate_filters({"operator": "AND", "conditions": [{"field": "q", "op": "eq", "value": "web"}]})
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_in_op_requires_non_empty_list() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters({"operator": "AND", "conditions": [{"field": "priority", "op": "in", "value": []}]})
    assert _error_code(excinfo) == "invalid_filters"
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "priority", "op": "in", "value": "high"}],
            }
        )
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_null_ops_take_no_value() -> None:
    value = {"operator": "AND", "conditions": [{"field": "due_date", "op": "is_null"}]}
    assert validate_filters(value) == value
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "due_date", "op": "is_null", "value": "2026-01-01"}],
            }
        )
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_scalar_ops_require_value() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters({"operator": "AND", "conditions": [{"field": "priority", "op": "eq"}]})
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_unknown_extra_key_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(
            {
                "operator": "AND",
                "conditions": [{"field": "priority", "op": "eq", "value": "high", "escape": True}],
            }
        )
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_custom_field_condition() -> None:
    condition = {
        "field_kind": "custom_field",
        "field_def_id": "33333333-3333-3333-3333-333333333333",
        "op": "eq",
        "value": "opt_major",
    }
    value = {"operator": "AND", "conditions": [condition]}
    assert validate_filters(value) == value
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(
            {
                "operator": "AND",
                "conditions": [{"field_kind": "custom_field", "op": "eq", "value": "x"}],
            }
        )
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_boolean_values_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_filters(
            {"operator": "AND", "conditions": [{"field": "priority", "op": "eq", "value": True}]}
        )
    assert _error_code(excinfo) == "invalid_filters"


def test_filters_custom_field_boolean_values_are_valid_scalars() -> None:
    for op, value in (("eq", True), ("in", [True, False])):
        filters = {
            "operator": "AND",
            "conditions": [
                {
                    "field_kind": "custom_field",
                    "field_def_id": "33333333-3333-3333-3333-333333333333",
                    "op": op,
                    "value": value,
                }
            ],
        }
        assert validate_filters(filters) == filters


# ---------------------------------------------------------------------------
# sort
# ---------------------------------------------------------------------------


def test_sort_valid_rules() -> None:
    value = [
        {"field": "position", "order": "asc"},
        {"field": "created_at", "order": "desc"},
    ]
    assert validate_sort(value) == value


def test_sort_empty_list_ok() -> None:
    assert validate_sort([]) == []


def test_sort_custom_field_rule() -> None:
    value = [
        {
            "field_kind": "custom_field",
            "field_def_id": "33333333-3333-3333-3333-333333333333",
            "order": "asc",
        }
    ]
    assert validate_sort(value) == value


@pytest.mark.parametrize(
    "bad",
    [
        "position",
        {"field": "position", "order": "asc"},
        [{"field": "evil", "order": "asc"}],
        [{"field": "position", "order": "sideways"}],
        [{"order": "asc"}],
        [{"field": "position"}],
        [{"field": "position", "order": "asc", "extra": 1}],
    ],
)
def test_sort_invalid_shapes(bad) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_sort(bad)
    assert _error_code(excinfo) == "invalid_sort"


# ---------------------------------------------------------------------------
# board_settings
# ---------------------------------------------------------------------------


def test_board_settings_valid_full_shape() -> None:
    value = {
        "columns": ["backlog", "todo", "in_progress", "in_review", "done"],
        "collapsed_columns": ["done"],
        "card_fields": ["labels", "estimate", "due_date", "sub_issue_progress", "assignee"],
        "wip": {"in_progress": {"limit": 5, "enforcement": "warn"}},
    }
    assert validate_board_settings(value) == value


def test_board_settings_empty_object_ok() -> None:
    assert validate_board_settings({}) == {}


def test_board_settings_wip_defaults_enforcement() -> None:
    value = {"wip": {"todo": {"limit": 3}}}
    assert validate_board_settings(value) == {"wip": {"todo": {"limit": 3, "enforcement": "warn"}}}


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"unknown_key": []},
        {"columns": "todo"},
        {"columns": [1]},
        {"collapsed_columns": [None]},
        {"card_fields": {"labels": True}},
        {"wip": []},
        {"wip": {"in_progress": {"limit": 0}}},
        {"wip": {"in_progress": {"limit": "5"}}},
        {"wip": {"in_progress": {"limit": 5, "enforcement": "hard"}}},
        {"wip": {"in_progress": {"enforcement": "warn"}}},
        {"wip": {"in_progress": {"limit": 5, "enforcement": "warn", "extra": 1}}},
        {"wip": {"": {"limit": 5}}},
    ],
)
def test_board_settings_invalid_shapes(bad) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_board_settings(bad)
    assert _error_code(excinfo) == "invalid_board_settings"


# ---------------------------------------------------------------------------
# group_by / layout / visibility
# ---------------------------------------------------------------------------


def test_group_by_none_allowed() -> None:
    assert validate_group_by(None) is None


@pytest.mark.parametrize("field", ["state_category", "status", "assignee", "priority", "project", "label"])
def test_group_by_builtin_values(field: str) -> None:
    assert validate_group_by(field) == field


def test_group_by_invalid_value() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_group_by("severity")
    assert _error_code(excinfo) == "invalid_group_by"


def test_group_by_accepts_custom_field_definition_id() -> None:
    field_id = "00000000-0000-0000-0000-000000000201"
    assert validate_group_by(field_id) == field_id


def test_group_axes_reject_same_effective_field() -> None:
    validate_group_axes("state_category", None)
    validate_group_axes("priority", "assignee")
    with pytest.raises(ValidationError) as excinfo:
        validate_group_axes(None, "state_category")
    assert _error_code(excinfo) == "validation_error"
    assert excinfo.value.details == {
        "group_by": "state_category",
        "sub_group_by": "state_category",
    }
    with pytest.raises(ValidationError):
        validate_group_axes("priority", "priority")
    with pytest.raises(ValidationError) as excinfo:
        validate_group_by("")
    assert _error_code(excinfo) == "invalid_group_by"
    with pytest.raises(ValidationError) as excinfo:
        validate_group_by(123)
    assert _error_code(excinfo) == "invalid_group_by"


def test_layout_and_visibility_enums() -> None:
    assert validate_layout("board") == "board"
    assert validate_layout("table") == "table"
    with pytest.raises(ValidationError) as excinfo:
        validate_layout("gantt")
    assert _error_code(excinfo) == "invalid_layout"
    assert validate_visibility("shared") == "shared"
    with pytest.raises(ValidationError) as excinfo:
        validate_visibility("public")
    assert _error_code(excinfo) == "invalid_visibility"


def test_column_key_constants() -> None:
    assert STATE_CATEGORY_KEYS == (
        "backlog",
        "todo",
        "in_progress",
        "in_review",
        "blocked",
        "done",
        "cancelled",
    )
    assert PRIORITY_KEYS == ("urgent", "high", "medium", "low", "none")


# ---------------------------------------------------------------------------
# display_fields / name
# ---------------------------------------------------------------------------


def test_display_fields_valid_and_invalid() -> None:
    assert validate_display_fields([]) == []
    assert validate_display_fields(["status", "assignee"]) == ["status", "assignee"]
    for bad in ("status", [1], [""], [None]):
        with pytest.raises(ValidationError) as excinfo:
            validate_display_fields(bad)
        assert _error_code(excinfo) == "invalid_display_fields"


def test_name_bounds() -> None:
    assert validate_name("  Board  ") == "Board"
    assert validate_name("x" * 100) == "x" * 100
    for bad in ("", "   ", "x" * 101, None, 5):
        with pytest.raises(ValidationError) as excinfo:
            validate_name(bad)
        assert _error_code(excinfo) == "invalid_name"
