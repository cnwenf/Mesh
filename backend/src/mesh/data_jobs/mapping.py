"""Import/export mapping configuration validation (import-export.md §2.4).

The mapping is JSONB authored by the client (or auto-inferred): import
maps ``source`` columns onto Mesh ``target`` fields with a value
transform; export selects source fields and renames output columns
(``target`` = source field, ``source`` = output column name). Invalid
mappings fail fast with ``400 mapping_invalid`` (§3.12) — never inside
the worker.
"""

from __future__ import annotations

from typing import Any

from mesh.errors import ValidationError

# Import targets — issues (issue.md §1.2.1 field set, §2.4).
ISSUE_TARGETS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "status",
        "priority",
        "assignee",
        "reporter",
        "estimate",
        "due_date",
        "start_date",
        "project",
        "milestone",
        "cycle",
        "labels",
        "parent",
        "external_ref",
    }
)
# custom_field_values.<field_key> targets are validated by prefix.
CUSTOM_FIELD_TARGET_PREFIX = "custom_field_values."

# Import targets — projects (project.md §2.2).
PROJECT_TARGETS: frozenset[str] = frozenset(
    {"name", "key", "description", "status", "health", "lead", "start_date", "target_date"}
)

TRANSFORM_TYPES: frozenset[str] = frozenset(
    {
        "direct",
        "value_map",
        "status_by_name",
        "member_by_email",
        "date_parse",
        "list_split",
        "parent_by_external_ref",
    }
)

# Required import targets: an issue without a title mapping cannot be
# created (§5.1 — missing required mapping → 400 mapping_invalid).
REQUIRED_ISSUE_TARGETS: frozenset[str] = frozenset({"title"})
REQUIRED_PROJECT_TARGETS: frozenset[str] = frozenset({"name", "key"})

# Export source fields — issues. Superset of the import targets plus
# read-only system fields (§3.5 example maps ``identifier`` etc.).
ISSUE_EXPORT_FIELDS: frozenset[str] = frozenset(
    {
        "identifier",
        "title",
        "description",
        "status",
        "status_category",
        "priority",
        "assignee",
        "reporter",
        "estimate",
        "due_date",
        "start_date",
        "project",
        "labels",
        "parent",
        "external_ref",
        "created_at",
        "updated_at",
    }
)
PROJECT_EXPORT_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "key",
        "description",
        "status",
        "health",
        "lead",
        "start_date",
        "target_date",
        "created_at",
        "updated_at",
    }
)

DEFAULT_ISSUE_EXPORT_FIELDS: tuple[str, ...] = (
    "identifier",
    "title",
    "description",
    "status",
    "status_category",
    "priority",
    "assignee",
    "reporter",
    "estimate",
    "due_date",
    "start_date",
    "labels",
    "created_at",
    "updated_at",
)
DEFAULT_PROJECT_EXPORT_FIELDS: tuple[str, ...] = (
    "name",
    "key",
    "description",
    "status",
    "health",
    "lead",
    "start_date",
    "target_date",
)


def _mapping_invalid(message: str, **details: object) -> ValidationError:
    return ValidationError(message, code="mapping_invalid", details=details or None)


def _custom_field_key(target: str) -> str | None:
    if target.startswith(CUSTOM_FIELD_TARGET_PREFIX):
        key = target[len(CUSTOM_FIELD_TARGET_PREFIX) :]
        return key or None
    return None


def validate_import_mapping(mapping: dict[str, Any], *, entity_type: str) -> dict[str, Any]:
    """Validate + normalize an import mapping (§2.4).

    Raises ``ValidationError(code='mapping_invalid')`` on unknown targets,
    missing/unknown transforms, duplicate targets, or a missing required
    mapping. Returns the mapping unchanged on success.
    """
    columns = mapping.get("columns")
    if not isinstance(columns, list) or not columns:
        raise _mapping_invalid("mapping.columns must be a non-empty array")
    targets = entity_targets(entity_type)
    seen: set[str] = set()
    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            raise _mapping_invalid("mapping.columns entries must be objects", index=index)
        source = column.get("source")
        target = column.get("target")
        if not isinstance(source, str) or not source:
            raise _mapping_invalid("column.source is required", index=index)
        if not isinstance(target, str) or not target:
            raise _mapping_invalid("column.target is required", index=index)
        custom_key = _custom_field_key(target)
        if custom_key is None and target not in targets:
            raise _mapping_invalid(f"unknown mapping target {target!r}", index=index, target=target)
        if target in seen:
            raise _mapping_invalid(f"duplicate mapping target {target!r}", index=index)
        seen.add(target)
        transform = column.get("transform")
        if not isinstance(transform, dict):
            raise _mapping_invalid("column.transform is required", index=index)
        transform_type = transform.get("type")
        if transform_type not in TRANSFORM_TYPES:
            raise _mapping_invalid(f"unknown transform type {transform_type!r}", index=index)
        if transform_type == "value_map":
            value_map = transform.get("map")
            if not isinstance(value_map, dict) or not value_map:
                raise _mapping_invalid("value_map transform requires a non-empty 'map'", index=index)
        if transform_type == "status_by_name":
            fallback = transform.get("fallback", "default")
            if fallback not in ("default", "error"):
                raise _mapping_invalid("status_by_name fallback must be 'default' or 'error'", index=index)
        if transform_type == "member_by_email":
            on_missing = transform.get("on_missing", "null")
            if on_missing not in ("null", "error"):
                raise _mapping_invalid("member_by_email on_missing must be 'null' or 'error'", index=index)
        if transform_type == "list_split":
            delimiter = transform.get("delimiter", ",")
            if not isinstance(delimiter, str) or not delimiter:
                raise _mapping_invalid("list_split delimiter must be a string", index=index)
    required = REQUIRED_ISSUE_TARGETS if entity_type == "issues" else REQUIRED_PROJECT_TARGETS
    missing = sorted(required - seen)
    if missing:
        raise _mapping_invalid(f"required target(s) not mapped: {', '.join(missing)}", missing=missing)
    defaults = mapping.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        raise _mapping_invalid("mapping.defaults must be an object")
    options = mapping.get("options")
    if options is not None and not isinstance(options, dict):
        raise _mapping_invalid("mapping.options must be an object")
    return mapping


