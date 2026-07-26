"""Whitelist validators for view JSONB config (kanban.md §2.3/§3.4).

Every config write path (create / PATCH / wip) validates through these
functions BEFORE the JSONB is stored, so no unvalidated JSON ever reaches a
query compiler (injection guard, kanban §2.9 closing note). Failures raise
400-class :class:`ValidationError` with named codes (kanban §3.3):

- ``invalid_filters`` / ``invalid_sort`` / ``invalid_board_settings`` /
  ``invalid_group_by`` / ``invalid_layout`` / ``invalid_visibility`` — shape
  or whitelist violations;
- ``filter_too_complex`` — nesting deeper than 3 or more than 20 conditions
  (README §6.14 filter limits).
"""

from __future__ import annotations

from typing import Any

from mesh.db.models.view import VIEW_LAYOUT_VALUES, VIEW_VISIBILITY_VALUES
from mesh.errors import ValidationError

# README §6.14 filter limits (kanban §3.4).
FILTER_MAX_DEPTH = 3
FILTER_MAX_CONDITIONS = 20

NAME_MIN_LENGTH = 1
NAME_MAX_LENGTH = 100

# Built-in filterable fields (kanban §2.3).
FILTER_FIELDS: frozenset[str] = frozenset(
    {
        "state_category",
        "status_id",
        "priority",
        "assignee_id",
        "reporter_id",
        "project_id",
        "cycle_id",
        "milestone_id",
        "due_date",
        "start_date",
        "created_at",
        "updated_at",
        "label",
        "parent_id",
        "q",
    }
)

FILTER_OPS: frozenset[str] = frozenset(
    {
        "eq",
        "neq",
        "in",
        "not_in",
        "lt",
        "lte",
        "gt",
        "gte",
        "is_null",
        "is_not_null",
        "contains",
    }
)

# Per-field op restrictions (kanban §2.3): label is multi-valued (in/not_in
# only); q is the title/identifier search field (contains only).
FIELD_OP_RESTRICTIONS: dict[str, frozenset[str]] = {
    "label": frozenset({"in", "not_in"}),
    "q": frozenset({"contains"}),
}

_LIST_OPS: frozenset[str] = frozenset({"in", "not_in"})
_NULL_OPS: frozenset[str] = frozenset({"is_null", "is_not_null"})

# Sortable fields (kanban §1.2 list/board sorting + §2.3 sort shape).
SORT_FIELDS: frozenset[str] = frozenset(
    {
        "position",
        "priority",
        "due_date",
        "start_date",
        "created_at",
        "updated_at",
        "status_id",
    }
)

# group_by columns (kanban §2.4); NULL group_by defaults a board to
# state_category at render time. Custom-field grouping lands with the
# label-property increment.
GROUP_BY_FIELDS: frozenset[str] = frozenset(
    {"state_category", "status", "assignee", "priority", "project", "label"}
)

# Fixed column key sets used by the board renderer (kanban §2.4 / issue.md).
STATE_CATEGORY_KEYS: tuple[str, ...] = (
    "backlog",
    "todo",
    "in_progress",
    "in_review",
    "blocked",
    "done",
    "cancelled",
)
PRIORITY_KEYS: tuple[str, ...] = ("urgent", "high", "medium", "low", "none")

_BOARD_SETTING_KEYS: frozenset[str] = frozenset(
    {"columns", "collapsed_columns", "card_fields", "wip"}
)
_STRING_ARRAY_KEYS: frozenset[str] = frozenset({"columns", "collapsed_columns", "card_fields"})
WIP_ENFORCEMENTS: frozenset[str] = frozenset({"warn", "block"})
WIP_LIMIT_KEYS: frozenset[str] = frozenset({"limit", "enforcement"})

_FILTER_CONDITION_KEYS: frozenset[str] = frozenset({"field", "op", "value"})
_FILTER_CUSTOM_KEYS: frozenset[str] = frozenset({"field_kind", "field_def_id", "op", "value"})
_FILTER_GROUP_KEYS: frozenset[str] = frozenset({"operator", "conditions"})


