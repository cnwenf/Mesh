"""Pure per-row value transforms for imports (import-export.md §2.4 / §3.3).

``transform_row`` applies the mapping to one source row and returns
``(values, errors, warnings)`` with ZERO database access — the
``TransformContext`` is preloaded once per job (statuses / members /
labels / custom field defs / milestones / cycles / projects) so the hot
row loop never touches the DB (memory + latency, §5).

Row-level error codes are the §2.4 closed vocabulary; anything raised as
a Python exception here is a bug, not a row error.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.issue import ISSUE_PRIORITY_VALUES, TITLE_MAX_LENGTH, IssueStatus
from mesh.db.models.label import CustomFieldDef, Label
from mesh.db.models.member import Member
from mesh.db.models.project import Cycle, Milestone, Project
from mesh.db.models.user import User

# §2.4 row-level code vocabulary (closed set).
ROW_ERROR_CODES: frozenset[str] = frozenset(
    {
        "required_field_missing",
        "unknown_member",
        "unknown_status",
        "unknown_label",
        "invalid_date",
        "invalid_value",
        "parent_not_found",
        "duplicate_within_file",
        "project_key_taken",
        "unsupported_value",
    }
)

# project.md key format (prefix registry, README §6.3).
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,9}$")

_MISSING = object()  # sentinel: source cell absent / empty


@dataclass(frozen=True)
class RowError:
    """One row-level error/warning entry (§2.4 error_report shape)."""

    row: int
    field: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.code not in ROW_ERROR_CODES:
            raise ValueError(f"row error code {self.code!r} outside §2.4 vocabulary")

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class StatusInfo:
    """Resolved issue status (id + denormalized category)."""

    id: uuid.UUID
    category: str


@dataclass(frozen=True)
class CustomFieldInfo:
    """Custom field definition reference for value coercion."""

    id: uuid.UUID
    type: str


@dataclass(frozen=True)
class TransformContext:
    """Preloaded lookup tables — the row loop must stay DB-free (§5)."""

    entity_type: str
    statuses_by_name: dict[str, StatusInfo] = field(default_factory=dict)
    default_status: StatusInfo | None = None
    members_by_email: dict[str, uuid.UUID] = field(default_factory=dict)
    # Human member ids only — agent assignees are rejected on import (a
    # bulk import must not enqueue a fleet of agent executions).
    human_member_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    labels_by_name: dict[str, uuid.UUID] = field(default_factory=dict)
    custom_fields_by_key: dict[str, CustomFieldInfo] = field(default_factory=dict)
    projects_by_key: dict[str, uuid.UUID] = field(default_factory=dict)
    milestones_by_name: dict[str, uuid.UUID] = field(default_factory=dict)
    cycles_by_name: dict[str, uuid.UUID] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


async def build_context(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    entity_type: str,
    project_id: uuid.UUID | None,
    mapping: dict[str, Any],
) -> TransformContext:
    """Preload every lookup table the transforms need (one pass, hot loop DB-free).

    Conflicts: statuses — project-scoped wins over workspace-level;
    labels — workspace-level wins (import scope agnostic); default status
    — project default first, else workspace default.
    """
    options = mapping.get("options") or {}
    statuses_by_name: dict[str, StatusInfo] = {}
    default_status: StatusInfo | None = None
    members_by_email: dict[str, uuid.UUID] = {}
    labels_by_name: dict[str, uuid.UUID] = {}
    custom_fields_by_key: dict[str, CustomFieldInfo] = {}
    projects_by_key: dict[str, uuid.UUID] = {}
    milestones_by_name: dict[str, uuid.UUID] = {}
    cycles_by_name: dict[str, uuid.UUID] = {}
    if entity_type == "issues":
        status_rows = (
            (
                await session.execute(
                    select(IssueStatus).where(
                        IssueStatus.workspace_id == workspace_id,
                        IssueStatus.project_id.is_(None)
                        if project_id is None
                        else IssueStatus.project_id.in_((project_id, None)),
                    )
                )
            )
            .scalars()
            .all()
        )
        workspace_defaults: list[StatusInfo] = []
        project_defaults: list[StatusInfo] = []
        for status in status_rows:
            info = StatusInfo(id=status.id, category=status.category)
            if status.project_id is None:
                statuses_by_name.setdefault(status.name, info)
                if status.is_default:
                    workspace_defaults.append(info)
            else:
                statuses_by_name[status.name] = info  # project wins
                if status.is_default:
                    project_defaults.append(info)
        if project_defaults or workspace_defaults:
            default_status = (project_defaults or workspace_defaults)[0]
        member_rows = (
            await session.execute(
                select(Member.id, func.lower(User.email))
                .join(User, User.id == Member.user_id)
                .where(
                    Member.workspace_id == workspace_id,
                    Member.status == "active",
                )
            )
        ).all()
        members_by_email = {email: member_id for member_id, email in member_rows}
        label_rows = (
            await session.execute(
                select(Label.id, Label.name, Label.project_id).where(
                    Label.workspace_id == workspace_id,
                    Label.project_id.is_(None)
                    if project_id is None
                    else Label.project_id.in_((project_id, None)),
                )
            )
        ).all()
        for label_id, name, label_project_id in label_rows:
            if label_project_id is None:
                labels_by_name[name] = label_id  # workspace wins
            else:
                labels_by_name.setdefault(name, label_id)
        field_rows = (
            (
                await session.execute(
                    select(CustomFieldDef).where(
                        CustomFieldDef.workspace_id == workspace_id,
                        CustomFieldDef.project_id.is_(None),
                        CustomFieldDef.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        custom_fields_by_key = {
            defn.field_key: CustomFieldInfo(id=defn.id, type=defn.type) for defn in field_rows
        }
        project_rows = (
            await session.execute(
                select(Project.id, Project.key).where(
                    Project.workspace_id == workspace_id, Project.deleted_at.is_(None)
                )
            )
        ).all()
        projects_by_key = {key: pid for pid, key in project_rows}
        if project_id is not None:
            milestone_rows = (
                await session.execute(
                    select(Milestone.id, Milestone.title).where(
                        Milestone.workspace_id == workspace_id,
                        Milestone.project_id == project_id,
                    )
                )
            ).all()
            milestones_by_name = {title: mid for mid, title in milestone_rows}
            cycle_rows = (
                await session.execute(
                    select(Cycle.id, Cycle.name).where(
                        Cycle.workspace_id == workspace_id, Cycle.project_id == project_id
                    )
                )
            ).all()
            cycles_by_name = {name: cid for cid, name in cycle_rows}
    else:  # projects import: only member (lead) resolution is needed.
        member_rows = (
            await session.execute(
                select(Member.id, func.lower(User.email))
                .join(User, User.id == Member.user_id)
                .where(Member.workspace_id == workspace_id, Member.status == "active")
            )
        ).all()
        members_by_email = {email: member_id for member_id, email in member_rows}
    return TransformContext(
        entity_type=entity_type,
        statuses_by_name=statuses_by_name,
        default_status=default_status,
        members_by_email=members_by_email,
        human_member_ids=frozenset(members_by_email.values()),
        labels_by_name=labels_by_name,
        custom_fields_by_key=custom_fields_by_key,
        projects_by_key=projects_by_key,
        milestones_by_name=milestones_by_name,
        cycles_by_name=cycles_by_name,
        options=options,
    )


def _cell(raw_row: dict[str, Any], source: str) -> Any:
    value = raw_row.get(source, _MISSING)
    if value is _MISSING or value is None:
        return _MISSING
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else _MISSING
    return value


def parse_date_value(raw: Any) -> date:
    """Parse a date/datetime back to a UTC date (§2.4 date_parse)."""
    if isinstance(raw, datetime):
        return raw.astimezone(UTC).date() if raw.tzinfo else raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed.astimezone(UTC).date() if parsed.tzinfo else parsed.date()
    text_raw = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text_raw, fmt).date()
        except ValueError:
            continue
    # %d/%m/%Y only when the first segment is unambiguously a day (>12).
    parts = text_raw.split("/")
    if len(parts) == 3 and parts[0].isdigit() and int(parts[0]) > 12:
        try:
            return datetime.strptime(text_raw, "%d/%m/%Y").date()
        except ValueError:
            pass
    raise ValueError(f"unparseable date: {raw!r}")


def _coerce_custom_value(field_info: CustomFieldInfo, value: Any, context: TransformContext) -> Any:
    """Coerce a mapped cell to the custom field's storage type."""
    if field_info.type in ("text", "textarea", "url", "single_select", "multi_select"):
        return str(value)
    if field_info.type == "number":
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"not a number: {value!r}") from exc
    if field_info.type in ("date", "datetime"):
        return parse_date_value(value)
    if field_info.type == "boolean":
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
        raise ValueError(f"not a boolean: {value!r}")
    if field_info.type == "member":
        member_id = context.members_by_email.get(str(value).strip().lower())
        if member_id is None:
            raise ValueError(f"no member matches email {value!r}")
        return member_id
    raise ValueError(f"unsupported field type {field_info.type!r}")