def validate_export_mapping(mapping: dict[str, Any] | None, *, entity_type: str) -> list[dict[str, str]]:
    """Validate an export mapping and return the resolved column list.

    Export columns select source fields (``target``) and rename them
    (``source`` = output column name); transforms do not apply (§2.4 last
    paragraph / §3.5). An absent/empty mapping falls back to the default
    field set.
    """
    fields = ISSUE_EXPORT_FIELDS if entity_type == "issues" else PROJECT_EXPORT_FIELDS
    defaults = DEFAULT_ISSUE_EXPORT_FIELDS if entity_type == "issues" else DEFAULT_PROJECT_EXPORT_FIELDS
    columns_in = (mapping or {}).get("columns") if isinstance(mapping, dict) else None
    if not columns_in:
        return [{"target": field, "source": field} for field in defaults]
    if not isinstance(columns_in, list):
        raise _mapping_invalid("mapping.columns must be an array")
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, column in enumerate(columns_in):
        if not isinstance(column, dict):
            raise _mapping_invalid("mapping.columns entries must be objects", index=index)
        target = column.get("target")
        output = column.get("source") or target
        if not isinstance(target, str) or target not in fields:
            raise _mapping_invalid(f"unknown export field {target!r}", index=index, target=target)
        if not isinstance(output, str) or not output:
            raise _mapping_invalid("column.source must be a string", index=index)
        if target in seen:
            raise _mapping_invalid(f"duplicate export field {target!r}", index=index)
        seen.add(target)
        resolved.append({"target": target, "source": output})
    return resolved


def entity_targets(entity_type: str) -> frozenset[str]:
    return ISSUE_TARGETS if entity_type == "issues" else PROJECT_TARGETS


# ---------------------------------------------------------------------------
# auto-inference (§3.2: server-side mapping draft from headers/samples)
# ---------------------------------------------------------------------------

# Lowercased header token → (target, transform) draft rules. First match
# wins; unknown headers are left unmapped for the user to configure.
_INFER_RULES: tuple[tuple[tuple[str, ...], str, dict[str, Any]], ...] = (
    (("title", "summary", "name", "subject", "标题", "名称"), "title", {"type": "direct"}),
    (("description", "body", "content", "描述"), "description", {"type": "direct"}),
    (("state", "status", "状态"), "status", {"type": "status_by_name", "fallback": "default"}),
    (
        ("priority", "优先级"),
        "priority",
        {
            "type": "value_map",
            "map": {
                "highest": "urgent",
                "high": "high",
                "medium": "medium",
                "low": "low",
                "none": "none",
                "urgent": "urgent",
                "紧急": "urgent",
                "高": "high",
                "中": "medium",
                "低": "low",
            },
            "default": "none",
        },
    ),
    (
        ("assignee email", "assignee", "assigned to", "owner", "负责人", "指派给"),
        "assignee",
        {"type": "member_by_email", "on_missing": "null"},
    ),
    (
        ("reporter email", "reporter", "creator", "created by", "报告人", "创建人"),
        "reporter",
        {"type": "member_by_email", "on_missing": "null"},
    ),
    (("due", "due date", "deadline", "截止日期"), "due_date", {"type": "date_parse", "format": "auto"}),
    (("start date", "start", "开始日期"), "start_date", {"type": "date_parse", "format": "auto"}),
    (
        ("labels", "tags", "label", "tag", "标签"),
        "labels",
        {"type": "list_split", "delimiter": ",", "create_missing": True},
    ),
    (("estimate", "story points", "points", "估时", "工时"), "estimate", {"type": "direct"}),
    (("key", "id", "issue id", "number", "编号", "编号 "), "external_ref", {"type": "direct"}),
    (("parent", "parent key", "epic", "父任务"), "parent", {"type": "parent_by_external_ref"}),
)


def infer_mapping(headers: list[str], *, entity_type: str) -> dict[str, Any]:
    """Draft a mapping from source headers by name similarity (§3.2)."""
    targets = entity_targets(entity_type)
    columns: list[dict[str, Any]] = []
    used: set[str] = set()
    for header in headers:
        normalized = str(header).strip().lower()
        for tokens, target, transform in _INFER_RULES:
            if target not in targets or target in used:
                continue
            if normalized in tokens or any(token in normalized for token in tokens):
                columns.append({"source": header, "target": target, "transform": dict(transform)})
                used.add(target)
                break
    return {"columns": columns, "defaults": {}, "options": {"strict": False}}