def _invalid(code: str, message: str, *, path: str, **extra: Any) -> ValidationError:
    details: dict[str, Any] = {"path": path, **extra}
    return ValidationError(message, code=code, details=details)


def _is_scalar_json(value: Any) -> bool:
    """Acceptable condition value: str/int/float/None (booleans excluded)."""
    if isinstance(value, bool):
        return False
    return isinstance(value, (str, int, float)) or value is None


def _count_conditions(conditions: list[Any]) -> int:
    total = 0
    for condition in conditions:
        if isinstance(condition, dict) and "operator" in condition:
            inner = condition.get("conditions")
            total += _count_conditions(inner) if isinstance(inner, list) else 0
        else:
            total += 1
    return total


def _validate_condition(condition: Any, *, path: str, depth: int) -> None:
    if not isinstance(condition, dict):
        raise _invalid("invalid_filters", "filter condition must be an object", path=path)

    if "operator" in condition or "conditions" in condition:
        # ``depth`` counts group nesting (the top-level group is depth 1);
        # leaf conditions inside a depth-3 group are still allowed.
        if depth > FILTER_MAX_DEPTH:
            raise ValidationError(
                "filter nesting exceeds the maximum depth",
                code="filter_too_complex",
                details={"depth": depth, "path": path},
            )
        extra = set(condition) - _FILTER_GROUP_KEYS
        if extra:
            raise _invalid(
                "invalid_filters",
                "unknown keys in filter group",
                path=path,
                keys=sorted(extra),
            )
        operator = condition.get("operator")
        conditions = condition.get("conditions")
        if operator not in ("AND", "OR"):
            raise _invalid(
                "invalid_filters", "operator must be AND or OR", path=f"{path}.operator"
            )
        if not isinstance(conditions, list) or not conditions:
            raise _invalid(
                "invalid_filters",
                "conditions must be a non-empty array",
                path=f"{path}.conditions",
            )
        for index, nested in enumerate(conditions):
            _validate_condition(nested, path=f"{path}.conditions[{index}]", depth=depth + 1)
        return

    if condition.get("field_kind") == "custom_field" or "field_def_id" in condition:
        extra = set(condition) - _FILTER_CUSTOM_KEYS
        if extra:
            raise _invalid(
                "invalid_filters",
                "unknown keys in custom-field condition",
                path=path,
                keys=sorted(extra),
            )
        field_def_id = condition.get("field_def_id")
        if not isinstance(field_def_id, str) or not field_def_id:
            raise _invalid(
                "invalid_filters",
                "custom-field condition needs a non-empty field_def_id",
                path=f"{path}.field_def_id",
            )
        _validate_op(condition, field="<custom_field>", path=path)
        return

    extra = set(condition) - _FILTER_CONDITION_KEYS
    if extra:
        raise _invalid(
            "invalid_filters", "unknown keys in filter condition", path=path, keys=sorted(extra)
        )
    field = condition.get("field")
    if not isinstance(field, str) or field not in FILTER_FIELDS:
        raise _invalid(
            "invalid_filters", "unknown filter field", path=f"{path}.field", field=str(field)
        )
    _validate_op(condition, field=field, path=path)