def transform_row(
    row_number: int,
    raw_row: dict[str, Any],
    mapping: dict[str, Any],
    context: TransformContext,
) -> tuple[dict[str, Any], list[RowError], list[RowError]]:
    """Apply the mapping to one source row (pure; DB-free).

    Returns ``(values, errors, warnings)``. ``values`` is keyed by target
    with converted Python values (``status`` → StatusInfo, members →
    UUID, labels → list[str], ``custom_field_values`` → dict, parent →
    ``parent_external_ref``). Errors make the row fail; warnings are
    recorded but the row still imports.
    """
    values: dict[str, Any] = {}
    errors: list[RowError] = []
    warnings: list[RowError] = []
    custom_values: dict[str, Any] = {}
    defaults = mapping.get("defaults") or {}

    for column in mapping.get("columns") or ():
        source = column["source"]
        target = column["target"]
        transform = column["transform"]
        transform_type = transform["type"]
        raw = _cell(raw_row, source)

        if raw is _MISSING:
            required_targets = {"title"} if context.entity_type == "issues" else {"name", "key"}
            if target in required_targets:
                errors.append(
                    RowError(
                        row_number, target, "required_field_missing", f"required column {source!r} is empty"
                    )
                )
            continue

        try:
            if transform_type == "direct":
                converted: Any = str(raw)
            elif transform_type == "value_map":
                value_map = transform.get("map") or {}
                lowered_map = {str(k).lower(): v for k, v in value_map.items()}
                matched = value_map.get(str(raw)) or lowered_map.get(str(raw).lower())
                converted = matched if matched is not None else transform.get("default")
                if converted is None:
                    raise ValueError(f"no value_map entry for {raw!r}")
            elif transform_type == "status_by_name":
                status = context.statuses_by_name.get(str(raw))
                if status is None:
                    if transform.get("fallback", "default") == "error":
                        errors.append(
                            RowError(
                                row_number, target, "unknown_status", f"no status named {raw!r} in scope"
                            )
                        )
                        continue
                    status = context.default_status
                    if status is None:
                        errors.append(
                            RowError(
                                row_number,
                                target,
                                "unknown_status",
                                f"no status named {raw!r} and no default status",
                            )
                        )
                        continue
                    warnings.append(
                        RowError(
                            row_number,
                            target,
                            "unknown_status",
                            f"status {raw!r} unmatched; fell back to default",
                        )
                    )
                converted = status
            elif transform_type == "member_by_email":
                member_id = context.members_by_email.get(str(raw).lower())
                if member_id is None:
                    if transform.get("on_missing", "null") == "error":
                        errors.append(
                            RowError(row_number, target, "unknown_member", f"no member matches email {raw!r}")
                        )
                        continue
                    converted = None
                else:
                    converted = member_id
            elif transform_type == "date_parse":
                converted = parse_date_value(raw)
            elif transform_type == "list_split":
                delimiter = transform.get("delimiter", ",")
                converted = [part.strip() for part in str(raw).split(delimiter) if part.strip()]
            elif transform_type == "parent_by_external_ref":
                values["parent_external_ref"] = str(raw)
                continue
            else:  # validated at mapping time; defensive
                raise ValueError(f"unsupported transform {transform_type!r}")
        except ValueError as exc:
            code = "invalid_date" if transform_type == "date_parse" else "invalid_value"
            errors.append(RowError(row_number, target, code, str(exc)))
            continue

        # Per-target post-validation / placement.
        if target == "title":
            title_str = str(converted)
            # Pre-validate length here so the dry-run PREDICTS the same row
            # failure the run would hit (H1 — keep validate/run in lockstep).
            if len(title_str) > TITLE_MAX_LENGTH:
                errors.append(
                    RowError(
                        row_number,
                        target,
                        "invalid_value",
                        f"title exceeds {TITLE_MAX_LENGTH} characters",
                    )
                )
            values["title"] = title_str
        elif target == "description":
            values["description"] = str(converted)
        elif target == "status":
            values["status"] = converted
        elif target == "priority":
            if converted not in ISSUE_PRIORITY_VALUES:
                errors.append(
                    RowError(row_number, target, "invalid_value", f"invalid priority {converted!r}")
                )
            else:
                values["priority"] = converted
        elif target in ("assignee", "reporter"):
            member_id = converted
            if isinstance(member_id, str):  # 'direct' transform carries a raw id
                try:
                    member_id = uuid.UUID(str(member_id))
                except ValueError:
                    errors.append(
                        RowError(row_number, target, "unknown_member", f"invalid member id {converted!r}")
                    )
                    continue
                if member_id not in context.human_member_ids:
                    errors.append(
                        RowError(
                            row_number, target, "unknown_member", "member id not found or not a human member"
                        )
                    )
                    continue
            values[f"{target}_id"] = member_id
        elif target == "estimate":
            try:
                values["estimate"] = Decimal(str(converted))
            except InvalidOperation:
                errors.append(
                    RowError(row_number, target, "invalid_value", f"estimate not numeric: {converted!r}")
                )
        elif target in ("due_date", "start_date"):
            values[target] = converted
        elif target == "project":
            project_id = context.projects_by_key.get(str(converted))
            if project_id is None:
                errors.append(
                    RowError(row_number, target, "invalid_value", f"no project with key {converted!r}")
                )
            else:
                values["project_id"] = project_id
        elif target == "milestone":
            milestone_id = context.milestones_by_name.get(str(converted))
            if milestone_id is None:
                errors.append(
                    RowError(row_number, target, "invalid_value", f"no milestone named {converted!r}")
                )
            else:
                values["milestone_id"] = milestone_id
        elif target == "cycle":
            cycle_id = context.cycles_by_name.get(str(converted))
            if cycle_id is None:
                errors.append(RowError(row_number, target, "invalid_value", f"no cycle named {converted!r}"))
            else:
                values["cycle_id"] = cycle_id
        elif target == "labels":
            create_missing = transform.get(
                "create_missing", (context.options or {}).get("create_missing_labels", True)
            )
            names = converted if isinstance(converted, list) else [str(converted)]
            if not create_missing:
                known = [n for n in names if n in context.labels_by_name]
                for missing_name in [n for n in names if n not in context.labels_by_name]:
                    errors.append(
                        RowError(row_number, target, "unknown_label", f"no label named {missing_name!r}")
                    )
                names = known
            values["labels"] = names
        elif target == "external_ref":
            values["external_ref"] = str(converted)
        elif target in ("name", "key", "health"):  # project targets (project.md §2.2)
            values[target] = str(converted)
        elif target == "lead":
            values["lead_member_id"] = converted
        elif target == "target_date":
            values["target_date"] = converted
        elif target.startswith("custom_field_values."):
            key = target.split(".", 1)[1]
            field_info = context.custom_fields_by_key.get(key)
            if field_info is None:
                errors.append(
                    RowError(
                        row_number, target, "unsupported_value", f"no active custom field with key {key!r}"
                    )
                )
                continue
            try:
                custom_values[key] = _coerce_custom_value(field_info, converted, context)
            except ValueError as exc:
                errors.append(RowError(row_number, target, "invalid_value", str(exc)))

    if context.entity_type == "issues":
        if not values.get("title"):
            if not any(e.field == "title" for e in errors):
                errors.append(RowError(row_number, "title", "required_field_missing", "title is required"))
        values.setdefault("priority", "none")
        fallback_category = defaults.get("state_category_fallback")
        if values.get("status") is None and context.default_status is not None:
            values["status"] = context.default_status
            if fallback_category:
                warnings.append(
                    RowError(row_number, "status", "unknown_status", "no status mapped; used scope default")
                )
        # Cross-field check (H1): predict the due<start rejection the run enforces.
        due_v = values.get("due_date")
        start_v = values.get("start_date")
        if isinstance(due_v, date) and isinstance(start_v, date) and due_v < start_v:
            errors.append(
                RowError(row_number, "due_date", "invalid_value", "due_date is before start_date")
            )
    else:  # projects
        for required in ("name", "key"):
            if not values.get(required) and not any(e.field == required for e in errors):
                errors.append(
                    RowError(row_number, required, "required_field_missing", f"{required} is required")
                )
        if values.get("key") and not PROJECT_KEY_RE.match(str(values["key"])):
            errors.append(
                RowError(row_number, "key", "invalid_value", f"invalid project key {values['key']!r}")
            )
        if values.get("status") and values["status"] not in (
            "planning",
            "active",
            "paused",
            "completed",
            "cancelled",
        ):
            errors.append(
                RowError(
                    row_number, "status", "invalid_value", f"invalid project status {values['status']!r}"
                )
            )
        if values.get("health") and values["health"] not in ("on_track", "at_risk", "off_track"):
            errors.append(
                RowError(
                    row_number, "health", "invalid_value", f"invalid project health {values['health']!r}"
                )
            )
    if custom_values:
        values["custom_field_values"] = custom_values
    return values, errors, warnings
