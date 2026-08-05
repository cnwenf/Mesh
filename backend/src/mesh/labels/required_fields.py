"""Required custom-field validation hook (label-property.md §4.5 / §3.3).

The issue module calls :func:`validate_required_field_values` at its two
spec-mandated occasions — issue save and status-category transition (issue.md
calls this module's hook BEFORE the flow completes). ``required_on`` grammar
is ``"save"`` / ``"status:<category>"`` (labels/service.py); an EMPTY list
means "validate on save" (§2.4 空=保存即校验).

Runs inside the CALLER's transaction on the caller's session — no tenant GUC
work of its own, no events: a failure raises 422 ``required_field_missing``
and aborts the surrounding write (就地阻断,属校验而非通知).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.issue import Issue
from mesh.db.models.label import CustomFieldDef, IssueCustomFieldValue
from mesh.errors import BusinessRuleError

# §2.4 required_on entry marking status-category transitions.
_STATUS_OCCASION_PREFIX = "status:"


def _occasion_matches(required_on: list, occasion: str) -> bool:
    """Does a ``required_on`` config fire on ``occasion`` ("save" / "status:x")?

    Empty config = validate on save only (§2.4). Otherwise the occasion must
    appear verbatim in the list.
    """
    if not required_on:
        return occasion == "save"
    return occasion in required_on


def _required_value_is_present(
    definition: CustomFieldDef,
    row: IssueCustomFieldValue | None,
) -> bool:
    """Return whether ``row`` holds a non-empty value for its field type.

    Row existence alone is insufficient: the member FK intentionally leaves
    an all-NULL row after physical member deletion, and JSONB can represent an
    empty multi-select. Valid falsy scalar values (number zero and boolean
    false) remain present.
    """
    if row is None:
        return False
    if definition.type in {"text", "textarea", "url"}:
        return isinstance(row.value_text, str) and row.value_text != ""
    if definition.type == "number":
        return row.value_number is not None
    if definition.type in {"date", "datetime"}:
        return row.value_date is not None
    if definition.type == "member":
        return row.value_member_id is not None
    if definition.type == "boolean":
        return row.value_boolean is not None
    if definition.type == "single_select":
        return isinstance(row.value_json, str) and row.value_json != ""
    if definition.type == "multi_select":
        return (
            isinstance(row.value_json, list)
            and bool(row.value_json)
            and all(isinstance(item, str) and item != "" for item in row.value_json)
        )
    return False


async def find_missing_required_values(
    session: AsyncSession,
    *,
    issue: Issue,
    occasions: Iterable[str],
) -> list[dict]:
    """Return ``[{"field_def_id", "name"}]`` for required fields lacking a value.

    Applicable definitions = workspace-level + the issue's project scope,
    active, required, and matching at least one occasion. Presence is checked
    against the definition's value column and empty semantics; an all-NULL row
    left by a member FK deletion and an empty multi-select remain missing.
    """
    occasion_set = set(occasions)
    if not occasion_set:
        return []
    stmt = select(CustomFieldDef).where(
        CustomFieldDef.workspace_id == issue.workspace_id,
        CustomFieldDef.is_active.is_(True),
        CustomFieldDef.is_required.is_(True),
    )
    if issue.project_id is not None:
        stmt = stmt.where(
            CustomFieldDef.project_id.is_(None)
            | (CustomFieldDef.project_id == issue.project_id)
        )
    else:
        stmt = stmt.where(CustomFieldDef.project_id.is_(None))
    definitions = list((await session.execute(stmt)).scalars().all())
    candidates = [
        definition
        for definition in definitions
        if any(_occasion_matches(list(definition.required_on), o) for o in occasion_set)
    ]
    if not candidates:
        return []
    value_rows = list(
        (
            await session.execute(
                select(IssueCustomFieldValue).where(
                    IssueCustomFieldValue.workspace_id == issue.workspace_id,
                    IssueCustomFieldValue.issue_id == issue.id,
                    IssueCustomFieldValue.field_def_id.in_(
                        [definition.id for definition in candidates]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    values_by_field = {row.field_def_id: row for row in value_rows}
    return [
        {"field_def_id": str(definition.id), "name": definition.name}
        for definition in candidates
        if not _required_value_is_present(
            definition, values_by_field.get(definition.id)
        )
    ]


async def validate_required_field_values(
    session: AsyncSession,
    *,
    issue: Issue,
    occasions: Iterable[str],
) -> None:
    """Raise 422 ``required_field_missing`` when any required field is empty."""
    missing = await find_missing_required_values(
        session, issue=issue, occasions=occasions
    )
    if missing:
        raise BusinessRuleError(
            "required custom fields are missing",
            code="required_field_missing",
            details={"missing": missing},
        )
