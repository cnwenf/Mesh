"""Issue service — CRUD, numbering, two-layer state, list queries (issue.md).

Each public method owns its transaction (``session_factory() + begin()``);
tenant-bound transactions set the ``mesh.workspace_id`` GUC up front (RLS
correctness). Shared steps live in ``_*_tx`` helpers so move/bulk/template
flows compose them into single atomic transactions.

Contract anchors (issue.md + README §6):
- numbering (README §6.3 / §9 T15): project issues take ``projects.issue_seq``
  under row lock; project-less issues take ``workspaces.inbox_issue_seq`` +
  the reserved inbox prefix; identifiers are immutable;
- two-layer state (§2.5): ``state_category`` is denormalized from the status
  on every write; ``completed_at`` tracks entering/leaving ``done``;
- optimistic concurrency (§3.4 / §6.14): ``version`` mismatch → 409 conflict;
- no-change PATCH is a no-op (§6.9: empty diff emits nothing, enqueues nothing);
- realtime via the outbox single write path (§6.6/§6.7); private-project
  issues only emit on ``issue:{id}``, never the workspace list channel;
- list limits (§6.14): ≤20 conditions / depth ≤3 → 400 filter_too_complex;
  ``statement_timeout`` overrun → 422 query_cost_exceeded; grouped queries
  use the overall-cursor contract.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import decode_cursor, encode_cursor
from mesh.auth.audit import write_audit
from mesh.auth.rbac import assert_scope, role_satisfies
from mesh.comment_inbox import subscriptions as inbox_subscriptions
from mesh.comment_inbox.notifications import emit_issue_change_notifications
from mesh.db.models.issue import (
    ISSUE_PRIORITY_VALUES,
    Issue,
    IssueActivity,
    IssueStatus,
)
from mesh.db.models.label import IssueLabel, Label
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Cycle, Milestone, Project, ProjectMember
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.issue.filters import (
    LIST_STATEMENT_TIMEOUT_MS,
    coerce_date,
    compile_filter_tree,
    validate_combined_condition_count,
)
from mesh.issue.graph import detect_parent_cycle, lock_issue_graph
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.statuses import (
    render_status,
    resolve_default_status,
    resolve_status_in_scope,
)
from mesh.issue.triggers import apply_assign_triggers
from mesh.labels.required_fields import validate_required_field_values
from mesh.member.display import resolve_display_name
from mesh.outbox.service import emit_realtime
from mesh.project.service import ProjectService
from mesh.validation import LIKE_ESCAPE_CHAR, escape_like
from mesh.workspace.service import DEFAULT_INBOX_PREFIX, next_inbox_issue_number

ISSUE_NOT_FOUND = "issue not found"

ISSUE_CHANNEL = "issue:{issue_id}"
WORKSPACE_ISSUES_CHANNEL = "workspace:{workspace_id}:issues"

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

SORT_FIELDS = ("position", "created_at", "priority", "due_date")
GROUP_FIELDS = ("state_category", "assignee", "priority", "project", "label", "cycle")
_PRIORITY_RANK = {
    "urgent": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "none": 4,
}
# Fields whose PATCH carries no activity trail (issue.md §2.2 note: high
# frequency drags would drown the trail).
_SILENT_ACTIVITY_FIELDS = frozenset({"position"})


class _Unset:
    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return "<UNSET>"


UNSET = _Unset()


@dataclass(frozen=True)
class IssuePatch:
    """Tri-state PATCH payload resolved from ``model_fields_set``."""

    title: str | _Unset = UNSET
    description: str | _Unset | None = UNSET
    project_id: str | _Unset | None = UNSET  # routes send through move endpoint
    status_id: uuid.UUID | _Unset = UNSET
    priority: str | _Unset = UNSET
    assignee_id: uuid.UUID | _Unset | None = UNSET
    reporter_id: uuid.UUID | _Unset | None = UNSET
    estimate: object | _Unset | None = UNSET
    estimate_unit: str | _Unset | None = UNSET
    due_date: date | _Unset | None = UNSET
    start_date: date | _Unset | None = UNSET
    milestone_id: uuid.UUID | _Unset | None = UNSET
    cycle_id: uuid.UUID | _Unset | None = UNSET
    parent_id: uuid.UUID | _Unset | None = UNSET
    position: float | _Unset = UNSET


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _isoformat(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _issue_channel(issue_id: uuid.UUID) -> str:
    return ISSUE_CHANNEL.format(issue_id=issue_id)


def _workspace_issues_channel(workspace_id: uuid.UUID) -> str:
    return WORKSPACE_ISSUES_CHANNEL.format(workspace_id=workspace_id)


def _limit_page(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit < 1:
        raise ValidationError("limit must be >= 1", code="invalid_limit")
    return min(limit, MAX_PAGE_LIMIT)


def _parse_uuid(raw: str | uuid.UUID | None, *, field: str) -> uuid.UUID | None:
    """Request schemas carry UUIDs as strings; normalize with a 400 on junk."""
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details={field: raw[:64]}) from exc


def _json_value(value: object) -> object:
    """Render a field value for the activity trail JSONB columns."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return _isoformat(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