def _validate_op(condition: dict, *, field: str, path: str) -> None:
    op = condition.get("op")
    if not isinstance(op, str) or op not in FILTER_OPS:
        raise _invalid("invalid_filters", "unknown filter op", path=f"{path}.op", op=str(op))
    restrictions = FIELD_OP_RESTRICTIONS.get(field)
    if restrictions is not None and op not in restrictions:
        raise _invalid(
            "invalid_filters",
            f"op {op!r} is not allowed for field {field!r}",
            path=f"{path}.op",
            field=field,
            allowed=sorted(restrictions),
        )
    value = condition.get("value")
    if op in _NULL_OPS:
        if "value" in condition and value is not None:
            raise _invalid(
                "invalid_filters",
                f"op {op!r} takes no value",
                path=f"{path}.value",
            )
        return
    if "value" not in condition or value is None:
        raise _invalid(
            "invalid_filters", f"op {op!r} requires a value", path=f"{path}.value"
        )
    if op in _LIST_OPS:
        if not isinstance(value, list) or not value:
            raise _invalid(
                "invalid_filters",
                f"op {op!r} requires a non-empty array value",
                path=f"{path}.value",
            )
        if not all(_is_scalar_json(item) for item in value):
            raise _invalid(
                "invalid_filters", "list values must be scalars", path=f"{path}.value"
            )
        return
    if not _is_scalar_json(value):
        raise _invalid(
            "invalid_filters", "value must be a scalar", path=f"{path}.value"
        )


def validate_filters(value: Any) -> dict:
    """Validate a filters JSONB payload; returns the normalized form.

    ``{}`` means "no filter". A non-empty filter is ``{"operator":
    "AND"|"OR", "conditions": [...]}`` with nesting capped at depth 3 and 20
    conditions total (README §6.14).
    """
    if value == {}:
        return {}
    if not isinstance(value, dict):
        raise _invalid("invalid_filters", "filters must be an object", path="$")
    extra = set(value) - _FILTER_GROUP_KEYS
    if extra:
        raise _invalid("invalid_filters", "unknown keys in filters", path="$", keys=sorted(extra))
    operator = value.get("operator")
    conditions = value.get("conditions")
    if operator not in ("AND", "OR"):
        raise _invalid("invalid_filters", "operator must be AND or OR", path="$.operator")
    if not isinstance(conditions, list) or not conditions:
        raise _invalid(
            "invalid_filters", "conditions must be a non-empty array", path="$.conditions"
        )
    if _count_conditions(conditions) > FILTER_MAX_CONDITIONS:
        raise ValidationError(
            "filter has too many conditions",
            code="filter_too_complex",
            details={
                "conditions": _count_conditions(conditions),
                "max": FILTER_MAX_CONDITIONS,
            },
        )
    for index, condition in enumerate(conditions):
        _validate_condition(condition, path=f"$.conditions[{index}]", depth=2)
    return value


def validate_sort(value: Any) -> list[dict]:
    """Validate a sort rules array (ordered, earlier rules win)."""
    if not isinstance(value, list):
        raise _invalid("invalid_sort", "sort must be an array", path="$")
    for index, rule in enumerate(value):
        path = f"$.[{index}]"
        if not isinstance(rule, dict):
            raise _invalid("invalid_sort", "sort rule must be an object", path=path)
        order = rule.get("order")
        if order not in ("asc", "desc"):
            raise _invalid("invalid_sort", "order must be asc or desc", path=f"{path}.order")
        if rule.get("field_kind") == "custom_field" or "field_def_id" in rule:
            extra = set(rule) - {"field_kind", "field_def_id", "order"}
            if extra:
                raise _invalid(
                    "invalid_sort", "unknown keys in sort rule", path=path, keys=sorted(extra)
                )
            field_def_id = rule.get("field_def_id")
            if not isinstance(field_def_id, str) or not field_def_id:
                raise _invalid(
                    "invalid_sort",
                    "custom-field sort rule needs a non-empty field_def_id",
                    path=f"{path}.field_def_id",
                )
            continue
        extra = set(rule) - {"field", "order"}
        if extra:
            raise _invalid(
                "invalid_sort", "unknown keys in sort rule", path=path, keys=sorted(extra)
            )
        field = rule.get("field")
        if not isinstance(field, str) or field not in SORT_FIELDS:
            raise _invalid(
                "invalid_sort", "unknown sort field", path=f"{path}.field", field=str(field)
            )
    return value


