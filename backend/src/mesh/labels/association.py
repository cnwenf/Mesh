"""Issue-association layer service (label-property.md §2.3/§2.6/§3/§3.5).

The MES-32 remainder increment: hanging labels and typed custom-field values
off issues. Two stateless orchestrators composed on :class:`IssueService`
(same pattern as dependency / move / bulk / template services):

- :class:`IssueLabelService` — the ``issue_labels`` M2M surface: list / add /
  remove / whole-set replace, project-scope enforcement (422
  ``label_scope_mismatch``), ``issue.labels_changed`` broadcasts.
- :class:`FieldValueService` — the ``issue_custom_field_values`` EAV surface:
  list (field snapshot + current value) and whole-form PUT with per-type
  validation (422 ``invalid_field_value`` / ``field_inactive``), per-field
  ``issue.custom_field_changed`` broadcasts.

Both reuse the issue module's resource-level authorization
(``assert_can_view_issue`` / ``assert_can_write_issue``, issue.md §3.3) and
emit through the outbox unique write path on the issue channels: the detail
channel ``issue:{id}`` always, plus ``workspace:{ws}:issues`` for
workspace-level issues and public-project issues (mirrors
``IssueService._emit_issue_event``).
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.auth.audit import write_audit
from mesh.db.models.issue import Issue
from mesh.db.models.label import (
    SELECT_FIELD_TYPES,
    CustomFieldDef,
    CustomFieldOption,
    IssueCustomFieldValue,
    IssueLabel,
    Label,
)
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from mesh.issue.service import IssueService
from mesh.labels.service import LabelService
from mesh.member.display import resolve_display_name
from mesh.outbox.service import emit_realtime

ISSUE_CHANNEL = "issue:{issue_id}"
WORKSPACE_ISSUES_CHANNEL = "workspace:{workspace_id}:issues"

_ISSUE_NOT_FOUND = "issue not found"
_LABEL_NOT_FOUND = "label not found"
_FIELD_DEF_NOT_FOUND = "custom field not found"

# §3.4: labels/field values ride with the issue — whole lists, never paged.
# PUT cap mirrors the §5.4 write-performance baseline (≤20 fields) with slack.
MAX_VALUES_PER_PUT = 50

TEXT_VALUE_MAX = 2000
TEXTAREA_VALUE_MAX = 10000
URL_VALUE_MAX = 2048
URL_PATTERN = re.compile(r"^https?://[^\s]+$")

# def.type → the single value column legal for that type (§2.6).
TYPE_VALUE_COLUMN: dict[str, str] = {
    "text": "value_text",
    "textarea": "value_text",
    "url": "value_text",
    "number": "value_number",
    "date": "value_date",
    "datetime": "value_date",
    "single_select": "value_json",
    "multi_select": "value_json",
    "member": "value_member_id",
    "boolean": "value_boolean",
}

VALUE_COLUMNS = (
    "value_text",
    "value_number",
    "value_date",
    "value_member_id",
    "value_boolean",
    "value_json",
)


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _isoformat(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _matches_updated_at(updated_at: datetime, if_match: str) -> bool:
    """If-Match comparison against issue.updated_at (README §6.14)."""
    candidate = if_match.strip().strip('"')
    if candidate == _isoformat(updated_at):
        return True
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed == updated_at


def _invalid_value(field_def_id: uuid.UUID, reason: str, **details: Any) -> BusinessRuleError:
    return BusinessRuleError(
        "invalid custom field value",
        code="invalid_field_value",
        details={"field_def_id": str(field_def_id), "reason": reason, **details},
    )


# ---------------------------------------------------------------------------
# shared issue-side helpers
# ---------------------------------------------------------------------------


async def _load_issue_row(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    for_update: bool = False,
) -> Issue:
    stmt = select(Issue).where(
        Issue.id == issue_id,
        Issue.workspace_id == workspace_id,
        Issue.deleted_at.is_(None),
    )
    if for_update:
        # Serializes concurrent association writes so the emitted whole-list
        # payloads cannot interleave (mirrors IssueService's always-lock).
        stmt = stmt.with_for_update()
    issue = await session.scalar(stmt)
    if issue is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    return issue


async def _load_issue_project(session: AsyncSession, issue: Issue) -> Project | None:
    if issue.project_id is None:
        return None
    return await session.scalar(
        select(Project).where(
            Project.id == issue.project_id,
            Project.workspace_id == issue.workspace_id,
        )
    )


async def _emit_issue_event(
    session: AsyncSession,
    *,
    issue: Issue,
    project: Project | None,
    event: str,
    data: dict,
) -> None:
    """Detail channel always; workspace list channel for visible issues.

    Mirrors ``IssueService._emit_issue_event``: private-project issues only
    hit ``issue:{id}``; workspace-level / public-project issues additionally
    fan out to ``workspace:{ws}:issues``.
    """
    await emit_realtime(
        session,
        workspace_id=issue.workspace_id,
        channel=ISSUE_CHANNEL.format(issue_id=issue.id),
        event=event,
        data=data,
    )
    if project is None or project.visibility == "public":
        await emit_realtime(
            session,
            workspace_id=issue.workspace_id,
            channel=WORKSPACE_ISSUES_CHANNEL.format(workspace_id=issue.workspace_id),
            event=event,
            data=data,
        )


async def _audit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor: Member,
    action: str,
    resource_id: uuid.UUID,
    metadata: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_member_id=actor.id,
        actor_kind="member",
        action=action,
        resource_type="issue",
        resource_id=resource_id,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _assert_label_scope(label: Label, issue: Issue) -> None:
    """§2.3 note: project-level labels only apply to same-project issues."""
    if label.project_id is not None and label.project_id != issue.project_id:
        raise BusinessRuleError(
            "project-scoped label cannot be applied to another project's issue",
            code="label_scope_mismatch",
            details={
                "label_id": str(label.id),
                "label_project_id": str(label.project_id),
                "issue_project_id": str(issue.project_id)
                if issue.project_id is not None
                else None,
            },
        )


def _bump_issue(issue: Issue, now: datetime) -> None:
    """Advance ``updated_at`` + ``version`` on a REAL association change.

    §5.4: concurrent writes to the same issue arbitrate on ``updated_at`` —
    an association write must invalidate previously handed-out ``If-Match``
    tokens, otherwise concurrent writers silently overwrite each other (the
    second writer carrying the stale token must get 409 ``conflict``).
    """
    issue.version = issue.version + 1
    issue.updated_at = now


# ---------------------------------------------------------------------------
# issue ↔ labels
# ---------------------------------------------------------------------------


class IssueLabelService:
    """Stateless orchestrator over ``issue_labels`` (§2.3 / §3.1)."""

    def __init__(
        self,
        issue_service: IssueService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issues = issue_service
        self._clock = clock

    async def _current_labels(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, issue_id: uuid.UUID
    ) -> list[Label]:
        rows = (
            await session.execute(
                select(Label)
                .join(IssueLabel, IssueLabel.label_id == Label.id)
                .where(
                    IssueLabel.workspace_id == workspace_id,
                    IssueLabel.issue_id == issue_id,
                )
                .order_by(IssueLabel.created_at.asc(), IssueLabel.label_id.asc())
            )
        ).scalars().all()
        return list(rows)

    async def _load_label(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, label_id: uuid.UUID
    ) -> Label:
        label = await session.scalar(
            select(Label).where(
                Label.id == label_id, Label.workspace_id == workspace_id
            )
        )
        if label is None:
            raise NotFoundError(_LABEL_NOT_FOUND)
        return label

    async def list_issue_labels(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
    ) -> list[dict]:
        async with self._issues._factory() as session:
            await set_tenant_context(session, workspace_id)
            issue = await _load_issue_row(
                session, workspace_id=workspace_id, issue_id=issue_id
            )
            await self._issues.assert_can_view_issue(session, viewer=viewer, issue=issue)
            labels = await self._current_labels(
                session, workspace_id=workspace_id, issue_id=issue_id
            )
            return [LabelService.render_label(label) for label in labels]

    async def add_label(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        label_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._issues._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            issue = await _load_issue_row(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            label = await self._load_label(
                session, workspace_id=workspace_id, label_id=label_id
            )
            _assert_label_scope(label, issue)
            attached = await session.scalar(
                select(IssueLabel.label_id).where(
                    IssueLabel.issue_id == issue.id, IssueLabel.label_id == label.id
                )
            )
            if attached is None:
                session.add(
                    IssueLabel(
                        workspace_id=workspace_id,
                        issue_id=issue.id,
                        label_id=label.id,
                    )
                )
                _bump_issue(issue, _now(self._clock))
                await session.flush()
                project = await _load_issue_project(session, issue)
                labels = await self._current_labels(
                    session, workspace_id=workspace_id, issue_id=issue.id
                )
                rendered = [LabelService.render_label(row) for row in labels]
                await _emit_issue_event(
                    session,
                    issue=issue,
                    project=project,
                    event="issue.labels_changed",
                    data={"issue_id": str(issue.id), "labels": rendered},
                )
                await _audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="issue.label_added",
                    resource_id=issue.id,
                    metadata={"label_id": str(label.id)},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            labels = await self._current_labels(
                session, workspace_id=workspace_id, issue_id=issue.id
            )
            return {"labels": [LabelService.render_label(row) for row in labels]}

    async def remove_label(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        label_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._issues._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            issue = await _load_issue_row(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            # Removing a non-attached label is an idempotent no-op (no event).
            result = await session.execute(
                delete(IssueLabel).where(
                    IssueLabel.issue_id == issue.id, IssueLabel.label_id == label_id
                )
            )
            if result.rowcount:
                _bump_issue(issue, _now(self._clock))
                project = await _load_issue_project(session, issue)
                labels = await self._current_labels(
                    session, workspace_id=workspace_id, issue_id=issue.id
                )
                rendered = [LabelService.render_label(row) for row in labels]
                await _emit_issue_event(
                    session,
                    issue=issue,
                    project=project,
                    event="issue.labels_changed",
                    data={"issue_id": str(issue.id), "labels": rendered},
                )
                await _audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="issue.label_removed",
                    resource_id=issue.id,
                    metadata={"label_id": str(label_id)},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            labels = await self._current_labels(
                session, workspace_id=workspace_id, issue_id=issue.id
            )
            return {"labels": [LabelService.render_label(row) for row in labels]}

    async def replace_labels(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        label_ids: list[uuid.UUID],
        if_match: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        # De-duplicate while preserving request order.
        wanted: list[uuid.UUID] = list(dict.fromkeys(label_ids))
        if len(wanted) > MAX_VALUES_PER_PUT:
            raise ValidationError(
                f"at most {MAX_VALUES_PER_PUT} labels per request",
                details={"count": len(wanted)},
            )
        async with self._issues._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            issue = await _load_issue_row(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            if if_match is not None and not _matches_updated_at(
                issue.updated_at, if_match
            ):
                raise ConflictError(
                    "issue was modified concurrently",
                    code="conflict",
                    details={"id": str(issue.id)},
                )
            found = list(
                (
                    await session.execute(
                        select(Label).where(
                            Label.workspace_id == workspace_id,
                            Label.id.in_(wanted) if wanted else Label.id.is_(None),
                        )
                    )
                ).scalars().all()
            )
            found_ids = {label.id for label in found}
            missing = [str(label_id) for label_id in wanted if label_id not in found_ids]
            if missing:
                raise NotFoundError(
                    "labels not found", details={"label_ids": missing}
                )
            for label in found:
                _assert_label_scope(label, issue)
            current = await self._current_labels(
                session, workspace_id=workspace_id, issue_id=issue.id
            )
            current_ids = {label.id for label in current}
            wanted_set = set(wanted)
            to_remove = current_ids - wanted_set
            to_add = wanted_set - current_ids
            if to_remove:
                await session.execute(
                    delete(IssueLabel).where(
                        IssueLabel.issue_id == issue.id,
                        IssueLabel.label_id.in_(to_remove),
                    )
                )
            for label_id in sorted(to_add, key=str):
                session.add(
                    IssueLabel(
                        workspace_id=workspace_id,
                        issue_id=issue.id,
                        label_id=label_id,
                    )
                )
            if to_remove or to_add:
                _bump_issue(issue, _now(self._clock))
                await session.flush()
                project = await _load_issue_project(session, issue)
                labels = await self._current_labels(
                    session, workspace_id=workspace_id, issue_id=issue.id
                )
                rendered = [LabelService.render_label(row) for row in labels]
                await _emit_issue_event(
                    session,
                    issue=issue,
                    project=project,
                    event="issue.labels_changed",
                    data={"issue_id": str(issue.id), "labels": rendered},
                )
                await _audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="issue.labels_replaced",
                    resource_id=issue.id,
                    metadata={
                        "added": sorted(str(i) for i in to_add),
                        "removed": sorted(str(i) for i in to_remove),
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            labels = await self._current_labels(
                session, workspace_id=workspace_id, issue_id=issue.id
            )
            return {"labels": [LabelService.render_label(row) for row in labels]}


# ---------------------------------------------------------------------------
# issue custom-field values
# ---------------------------------------------------------------------------


class FieldValueService:
    """Stateless orchestrator over ``issue_custom_field_values`` (§2.6)."""

    def __init__(
        self,
        issue_service: IssueService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issues = issue_service
        self._clock = clock

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_value(
        row: IssueCustomFieldValue,
        member: dict | None = None,
    ) -> dict:
        number = row.value_number
        return {
            "field_def_id": str(row.field_def_id),
            "issue_id": str(row.issue_id),
            "value_text": row.value_text,
            "value_number": float(number) if number is not None else None,
            "value_date": _isoformat(row.value_date),
            "value_member_id": str(row.value_member_id)
            if row.value_member_id is not None
            else None,
            "value_member": member,
            "value_boolean": row.value_boolean,
            "value_json": row.value_json,
            "created_at": _isoformat(row.created_at),
            "updated_at": _isoformat(row.updated_at),
        }

    async def _member_snapshots(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, dict]:
        if not member_ids:
            return {}
        rows = (
            await session.execute(
                select(Member, User)
                .join(User, User.id == Member.user_id, isouter=True)
                .where(
                    Member.workspace_id == workspace_id,
                    Member.id.in_(member_ids),
                )
            )
        ).all()
        snapshots: dict[uuid.UUID, dict] = {}
        for member, user in rows:
            snapshots[member.id] = {
                "id": str(member.id),
                # member.md §2.4 single display_name (agent_name joins once
                # the agents table exists — None until then).
                "name": resolve_display_name(member=member, user=user),
                "member_type": "agent" if member.agent_id is not None else "human",
            }
        return snapshots

    async def _applicable_defs(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        issue: Issue,
        active_only: bool = True,
    ) -> list[CustomFieldDef]:
        """Workspace-level defs + the issue's project-scoped defs (§4.3)."""
        stmt = select(CustomFieldDef).where(CustomFieldDef.workspace_id == workspace_id)
        if active_only:
            stmt = stmt.where(CustomFieldDef.is_active.is_(True))
        if issue.project_id is not None:
            stmt = stmt.where(
                CustomFieldDef.project_id.is_(None)
                | (CustomFieldDef.project_id == issue.project_id)
            )
        else:
            stmt = stmt.where(CustomFieldDef.project_id.is_(None))
        stmt = stmt.order_by(CustomFieldDef.position.asc(), CustomFieldDef.created_at.asc())
        return list((await session.execute(stmt)).scalars().all())

    async def _options_for(
        self, session: AsyncSession, *, field_def_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[CustomFieldOption]]:
        if not field_def_ids:
            return {}
        rows = (
            await session.execute(
                select(CustomFieldOption)
                .where(CustomFieldOption.field_def_id.in_(field_def_ids))
                .order_by(
                    CustomFieldOption.position.asc(), CustomFieldOption.created_at.asc()
                )
            )
        ).scalars().all()
        grouped: dict[uuid.UUID, list[CustomFieldOption]] = {}
        for row in rows:
            grouped.setdefault(row.field_def_id, []).append(row)
        return grouped

    async def _build_listing(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        issue: Issue,
    ) -> list[dict]:
        """Full panel payload: every applicable active def + its current value."""
        definitions = await self._applicable_defs(
            session, workspace_id=workspace_id, issue=issue
        )
        value_rows = list(
            (
                await session.execute(
                    select(IssueCustomFieldValue).where(
                        IssueCustomFieldValue.workspace_id == workspace_id,
                        IssueCustomFieldValue.issue_id == issue.id,
                    )
                )
            ).scalars().all()
        )
        values_by_def = {row.field_def_id: row for row in value_rows}
        select_defs = [d for d in definitions if d.type in SELECT_FIELD_TYPES]
        options_by_def = await self._options_for(
            session, field_def_ids=[d.id for d in select_defs]
        )
        member_ids = {
            row.value_member_id
            for row in value_rows
            if row.value_member_id is not None
        }
        members = await self._member_snapshots(
            session, workspace_id=workspace_id, member_ids=member_ids
        )
        listing: list[dict] = []
        for definition in definitions:
            row = values_by_def.get(definition.id)
            listing.append(
                {
                    "field_def": LabelService.render_field_def(
                        definition, options_by_def.get(definition.id)
                    ),
                    "value": self.render_value(
                        row, member=members.get(row.value_member_id)
                    )
                    if row is not None
                    else None,
                }
            )
        return listing

    async def list_values(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
    ) -> list[dict]:
        async with self._issues._factory() as session:
            await set_tenant_context(session, workspace_id)
            issue = await _load_issue_row(
                session, workspace_id=workspace_id, issue_id=issue_id
            )
            await self._issues.assert_can_view_issue(session, viewer=viewer, issue=issue)
            return await self._build_listing(
                session, workspace_id=workspace_id, issue=issue
            )

    # ------------------------------------------------------------------
    # per-type value validation (§2.6 — service layer is the main front)
    # ------------------------------------------------------------------

    async def _coerce_value(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        definition: CustomFieldDef,
        column: str,
        raw: Any,
    ) -> Any:
        """Validate ``raw`` for ``definition.type`` → normalized stored value.

        Raises 422 ``invalid_field_value`` with a ``reason`` in details.
        """
        field_type = definition.type
        config = definition.config or {}
        if field_type in ("text", "textarea"):
            maximum = TEXT_VALUE_MAX if field_type == "text" else TEXTAREA_VALUE_MAX
            if not isinstance(raw, str) or len(raw) > maximum:
                raise _invalid_value(
                    definition.id,
                    "text_value_invalid",
                    expected=field_type,
                    max_length=maximum,
                )
            return raw
        if field_type == "url":
            if (
                not isinstance(raw, str)
                or len(raw) > URL_VALUE_MAX
                or not URL_PATTERN.match(raw)
            ):
                raise _invalid_value(definition.id, "url_value_invalid", expected="url")
            if config.get("require_https") and not raw.startswith("https://"):
                raise _invalid_value(definition.id, "url_https_required", expected="url")
            return raw
        if field_type == "number":
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                raise _invalid_value(
                    definition.id, "number_value_invalid", expected="number"
                )
            minimum = config.get("min")
            maximum = config.get("max")
            if minimum is not None and raw < minimum:
                raise _invalid_value(
                    definition.id, "number_below_min", min=minimum
                )
            if maximum is not None and raw > maximum:
                raise _invalid_value(
                    definition.id, "number_above_max", max=maximum
                )
            precision = config.get("precision")
            # Same-sign comparison: round(...,10) normalizes float
            # representation noise, the right side is the target precision.
            # (abs() on only one side rejected every negative in precision.)
            if precision is not None and round(float(raw), 10) != round(
                float(raw), precision
            ):
                raise _invalid_value(
                    definition.id, "number_precision_exceeded", precision=precision
                )
            return Decimal(str(raw))
        if field_type in ("date", "datetime"):
            if not isinstance(raw, str):
                raise _invalid_value(
                    definition.id, "date_value_invalid", expected=field_type
                )
            try:
                if field_type == "date":
                    parsed_date = date.fromisoformat(raw)
                    return datetime(
                        parsed_date.year,
                        parsed_date.month,
                        parsed_date.day,
                        tzinfo=UTC,
                    )
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                raise _invalid_value(
                    definition.id, "date_value_invalid", expected=field_type
                ) from None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        if field_type == "boolean":
            if not isinstance(raw, bool):
                raise _invalid_value(
                    definition.id, "boolean_value_invalid", expected="boolean"
                )
            return raw
        if field_type == "member":
            if not isinstance(raw, str):
                raise _invalid_value(
                    definition.id, "member_value_invalid", expected="member_id"
                )
            try:
                member_id = uuid.UUID(raw)
            except ValueError:
                raise _invalid_value(
                    definition.id, "member_value_invalid", expected="member_id"
                ) from None
            member = await session.scalar(
                select(Member.id).where(
                    Member.workspace_id == workspace_id, Member.id == member_id
                )
            )
            if member is None:
                raise _invalid_value(
                    definition.id,
                    "member_not_in_workspace",
                    value_member_id=raw,
                )
            return member_id
        if field_type in SELECT_FIELD_TYPES:
            options = list(
                (
                    await session.execute(
                        select(CustomFieldOption).where(
                            CustomFieldOption.field_def_id == definition.id
                        )
                    )
                ).scalars().all()
            )
            active_ids = {str(o.id) for o in options if o.is_active}
            if field_type == "single_select":
                if not isinstance(raw, str) or raw not in active_ids:
                    raise _invalid_value(
                        definition.id,
                        "option_not_in_field",
                        unknown_option_ids=[raw] if isinstance(raw, str) else None,
                    )
                return raw
            if (
                not isinstance(raw, list)
                or not all(isinstance(item, str) for item in raw)
            ):
                raise _invalid_value(
                    definition.id, "multi_select_value_invalid", expected="option id array"
                )
            unknown = [item for item in raw if item not in active_ids]
            if unknown:
                raise _invalid_value(
                    definition.id, "option_not_in_field", unknown_option_ids=unknown
                )
            # De-duplicate preserving order; [] normalizes to clear (no row).
            deduped = list(dict.fromkeys(raw))
            return deduped if deduped else None
        raise _invalid_value(definition.id, "unsupported_field_type", type=field_type)

    @staticmethod
    def _row_is_current(row: IssueCustomFieldValue, column: str, value: Any) -> bool:
        """True when the stored row already holds exactly (column, value)."""
        for candidate in VALUE_COLUMNS:
            stored = getattr(row, candidate)
            if candidate == column:
                if candidate == "value_json":
                    if stored != value:
                        return False
                elif stored != value:
                    return False
            elif stored is not None:
                return False
        return True

    # ------------------------------------------------------------------
    # PUT /issues/{id}/custom-field-values
    # ------------------------------------------------------------------

    async def set_values(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        values: list[dict],
        if_match: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> list[dict]:
        if not isinstance(values, list):
            raise ValidationError("values must be an array")
        if len(values) > MAX_VALUES_PER_PUT:
            raise ValidationError(
                f"at most {MAX_VALUES_PER_PUT} field values per request",
                details={"count": len(values)},
            )
        parsed: list[tuple[uuid.UUID, str | None, Any]] = []
        seen: set[uuid.UUID] = set()
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                raise ValidationError(
                    "each values entry must be an object", details={"index": index}
                )
            raw_def_id = item.get("field_def_id")
            if not isinstance(raw_def_id, str):
                raise ValidationError(
                    "values[].field_def_id is required", details={"index": index}
                )
            try:
                field_def_id = uuid.UUID(raw_def_id)
            except ValueError:
                raise ValidationError(
                    "invalid field_def_id",
                    details={"index": index, "field_def_id": raw_def_id[:64]},
                ) from None
            if field_def_id in seen:
                raise ValidationError(
                    "duplicate field_def_id in values",
                    details={"field_def_id": raw_def_id},
                )
            seen.add(field_def_id)
            provided = [key for key in VALUE_COLUMNS if key in item]
            unknown_keys = sorted(
                set(item) - {"field_def_id", *VALUE_COLUMNS}
            )
            if unknown_keys:
                raise ValidationError(
                    "unknown keys in values entry",
                    details={"index": index, "unknown_keys": unknown_keys},
                )
            if len(provided) > 1:
                raise _invalid_value(
                    field_def_id,
                    "exactly_one_value_column",
                    provided=provided,
                )
            column = provided[0] if provided else None
            raw_value = item.get(column) if column is not None else None
            parsed.append((field_def_id, column, raw_value))

        async with self._issues._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            issue = await _load_issue_row(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            if if_match is not None and not _matches_updated_at(
                issue.updated_at, if_match
            ):
                raise ConflictError(
                    "issue was modified concurrently",
                    code="conflict",
                    details={"id": str(issue.id)},
                )

            definitions = list(
                (
                    await session.execute(
                        select(CustomFieldDef).where(
                            CustomFieldDef.workspace_id == workspace_id,
                            CustomFieldDef.id.in_(list(seen))
                            if seen
                            else CustomFieldDef.id.is_(None),
                        )
                    )
                ).scalars().all()
            )
            defs_by_id = {definition.id: definition for definition in definitions}
            missing = [str(field_def_id) for field_def_id, _, _ in parsed
                       if field_def_id not in defs_by_id]
            if missing:
                raise NotFoundError(
                    "custom fields not found", details={"field_def_ids": missing}
                )

            existing_rows = {
                row.field_def_id: row
                for row in (
                    await session.execute(
                        select(IssueCustomFieldValue).where(
                            IssueCustomFieldValue.workspace_id == workspace_id,
                            IssueCustomFieldValue.issue_id == issue.id,
                            IssueCustomFieldValue.field_def_id.in_(list(seen)),
                        )
                    )
                ).scalars().all()
            }

            changed: list[tuple[CustomFieldDef, IssueCustomFieldValue | None]] = []
            stamp = _now(self._clock)
            for field_def_id, column, raw_value in parsed:
                definition = defs_by_id[field_def_id]
                if not definition.is_active:
                    raise BusinessRuleError(
                        "cannot write values for an inactive field",
                        code="field_inactive",
                        details={"field_def_id": str(field_def_id)},
                    )
                if (
                    definition.project_id is not None
                    and definition.project_id != issue.project_id
                ):
                    # A project-scoped definition does not apply to this issue;
                    # surface as 404 exactly like a foreign-tenant definition.
                    raise NotFoundError(
                        _FIELD_DEF_NOT_FOUND,
                        details={"field_def_id": str(field_def_id)},
                    )
                row = existing_rows.get(field_def_id)
                if column is None or raw_value is None:
                    # Explicit clear: drop the row (empty multi_select included).
                    if row is not None:
                        await session.delete(row)
                        changed.append((definition, None))
                    continue
                expected_column = TYPE_VALUE_COLUMN[definition.type]
                if column != expected_column:
                    raise _invalid_value(
                        definition.id,
                        "wrong_value_column",
                        expected_column=expected_column,
                        got_column=column,
                    )
                stored = await self._coerce_value(
                    session,
                    workspace_id=workspace_id,
                    definition=definition,
                    column=column,
                    raw=raw_value,
                )
                if stored is None:
                    # Normalized to empty (e.g. multi_select []) → clear.
                    if row is not None:
                        await session.delete(row)
                        changed.append((definition, None))
                    continue
                if row is not None:
                    if self._row_is_current(row, column, stored):
                        continue  # §6.9: no change → no event
                    for candidate in VALUE_COLUMNS:
                        setattr(row, candidate, None)
                    setattr(row, column, stored)
                    row.updated_at = stamp
                    changed.append((definition, row))
                else:
                    new_row = IssueCustomFieldValue(
                        workspace_id=workspace_id,
                        issue_id=issue.id,
                        field_def_id=definition.id,
                    )
                    setattr(new_row, column, stored)
                    session.add(new_row)
                    changed.append((definition, new_row))

            if changed:
                _bump_issue(issue, _now(self._clock))
                await session.flush()
                project = await _load_issue_project(session, issue)
                member_ids = {
                    row.value_member_id
                    for _, row in changed
                    if row is not None and row.value_member_id is not None
                }
                members = await self._member_snapshots(
                    session, workspace_id=workspace_id, member_ids=member_ids
                )
                for definition, row in changed:
                    await _emit_issue_event(
                        session,
                        issue=issue,
                        project=project,
                        event="issue.custom_field_changed",
                        data={
                            "issue_id": str(issue.id),
                            "field_def_id": str(definition.id),
                            "field_key": definition.field_key,
                            "value": self.render_value(
                                row, member=members.get(row.value_member_id)
                            )
                            if row is not None
                            else None,
                        },
                    )
                await _audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="issue.custom_fields_updated",
                    resource_id=issue.id,
                    metadata={
                        "fields": sorted(d.field_key for d, _ in changed),
                        "cleared": sorted(
                            d.field_key for d, r in changed if r is None
                        ),
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            return await self._build_listing(
                session, workspace_id=workspace_id, issue=issue
            )