class IssueService:
    """Stateless orchestrator over the issue tables (issue.md §3)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
        squad_assignee_watcher: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock
        self._projects = ProjectService(session_factory, clock=clock)
        # squad.md §2.5: when an issue's assignee moves away from its active
        # squad leader, the squad module cancels that assignment (same txn).
        self._squad_assignee_watcher = squad_assignee_watcher

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    async def _member_summary(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, member_id: uuid.UUID | None
    ) -> dict | None:
        if member_id is None:
            return None
        member = await session.scalar(
            select(Member).where(Member.id == member_id, Member.workspace_id == workspace_id)
        )
        if member is None:
            return None
        user = None
        if member.user_id is not None:
            user = await session.scalar(select(User).where(User.id == member.user_id))
        return {
            "id": str(member.id),
            "name": resolve_display_name(member=member, user=user, agent_name=None),
            "member_type": member.member_type,
        }

    async def _labels_for_issues(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        issue_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[dict]]:
        """Load deterministic compact label snapshots for an issue page.

        Lists call this once for the whole page, avoiding one association query
        per row. Detail/create/update rendering reuses the same helper with a
        single id so every generic issue payload has the same label shape.
        """
        if not issue_ids:
            return {}
        rows = (
            await session.execute(
                select(IssueLabel.issue_id, Label.id, Label.name, Label.color)
                .join(
                    Label,
                    and_(
                        Label.workspace_id == IssueLabel.workspace_id,
                        Label.id == IssueLabel.label_id,
                    ),
                )
                .where(
                    IssueLabel.workspace_id == workspace_id,
                    IssueLabel.issue_id.in_(issue_ids),
                )
            )
        ).all()
        rows.sort(key=lambda row: (row.name.casefold(), str(row.id), str(row.issue_id)))
        result: dict[uuid.UUID, list[dict]] = {issue_id: [] for issue_id in issue_ids}
        for issue_id, label_id, name, color in rows:
            result[issue_id].append(
                {"id": str(label_id), "name": name, "color": color}
            )
        return result

    async def render_issue(
        self,
        session: AsyncSession,
        issue: Issue,
        *,
        with_children_progress: bool = False,
        labels: list[dict] | None = None,
    ) -> dict:
        status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.id == issue.status_id,
                IssueStatus.workspace_id == issue.workspace_id,
            )
        )
        project = None
        if issue.project_id is not None:
            project_row = await session.scalar(
                select(Project).where(
                    Project.id == issue.project_id, Project.workspace_id == issue.workspace_id
                )
            )
            if project_row is not None:
                project = {
                    "id": str(project_row.id),
                    "name": project_row.name,
                    "key": project_row.key,
                }
        if labels is None:
            labels = (
                await self._labels_for_issues(
                    session,
                    workspace_id=issue.workspace_id,
                    issue_ids=[issue.id],
                )
            )[issue.id]
        payload = {
            "id": str(issue.id),
            "workspace_id": str(issue.workspace_id),
            "project_id": str(issue.project_id) if issue.project_id is not None else None,
            "project": project,
            "identifier_namespace_key": issue.identifier_namespace_key,
            "number": issue.number,
            "identifier": issue.identifier,
            "title": issue.title,
            "description": issue.description,
            "status": render_status(status) if status is not None else None,
            "status_id": str(issue.status_id),
            "state_category": issue.state_category,
            "priority": issue.priority,
            "labels": labels,
            "assignee": await self._member_summary(
                session, workspace_id=issue.workspace_id, member_id=issue.assignee_id
            ),
            "assignee_id": str(issue.assignee_id) if issue.assignee_id is not None else None,
            "reporter": await self._member_summary(
                session, workspace_id=issue.workspace_id, member_id=issue.reporter_id
            ),
            "reporter_id": str(issue.reporter_id) if issue.reporter_id is not None else None,
            "estimate": float(issue.estimate) if issue.estimate is not None else None,
            "estimate_unit": issue.estimate_unit,
            "due_date": _isoformat(issue.due_date),
            "start_date": _isoformat(issue.start_date),
            "milestone_id": str(issue.milestone_id) if issue.milestone_id is not None else None,
            "cycle_id": str(issue.cycle_id) if issue.cycle_id is not None else None,
            "parent_id": str(issue.parent_id) if issue.parent_id is not None else None,
            "position": issue.position,
            "completed_at": issue.completed_at,
            "version": issue.version,
            "created_at": issue.created_at,
            "updated_at": issue.updated_at,
        }
        if with_children_progress:
            payload["children_progress"] = await self._children_progress(session, issue)
        return payload

    async def _children_progress(self, session: AsyncSession, issue: Issue) -> dict:
        rows = (
            await session.execute(
                select(Issue.state_category).where(
                    Issue.workspace_id == issue.workspace_id,
                    Issue.parent_id == issue.id,
                    Issue.deleted_at.is_(None),
                )
            )
        ).all()
        total = len(rows)
        done = sum(1 for (category,) in rows if category == "done")
        return {"total": total, "done": done}

    # ------------------------------------------------------------------
    # loading + authorization (issue.md §3.5)
    # ------------------------------------------------------------------

    async def _load_issue(
        self,
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
            stmt = stmt.with_for_update()
        issue = await session.scalar(stmt)
        if issue is None:
            raise NotFoundError(ISSUE_NOT_FOUND)
        return issue

    async def _project_of(self, session: AsyncSession, issue: Issue) -> Project | None:
        if issue.project_id is None:
            return None
        return await session.scalar(
            select(Project).where(
                Project.id == issue.project_id,
                Project.workspace_id == issue.workspace_id,
                Project.deleted_at.is_(None),
            )
        )

    async def assert_can_view_issue(
        self, session: AsyncSession, *, viewer: Member, issue: Issue
    ) -> None:
        """Read gate: public/no-project for members; private needs membership.

        Guests see only issues inside explicitly granted projects, or issues
        that involve them (assignee/reporter). Invisible → 404 for guests,
        403 for other members (project.md §3.3 pattern).
        """
        if role_satisfies(viewer.role, "project:manage"):
            return
        project = await self._project_of(session, issue)
        if viewer.role == "guest":
            if issue.assignee_id == viewer.id or issue.reporter_id == viewer.id:
                return
            if project is None:
                raise NotFoundError(ISSUE_NOT_FOUND)
            grant = await session.scalar(
                select(MemberProjectAccess.id).where(
                    MemberProjectAccess.project_id == project.id,
                    MemberProjectAccess.member_id == viewer.id,
                )
            )
            if grant is None:
                raise NotFoundError(ISSUE_NOT_FOUND)
            return
        if project is None or project.visibility == "public":
            return
        role = await session.scalar(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project.id, ProjectMember.member_id == viewer.id
            )
        )
        if role is None:
            raise ForbiddenError("project is private")

    async def assert_can_write_issue(
        self, session: AsyncSession, *, actor: Member, issue: Issue
    ) -> None:
        """Write gate (issue.md §3.5): project write permission or member+.

        Guests need an explicit write grant on the issue's project;
        project-less issues are not writable by guests. Scoped credentials
        (PAT / agent / device) are additionally ∩-gated on ``issue:write``
        (auth.md §2.5.1 — the role matrix is the ceiling, scopes the floor).
        """
        assert_scope(actor, "issue:write")
        if role_satisfies(actor.role, "project:manage"):
            return
        project = await self._project_of(session, issue)
        if actor.role == "guest":
            if project is None:
                raise ForbiddenError("guests cannot modify this issue")
            grant = await session.scalar(
                select(MemberProjectAccess.permission).where(
                    MemberProjectAccess.project_id == project.id,
                    MemberProjectAccess.member_id == actor.id,
                )
            )
            if grant != "write":
                raise ForbiddenError("guests cannot modify this issue")
            return
        if project is not None:
            await self._projects.assert_can_write(session, viewer=actor, project=project)

    # ------------------------------------------------------------------
    # realtime + audit helpers
    # ------------------------------------------------------------------

    async def _emit_issue_event(
        self,
        session: AsyncSession,
        *,
        issue: Issue,
        event: str,
        data: dict,
        project: Project | None,
    ):
        """Detail channel always; workspace list channel for visible issues.

        Private-project issues ONLY hit ``issue:{id}`` (README §6.7 —
        workspace channels must not broadcast private content for frontend
        filtering). Returns the detail-channel outbox event (its id anchors
        the §6.5 trigger idempotency keys).
        """
        detail_event = await emit_realtime(
            session,
            workspace_id=issue.workspace_id,
            channel=_issue_channel(issue.id),
            event=event,
            data=data,
        )
        if project is None or project.visibility == "public":
            await emit_realtime(
                session,
                workspace_id=issue.workspace_id,
                channel=_workspace_issues_channel(issue.workspace_id),
                event=event,
                data=data,
            )
        return detail_event

    async def _audit(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        actor: Member | None,
        action: str,
        resource_id: uuid.UUID,
        resource_type: str = "issue",
        metadata: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_member_id=actor.id if actor is not None else None,
            actor_kind="member" if actor is not None else "system",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def resolve_issue_workspace(self, issue_id: uuid.UUID) -> uuid.UUID | None:
        """Narrow SECURITY DEFINER lookup (migration 0009) — no tenant GUC yet."""
        async with self._factory() as session:
            return await session.scalar(
                text("SELECT mesh_issue_workspace_id(:id)"), {"id": issue_id}
            )

    # ------------------------------------------------------------------
    # numbering (issue.md §2.4, README §6.3 / §9 T15)
    # ------------------------------------------------------------------

    async def _next_identifier(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        project: Project | None,
    ) -> tuple[str, int, str]:
        """Row-locked namespace increment → (namespace_key, number, identifier)."""
        if project is not None:
            number = await self._projects.next_issue_number(session, project_id=project.id)
            key = project.key
        else:
            number = await next_inbox_issue_number(session, workspace_id=workspace_id)
            workspace = await session.scalar(
                select(Workspace).where(Workspace.id == workspace_id)
            )
            settings = (workspace.settings if workspace is not None else None) or {}
            key = settings.get("inbox_issue_prefix") or DEFAULT_INBOX_PREFIX
        return key, number, f"{key}-{number}"

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def _validate_member_ref(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
        field: str,
    ) -> Member:
        member = await session.scalar(
            select(Member).where(
                Member.id == member_id,
                Member.workspace_id == workspace_id,
                Member.status == "active",
            )
        )
        if member is None:
            raise BusinessRuleError(
                f"{field} is not an active member of this workspace",
                code="assignee_not_member",
                details={field: str(member_id)},
            )
        return member

    async def _validate_milestone(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID | None,
        milestone_id: uuid.UUID,
    ) -> None:
        milestone = await session.scalar(
            select(Milestone).where(
                Milestone.id == milestone_id, Milestone.workspace_id == workspace_id
            )
        )
        if milestone is None:
            raise ValidationError("milestone not found", details={"milestone_id": str(milestone_id)})
        if milestone.project_id != project_id:
            raise ValidationError(
                "milestone belongs to another project",
                details={"milestone_id": str(milestone_id)},
            )

    async def _validate_cycle(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, cycle_id: uuid.UUID
    ) -> None:
        cycle = await session.scalar(
            select(Cycle).where(Cycle.id == cycle_id, Cycle.workspace_id == workspace_id)
        )
        if cycle is None:
            raise ValidationError("cycle not found", details={"cycle_id": str(cycle_id)})

    async def _create_issue_tx(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateIssueRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
        template_skipped: list[dict] | None = None,
    ) -> dict:
        assert_scope(actor, "issue:write")
        if body.priority not in ISSUE_PRIORITY_VALUES:
            raise ValidationError("invalid priority", details={"priority": body.priority})
        if body.estimate_unit is not None and body.estimate_unit not in ("points", "hours"):
            raise ValidationError(
                "invalid estimate_unit", details={"estimate_unit": body.estimate_unit}
            )
        if (
            body.due_date is not None
            and body.start_date is not None
            and body.due_date < body.start_date
        ):
            raise ValidationError(
                "due_date must not be earlier than start_date",
                details={
                    "due_date": body.due_date.isoformat(),
                    "start_date": body.start_date.isoformat(),
                },
            )

        project: Project | None = None
        if body.project_id is not None:
            project = await self._projects._load_project(
                session,
                workspace_id=workspace_id,
                project_id=_parse_uuid(body.project_id, field="project_id"),
            )
            await self._projects.assert_can_write(session, viewer=actor, project=project)
        elif actor.role == "guest":
            raise ForbiddenError("guests cannot create issues without a project")

        # Status: explicit (must be usable in scope) or the scope default
        # (self-healing seed, README §6.3).
        status_id = _parse_uuid(body.status_id, field="status_id")
        if status_id is not None:
            status = await resolve_status_in_scope(
                session,
                workspace_id=workspace_id,
                project_id=project.id if project is not None else None,
                status_id=status_id,
            )
        else:
            status = await resolve_default_status(
                session,
                workspace_id=workspace_id,
                project_id=project.id if project is not None else None,
            )

        reporter_id = actor.id
        if body.reporter_id is not None:
            reporter = await self._validate_member_ref(
                session,
                workspace_id=workspace_id,
                member_id=_parse_uuid(body.reporter_id, field="reporter_id"),
                field="reporter_id",
            )
            reporter_id = reporter.id
        assignee_id: uuid.UUID | None = None
        if body.assignee_id is not None:
            assignee = await self._validate_member_ref(
                session,
                workspace_id=workspace_id,
                member_id=_parse_uuid(body.assignee_id, field="assignee_id"),
                field="assignee_id",
            )
            assignee_id = assignee.id
        milestone_id = _parse_uuid(body.milestone_id, field="milestone_id")
        if milestone_id is not None:
            await self._validate_milestone(
                session,
                workspace_id=workspace_id,
                project_id=project.id if project is not None else None,
                milestone_id=milestone_id,
            )
        cycle_id = _parse_uuid(body.cycle_id, field="cycle_id")
        if cycle_id is not None:
            await self._validate_cycle(session, workspace_id=workspace_id, cycle_id=cycle_id)
        parent_id = _parse_uuid(body.parent_id, field="parent_id")
        if parent_id is not None:
            parent = await self._load_issue(
                session, workspace_id=workspace_id, issue_id=parent_id
            )
            await self.assert_can_view_issue(session, viewer=actor, issue=parent)

        namespace_key, number, identifier = await self._next_identifier(
            session, workspace_id=workspace_id, project=project
        )
        issue = Issue(
            workspace_id=workspace_id,
            project_id=project.id if project is not None else None,
            identifier_namespace_key=namespace_key,
            number=number,
            identifier=identifier,
            title=body.title,
            description=body.description,
            status_id=status.id,
            state_category=status.category,
            priority=body.priority,
            assignee_id=assignee_id,
            reporter_id=reporter_id,
            estimate=body.estimate,
            estimate_unit=body.estimate_unit,
            due_date=body.due_date,
            start_date=body.start_date,
            milestone_id=milestone_id,
            cycle_id=cycle_id,
            parent_id=parent_id,
            position=body.position if body.position is not None else 0.0,
            completed_at=_now(self._clock) if status.category == "done" else None,
        )
        session.add(issue)
        await session.flush()

        # §2.5/§6.13 (L2): seed creator/assignee subscription rows so the issue
        # appears in their subscription list and per-issue mute is meaningful
        # from the start (implicit routing alone leaves the list empty).
        await inbox_subscriptions.ensure_subscription(
            session,
            workspace_id=workspace_id,
            issue_id=issue.id,
            subscriber_id=reporter_id,
            reason="creator",
        )
        if assignee_id is not None and assignee_id != reporter_id:
            await inbox_subscriptions.ensure_subscription(
                session,
                workspace_id=workspace_id,
                issue_id=issue.id,
                subscriber_id=assignee_id,
                reason="assignee",
            )

        rendered = await self.render_issue(session, issue)
        # §6.9 trigger hook: assignment to an agent emits issue.assigned in
        # the SAME transaction (realtime event id as the trigger anchor).
        realtime_event = await self._emit_issue_event(
            session,
            issue=issue,
            event="issue.created",
            data={"issue": _jsonify_issue(rendered)},
            project=project,
        )
        if assignee_id is not None:
            await apply_assign_triggers(
                session,
                workspace_id=workspace_id,
                issue=issue,
                previous_assignee_id=None,
                trigger_event_id=realtime_event.id,
            )
        await self._audit(
            session,
            workspace_id=workspace_id,
            actor=actor,
            action="issue.created",
            resource_id=issue.id,
            metadata={"identifier": identifier},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        # §5.3 I1: assignment at creation time notifies the assignee. The
        # fan-out matrix (comment-inbox owns it) self-suppresses when the
        # actor assigned themselves; we only register the outbox event in the
        # SAME transaction (README §4.4) so commit ⇒ notification is atomic.
        if assignee_id is not None:
            await emit_issue_change_notifications(
                session,
                workspace_id=workspace_id,
                issue=issue,
                actor=actor,
                actor_name=await self._actor_display_name(session, actor),
                actor_member_type=actor.member_type,
                assigned_to=assignee_id,
            )
        if template_skipped:
            rendered["skipped_fields"] = template_skipped
        return rendered

    async def create_issue(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateIssueRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            return await self._create_issue_tx(
                session,
                actor=actor,
                workspace_id=workspace_id,
                body=body,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    async def create_issue_in_session(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateIssueRequest,
    ) -> dict:
        """Create an issue inside the CALLER's transaction.

        Cross-module atomic write (same pattern as
        ``apply_confirmed_move_in_session``): the autopilot executor's
        ``create_issue`` action must commit the issue, its ``issue.created``
        outbox row and the autopilot issue artifact in ONE transaction —
        otherwise the relay can match the trigger event before the lineage
        anchor (artifact) exists and the ``create_issue ↔ issue_created``
        cascade escapes the depth guard (autopilot.md §4.5 / §5.3).
        """
        await set_tenant_context(session, workspace_id)
        return await self._create_issue_tx(
            session, actor=actor, workspace_id=workspace_id, body=body
        )

    # ------------------------------------------------------------------
    # get (UUID + by-identifier)
    # ------------------------------------------------------------------

    async def get_issue(
        self, *, viewer: Member, workspace_id: uuid.UUID, issue_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            issue = await self._load_issue(session, workspace_id=workspace_id, issue_id=issue_id)
            await self.assert_can_view_issue(session, viewer=viewer, issue=issue)
            return await self.render_issue(session, issue, with_children_progress=True)

    async def get_issue_by_identifier(
        self, *, viewer: Member, workspace_id: uuid.UUID, identifier: str
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            issue = await session.scalar(
                select(Issue).where(
                    Issue.workspace_id == workspace_id,
                    Issue.identifier == identifier,
                    Issue.deleted_at.is_(None),
                )
            )
            if issue is None:
                raise NotFoundError(ISSUE_NOT_FOUND)
            await self.assert_can_view_issue(session, viewer=viewer, issue=issue)
            return await self.render_issue(session, issue, with_children_progress=True)

    # ------------------------------------------------------------------
    # list (filter / sort / group, §3.2 / §3.5 / §6.14)
    # ------------------------------------------------------------------

    def _base_visibility_clause(self, viewer: Member, workspace_id: uuid.UUID):
        """SQL filter restricting rows to what the viewer may read.

        Every subquery is anchored on ``workspace_id`` (README §6.2 rule 5/6):
        the outer ``Issue.workspace_id`` filter plus RLS already guarantee
        correctness — this also keeps the subqueries off cross-tenant scans.
        """
        if role_satisfies(viewer.role, "project:manage"):
            return None
        if viewer.role == "guest":
            granted = select(MemberProjectAccess.project_id).where(
                MemberProjectAccess.member_id == viewer.id,
                MemberProjectAccess.workspace_id == workspace_id,
            )
            return or_(
                Issue.project_id.in_(granted),
                Issue.assignee_id == viewer.id,
                Issue.reporter_id == viewer.id,
            )
        member_projects = select(ProjectMember.project_id).where(
            ProjectMember.member_id == viewer.id,
            ProjectMember.workspace_id == workspace_id,
        )
        visible_projects = select(Project.id).where(
            Project.workspace_id == workspace_id,
            Project.visibility == "public",
            Project.deleted_at.is_(None),
        )
        return or_(
            Issue.project_id.is_(None),
            Issue.project_id.in_(member_projects),
            Issue.project_id.in_(visible_projects),
        )

    async def list_issues(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        status_id: uuid.UUID | None = None,
        state_category: str | None = None,
        priority: str | None = None,
        assignee_id: uuid.UUID | None = None,
        reporter_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        cycle_id: uuid.UUID | None = None,
        milestone_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
        due_before: date | None = None,
        due_after: date | None = None,
        q: str | None = None,
        filters: object | None = None,
        sort: str = "created_at",
        order: str = "desc",
        group_by: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        if sort not in SORT_FIELDS:
            raise ValidationError("invalid sort", details={"sort": sort})
        if order not in ("asc", "desc"):
            raise ValidationError("invalid order", details={"order": order})
        if group_by is not None:
            if group_by not in GROUP_FIELDS:
                raise ValidationError("invalid group_by", details={"group_by": group_by})
        if priority is not None and priority not in ISSUE_PRIORITY_VALUES:
            raise ValidationError("invalid priority", details={"priority": priority})
        if state_category is not None and state_category not in (
            "backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled"
        ):
            raise ValidationError("invalid state_category", details={"state_category": state_category})

        flat_conditions = sum(
            1
            for value in (
                status_id, state_category, priority, assignee_id, reporter_id,
                project_id, cycle_id, milestone_id, parent_id, due_before,
                due_after, q,
            )
            if value is not None
        )
        # §6.14: flat query params and the structured tree share ONE
        # 20-condition budget (MES-51 L6 — counting them apart allowed 32).
        validate_combined_condition_count(flat_conditions, filters)
        page_limit = _limit_page(limit)

        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            await session.execute(
                text(f"SET LOCAL statement_timeout = {LIST_STATEMENT_TIMEOUT_MS}")
            )
            stmt = select(Issue).where(
                Issue.workspace_id == workspace_id, Issue.deleted_at.is_(None)
            )
            visibility = self._base_visibility_clause(viewer, workspace_id)
            if visibility is not None:
                stmt = stmt.where(visibility)
            if status_id is not None:
                stmt = stmt.where(Issue.status_id == status_id)
            if state_category is not None:
                stmt = stmt.where(Issue.state_category == state_category)
            if priority is not None:
                stmt = stmt.where(Issue.priority == priority)
            if assignee_id is not None:
                stmt = stmt.where(Issue.assignee_id == assignee_id)
            if reporter_id is not None:
                stmt = stmt.where(Issue.reporter_id == reporter_id)
            if project_id is not None:
                stmt = stmt.where(Issue.project_id == project_id)
            if cycle_id is not None:
                stmt = stmt.where(Issue.cycle_id == cycle_id)
            if milestone_id is not None:
                stmt = stmt.where(Issue.milestone_id == milestone_id)
            if parent_id is not None:
                stmt = stmt.where(Issue.parent_id == parent_id)
            if due_before is not None:
                stmt = stmt.where(Issue.due_date <= due_before)
            if due_after is not None:
                stmt = stmt.where(Issue.due_date >= due_after)
            if q is not None:
                pattern = f"%{escape_like(q.strip())}%"
                stmt = stmt.where(
                    or_(
                        Issue.title.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                        Issue.identifier.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                    )
                )
            if filters is not None:
                compiled = compile_filter_tree(
                    filters,
                    {
                        "state_category": Issue.state_category,
                        "priority": Issue.priority,
                        "title": Issue.title,
                        "identifier": Issue.identifier,
                        "due_date": Issue.due_date,
                        "start_date": Issue.start_date,
                        "position": Issue.position,
                        "created_at": Issue.created_at,
                        "estimate": Issue.estimate,
                        "parent_id": Issue.parent_id,
                    },
                    value_coercers={"due_date": coerce_date, "start_date": coerce_date},
                )
                stmt = stmt.where(compiled)

            try:
                return await self._run_list(
                    session,
                    stmt=stmt,
                    workspace_id=workspace_id,
                    sort=sort,
                    order=order,
                    group_by=group_by,
                    page_limit=page_limit,
                    cursor=cursor,
                )
            except DBAPIError as exc:
                # statement_timeout overrun → QueryCanceled surfaces as a
                # DBAPIError wrapping asyncpg QueryCanceledError (README §6.14).
                orig = getattr(exc, "orig", None)
                if "canceling statement due to statement timeout" in str(orig):
                    raise BusinessRuleError(
                        "query exceeded the cost budget; narrow the conditions",
                        code="query_cost_exceeded",
                    ) from exc
                raise

    def _sort_expression(self, sort: str):
        if sort == "priority":
            return self._priority_rank_case()
        return getattr(Issue, sort)

    @staticmethod
    def _priority_rank_case():
        from sqlalchemy import case

        return case(
            (Issue.priority == "urgent", 0),
            (Issue.priority == "high", 1),
            (Issue.priority == "medium", 2),
            (Issue.priority == "low", 3),
            else_=4,
        )

    async def _run_list(
        self,
        session: AsyncSession,
        *,
        stmt,
        workspace_id: uuid.UUID,
        sort: str,
        order: str,
        group_by: str | None,
        page_limit: int,
        cursor: str | None,
    ) -> dict:
        descending = order == "desc"
        if sort == "priority":
            rank = self._priority_rank_case()
            sort_column = rank
            if descending:
                # urgent-first = rank ASC; keyset "after" is row > cursor.
                ordered = stmt.order_by(rank.asc(), Issue.id.asc())
                cursor_after = True
            else:
                # none-first = rank DESC; keyset "after" is row < cursor.
                ordered = stmt.order_by(rank.desc(), Issue.id.desc())
                cursor_after = False
            sort_value_of = lambda row: _PRIORITY_RANK.get(row.priority, 4)  # noqa: E731
        elif sort == "due_date":
            # NULL-safe keyset (B3): sentinel pushes NULL due dates last in
            # BOTH directions; cursor encodes the sentinel for NULL rows.
            sentinel = date(9999, 12, 31) if not descending else date(1, 1, 1)
            sort_column = func.coalesce(Issue.due_date, sentinel)
            ordered = stmt.order_by(
                sort_column.desc() if descending else sort_column.asc(),
                Issue.id.desc() if descending else Issue.id.asc(),
            )
            cursor_after = not descending
            sort_value_of = (  # noqa: E731
                lambda row: row.due_date if row.due_date is not None else sentinel
            )
        else:
            sort_column = getattr(Issue, sort)
            ordered = stmt.order_by(
                sort_column.desc() if descending else sort_column.asc(),
                Issue.id.desc() if descending else Issue.id.asc(),
            )
            cursor_after = not descending
            sort_value_of = lambda row: getattr(row, sort)  # noqa: E731
        if cursor is not None:
            position = decode_cursor(cursor)
            if cursor_after:
                ordered = ordered.where(
                    func.row(sort_column, Issue.id)
                    > func.row(position.sort_value, position.id)
                )
            else:
                ordered = ordered.where(
                    func.row(sort_column, Issue.id)
                    < func.row(position.sort_value, position.id)
                )
        rows = list((await session.execute(ordered.limit(page_limit + 1))).scalars().all())
        next_cursor = None
        if len(rows) > page_limit:
            rows = rows[:page_limit]
            last = rows[-1]
            next_cursor = encode_cursor(sort_value_of(last), last.id)
        labels_by_issue = await self._labels_for_issues(
            session,
            workspace_id=workspace_id,
            issue_ids=[row.id for row in rows],
        )
        rendered = [
            await self.render_issue(session, row, labels=labels_by_issue[row.id])
            for row in rows
        ]
        if group_by is None:
            return {"data": rendered, "next_cursor": next_cursor}
        return await self._group_response(
            session,
            base_stmt=stmt,
            rendered=rendered,
            rows=rows,
            group_by=group_by,
            workspace_id=workspace_id,
            next_cursor=next_cursor,
        )

    async def _group_response(
        self,
        session: AsyncSession,
        *,
        base_stmt,
        rendered: list[dict],
        rows: list[Issue],
        group_by: str,
        workspace_id: uuid.UUID,
        next_cursor: str | None,
    ) -> dict:
        """Overall-cursor contract (README §6.14): one next_cursor for all
        groups; ``count`` is the FULL per-group total while ``data`` is the
        current page slice; per-group cursors are forbidden.
        """
        if group_by == "label":
            return await self._group_by_label_response(
                session,
                base_stmt=base_stmt,
                rendered=rendered,
                workspace_id=workspace_id,
                next_cursor=next_cursor,
            )

        by_id = {item["id"]: item for item in rendered}
        group_keys: list[str] = []
        page_membership: dict[str, list[dict]] = {}
        for row in rows:
            key = await self._group_key_for(session, row, group_by)
            if key not in page_membership:
                page_membership[key] = []
                group_keys.append(key)
            page_membership[key].append(by_id[str(row.id)])

        # Full per-group totals over the WHOLE filtered set (not just the
        # page slice) — count is the group total, data is the page slice
        # (README §6.14 overall-cursor contract).
        group_column = {
            "state_category": Issue.state_category,
            "priority": Issue.priority,
            "assignee": Issue.assignee_id,
            "project": Issue.project_id,
            "cycle": Issue.cycle_id,
        }[group_by]
        counts: dict[str, int] = {}
        count_rows = (
            await session.execute(
                base_stmt.with_only_columns(group_column, func.count()).group_by(group_column)
            )
        ).all()
        empty_key = {"assignee": "unassigned", "project": "no_project", "cycle": "no_cycle"}.get(
            group_by
        )
        for key, count in count_rows:
            counts[str(key) if key is not None else empty_key] = int(count)

        groups = []
        for key in group_keys:
            label = await self._group_label(session, key, group_by, workspace_id)
            groups.append(
                {
                    "key": key,
                    "label": label,
                    "count": counts.get(key, len(page_membership.get(key, []))),
                    "data": page_membership.get(key, []),
                }
            )
        return {"groups": groups, "next_cursor": next_cursor}

    async def _group_by_label_response(
        self,
        session: AsyncSession,
        *,
        base_stmt,
        rendered: list[dict],
        workspace_id: uuid.UUID,
        next_cursor: str | None,
    ) -> dict:
        """Project each issue into every label group it belongs to.

        Label is the generic issue list's only multi-valued grouping axis, so
        a card may appear in multiple groups. Counts cover the complete
        filtered issue set while each group's data contains only the current
        overall-cursor page. Unlabelled issues use the canonical ``__none__``
        bucket, which is always ordered last.
        """
        filtered_issue_ids = base_stmt.with_only_columns(Issue.id).subquery(
            "filtered_issue_ids"
        )
        label_count_rows = (
            await session.execute(
                select(IssueLabel.label_id, Label.name, func.count(IssueLabel.issue_id))
                .select_from(IssueLabel)
                .join(
                    filtered_issue_ids,
                    filtered_issue_ids.c.id == IssueLabel.issue_id,
                )
                .join(
                    Label,
                    and_(
                        Label.workspace_id == IssueLabel.workspace_id,
                        Label.id == IssueLabel.label_id,
                    ),
                )
                .where(IssueLabel.workspace_id == workspace_id)
                .group_by(IssueLabel.label_id, Label.name)
            )
        ).all()
        label_count_rows.sort(key=lambda row: (row.name.casefold(), str(row.label_id)))

        has_label = (
            select(IssueLabel.issue_id)
            .where(
                IssueLabel.workspace_id == workspace_id,
                IssueLabel.issue_id == filtered_issue_ids.c.id,
            )
            .correlate(filtered_issue_ids)
            .exists()
        )
        empty_count = int(
            await session.scalar(
                select(func.count()).select_from(filtered_issue_ids).where(~has_label)
            )
            or 0
        )

        page_membership: dict[str, list[dict]] = {}
        for item in rendered:
            keys = [label["id"] for label in item.get("labels", [])] or ["__none__"]
            for key in keys:
                page_membership.setdefault(key, []).append(item)

        groups = [
            {
                "key": str(row.label_id),
                "label": row.name,
                "count": int(row[2]),
                "data": page_membership.get(str(row.label_id), []),
            }
            for row in label_count_rows
        ]
        if empty_count:
            groups.append(
                {
                    "key": "__none__",
                    "label": "No label",
                    "count": empty_count,
                    "data": page_membership.get("__none__", []),
                }
            )
        return {"groups": groups, "next_cursor": next_cursor}

    async def _group_key_for(self, session: AsyncSession, row: Issue, group_by: str) -> str:
        if group_by == "assignee":
            return str(row.assignee_id) if row.assignee_id is not None else "unassigned"
        if group_by == "project":
            return str(row.project_id) if row.project_id is not None else "no_project"
        if group_by == "cycle":
            return str(row.cycle_id) if row.cycle_id is not None else "no_cycle"
        return str(getattr(row, group_by))

    async def _group_label(
        self, session: AsyncSession, key: str, group_by: str, workspace_id: uuid.UUID
    ) -> str:
        if group_by == "assignee" and key != "unassigned":
            summary = await self._member_summary(
                session, workspace_id=workspace_id, member_id=uuid.UUID(key)
            )
            return summary["name"] if summary is not None else key
        if group_by == "project" and key != "no_project":
            name = await session.scalar(
                select(Project.name).where(
                    Project.id == uuid.UUID(key), Project.workspace_id == workspace_id
                )
            )
            return name or key
        if group_by == "cycle" and key != "no_cycle":
            name = await session.scalar(
                select(Cycle.name).where(
                    Cycle.id == uuid.UUID(key), Cycle.workspace_id == workspace_id
                )
            )
            return name or key
        return key.replace("_", " ").title()

    # ------------------------------------------------------------------
    # update (PATCH, §3.4 / §6.9)
    # ------------------------------------------------------------------

    async def update_issue(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        patch: IssuePatch,
        expected_version: int | None = None,
        if_match: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            # Always row-lock: closes the non-OCC lost-update window where two
            # bare PATCHes race version+1 (必修3); OCC checks still apply on top.
            issue = await self._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self.assert_can_write_issue(session, actor=actor, issue=issue)
            if expected_version is not None and issue.version != expected_version:
                raise ConflictError(
                    "issue was modified concurrently",
                    code="conflict",
                    details={"id": str(issue.id), "current_version": issue.version},
                )
            if if_match is not None and not self._matches_updated_at(issue, if_match):
                raise ConflictError(
                    "issue was modified concurrently",
                    code="conflict",
                    details={"id": str(issue.id)},
                )
            updated, changes = await self._apply_patch_tx(
                session, actor=actor, issue=issue, patch=patch
            )
            if not changes:
                # §6.9: empty diff → no event, no trail, no trigger.
                return await self.render_issue(session, updated)
            rendered = await self.render_issue(session, updated)
            # F5: include rendered snapshots so realtime consumers can update
            # display names without a refetch (assignee/status objects).
            if "assignee_id" in changes:
                changes["assignee"] = rendered["assignee"]
            if "status_id" in changes and rendered["status"] is not None:
                changes["status"] = {
                    **rendered["status"],
                    "created_at": _isoformat(rendered["status"]["created_at"]),
                    "updated_at": _isoformat(rendered["status"]["updated_at"]),
                }
            project = await self._project_of(session, updated)
            payload = {
                "id": str(updated.id),
                "changes": {k: v for k, v in changes.items() if not k.startswith("_")},
                "version": updated.version,
                "visibility": {
                    "project_id": str(updated.project_id)
                    if updated.project_id is not None
                    else None,
                    "state_category": updated.state_category,
                },
                "updated_at": _isoformat(updated.updated_at),
            }
            realtime_event = await self._emit_issue_event(
                session, issue=updated, event="issue.updated", data=payload, project=project
            )
            if "state_category" in changes or "status_id" in changes:
                moved_payload = {
                    "id": str(updated.id),
                    "from": {"state_category": changes.get("_prev_category")},
                    "to": {"state_category": updated.state_category},
                }
                await self._emit_issue_event(
                    session,
                    issue=updated,
                    event="issue.moved",
                    data=moved_payload,
                    project=project,
                )
            if "assignee_id" in changes:
                previous = changes.get("_prev_assignee")
                await apply_assign_triggers(
                    session,
                    workspace_id=workspace_id,
                    issue=updated,
                    previous_assignee_id=uuid.UUID(previous) if previous else None,
                    trigger_event_id=realtime_event.id,
                )
                # squad.md §2.5 (issue_reassigned): if the issue moved away from
                # its active squad leader, cancel that squad assignment in the
                # SAME transaction. Runs in-process (not via outbox) so the
                # leader-change path's same-txn updates stay consistent.
                if self._squad_assignee_watcher is not None:
                    await self._squad_assignee_watcher(
                        session,
                        workspace_id=workspace_id,
                        issue_id=updated.id,
                        previous_assignee_id=uuid.UUID(previous) if previous else None,
                        new_assignee_id=updated.assignee_id,
                    )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="issue.updated",
                resource_id=updated.id,
                metadata={"changes": sorted(k for k in changes if not k.startswith("_"))},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            # §5.3 I1/I3/I4: register notification fan-outs for assign / status /
            # field changes in the SAME transaction (comment-inbox is the single
            # notification authority — this call only picks the type(s) and the
            # explicit recipients its matrix rows need; routing/de-noise/quiet
            # hours live in the relay-side fan-out handler). The actor is
            # self-suppressed there; the reporter/creator receives via their
            # seeded subscription (I4).
            field_changed = any(
                not key.startswith("_")
                and key not in ("assignee_id", "status_id", "state_category", "assignee", "status")
                for key in changes
            )
            await emit_issue_change_notifications(
                session,
                workspace_id=workspace_id,
                issue=updated,
                actor=actor,
                actor_name=await self._actor_display_name(session, actor),
                actor_member_type=actor.member_type,
                assigned_to=updated.assignee_id if "assignee_id" in changes else None,
                status_changed=("status_id" in changes or "state_category" in changes),
                subscribed_update=field_changed,
            )
            return rendered

    async def _actor_display_name(self, session: AsyncSession, actor: Member) -> str:
        """Display name for notification payloads (member.md §2.4 resolution)."""
        actor_user = None
        if actor.user_id is not None:
            actor_user = await session.scalar(select(User).where(User.id == actor.user_id))
        return resolve_display_name(member=actor, user=actor_user)

    @staticmethod
    def _matches_updated_at(issue: Issue, if_match: str) -> bool:
        candidate = if_match.strip().strip('"')
        if candidate == _isoformat(issue.updated_at):
            return True
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed == issue.updated_at

    async def _assert_transition_allowed(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        current_status_id: uuid.UUID,
        target_status,
    ) -> None:
        """Strict-mode transition gate (issue.md §3.4/§4.4/§5.2, migration 0010).

        The workspace setting ``status_strict_mode`` (default false) enables
        strict mode; the per-status ``allowed_transitions`` list (JSONB array
        of target status ids) then defines the ONLY legal next steps — an
        empty list allows no transition at all. Violations raise 409
        ``invalid_status_transition`` with from/to/allowed details.
        """
        workspace = await session.scalar(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        settings = (workspace.settings if workspace is not None else None) or {}
        if not bool(settings.get("status_strict_mode", False)):
            return
        current = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.id == current_status_id,
                IssueStatus.workspace_id == workspace_id,
            )
        )
        allowed = [str(t) for t in (current.allowed_transitions or [])] if current else []
        if str(target_status.id) not in allowed:
            raise ConflictError(
                "status transition not allowed under strict mode",
                code="invalid_status_transition",
                details={
                    "from": str(current_status_id),
                    "to": str(target_status.id),
                    "allowed": allowed,
                },
            )

    async def _apply_patch_tx(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        issue: Issue,
        patch: IssuePatch,
        validate_required_fields: bool = True,
    ) -> tuple[Issue, dict]:
        """Apply a tri-state patch; returns (issue, changes-diff).

        ``changes`` keys are the user-visible fields (new values); ``_prev_*``
        keys carry the pre-image the event/trigger layer needs (never
        rendered). The activity trail stores old/new per changed field.
        """
        changes: dict = {}
        trail: list[tuple[str, object, object]] = []

        def record(field: str, old_value: object, new_value: object) -> None:
            changes[field] = _json_value(new_value)
            if field not in _SILENT_ACTIVITY_FIELDS:
                trail.append((field, _json_value(old_value), _json_value(new_value)))

        if not isinstance(patch.title, _Unset) and patch.title != issue.title:
            old = issue.title
            issue.title = patch.title
            record("title", old, patch.title)
        if not isinstance(patch.description, _Unset) and patch.description != issue.description:
            old = issue.description
            issue.description = patch.description
            record("description", old, patch.description)
        if not isinstance(patch.status_id, _Unset) and patch.status_id != issue.status_id:
            status = await resolve_status_in_scope(
                session,
                workspace_id=issue.workspace_id,
                project_id=issue.project_id,
                status_id=patch.status_id,
            )
            # 严格模式状态流转校验(issue.md §3.4/§4.4/§5.2,README §6.14):
            # 工作区设置 status_strict_mode 开启时,仅允许当前状态
            # allowed_transitions 列出的目标;违规 409 invalid_status_transition。
            # 默认模式自由流转。系统驱动的迁移映射(move.py)不受此约束。
            await self._assert_transition_allowed(
                session,
                workspace_id=issue.workspace_id,
                current_status_id=issue.status_id,
                target_status=status,
            )
            changes["_prev_category"] = issue.state_category
            old_status, old_category = issue.status_id, issue.state_category
            issue.status_id = status.id
            issue.state_category = status.category
            if status.category == "done" and issue.completed_at is None:
                issue.completed_at = _now(self._clock)
            elif status.category != "done" and issue.completed_at is not None:
                issue.completed_at = None
            record("status_id", old_status, status.id)
            record("state_category", old_category, status.category)
        if not isinstance(patch.priority, _Unset) and patch.priority != issue.priority:
            if patch.priority not in ISSUE_PRIORITY_VALUES:
                raise ValidationError("invalid priority", details={"priority": patch.priority})
            old = issue.priority
            issue.priority = patch.priority
            record("priority", old, patch.priority)
        if not isinstance(patch.assignee_id, _Unset) and patch.assignee_id != issue.assignee_id:
            if patch.assignee_id is not None:
                await self._validate_member_ref(
                    session,
                    workspace_id=issue.workspace_id,
                    member_id=patch.assignee_id,
                    field="assignee_id",
                )
            changes["_prev_assignee"] = (
                str(issue.assignee_id) if issue.assignee_id is not None else None
            )
            old = issue.assignee_id
            issue.assignee_id = patch.assignee_id
            record("assignee_id", old, patch.assignee_id)
        if not isinstance(patch.reporter_id, _Unset) and patch.reporter_id != issue.reporter_id:
            if patch.reporter_id is not None:
                await self._validate_member_ref(
                    session,
                    workspace_id=issue.workspace_id,
                    member_id=patch.reporter_id,
                    field="reporter_id",
                )
            old = issue.reporter_id
            issue.reporter_id = patch.reporter_id
            record("reporter_id", old, patch.reporter_id)
        if not isinstance(patch.estimate, _Unset) and patch.estimate != issue.estimate:
            old = issue.estimate
            issue.estimate = patch.estimate  # type: ignore[assignment]
            record("estimate", old, patch.estimate)
        if not isinstance(patch.estimate_unit, _Unset) and (
            patch.estimate_unit != issue.estimate_unit
        ):
            if patch.estimate_unit is not None and patch.estimate_unit not in ("points", "hours"):
                raise ValidationError(
                    "invalid estimate_unit", details={"estimate_unit": patch.estimate_unit}
                )
            old = issue.estimate_unit
            issue.estimate_unit = patch.estimate_unit
            record("estimate_unit", old, patch.estimate_unit)
        if not isinstance(patch.due_date, _Unset) and patch.due_date != issue.due_date:
            next_start = (
                patch.start_date if not isinstance(patch.start_date, _Unset) else issue.start_date
            )
            if patch.due_date is not None and next_start is not None and patch.due_date < next_start:
                raise ValidationError("due_date must not be earlier than start_date")
            old = issue.due_date
            issue.due_date = patch.due_date
            record("due_date", old, patch.due_date)
        if not isinstance(patch.start_date, _Unset) and patch.start_date != issue.start_date:
            next_due = (
                patch.due_date if not isinstance(patch.due_date, _Unset) else issue.due_date
            )
            if next_due is not None and patch.start_date is not None and next_due < patch.start_date:
                raise ValidationError("due_date must not be earlier than start_date")
            old = issue.start_date
            issue.start_date = patch.start_date
            record("start_date", old, patch.start_date)
        if not isinstance(patch.milestone_id, _Unset) and patch.milestone_id != issue.milestone_id:
            if patch.milestone_id is not None:
                await self._validate_milestone(
                    session,
                    workspace_id=issue.workspace_id,
                    project_id=issue.project_id,
                    milestone_id=patch.milestone_id,
                )
            old = issue.milestone_id
            issue.milestone_id = patch.milestone_id
            record("milestone_id", old, patch.milestone_id)
        if not isinstance(patch.cycle_id, _Unset) and patch.cycle_id != issue.cycle_id:
            if patch.cycle_id is not None:
                await self._validate_cycle(
                    session, workspace_id=issue.workspace_id, cycle_id=patch.cycle_id
                )
            old = issue.cycle_id
            issue.cycle_id = patch.cycle_id
            record("cycle_id", old, patch.cycle_id)
        if not isinstance(patch.parent_id, _Unset) and patch.parent_id != issue.parent_id:
            if patch.parent_id is not None:
                parent = await self._load_issue(
                    session, workspace_id=issue.workspace_id, issue_id=patch.parent_id
                )
                await self.assert_can_view_issue(session, viewer=actor, issue=parent)
                # Serialize graph changes, THEN walk ancestors (issue.md §2.5
                # rule 3): lock-before-check closes the concurrent-cycle
                # window (README §9 T12).
                await lock_issue_graph(session, issue.workspace_id)
                path = await detect_parent_cycle(
                    session,
                    workspace_id=issue.workspace_id,
                    issue_id=issue.id,
                    new_parent_id=patch.parent_id,
                )
                if path is not None:
                    raise ConflictError(
                        "parent change would create a cycle",
                        code="circular_parent",
                        details={"path": path},
                    )
            old = issue.parent_id
            issue.parent_id = patch.parent_id
            record("parent_id", old, patch.parent_id)
        if not isinstance(patch.position, _Unset) and patch.position != issue.position:
            old = issue.position
            issue.position = patch.position
            record("position", old, patch.position)

        if not changes:
            return issue, {}

        # label-property.md §4.5 required-field gate: issue.md calls the
        # label-property module's hook BEFORE the save/transition completes —
        # "save" on every non-empty PATCH, plus "status:<category>" when the
        # status moved. Raises 422 required_field_missing and aborts in place
        # (validation, not notification). System-driven remaps (move.py) do
        # not pass through _apply_patch_tx and stay exempt, mirroring the
        # strict-mode transition gate above.
        occasions = {"save"}
        if "status_id" in changes and issue.state_category is not None:
            occasions.add(f"status:{issue.state_category}")
        if validate_required_fields:
            await validate_required_field_values(session, issue=issue, occasions=occasions)

        # Optimistic version bump + activity trail (one row per field).
        issue.version = issue.version + 1
        issue.updated_at = _now(self._clock)
        for field, old_value, new_value in trail:
            session.add(
                IssueActivity(
                    workspace_id=issue.workspace_id,
                    issue_id=issue.id,
                    actor_member_id=actor.id,
                    field=field,
                    old_value=old_value,
                    new_value=new_value,
                )
            )
        await session.flush()
        return issue, changes

    # ------------------------------------------------------------------
    # delete / children / activity
    # ------------------------------------------------------------------

    async def delete_issue(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            issue = await self._load_issue(session, workspace_id=workspace_id, issue_id=issue_id)
            await self.assert_can_write_issue(session, actor=actor, issue=issue)
            project = await self._project_of(session, issue)
            # Soft delete: the identifier stays permanently reserved — the
            # row is a tombstone, counters never roll back (README §6.3).
            issue.deleted_at = _now(self._clock)
            issue.updated_at = issue.deleted_at
            await session.flush()
            await self._emit_issue_event(
                session,
                issue=issue,
                event="issue.deleted",
                data={"id": str(issue.id)},
                project=project,
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="issue.deleted",
                resource_id=issue.id,
                metadata={"identifier": issue.identifier},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return {"id": str(issue.id), "deleted": True}

    async def list_children(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            parent = await self._load_issue(session, workspace_id=workspace_id, issue_id=issue_id)
            await self.assert_can_view_issue(session, viewer=viewer, issue=parent)
            stmt = (
                select(Issue)
                .where(
                    Issue.workspace_id == workspace_id,
                    Issue.parent_id == parent.id,
                    Issue.deleted_at.is_(None),
                )
                .order_by(Issue.created_at.asc(), Issue.id.asc())
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(Issue.created_at, Issue.id)
                    > func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            rendered = [await self.render_issue(session, row) for row in rows]
            return rendered, next_cursor

    async def list_activity(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            issue = await self._load_issue(session, workspace_id=workspace_id, issue_id=issue_id)
            await self.assert_can_view_issue(session, viewer=viewer, issue=issue)
            stmt = (
                select(IssueActivity)
                .where(
                    IssueActivity.workspace_id == workspace_id,
                    IssueActivity.issue_id == issue.id,
                )
                .order_by(IssueActivity.created_at.desc(), IssueActivity.id.desc())
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(IssueActivity.created_at, IssueActivity.id)
                    < func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            rendered = []
            for row in rows:
                actor = await self._member_summary(
                    session, workspace_id=workspace_id, member_id=row.actor_member_id
                )
                rendered.append(
                    {
                        "id": str(row.id),
                        "issue_id": str(row.issue_id),
                        "actor": actor,
                        "field": row.field,
                        "old_value": row.old_value,
                        "new_value": row.new_value,
                        "created_at": row.created_at,
                    }
                )
            return rendered, next_cursor

    # ------------------------------------------------------------------
    # shared write helper for bulk/move flows
    # ------------------------------------------------------------------

    async def apply_changes_in_tx(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        issue: Issue,
        patch: IssuePatch,
        validate_required_fields: bool = True,
    ) -> tuple[dict, dict]:
        """Apply a patch inside the caller's transaction (bulk/templates).

        Emits the same events/trail as the route-level update; returns
        (rendered, changes).
        """
        updated, changes = await self._apply_patch_tx(
            session,
            actor=actor,
            issue=issue,
            patch=patch,
            validate_required_fields=validate_required_fields,
        )
        if not changes:
            return await self.render_issue(session, updated), {}
        rendered = await self.render_issue(session, updated)
        project = await self._project_of(session, updated)
        payload = {
            "id": str(updated.id),
            "changes": {k: v for k, v in changes.items() if not k.startswith("_")},
            "version": updated.version,
            "updated_at": _isoformat(updated.updated_at),
        }
        await self._emit_issue_event(
            session, issue=updated, event="issue.updated", data=payload, project=project
        )
        return rendered, changes


def _jsonify_issue(rendered: dict) -> dict:
    """Make a rendered issue JSON-safe for outbox payloads (no datetimes)."""
    out = dict(rendered)
    for key in ("created_at", "updated_at", "completed_at", "due_date", "start_date"):
        if key in out and not isinstance(out[key], (str, type(None))):
            out[key] = _isoformat(out[key])
    status = out.get("status")
    if isinstance(status, dict):
        out["status"] = {
            **status,
            "created_at": _isoformat(status.get("created_at")),
            "updated_at": _isoformat(status.get("updated_at")),
        }
    out.pop("children_progress", None)
    out.pop("skipped_fields", None)
    return out


__all__ = [
    "ISSUE_NOT_FOUND",
    "IssuePatch",
    "IssueService",
    "UNSET",
]