def _validate_wip(wip: Any) -> dict:
    if not isinstance(wip, dict):
        raise _invalid("invalid_board_settings", "wip must be an object", path="$.wip")
    normalized: dict[str, dict] = {}
    for group_key, rule in wip.items():
        path = f"$.wip[{group_key!r}]"
        if not isinstance(group_key, str) or not group_key:
            raise _invalid(
                "invalid_board_settings", "wip group key must be a non-empty string", path=path
            )
        if not isinstance(rule, dict):
            raise _invalid("invalid_board_settings", "wip rule must be an object", path=path)
        extra = set(rule) - WIP_LIMIT_KEYS
        if extra:
            raise _invalid(
                "invalid_board_settings", "unknown keys in wip rule", path=path, keys=sorted(extra)
            )
        limit = rule.get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise _invalid(
                "invalid_board_settings", "wip limit must be an integer >= 1", path=f"{path}.limit"
            )
        enforcement = rule.get("enforcement", "warn")
        if enforcement not in WIP_ENFORCEMENTS:
            raise _invalid(
                "invalid_board_settings",
                "wip enforcement must be warn or block",
                path=f"{path}.enforcement",
            )
        normalized[group_key] = {"limit": limit, "enforcement": enforcement}
    return normalized


def validate_display_fields(value: Any) -> list[str]:
    """Validate display_fields: an array of non-empty column/field keys."""
    if not isinstance(value, list):
        raise _invalid("invalid_display_fields", "display_fields must be an array", path="$")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise _invalid(
                "invalid_display_fields",
                "display_fields entries must be non-empty strings",
                path=f"$.[{index}]",
            )
    return list(value)


def validate_board_settings(value: Any) -> dict:
    """Validate board_settings (columns / collapsed / card_fields / wip)."""
    if not isinstance(value, dict):
        raise _invalid("invalid_board_settings", "board_settings must be an object", path="$")
    extra = set(value) - _BOARD_SETTING_KEYS
    if extra:
        raise _invalid(
            "invalid_board_settings",
            "unknown board_settings keys",
            path="$",
            keys=sorted(extra),
        )
    normalized: dict[str, Any] = {}
    for key in _STRING_ARRAY_KEYS:
        if key in value:
            entries = value[key]
            if not isinstance(entries, list) or not all(
                isinstance(item, str) and item for item in entries
            ):
                raise _invalid(
                    "invalid_board_settings",
                    f"{key} must be an array of non-empty strings",
                    path=f"$.{key}",
                )
            normalized[key] = entries
    if "wip" in value:
        normalized["wip"] = _validate_wip(value["wip"])
    return normalized


def validate_group_by(value: Any) -> str | None:
    """Validate group_by (NULL = board default state_category at render time)."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in GROUP_BY_FIELDS:
        raise ValidationError(
            "unknown group_by field",
            code="invalid_group_by",
            details={"group_by": str(value), "allowed": sorted(GROUP_BY_FIELDS)},
        )
    return value


def validate_name(name: Any) -> str:
    """Validate the view name length bounds (kanban §2.2 CHECK)."""
    if not isinstance(name, str) or not (
        NAME_MIN_LENGTH <= len(name.strip()) <= NAME_MAX_LENGTH
    ):
        raise ValidationError(
            "view name must be 1-100 characters",
            code="invalid_name",
            details={"length": len(name) if isinstance(name, str) else None},
        )
    return name.strip()


def validate_layout(value: Any) -> str:
    if not isinstance(value, str) or value not in VIEW_LAYOUT_VALUES:
        raise ValidationError(
            "unknown layout",
            code="invalid_layout",
            details={"layout": str(value), "allowed": list(VIEW_LAYOUT_VALUES)},
        )
    return value


def validate_visibility(value: Any) -> str:
    if not isinstance(value, str) or value not in VIEW_VISIBILITY_VALUES:
        raise ValidationError(
            "unknown visibility",
            code="invalid_visibility",
            details={"visibility": str(value), "allowed": list(VIEW_VISIBILITY_VALUES)},
        )
    return value
