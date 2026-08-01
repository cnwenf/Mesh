"""Project service — projects, health trail, milestones, cycles, templates.

Each public method owns its transaction (``session_factory() + begin()``) so
it can be exercised directly from tests without route plumbing. Tenant-bound
transactions set the ``mesh.workspace_id`` GUC up front so every read/write is
correct under RLS (restricted app role) and without it (owner role). Shared
steps are factored into ``_*_tx`` helpers that run inside the caller's
transaction — template instantiation (project.md §3.2b) composes them into a
single atomic transaction.

Contract anchors (project.md + README §6):
- prefix permanent reservation (README §6.3): the key is registered in the
  workspace-level ``identifier_prefix_registry`` in the SAME transaction as
  the project row; conflict with any registered prefix (project / inbox /
  retired) → 409 ``project_key_taken``;
- archived projects are read-only: every write returns 422 ``project_archived``;
- realtime events go through the outbox single write path (§6.6/§6.7) —
  private project events ONLY hit the ``project:{id}`` channel, public ones
  additionally hit ``workspace:{ws}:projects``;
- no-change PATCH is a no-op (§6.9: empty diff emits nothing).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import decode_cursor, encode_cursor
from mesh.auth.audit import write_audit
from mesh.auth.rbac import role_satisfies
from mesh.db.constraints import violates as _violates
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import (
    CYCLE_STATE_VALUES,
    MILESTONE_STATE_VALUES,
    PROJECT_HEALTH_VALUES,
    PROJECT_MEMBER_ROLE_VALUES,
    PROJECT_STATUS_VALUES,
    PROJECT_VISIBILITY_VALUES,
    Cycle,
    Milestone,
    Project,
    ProjectMember,
    ProjectTemplate,
    ProjectUpdate,
)
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.member.display import resolve_display_name
from mesh.outbox.service import emit_realtime
from mesh.project.schemas import (
    AddProjectMemberRequest,
    AddProjectUpdateRequest,
    CreateCycleRequest,
    CreateMilestoneRequest,
    CreateProjectRequest,
    CreateProjectTemplateRequest,
    InstantiateProjectTemplateRequest,
    UpdateProjectMemberRequest,
    UpdateProjectTemplateRequest,
)
from mesh.workspace.service import occupy_project_prefix

PROJECT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,11}$")
PROJECT_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
PROJECT_CHANNEL = "project:{project_id}"
WORKSPACE_PROJECTS_CHANNEL = "workspace:{workspace_id}:projects"

NAME_MAX_LENGTH = 120
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100

_PROJECT_NOT_FOUND = "project not found"
_MILESTONE_NOT_FOUND = "milestone not found"
_CYCLE_NOT_FOUND = "cycle not found"
_TEMPLATE_NOT_FOUND = "project template not found"
_GUEST_WRITE_FORBIDDEN = "guests cannot perform this action"


class _Unset:
    """Sentinel distinguishing an omitted PATCH field from an explicit null."""

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return "<UNSET>"


UNSET = _Unset()


@dataclass(frozen=True)
class ProjectPatch:
    """Tri-state PATCH payload resolved from ``model_fields_set`` in the route."""

    name: str | _Unset | None = UNSET
    description: str | _Unset | None = UNSET
    icon: str | _Unset | None = UNSET
    color: str | _Unset | None = UNSET
    status: str | _Unset | None = UNSET
    health: str | _Unset | None = UNSET
    visibility: str | _Unset | None = UNSET
    lead_member_id: uuid.UUID | _Unset | None = UNSET
    start_date: date | _Unset | None = UNSET
    target_date: date | _Unset | None = UNSET


@dataclass(frozen=True)
class MilestonePatch:
    title: str | _Unset | None = UNSET
    description: str | _Unset | None = UNSET
    target_date: date | _Unset | None = UNSET
    state: str | _Unset | None = UNSET


@dataclass(frozen=True)
class CyclePatch:
    name: str | _Unset | None = UNSET
    starts_at: date | _Unset | None = UNSET
    ends_at: date | _Unset | None = UNSET
    state: str | _Unset | None = UNSET
    auto_roll: bool | _Unset | None = UNSET


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _today(clock: Callable[[], datetime] | None) -> date:
    return _now(clock).date()


def _isoformat(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _validate_project_key(key: str) -> None:
    if not isinstance(key, str) or not PROJECT_KEY_PATTERN.match(key):
        raise ValidationError(
            "key must match ^[A-Z][A-Z0-9_]{1,11}$",
            details={"key": key[:32] if isinstance(key, str) else None},
        )


def _validate_name(name: str, *, field: str = "name") -> None:
    if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX_LENGTH:
        raise ValidationError(f"{field} must be 1-{NAME_MAX_LENGTH} characters")


def _normalize_project_color(color: str | None) -> str | None:
    if color is None:
        return None
    if not isinstance(color, str) or PROJECT_COLOR_PATTERN.fullmatch(color) is None:
        raise ValidationError("color must be a #RRGGBB hexadecimal value")
    return color.upper()


def _validate_date_range(start: date | None, target: date | None) -> None:
    if start is not None and target is not None and target < start:
        raise ValidationError(
            "target_date must not be earlier than start_date",
            details={"start_date": start.isoformat(), "target_date": target.isoformat()},
        )


def _validate_cycle_range(starts_at: date, ends_at: date) -> None:
    if ends_at < starts_at:
        raise ValidationError(
            "ends_at must not be earlier than starts_at",
            details={"starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()},
        )


def _validate_enum(value: str, allowed: tuple[str, ...], *, field: str) -> None:
    if value not in allowed:
        raise ValidationError(f"invalid {field}", details={field: value})


def _project_channel(project_id: uuid.UUID) -> str:
    return PROJECT_CHANNEL.format(project_id=project_id)


def _workspace_projects_channel(workspace_id: uuid.UUID) -> str:
    return WORKSPACE_PROJECTS_CHANNEL.format(workspace_id=workspace_id)


def _limit_page(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit < 1:
        raise ValidationError("limit must be >= 1", code="invalid_limit")
    return min(limit, MAX_PAGE_LIMIT)


class ProjectService:
    """Stateless orchestrator over the project tables (project.md §3)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    async def _member_summary(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, member_id: uuid.UUID
    ) -> dict | None:
        member = await session.scalar(
            select(Member).where(
                Member.id == member_id,
                Member.workspace_id == workspace_id,
            )
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

    def _progress_payload(self, project: Project) -> dict:
        """Progress fields for responses (project.md §2.4).

        Live aggregation joins ``issues.state_category`` — the issue.md
        increment provides that table. Until then the materialized
        ``progress_cache`` wins and the aggregation falls back to zeros
        (an empty issue set yields progress 0.0, open 0, done 0).
        """
        progress = project.progress_cache if project.progress_cache is not None else 0.0
        return {"progress": progress, "open_issues": 0, "done_issues": 0}

    def render_milestone(self, milestone: Milestone) -> dict:
        overdue = (
            milestone.state == "open"
            and milestone.target_date is not None
            and milestone.target_date < _today(self._clock)
        )
        return {
            "id": str(milestone.id),
            "project_id": str(milestone.project_id),
            "title": milestone.title,
            "description": milestone.description,
            "target_date": _isoformat(milestone.target_date),
            "state": milestone.state,
            "overdue": overdue,
            "created_at": milestone.created_at,
            "updated_at": milestone.updated_at,
        }

    def render_cycle(self, cycle: Cycle) -> dict:
        return {
            "id": str(cycle.id),
            "project_id": str(cycle.project_id) if cycle.project_id is not None else None,
            "name": cycle.name,
            "starts_at": _isoformat(cycle.starts_at),
            "ends_at": _isoformat(cycle.ends_at),
            "state": cycle.state,
            "auto_roll": cycle.auto_roll,
            "created_at": cycle.created_at,
            "updated_at": cycle.updated_at,
        }

    def render_update(self, update: ProjectUpdate, author: dict | None) -> dict:
        return {
            "id": str(update.id),
            "project_id": str(update.project_id),
            "author": author,
            "health": update.health,
            "status": update.status,
            "message": update.message,
            "created_at": update.created_at,
        }

    def _render_project_summary(self, project: Project, lead: dict | None = None) -> dict:
        """JSON-safe summary used in realtime payloads (no raw datetimes)."""
        progress = self._progress_payload(project)
        return {
            "id": str(project.id),
            "name": project.name,
            "key": project.key,
            "status": project.status,
            "health": project.health,
            "visibility": project.visibility,
            "lead": lead,
            "progress": progress["progress"],
            "target_date": _isoformat(project.target_date),
            "archived": project.archived_at is not None,
            "updated_at": _isoformat(project.updated_at),
        }

    def render_project(
        self,
        project: Project,
        *,
        lead: dict | None = None,
        milestones: list[Milestone] | None = None,
        my_role: str | None = None,
    ) -> dict:
        progress = self._progress_payload(project)
        payload = {
            "id": str(project.id),
            "workspace_id": str(project.workspace_id),
            "name": project.name,
            "key": project.key,
            "description": project.description,
            "icon": project.icon,
            "color": project.color,
            "status": project.status,
            "health": project.health,
            "visibility": project.visibility,
            "lead": lead,
            "lead_member_id": str(project.lead_member_id)
            if project.lead_member_id is not None
            else None,
            "start_date": _isoformat(project.start_date),
            "target_date": _isoformat(project.target_date),
            "progress": progress["progress"],
            "open_issues": progress["open_issues"],
            "done_issues": progress["done_issues"],
            "issue_seq": project.issue_seq,
            "archived": project.archived_at is not None,
            "archived_at": project.archived_at,
            "my_role": my_role,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        }
        if milestones is not None:
            payload["milestones"] = [self.render_milestone(milestone) for milestone in milestones]
        return payload

    def render_template(self, template: ProjectTemplate, creator: dict | None = None) -> dict:
        return {
            "id": str(template.id),
            "name": template.name,
            "template_body": template.template_body,
            "created_by": str(template.created_by),
            "creator": creator,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
        }

    # ------------------------------------------------------------------
    # loading + authorization (project.md §3.4)
    # ------------------------------------------------------------------

    async def _load_project(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        for_update: bool = False,
    ) -> Project:
        stmt = select(Project).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
            Project.deleted_at.is_(None),
        )
        if for_update:
            # Row lock for write paths: serialises the If-Match version check
            # against the UPDATE so two concurrent PATCHes carrying the same
            # valid If-Match cannot both pass and silently lose an update
            # (CWE-362). Under READ COMMITTED the lock is held until commit.
            stmt = stmt.with_for_update()
        project = await session.scalar(stmt)
        if project is None:
            raise NotFoundError(_PROJECT_NOT_FOUND)
        return project

    @staticmethod
    def _is_workspace_manager(viewer: Member) -> bool:
        return role_satisfies(viewer.role, "project:manage")

    async def _project_role(
        self, session: AsyncSession, *, project_id: uuid.UUID, member_id: uuid.UUID
    ) -> str | None:
        return await session.scalar(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id,
                ProjectMember.member_id == member_id,
            )
        )

    async def _guest_permission(
        self, session: AsyncSession, *, project_id: uuid.UUID, member_id: uuid.UUID
    ) -> str | None:
        return await session.scalar(
            select(MemberProjectAccess.permission).where(
                MemberProjectAccess.project_id == project_id,
                MemberProjectAccess.member_id == member_id,
            )
        )

    async def _is_lead(
        self, session: AsyncSession, *, viewer: Member, project: Project
    ) -> bool:
        if self._is_workspace_manager(viewer):
            return True
        if project.lead_member_id == viewer.id:
            return True
        return (
            await self._project_role(session, project_id=project.id, member_id=viewer.id)
        ) == "lead"

    async def assert_can_view(
        self, session: AsyncSession, *, viewer: Member, project: Project
    ) -> None:
        """Read gate: public for members; private needs membership or admin.

        Guests only ever see explicitly granted projects (member.md §2.3);
        invisible projects are 404 for guests and 403 for other members
        (project.md §3.3 error table).
        """
        if self._is_workspace_manager(viewer):
            return
        if viewer.role == "guest":
            permission = await self._guest_permission(
                session, project_id=project.id, member_id=viewer.id
            )
            if permission is None:
                raise NotFoundError(_PROJECT_NOT_FOUND)
            return
        if project.visibility == "public":
            return
        role = await self._project_role(session, project_id=project.id, member_id=viewer.id)
        if role is None:
            raise ForbiddenError("project is private")

    async def assert_can_write(
        self, session: AsyncSession, *, viewer: Member, project: Project
    ) -> None:
        """Write gate: project member/lead or workspace admin (project.md §3.4).

        Guests follow the view-gate convention (§3.3): a project without a
        grant is INVISIBLE → 404, so the write gate cannot double as a
        project-existence oracle; a visible (read-granted) project they
        cannot write is 403.
        """
        if project.archived_at is not None:
            raise BusinessRuleError("project is archived", code="project_archived")
        if self._is_workspace_manager(viewer):
            return
        if viewer.role == "guest":
            permission = await self._guest_permission(
                session, project_id=project.id, member_id=viewer.id
            )
            if permission == "write":
                return
            if permission is None:
                raise NotFoundError(_PROJECT_NOT_FOUND)
            raise ForbiddenError("not a project member")
        role = await self._project_role(session, project_id=project.id, member_id=viewer.id)
        if role in ("lead", "member"):
            return
        raise ForbiddenError("not a project member")

    async def assert_is_lead(
        self, session: AsyncSession, *, viewer: Member, project: Project
    ) -> None:
        """Lead gate: delete/archive/member-management (project.md §3.4)."""
        if project.archived_at is not None:
            raise BusinessRuleError("project is archived", code="project_archived")
        if not await self._is_lead(session, viewer=viewer, project=project):
            raise ForbiddenError("project lead or workspace admin required")

    async def _lead_member(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, lead_member_id: uuid.UUID
    ) -> Member:
        member = await session.scalar(
            select(Member).where(
                Member.id == lead_member_id,
                Member.workspace_id == workspace_id,
                Member.status == "active",
            )
        )
        if member is None:
            raise ValidationError(
                "lead is not an active member of this workspace",
                details={"lead_member_id": str(lead_member_id)},
            )
        return member

    # ------------------------------------------------------------------
    # realtime + audit helpers
    # ------------------------------------------------------------------

    async def _emit_project_event(
        self,
        session: AsyncSession,
        *,
        project: Project,
        event: str,
        data: dict,
    ) -> None:
        """Emit on the detail channel, plus the workspace list channel when public.

        Private project events ONLY hit ``project:{id}`` (project.md §3.5).
        """
        await emit_realtime(
            session,
            workspace_id=project.workspace_id,
            channel=_project_channel(project.id),
            event=event,
            data=data,
        )
        if project.visibility == "public":
            await emit_realtime(
                session,
                workspace_id=project.workspace_id,
                channel=_workspace_projects_channel(project.workspace_id),
                event=event,
                data=data,
            )

    async def _audit(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        actor: Member | None,
        action: str,
        resource_id: uuid.UUID,
        resource_type: str = "project",
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

    # ------------------------------------------------------------------
    # workspace resolution for workspace-less paths (project.md §3.1)
    # ------------------------------------------------------------------

    async def _resolve_workspace(self, function: str, entity_id: uuid.UUID) -> uuid.UUID | None:
        """Narrow SECURITY DEFINER lookup (migration 0006) — no tenant GUC yet."""
        async with self._factory() as session:
            return await session.scalar(text(f"SELECT {function}(:id)"), {"id": entity_id})

    async def resolve_project_workspace(self, project_id: uuid.UUID) -> uuid.UUID | None:
        return await self._resolve_workspace("mesh_project_workspace_id", project_id)

    async def resolve_milestone_workspace(self, milestone_id: uuid.UUID) -> uuid.UUID | None:
        return await self._resolve_workspace("mesh_milestone_workspace_id", milestone_id)

    async def resolve_cycle_workspace(self, cycle_id: uuid.UUID) -> uuid.UUID | None:
        return await self._resolve_workspace("mesh_cycle_workspace_id", cycle_id)

    async def resolve_template_workspace(self, template_id: uuid.UUID) -> uuid.UUID | None:
        return await self._resolve_workspace("mesh_project_template_workspace_id", template_id)

    # ------------------------------------------------------------------
    # projects CRUD
    # ------------------------------------------------------------------

    async def _create_project_tx(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateProjectRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        if actor.role == "guest":
            raise ForbiddenError("guests cannot create projects")
        _validate_name(body.name)
        _validate_project_key(body.key)
        _validate_enum(body.status, PROJECT_STATUS_VALUES, field="status")
        _validate_enum(body.visibility, PROJECT_VISIBILITY_VALUES, field="visibility")
        _validate_date_range(body.start_date, body.target_date)
        color = _normalize_project_color(body.color)

        project = Project(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=body.name,
            key=body.key,
            description=body.description,
            icon=body.icon,
            color=color,
            status=body.status,
            visibility=body.visibility,
            start_date=body.start_date,
            target_date=body.target_date,
        )
        if body.lead_member_id is not None:
            lead = await self._lead_member(
                session, workspace_id=workspace_id, lead_member_id=body.lead_member_id
            )
            project.lead_member_id = lead.id
        session.add(project)
        # Same-transaction prefix registration (README §6.3): exclusive against
        # project/inbox/retired keys → 409 project_key_taken. The registry
        # insert flushes the project row too, so both unique constraints are
        # mapped here (the projects.key unique can fire before the registry's).
        try:
            await occupy_project_prefix(
                session, workspace_id=workspace_id, key=project.key, project_id=project.id
            )
            await session.flush()
        except IntegrityError as exc:
            if _violates(exc, "uq_projects_key") or _violates(
                exc, "uq_prefix_registry_ws_key"
            ):
                raise ConflictError(
                    "project key is already taken in this workspace",
                    code="project_key_taken",
                    details={"key": project.key},
                ) from exc
            if _violates(exc, "uq_projects_name"):
                raise ConflictError(
                    "a project with this name already exists",
                    code="project_name_taken",
                    details={"name": project.name},
                ) from exc
            raise
        # Guarantee the workspace-level issue status default set exists in
        # this transaction (README §6.3 self-heal: workspaces created before
        # the issue increment get seeded on first project creation). Runs
        # AFTER the project flush so its autoflush-free SELECT cannot surface
        # the key-conflict IntegrityError outside the mapping above.
        from mesh.issue.statuses import ensure_scope_seeded

        await ensure_scope_seeded(session, workspace_id=workspace_id)
        if project.lead_member_id is not None:
            session.add(
                ProjectMember(
                    workspace_id=workspace_id,
                    project_id=project.id,
                    member_id=project.lead_member_id,
                    role="lead",
                )
            )
        # The creator always gets project membership as lead so they can
        # manage the project they created (project.md §3.4 write gates).
        if project.lead_member_id != actor.id:
            session.add(
                ProjectMember(
                    workspace_id=workspace_id,
                    project_id=project.id,
                    member_id=actor.id,
                    role="lead",
                )
            )
        await session.flush()
        lead_summary = None
        if project.lead_member_id is not None:
            lead_summary = await self._member_summary(
                session, workspace_id=workspace_id, member_id=project.lead_member_id
            )
        await self._emit_project_event(
            session,
            project=project,
            event="project.created",
            data={"project": self._render_project_summary(project, lead_summary)},
        )
        await self._audit(
            session,
            workspace_id=workspace_id,
            actor=actor,
            action="project.created",
            resource_id=project.id,
            metadata={"key": project.key},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        my_role = "lead"  # creator is always a lead member of a new project
        return self.render_project(project, lead=lead_summary, milestones=[], my_role=my_role)

    async def create_project(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateProjectRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            return await self._create_project_tx(
                session,
                actor=actor,
                workspace_id=workspace_id,
                body=body,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    async def list_projects(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        status: str | None = None,
        visibility: str | None = None,
        archived: bool = False,
        mine: bool = False,
        lead_member_id: uuid.UUID | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        if status is not None:
            _validate_enum(status, PROJECT_STATUS_VALUES, field="status")
        if visibility is not None:
            _validate_enum(visibility, PROJECT_VISIBILITY_VALUES, field="visibility")
        page_limit = _limit_page(limit)

        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(Project).where(
                Project.workspace_id == workspace_id,
                Project.deleted_at.is_(None),
            )
            if archived:
                stmt = stmt.where(Project.archived_at.is_not(None))
            else:
                stmt = stmt.where(Project.archived_at.is_(None))
            if status is not None:
                stmt = stmt.where(Project.status == status)
            if visibility is not None:
                stmt = stmt.where(Project.visibility == visibility)
            if lead_member_id is not None:
                stmt = stmt.where(Project.lead_member_id == lead_member_id)
            member_project_ids = select(ProjectMember.project_id).where(
                ProjectMember.member_id == viewer.id
            )
            if mine:
                stmt = stmt.where(
                    Project.id.in_(member_project_ids) | (Project.lead_member_id == viewer.id)
                )
            elif not self._is_workspace_manager(viewer):
                if viewer.role == "guest":
                    granted_ids = select(MemberProjectAccess.project_id).where(
                        MemberProjectAccess.member_id == viewer.id
                    )
                    stmt = stmt.where(Project.id.in_(granted_ids))
                else:
                    stmt = stmt.where(
                        (Project.visibility == "public") | Project.id.in_(member_project_ids)
                    )
            stmt = stmt.order_by(Project.created_at.desc(), Project.id.desc())
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(Project.created_at, Project.id)
                    < func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            rendered = []
            for project in rows:
                lead = None
                if project.lead_member_id is not None:
                    lead = await self._member_summary(
                        session,
                        workspace_id=workspace_id,
                        member_id=project.lead_member_id,
                    )
                rendered.append(self.render_project(project, lead=lead))
            return rendered, next_cursor

    async def get_project(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            return await self._get_project_tx(session, viewer=viewer, workspace_id=workspace_id,
                                              project_id=project_id)

    async def _get_project_tx(
        self,
        session: AsyncSession,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> dict:
        project = await self._load_project(
            session, workspace_id=workspace_id, project_id=project_id
        )
        await self.assert_can_view(session, viewer=viewer, project=project)
        lead = None
        if project.lead_member_id is not None:
            lead = await self._member_summary(
                session, workspace_id=workspace_id, member_id=project.lead_member_id
            )
        milestones = (
            (
                await session.execute(
                    select(Milestone)
                    .where(
                        Milestone.workspace_id == workspace_id,
                        Milestone.project_id == project.id,
                    )
                    .order_by(Milestone.target_date.asc().nulls_last(), Milestone.id.asc())
                )
            )
            .scalars()
            .all()
        )
        my_role = await self._project_role(session, project_id=project.id, member_id=viewer.id)
        # Effective role: being the lead via lead_member_id outranks a plain
        # member/viewer row, so the UI gates (e.g. lead reassignment) see the
        # viewer as lead even when they also hold a project_members row.
        if project.lead_member_id == viewer.id:
            my_role = "lead"
        return self.render_project(
            project, lead=lead, milestones=list(milestones), my_role=my_role
        )

    async def update_project(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        patch: ProjectPatch,
        if_match: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            # Lock the row when an If-Match precondition is supplied so the
            # version check + UPDATE are atomic (CWE-362 lost-update guard).
            project = await self._load_project(
                session,
                workspace_id=workspace_id,
                project_id=project_id,
                for_update=if_match is not None,
            )
            await self.assert_can_write(session, viewer=actor, project=project)
            if if_match is not None and not self._matches_version(project, if_match):
                raise ConflictError(
                    "project was modified concurrently",
                    code="conflict",
                    details={"id": str(project.id)},
                )
            previous_visibility = project.visibility

            changes: dict = {}
            if not isinstance(patch.name, _Unset) and patch.name != project.name:
                _validate_name(patch.name)
                changes["name"] = patch.name
            if not isinstance(patch.description, _Unset) and patch.description != project.description:
                changes["description"] = patch.description
            if not isinstance(patch.icon, _Unset) and patch.icon != project.icon:
                changes["icon"] = patch.icon
            if not isinstance(patch.color, _Unset):
                color = _normalize_project_color(patch.color)
                if color != project.color:
                    changes["color"] = color
            if not isinstance(patch.status, _Unset) and patch.status != project.status:
                _validate_enum(patch.status, PROJECT_STATUS_VALUES, field="status")
                changes["status"] = patch.status
            if not isinstance(patch.health, _Unset) and patch.health != project.health:
                if patch.health is not None:
                    _validate_enum(patch.health, PROJECT_HEALTH_VALUES, field="health")
                changes["health"] = patch.health
            if not isinstance(patch.visibility, _Unset) and patch.visibility != project.visibility:
                _validate_enum(patch.visibility, PROJECT_VISIBILITY_VALUES, field="visibility")
                changes["visibility"] = patch.visibility
            if not isinstance(patch.lead_member_id, _Unset) and (
                patch.lead_member_id != project.lead_member_id
            ):
                # Reassigning (or clearing) the lead is a lead/admin act
                # (project.md §3.4): the plain write gate would let a project
                # member — or a guest with a write grant — self-assign the
                # lead and thereby unlock delete/archive/member management.
                await self.assert_is_lead(session, viewer=actor, project=project)
                if patch.lead_member_id is not None:
                    await self._lead_member(
                        session,
                        workspace_id=workspace_id,
                        lead_member_id=patch.lead_member_id,
                    )
                changes["lead_member_id"] = (
                    str(patch.lead_member_id) if patch.lead_member_id is not None else None
                )
            if not isinstance(patch.start_date, _Unset) and patch.start_date != project.start_date:
                changes["start_date"] = _isoformat(patch.start_date)
            if not isinstance(patch.target_date, _Unset) and patch.target_date != project.target_date:
                changes["target_date"] = _isoformat(patch.target_date)

            if not changes:
                # §6.9: empty diff is a no-op (no event, no audit).
                lead = None
                if project.lead_member_id is not None:
                    lead = await self._member_summary(
                        session, workspace_id=workspace_id, member_id=project.lead_member_id
                    )
                return self.render_project(project, lead=lead)

            next_start = (
                patch.start_date if not isinstance(patch.start_date, _Unset) else project.start_date
            )
            next_target = (
                patch.target_date
                if not isinstance(patch.target_date, _Unset)
                else project.target_date
            )
            _validate_date_range(next_start, next_target)

            for field, value in changes.items():
                if field == "lead_member_id":
                    project.lead_member_id = uuid.UUID(value) if isinstance(value, str) else value
                elif field in ("start_date", "target_date"):
                    setattr(
                        project,
                        field,
                        date.fromisoformat(value) if isinstance(value, str) else value,
                    )
                else:
                    setattr(project, field, value)
            project.updated_at = _now(self._clock)
            await session.flush()

            lead = None
            if project.lead_member_id is not None:
                lead = await self._member_summary(
                    session, workspace_id=workspace_id, member_id=project.lead_member_id
                )
            progress = self._progress_payload(project)
            await self._emit_project_event(
                session,
                project=project,
                event="project.updated",
                data={
                    "id": str(project.id),
                    "changes": changes,
                    "progress": progress["progress"],
                    "updated_at": _isoformat(project.updated_at),
                },
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="project.updated",
                resource_id=project.id,
                metadata={"changes": sorted(changes.keys())},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            # public → private: the workspace-list channel only carries public
            # summaries, so non-members would keep a stale card until reload.
            # Emit a list-removal frame so their lists drop it immediately.
            if previous_visibility == "public" and project.visibility == "private":
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_workspace_projects_channel(workspace_id),
                    event="project.deleted",
                    data={"id": str(project.id)},
                )
            return self.render_project(project, lead=lead)

    @staticmethod
    def _matches_version(project: Project, if_match: str) -> bool:
        """Optimistic concurrency (README §6.14): If-Match carries updated_at."""
        candidate = if_match.strip().strip('"')
        if candidate == _isoformat(project.updated_at):
            return True
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed == project.updated_at

    async def delete_project(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        # Delete protection (integrations.md §2.10 / §5.6 ④): a project deletion
        # cascades its project-scoped integration bindings; that cascade would
        # SET NULL their queue items, which ck_imq_orphan_terminal rejects while
        # any item is non-terminal (the whole DELETE rolls back — no silent
        # message loss). Fail closed up front: refuse while non-terminal items
        # exist; the caller must drain them via the binding ?force=cancel path
        # first. Lazy import avoids a project↔integrations import cycle.
        from mesh.integrations.service import assert_no_active_project_queue

        await assert_no_active_project_queue(
            self._factory, workspace_id=workspace_id, project_id=project_id
        )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            await self.assert_is_lead(session, viewer=actor, project=project)
            project.deleted_at = _now(self._clock)
            project.updated_at = project.deleted_at
            await session.flush()
            # The prefix registry row deliberately stays: the key is
            # permanently reserved (README §6.3).
            await self._emit_project_event(
                session,
                project=project,
                event="project.deleted",
                data={"id": str(project.id)},
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="project.deleted",
                resource_id=project.id,
                metadata={"key": project.key},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return {"id": str(project.id), "deleted": True}

    async def _set_archived(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        archive: bool,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict:
        event = "project.archived" if archive else "project.unarchived"
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            # Archiving/unarchiving is a lead action; allowed on an already
            # archived project (idempotent), so skip the archived write guard.
            if not await self._is_lead(session, viewer=actor, project=project):
                raise ForbiddenError("project lead or workspace admin required")
            if archive and project.archived_at is None:
                project.archived_at = _now(self._clock)
                project.updated_at = project.archived_at
                await session.flush()
                await self._emit_project_event(
                    session, project=project, event=event, data={"id": str(project.id)}
                )
                await self._audit(
                    session, workspace_id=workspace_id, actor=actor, action=event,
                    resource_id=project.id, ip_address=ip_address, user_agent=user_agent,
                )
            elif not archive and project.archived_at is not None:
                project.archived_at = None
                project.updated_at = _now(self._clock)
                await session.flush()
                await self._emit_project_event(
                    session, project=project, event=event, data={"id": str(project.id)}
                )
                await self._audit(
                    session, workspace_id=workspace_id, actor=actor, action=event,
                    resource_id=project.id, ip_address=ip_address, user_agent=user_agent,
                )
            lead = None
            if project.lead_member_id is not None:
                lead = await self._member_summary(
                    session, workspace_id=workspace_id, member_id=project.lead_member_id
                )
            return self.render_project(project, lead=lead)

    async def archive_project(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        return await self._set_archived(
            actor=actor, workspace_id=workspace_id, project_id=project_id, archive=True,
            ip_address=ip_address, user_agent=user_agent,
        )

    async def unarchive_project(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        return await self._set_archived(
            actor=actor, workspace_id=workspace_id, project_id=project_id, archive=False,
            ip_address=ip_address, user_agent=user_agent,
        )

    # ------------------------------------------------------------------
    # health / status trail (project_updates)
    # ------------------------------------------------------------------

    async def add_update(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        body: AddProjectUpdateRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        if body.health is None and body.status is None and body.message is None:
            raise ValidationError("at least one of health, status or message is required")
        if body.health is not None:
            _validate_enum(body.health, PROJECT_HEALTH_VALUES, field="health")
        if body.status is not None:
            _validate_enum(body.status, PROJECT_STATUS_VALUES, field="status")

        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            await self.assert_can_write(session, viewer=actor, project=project)
            update = ProjectUpdate(
                workspace_id=workspace_id,
                project_id=project.id,
                author_member_id=actor.id,
                health=body.health,
                status=body.status,
                message=body.message,
            )
            session.add(update)
            # Write the recorded values back to the project's current state.
            if body.health is not None:
                project.health = body.health
            if body.status is not None:
                project.status = body.status
            project.updated_at = _now(self._clock)
            await session.flush()
            author = await self._member_summary(
                session, workspace_id=workspace_id, member_id=actor.id
            )
            rendered = self.render_update(update, author)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_project_channel(project.id),
                event="project_update.added",
                data={"update": {**rendered, "created_at": _isoformat(rendered["created_at"])}},
            )
            progress = self._progress_payload(project)
            await self._emit_project_event(
                session,
                project=project,
                event="project.updated",
                data={
                    "id": str(project.id),
                    "changes": {
                        key: value
                        for key, value in (("health", body.health), ("status", body.status))
                        if value is not None
                    },
                    "progress": progress["progress"],
                    "updated_at": _isoformat(project.updated_at),
                },
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="project_update.added",
                resource_id=project.id,
                metadata={"health": body.health, "status": body.status},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def list_updates(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            await self.assert_can_view(session, viewer=viewer, project=project)
            stmt = (
                select(ProjectUpdate)
                .where(
                    ProjectUpdate.workspace_id == workspace_id,
                    ProjectUpdate.project_id == project.id,
                )
                .order_by(ProjectUpdate.created_at.desc(), ProjectUpdate.id.desc())
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(ProjectUpdate.created_at, ProjectUpdate.id)
                    < func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            rendered = []
            for update in rows:
                author = await self._member_summary(
                    session, workspace_id=workspace_id, member_id=update.author_member_id
                )
                rendered.append(self.render_update(update, author))
            return rendered, next_cursor

    # ------------------------------------------------------------------
    # milestones
    # ------------------------------------------------------------------

    async def _create_milestone_tx(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        body: CreateMilestoneRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        _validate_name(body.title, field="title")
        project = await self._load_project(
            session, workspace_id=workspace_id, project_id=project_id
        )
        await self.assert_can_write(session, viewer=actor, project=project)
        milestone = Milestone(
            workspace_id=workspace_id,
            project_id=project.id,
            title=body.title,
            description=body.description,
            target_date=body.target_date,
        )
        session.add(milestone)
        project.updated_at = _now(self._clock)
        await session.flush()
        rendered = self.render_milestone(milestone)
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=_project_channel(project.id),
            event="milestone.created",
            data={"milestone": _jsonify_milestone(rendered)},
        )
        await self._audit(
            session,
            workspace_id=workspace_id,
            actor=actor,
            action="milestone.created",
            resource_type="milestone",
            resource_id=milestone.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return rendered

    async def create_milestone(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        body: CreateMilestoneRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            return await self._create_milestone_tx(
                session,
                actor=actor,
                workspace_id=workspace_id,
                project_id=project_id,
                body=body,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    async def _load_milestone(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, milestone_id: uuid.UUID
    ) -> tuple[Milestone, Project]:
        milestone = await session.scalar(
            select(Milestone).where(
                Milestone.id == milestone_id,
                Milestone.workspace_id == workspace_id,
            )
        )
        if milestone is None:
            raise NotFoundError(_MILESTONE_NOT_FOUND)
        project = await self._load_project(
            session, workspace_id=workspace_id, project_id=milestone.project_id
        )
        return milestone, project

    async def update_milestone(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        milestone_id: uuid.UUID,
        patch: MilestonePatch,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            milestone, project = await self._load_milestone(
                session, workspace_id=workspace_id, milestone_id=milestone_id
            )
            await self.assert_can_write(session, viewer=actor, project=project)
            changes: dict = {}
            if not isinstance(patch.title, _Unset) and patch.title != milestone.title:
                _validate_name(patch.title, field="title")
                changes["title"] = patch.title
            if not isinstance(patch.description, _Unset) and (
                patch.description != milestone.description
            ):
                changes["description"] = patch.description
            if not isinstance(patch.target_date, _Unset) and (
                patch.target_date != milestone.target_date
            ):
                changes["target_date"] = _isoformat(patch.target_date)
            if not isinstance(patch.state, _Unset) and patch.state != milestone.state:
                _validate_enum(patch.state, MILESTONE_STATE_VALUES, field="state")
                changes["state"] = patch.state
            if changes:
                for field, value in changes.items():
                    if field == "target_date":
                        milestone.target_date = (
                            date.fromisoformat(value) if isinstance(value, str) else value
                        )
                    else:
                        setattr(milestone, field, value)
                milestone.updated_at = _now(self._clock)
                await session.flush()
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_project_channel(project.id),
                    event="milestone.updated",
                    data={
                        "milestone": _jsonify_milestone(self.render_milestone(milestone)),
                        "changes": changes,
                    },
                )
                await self._audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="milestone.updated",
                    resource_type="milestone",
                    resource_id=milestone.id,
                    metadata={"changes": sorted(changes.keys())},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            return self.render_milestone(milestone)

    async def delete_milestone(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        milestone_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            milestone, project = await self._load_milestone(
                session, workspace_id=workspace_id, milestone_id=milestone_id
            )
            await self.assert_can_write(session, viewer=actor, project=project)
            rendered = self.render_milestone(milestone)
            await session.delete(milestone)
            await session.flush()
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_project_channel(project.id),
                event="milestone.deleted",
                data={"milestone": _jsonify_milestone(rendered)},
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="milestone.deleted",
                resource_type="milestone",
                resource_id=milestone_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return {"id": str(milestone_id), "deleted": True}

    async def list_milestones(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        state: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        if state is not None:
            _validate_enum(state, MILESTONE_STATE_VALUES, field="state")
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            await self.assert_can_view(session, viewer=viewer, project=project)
            stmt = (
                select(Milestone)
                .where(
                    Milestone.workspace_id == workspace_id,
                    Milestone.project_id == project.id,
                )
                .order_by(Milestone.created_at.desc(), Milestone.id.desc())
            )
            if state is not None:
                stmt = stmt.where(Milestone.state == state)
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(Milestone.created_at, Milestone.id)
                    < func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            return [self.render_milestone(milestone) for milestone in rows], next_cursor

    # ------------------------------------------------------------------
    # cycles
    # ------------------------------------------------------------------

    async def _create_cycle_tx(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateCycleRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        _validate_name(body.name, field="name")
        _validate_enum(body.state, CYCLE_STATE_VALUES, field="state")
        _validate_cycle_range(body.starts_at, body.ends_at)
        project = None
        if body.project_id is not None:
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=body.project_id
            )
            await self.assert_can_write(session, viewer=actor, project=project)
        elif actor.role == "guest":
            raise ForbiddenError(_GUEST_WRITE_FORBIDDEN)
        cycle = Cycle(
            workspace_id=workspace_id,
            project_id=project.id if project is not None else None,
            name=body.name,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            state=body.state,
            auto_roll=body.auto_roll,
        )
        session.add(cycle)
        await session.flush()
        rendered = self.render_cycle(cycle)
        await self._emit_cycle_event(
            session, cycle=cycle, project=project, data={"cycle": _jsonify_cycle(rendered)}
        )
        await self._audit(
            session,
            workspace_id=workspace_id,
            actor=actor,
            action="cycle.created",
            resource_type="cycle",
            resource_id=cycle.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return rendered

    async def create_cycle(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateCycleRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            return await self._create_cycle_tx(
                session,
                actor=actor,
                workspace_id=workspace_id,
                body=body,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    async def _emit_cycle_event(
        self,
        session: AsyncSession,
        *,
        cycle: Cycle,
        project: Project | None,
        data: dict,
    ) -> None:
        if project is not None:
            await self._emit_project_event(
                session, project=project, event="cycle.updated", data=data
            )
        else:
            await emit_realtime(
                session,
                workspace_id=cycle.workspace_id,
                channel=_workspace_projects_channel(cycle.workspace_id),
                event="cycle.updated",
                data=data,
            )

    async def _load_cycle(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, cycle_id: uuid.UUID
    ) -> tuple[Cycle, Project | None]:
        cycle = await session.scalar(
            select(Cycle).where(Cycle.id == cycle_id, Cycle.workspace_id == workspace_id)
        )
        if cycle is None:
            raise NotFoundError(_CYCLE_NOT_FOUND)
        project = None
        if cycle.project_id is not None:
            project = await session.scalar(
                select(Project).where(
                    Project.id == cycle.project_id,
                    Project.workspace_id == workspace_id,
                    Project.deleted_at.is_(None),
                )
            )
        return cycle, project

    async def update_cycle(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        cycle_id: uuid.UUID,
        patch: CyclePatch,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            cycle, project = await self._load_cycle(
                session, workspace_id=workspace_id, cycle_id=cycle_id
            )
            if project is not None:
                await self.assert_can_write(session, viewer=actor, project=project)
            elif actor.role == "guest":
                raise ForbiddenError(_GUEST_WRITE_FORBIDDEN)

            changes: dict = {}
            if not isinstance(patch.name, _Unset) and patch.name != cycle.name:
                _validate_name(patch.name, field="name")
                changes["name"] = patch.name
            if not isinstance(patch.starts_at, _Unset) and patch.starts_at != cycle.starts_at:
                changes["starts_at"] = _isoformat(patch.starts_at)
            if not isinstance(patch.ends_at, _Unset) and patch.ends_at != cycle.ends_at:
                changes["ends_at"] = _isoformat(patch.ends_at)
            if not isinstance(patch.state, _Unset) and patch.state != cycle.state:
                _validate_enum(patch.state, CYCLE_STATE_VALUES, field="state")
                changes["state"] = patch.state
            if not isinstance(patch.auto_roll, _Unset) and patch.auto_roll != cycle.auto_roll:
                changes["auto_roll"] = patch.auto_roll
            if not changes:
                return self.render_cycle(cycle)

            next_starts = (
                patch.starts_at if not isinstance(patch.starts_at, _Unset) else cycle.starts_at
            )
            next_ends = (
                patch.ends_at if not isinstance(patch.ends_at, _Unset) else cycle.ends_at
            )
            _validate_cycle_range(next_starts, next_ends)
            completing = (
                not isinstance(patch.state, _Unset)
                and patch.state == "completed"
                and cycle.state != "completed"
            )
            for field, value in changes.items():
                if field in ("starts_at", "ends_at"):
                    setattr(
                        cycle, field, date.fromisoformat(value) if isinstance(value, str) else value
                    )
                else:
                    setattr(cycle, field, value)
            cycle.updated_at = _now(self._clock)
            await session.flush()

            next_cycle = None
            if completing and cycle.auto_roll:
                # Auto-roll (project.md §1.2.5): the next time box is created
                # immediately. Unfinished-issue rollover/back-to-backlog and
                # member notification degrade until the issue.md /
                # comment-inbox.md increments provide those tables.
                next_cycle = self._roll_next_cycle(cycle)
                session.add(next_cycle)
                await session.flush()

            rendered = self.render_cycle(cycle)
            await self._emit_cycle_event(
                session,
                cycle=cycle,
                project=project,
                data={"cycle": _jsonify_cycle(rendered), "changes": changes},
            )
            if next_cycle is not None:
                next_rendered = self.render_cycle(next_cycle)
                await self._emit_cycle_event(
                    session,
                    cycle=next_cycle,
                    project=project,
                    data={"cycle": _jsonify_cycle(next_rendered)},
                )
                rendered["next_cycle"] = next_rendered
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="cycle.updated",
                resource_type="cycle",
                resource_id=cycle.id,
                metadata={"changes": sorted(changes.keys())},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    def _roll_next_cycle(self, cycle: Cycle) -> Cycle:
        """Next cycle = same length, starts the day after (project.md §1.2.5)."""
        length = cycle.ends_at - cycle.starts_at
        next_starts = cycle.ends_at + timedelta(days=1)
        return Cycle(
            workspace_id=cycle.workspace_id,
            project_id=cycle.project_id,
            name=f"{cycle.name}+1",
            starts_at=next_starts,
            ends_at=next_starts + length,
            state="planned",
            auto_roll=cycle.auto_roll,
        )

    async def list_cycles(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        state: str | None = None,
        project_id: uuid.UUID | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        if state is not None:
            _validate_enum(state, CYCLE_STATE_VALUES, field="state")
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(Cycle)
                .where(Cycle.workspace_id == workspace_id)
                .order_by(Cycle.starts_at.desc(), Cycle.id.desc())
            )
            if state is not None:
                stmt = stmt.where(Cycle.state == state)
            if project_id is not None:
                stmt = stmt.where(Cycle.project_id == project_id)
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(Cycle.starts_at, Cycle.id)
                    < func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            has_more = len(rows) > page_limit
            scanned = rows[:page_limit]
            visible: list[Cycle] = []
            for cycle in scanned:
                if cycle.project_id is None:
                    # Workspace-level cycles are invisible to guests (no
                    # project to grant against).
                    if viewer.role != "guest":
                        visible.append(cycle)
                    continue
                project = await session.scalar(
                    select(Project).where(
                        Project.id == cycle.project_id,
                        Project.workspace_id == workspace_id,
                        Project.deleted_at.is_(None),
                    )
                )
                if project is None:
                    continue
                try:
                    await self.assert_can_view(session, viewer=viewer, project=project)
                except (ForbiddenError, NotFoundError):
                    continue
                visible.append(cycle)
            next_cursor = None
            if has_more:
                last = scanned[-1]
                next_cursor = encode_cursor(last.starts_at, last.id)
            return [self.render_cycle(cycle) for cycle in visible], next_cursor

    # ------------------------------------------------------------------
    # project members
    # ------------------------------------------------------------------

    async def add_project_member(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        body: AddProjectMemberRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        _validate_enum(body.role, PROJECT_MEMBER_ROLE_VALUES, field="role")
        try:
            target_member_id = uuid.UUID(body.member_id)
        except ValueError as exc:
            raise ValidationError(
                "invalid member_id", details={"member_id": body.member_id[:64]}
            ) from exc
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            await self.assert_is_lead(session, viewer=actor, project=project)
            member = await session.scalar(
                select(Member).where(
                    Member.id == target_member_id,
                    Member.workspace_id == workspace_id,
                    Member.status == "active",
                )
            )
            if member is None:
                raise ValidationError(
                    "member is not an active member of this workspace",
                    details={"member_id": body.member_id},
                )
            existing = await self._project_role(
                session, project_id=project.id, member_id=member.id
            )
            if existing is not None:
                raise ConflictError(
                    "member already belongs to this project",
                    code="project_member_exists",
                    details={"member_id": str(member.id)},
                )
            entry = ProjectMember(
                workspace_id=workspace_id,
                project_id=project.id,
                member_id=member.id,
                role=body.role,
            )
            session.add(entry)
            await session.flush()
            await self._emit_project_event(
                session,
                project=project,
                event="project.updated",
                data={
                    "id": str(project.id),
                    "changes": {"members": [{"member_id": str(member.id), "role": body.role}]},
                },
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="project_member.added",
                resource_id=project.id,
                metadata={"member_id": str(member.id), "role": body.role},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return {
                "id": str(entry.id),
                "project_id": str(project.id),
                "member_id": str(member.id),
                "role": entry.role,
            }

    async def update_project_member(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        member_id: uuid.UUID,
        body: UpdateProjectMemberRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        _validate_enum(body.role, PROJECT_MEMBER_ROLE_VALUES, field="role")
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            await self.assert_is_lead(session, viewer=actor, project=project)
            entry = await session.scalar(
                select(ProjectMember).where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.member_id == member_id,
                )
            )
            if entry is None:
                raise NotFoundError("project member not found")
            if entry.role != body.role:
                entry.role = body.role
                await session.flush()
                await self._emit_project_event(
                    session,
                    project=project,
                    event="project.updated",
                    data={
                        "id": str(project.id),
                        "changes": {"members": [{"member_id": str(member_id), "role": body.role}]},
                    },
                )
                await self._audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="project_member.role_changed",
                    resource_id=project.id,
                    metadata={"member_id": str(member_id), "role": body.role},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            return {
                "id": str(entry.id),
                "project_id": str(project.id),
                "member_id": str(member_id),
                "role": entry.role,
            }

    async def remove_project_member(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        member_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            await self.assert_is_lead(session, viewer=actor, project=project)
            entry = await session.scalar(
                select(ProjectMember).where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.member_id == member_id,
                )
            )
            if entry is None:
                raise NotFoundError("project member not found")
            await session.delete(entry)
            await session.flush()
            await self._emit_project_event(
                session,
                project=project,
                event="project.updated",
                data={"id": str(project.id), "changes": {"members_removed": [str(member_id)]}},
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="project_member.removed",
                resource_id=project.id,
                metadata={"member_id": str(member_id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return {"id": str(member_id), "removed": True}

    async def list_project_members(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            project = await self._load_project(
                session, workspace_id=workspace_id, project_id=project_id
            )
            await self.assert_can_view(session, viewer=viewer, project=project)
            stmt = (
                select(ProjectMember)
                .where(
                    ProjectMember.workspace_id == workspace_id,
                    ProjectMember.project_id == project.id,
                )
                .order_by(ProjectMember.created_at.desc(), ProjectMember.id.desc())
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(ProjectMember.created_at, ProjectMember.id)
                    < func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            rendered = []
            for entry in rows:
                member = await self._member_summary(
                    session, workspace_id=workspace_id, member_id=entry.member_id
                )
                rendered.append(
                    {
                        "id": str(entry.id),
                        "project_id": str(project.id),
                        "member_id": str(entry.member_id),
                        "member": member,
                        "role": entry.role,
                        "created_at": entry.created_at,
                    }
                )
            return rendered, next_cursor

    # ------------------------------------------------------------------
    # templates (project.md §3.2b)
    # ------------------------------------------------------------------

    async def create_template(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateProjectTemplateRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        if actor.role == "guest":
            raise ForbiddenError("guests cannot manage project templates")
        _validate_name(body.name)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            template = ProjectTemplate(
                workspace_id=workspace_id,
                name=body.name,
                template_body=body.template_body,
                created_by=actor.id,
            )
            session.add(template)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_project_templates_ws_name"):
                    raise ConflictError(
                        "a template with this name already exists",
                        code="template_name_taken",
                        details={"name": body.name},
                    ) from exc
                raise
            creator = await self._member_summary(
                session, workspace_id=workspace_id, member_id=actor.id
            )
            return self.render_template(template, creator)

    async def list_templates(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        if viewer.role == "guest":
            raise ForbiddenError("guests cannot view project templates")
        page_limit = _limit_page(limit)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(ProjectTemplate)
                .where(ProjectTemplate.workspace_id == workspace_id)
                .order_by(ProjectTemplate.created_at.desc(), ProjectTemplate.id.desc())
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(ProjectTemplate.created_at, ProjectTemplate.id)
                    < func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            rendered = []
            for template in rows:
                creator = await self._member_summary(
                    session, workspace_id=workspace_id, member_id=template.created_by
                )
                rendered.append(self.render_template(template, creator))
            return rendered, next_cursor

    async def _load_template(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, template_id: uuid.UUID
    ) -> ProjectTemplate:
        template = await session.scalar(
            select(ProjectTemplate).where(
                ProjectTemplate.id == template_id,
                ProjectTemplate.workspace_id == workspace_id,
            )
        )
        if template is None:
            raise NotFoundError(_TEMPLATE_NOT_FOUND)
        return template

    async def _assert_can_manage_template(
        self, *, actor: Member, template: ProjectTemplate
    ) -> None:
        if self._is_workspace_manager(actor) or template.created_by == actor.id:
            return
        raise ForbiddenError("template creator or workspace admin required")

    async def update_template(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        body: UpdateProjectTemplateRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            template = await self._load_template(
                session, workspace_id=workspace_id, template_id=template_id
            )
            await self._assert_can_manage_template(actor=actor, template=template)
            changed = False
            if body.name is not None and body.name != template.name:
                _validate_name(body.name)
                template.name = body.name
                changed = True
            if body.template_body is not None and body.template_body != template.template_body:
                template.template_body = body.template_body
                changed = True
            if changed:
                template.updated_at = _now(self._clock)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_project_templates_ws_name"):
                    # NB: use the request value, not template.name — after a
                    # failed ORM flush the instance is expired and attribute
                    # access would re-issue SQL on a dead transaction.
                    raise ConflictError(
                        "a template with this name already exists",
                        code="template_name_taken",
                        details={"name": body.name},
                    ) from exc
                raise
            creator = await self._member_summary(
                session, workspace_id=workspace_id, member_id=template.created_by
            )
            return self.render_template(template, creator)

    async def delete_template(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            template = await self._load_template(
                session, workspace_id=workspace_id, template_id=template_id
            )
            await self._assert_can_manage_template(actor=actor, template=template)
            await session.delete(template)
            await session.flush()
            return {"id": str(template_id), "deleted": True}

    async def instantiate_template(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        body: InstantiateProjectTemplateRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Create a project from a template in ONE transaction (§3.2b).

        Prefill items whose owning module is not built yet (issue status set,
        default view config) degrade gracefully into ``skipped`` rather than
        failing the whole instantiation.
        """
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            template = await self._load_template(
                session, workspace_id=workspace_id, template_id=template_id
            )
            blueprint = dict(template.template_body or {})
            blueprint.update(body.overrides or {})

            created = await self._create_project_tx(
                session,
                actor=actor,
                workspace_id=workspace_id,
                body=CreateProjectRequest(
                    name=body.name,
                    key=body.key,
                    description=blueprint.get("description"),
                    visibility=blueprint.get("default_visibility") or "public",
                ),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            project_id = uuid.UUID(created["id"])
            skipped: list[str] = []
            milestone_ids: list[str] = []
            for item in blueprint.get("initial_milestones") or []:
                title = item.get("title") if isinstance(item, dict) else None
                if not title:
                    skipped.append("initial_milestones:invalid_item")
                    continue
                milestone = await self._create_milestone_tx(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    body=CreateMilestoneRequest(
                        title=title,
                        description=item.get("description"),
                        target_date=item.get("target_date"),
                    ),
                )
                milestone_ids.append(milestone["id"])
            cycle_ids: list[str] = []
            for item in blueprint.get("initial_cycles") or []:
                parsed = _parse_cycle_item(item)
                if parsed is None:
                    skipped.append("initial_cycles:invalid_item")
                    continue
                starts_at, ends_at, name, auto_roll = parsed
                cycle = await self._create_cycle_tx(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    body=CreateCycleRequest(
                        name=name,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        project_id=str(project_id),
                        auto_roll=auto_roll,
                    ),
                )
                cycle_ids.append(cycle["id"])
            # Prefill items owned by not-yet-built modules degrade gracefully.
            if blueprint.get("status_set_seed"):
                skipped.append("status_set_seed:issue_module_pending")
            if blueprint.get("default_view_config"):
                skipped.append("default_view_config:kanban_module_pending")

            result = await self._get_project_tx(
                session, viewer=actor, workspace_id=workspace_id, project_id=project_id
            )
            result["template_id"] = str(template.id)
            result["milestone_ids"] = milestone_ids
            result["cycle_ids"] = cycle_ids
            result["skipped"] = skipped
            return result

    # ------------------------------------------------------------------
    # numbering hook for issue.md (README §6.3)
    # ------------------------------------------------------------------

    async def next_issue_number(self, session: AsyncSession, *, project_id: uuid.UUID) -> int:
        """Row-locked increment of ``projects.issue_seq`` (issue.md §2.4/T15).

        Mirrors ``workspace.service.next_inbox_issue_number``: concurrent
        issue creation in the same project never duplicates numbers.
        """
        return (
            await session.execute(
                text(
                    "UPDATE projects SET issue_seq = issue_seq + 1 "
                    "WHERE id = :project_id AND deleted_at IS NULL "
                    "RETURNING issue_seq"
                ),
                {"project_id": project_id},
            )
        ).scalar_one()


def _parse_cycle_item(item: object) -> tuple[date, date, str, bool] | None:
    """Parse a template ``initial_cycles`` entry; None when malformed."""
    if not isinstance(item, dict) or not item.get("name"):
        return None
    try:
        starts_at = item["starts_at"]
        ends_at = item["ends_at"]
        if isinstance(starts_at, str):
            starts_at = date.fromisoformat(starts_at)
        if isinstance(ends_at, str):
            ends_at = date.fromisoformat(ends_at)
    except (KeyError, ValueError, TypeError):
        return None
    if not isinstance(starts_at, date) or not isinstance(ends_at, date) or ends_at < starts_at:
        return None
    return starts_at, ends_at, str(item["name"]), bool(item.get("auto_roll", False))


def _jsonify_milestone(rendered: dict) -> dict:
    return {
        **rendered,
        "created_at": _isoformat(rendered["created_at"]),
        "updated_at": _isoformat(rendered["updated_at"]),
    }


def _jsonify_cycle(rendered: dict) -> dict:
    return {
        **rendered,
        "created_at": _isoformat(rendered["created_at"]),
        "updated_at": _isoformat(rendered["updated_at"]),
    }


__all__ = [
    "CyclePatch",
    "MilestonePatch",
    "ProjectPatch",
    "ProjectService",
    "UNSET",
]
