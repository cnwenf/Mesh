"""Issue statuses — two-layer state model (issue.md §1.2.3 / §2.2).

``category`` is the stable system semantic (aggregation, kanban columns,
automation); ``status`` is the user-customizable display layer. Each scope
(workspace-level = ``project_id IS NULL``; project-private) has AT MOST one
``is_default`` status (partial expression unique index ``uq_issue_statuses_default``,
README §6.3) and AT LEAST one, guaranteed transactionally:

* workspace creation seeds the canonical seven statuses (workspace service
  hook) — as does project creation for the workspace scope when it is missing;
* the service self-heals: any path that needs a scope's default calls
  :func:`ensure_scope_seeded`, which seeds the canonical set inside the
  caller's transaction when the scope is empty (and logs the gap).

Unsetting a default is only legal together with setting a new one in the SAME
transaction (README §6.3); the PATCH API refuses a bare unset (422
``default_status_required``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.constraints import violates as _violates
from mesh.db.models.issue import STATE_CATEGORY_VALUES, IssueStatus
from mesh.db.models.member import Member
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError

logger = logging.getLogger("mesh.issue.statuses")

# Canonical seed set: one status per category, positions follow the state
# machine order, ``Todo`` is the creation default (issue.md §1.2.3).
DEFAULT_STATUS_SEED: tuple[tuple[str, str, float, bool, str], ...] = (
    # (name, category, position, is_default, color)
    ("Backlog", "backlog", 0.0, False, "#8a8f98"),
    ("Todo", "todo", 1.0, True, "#4c9aff"),
    ("In Progress", "in_progress", 2.0, False, "#f2c94c"),
    ("In Review", "in_review", 3.0, False, "#b47cff"),
    ("Blocked", "blocked", 4.0, False, "#eb5757"),
    ("Done", "done", 5.0, False, "#27ae60"),
    ("Cancelled", "cancelled", 6.0, False, "#9aa0a6"),
)

STATUS_NOT_FOUND = "issue status not found"


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


async def scope_status_count(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID | None
) -> int:
    """Number of statuses defined exactly in this scope (no inheritance)."""
    stmt = select(func.count()).select_from(IssueStatus).where(
        IssueStatus.workspace_id == workspace_id
    )
    if project_id is None:
        stmt = stmt.where(IssueStatus.project_id.is_(None))
    else:
        stmt = stmt.where(IssueStatus.project_id == project_id)
    return int((await session.execute(stmt)).scalar_one())


async def seed_default_statuses(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
) -> list[IssueStatus]:
    """Seed the canonical status set for an EMPTY scope (idempotent).

    Returns the created rows (empty when the scope was already populated).
    Callers run this inside their own transaction so seeding is atomic with
    the workspace/project creation (README §6.3). A scope-level advisory
    lock + re-check makes concurrent first-use safe (two parallel issue
    creations can never both seed the same scope).
    """
    from sqlalchemy import text

    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtext("
            "'issue_status_seed:' || :ws || ':' || COALESCE(:pid, '*')))"
        ),
        {
            "ws": str(workspace_id),
            "pid": str(project_id) if project_id is not None else None,
        },
    )
    if await scope_status_count(session, workspace_id=workspace_id, project_id=project_id):
        return []
    created: list[IssueStatus] = []
    for name, category, position, is_default, color in DEFAULT_STATUS_SEED:
        status = IssueStatus(
            workspace_id=workspace_id,
            project_id=project_id,
            name=name,
            category=category,
            position=position,
            is_default=is_default,
            color=color,
        )
        session.add(status)
        created.append(status)
    await session.flush()
    return created


async def ensure_scope_seeded(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
) -> None:
    """Self-heal: seed the canonical set when a scope has no statuses at all.

    The workspace scope must always have at least one default status
    (README §6.3); a gap means the creation-time seeding did not run (e.g.
    workspaces created before the issue increment) — repair in-transaction
    and log the gap.
    """
    if await scope_status_count(session, workspace_id=workspace_id, project_id=project_id):
        return
    logger.warning(
        "issue status scope (%s, project=%s) missing default set — seeding in-transaction",
        workspace_id,
        project_id,
    )
    await seed_default_statuses(session, workspace_id=workspace_id, project_id=project_id)


async def visible_statuses(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None,
) -> list[IssueStatus]:
    """Workspace-level statuses plus the project's private ones (if any)."""
    stmt = (
        select(IssueStatus)
        .where(
            IssueStatus.workspace_id == workspace_id,
            IssueStatus.project_id.is_(None)
            if project_id is None
            else IssueStatus.project_id.in_((project_id,)) | IssueStatus.project_id.is_(None),
        )
        .order_by(IssueStatus.category.asc(), IssueStatus.position.asc(), IssueStatus.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def resolve_status_in_scope(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None,
    status_id: uuid.UUID,
) -> IssueStatus:
    """Load a status usable by an issue in this scope (404 when out of scope)."""
    status = await session.scalar(
        select(IssueStatus).where(
            IssueStatus.id == status_id,
            IssueStatus.workspace_id == workspace_id,
        )
    )
    if status is None:
        raise NotFoundError(STATUS_NOT_FOUND)
    if status.project_id is not None and status.project_id != project_id:
        # Another project's private status is not usable here.
        raise NotFoundError(STATUS_NOT_FOUND)
    return status


async def resolve_default_status(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None,
    category: str | None = None,
) -> IssueStatus:
    """The default status for issue creation / status mapping (README §6.3).

    Preference for a given category: project-private default → project-private
    lowest position → workspace-level default → workspace-level lowest
    position. Without a category: the scope default (self-healing first).
    """
    await ensure_scope_seeded(session, workspace_id=workspace_id, project_id=None)
    scope_filter = (
        IssueStatus.project_id.is_(None)
        if project_id is None
        else IssueStatus.project_id.in_((project_id,)) | IssueStatus.project_id.is_(None)
    )
    if category is not None:
        if project_id is not None:
            project_default = await session.scalar(
                select(IssueStatus)
                .where(
                    IssueStatus.workspace_id == workspace_id,
                    IssueStatus.project_id == project_id,
                    IssueStatus.category == category,
                    IssueStatus.is_default.is_(True),
                )
                .order_by(IssueStatus.position.asc(), IssueStatus.id.asc())
                .limit(1)
            )
            if project_default is not None:
                return project_default
            project_lowest = await session.scalar(
                select(IssueStatus)
                .where(
                    IssueStatus.workspace_id == workspace_id,
                    IssueStatus.project_id == project_id,
                    IssueStatus.category == category,
                )
                .order_by(IssueStatus.position.asc(), IssueStatus.id.asc())
                .limit(1)
            )
            if project_lowest is not None:
                return project_lowest
        fallback = await session.scalar(
            select(IssueStatus)
            .where(
                IssueStatus.workspace_id == workspace_id,
                IssueStatus.project_id.is_(None),
                IssueStatus.category == category,
                IssueStatus.is_default.is_(True),
            )
            .order_by(IssueStatus.position.asc(), IssueStatus.id.asc())
            .limit(1)
        )
        if fallback is not None:
            return fallback
        lowest = await session.scalar(
            select(IssueStatus)
            .where(
                IssueStatus.workspace_id == workspace_id,
                IssueStatus.project_id.is_(None),
                IssueStatus.category == category,
            )
            .order_by(IssueStatus.position.asc(), IssueStatus.id.asc())
            .limit(1)
        )
        if lowest is not None:
            return lowest
    default = await session.scalar(
        select(IssueStatus)
        .where(scope_filter, IssueStatus.is_default.is_(True))
        .order_by(IssueStatus.position.asc(), IssueStatus.id.asc())
        .limit(1)
    )
    if default is None:  # pragma: no cover — seeding guarantees a default
        raise BusinessRuleError(
            "no default status in scope", code="default_status_required"
        )
    return default


def render_status(status: IssueStatus) -> dict:
    return {
        "id": str(status.id),
        "project_id": str(status.project_id) if status.project_id is not None else None,
        "name": status.name,
        "category": status.category,
        "color": status.color,
        "position": status.position,
        "is_default": status.is_default,
        "created_at": status.created_at,
        "updated_at": status.updated_at,
    }


def validate_category(category: str) -> None:
    if category not in STATE_CATEGORY_VALUES:
        raise ValidationError("invalid category", details={"category": category})


@dataclass(frozen=True)
class StatusPatch:
    """Tri-state PATCH payload for issue statuses."""

    name: str | object | None = None  # sentinel-managed by routes (_Unset)
    color: str | object | None = None
    position: float | object | None = None
    category: str | object | None = None
    is_default: bool | object | None = None


class StatusService:
    """CRUD over ``issue_statuses`` (issue.md §3.1 status endpoints)."""

    def __init__(
        self,
        session_factory,
        *,
        clock: Callable[[], datetime] | None = None,
        is_workspace_manager: Callable[[Member], bool],
    ) -> None:
        self._factory = session_factory
        self._clock = clock
        self._is_manager = is_workspace_manager

    async def list_statuses(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
    ) -> list[dict]:
        async with self._factory() as session, session.begin():
            # Tenant GUC first: issue_statuses is RLS-protected and the app
            # engine runs under the restricted role.
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            await ensure_scope_seeded(session, workspace_id=workspace_id, project_id=None)
            statuses = await visible_statuses(
                session, workspace_id=workspace_id, project_id=project_id
            )
            return [render_status(status) for status in statuses]

    async def create_status(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        name: str,
        category: str,
        color: str | None = None,
        position: float = 0.0,
        is_default: bool = False,
        project_id: uuid.UUID | None = None,
    ) -> dict:
        validate_category(category)
        if not isinstance(name, str) or not 1 <= len(name) <= 50:
            raise ValidationError("name must be 1-50 characters")
        if actor.role == "guest":
            raise ValidationError("guests cannot manage statuses", code="forbidden")
        async with self._factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            await ensure_scope_seeded(session, workspace_id=workspace_id, project_id=None)
            if is_default:
                # Same-transaction handoff: the partial unique index allows at
                # most one default per scope (README §6.3).
                existing = (
                    (
                        await session.execute(
                            select(IssueStatus).where(
                                IssueStatus.workspace_id == workspace_id,
                                IssueStatus.project_id.is_(None)
                                if project_id is None
                                else IssueStatus.project_id == project_id,
                                IssueStatus.is_default.is_(True),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for other in existing:
                    other.is_default = False
                # Persist the unsets BEFORE inserting the new default:
                # flush ordering would otherwise evaluate the partial unique
                # index against the still-default old row.
                await session.flush()
            status = IssueStatus(
                workspace_id=workspace_id,
                project_id=project_id,
                name=name,
                category=category,
                color=color,
                position=position,
                is_default=is_default,
            )
            session.add(status)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_issue_statuses_name"):
                    raise ConflictError(
                        "a status with this name already exists in this scope",
                        code="status_name_taken",
                        details={"name": name},
                    ) from exc
                if _violates(exc, "uq_issue_statuses_default"):
                    raise ConflictError(
                        "scope already has a default status",
                        code="default_status_taken",
                    ) from exc
                raise
            return render_status(status)

    async def _load_status(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, status_id: uuid.UUID
    ) -> IssueStatus:
        status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.id == status_id,
                IssueStatus.workspace_id == workspace_id,
            )
        )
        if status is None:
            raise NotFoundError(STATUS_NOT_FOUND)
        return status

    async def update_status(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        status_id: uuid.UUID,
        patch: StatusPatch,
        is_unset: Callable[[object], bool],
    ) -> dict:
        if actor.role == "guest":
            raise ValidationError("guests cannot manage statuses", code="forbidden")
        async with self._factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            status = await self._load_status(
                session, workspace_id=workspace_id, status_id=status_id
            )
            if not is_unset(patch.name) and patch.name != status.name:
                if not isinstance(patch.name, str) or not 1 <= len(patch.name) <= 50:
                    raise ValidationError("name must be 1-50 characters")
                status.name = patch.name
            if not is_unset(patch.color) and patch.color != status.color:
                status.color = patch.color
            if not is_unset(patch.position) and patch.position != status.position:
                status.position = float(patch.position)  # type: ignore[arg-type]
            if not is_unset(patch.category) and patch.category != status.category:
                validate_category(patch.category)  # type: ignore[arg-type]
                status.category = patch.category  # type: ignore[assignment]
                # Keep the denormalized issues.state_category in lockstep
                # (issue.md §2.5 rule 1 forbids the two drifting apart).
                from sqlalchemy import update

                from mesh.db.models.issue import Issue

                await session.execute(
                    update(Issue)
                    .where(Issue.workspace_id == workspace_id, Issue.status_id == status.id)
                    .values(state_category=status.category, updated_at=_now(self._clock))
                )
            if not is_unset(patch.is_default) and patch.is_default != status.is_default:
                if patch.is_default:
                    siblings = (
                        (
                            await session.execute(
                                select(IssueStatus).where(
                                    IssueStatus.workspace_id == workspace_id,
                                    IssueStatus.project_id.is_(None)
                                    if status.project_id is None
                                    else IssueStatus.project_id == status.project_id,
                                    IssueStatus.is_default.is_(True),
                                    IssueStatus.id != status.id,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for other in siblings:
                        other.is_default = False
                    # Flush the unsets before setting the new default (same
                    # partial-unique-index ordering trap as create_status).
                    await session.flush()
                    status.is_default = True
                else:
                    # Bare unset would leave the scope default-less; the
                    # handoff must happen in the same transaction (README
                    # §6.3) — the client sets the new default first.
                    raise BusinessRuleError(
                        "cannot unset the scope default without setting a replacement",
                        code="default_status_required",
                    )
            status.updated_at = _now(self._clock)
            # Capture before flush: a failed flush expires the instance and
            # attribute access would re-issue SQL on the aborted transaction.
            requested_name = patch.name if isinstance(patch.name, str) else None
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_issue_statuses_name"):
                    raise ConflictError(
                        "a status with this name already exists in this scope",
                        code="status_name_taken",
                        details={"name": requested_name},
                    ) from exc
                raise
            return render_status(status)

    async def delete_status(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        status_id: uuid.UUID,
    ) -> dict:
        if actor.role == "guest":
            raise ValidationError("guests cannot manage statuses", code="forbidden")
        async with self._factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            status = await self._load_status(
                session, workspace_id=workspace_id, status_id=status_id
            )
            await session.delete(status)
            try:
                await session.flush()
            except IntegrityError as exc:
                # issues.status_id ON DELETE RESTRICT: referenced statuses must
                # be emptied first (issue.md §5.2).
                raise ConflictError(
                    "status is still referenced by issues; migrate them first",
                    code="status_in_use",
                    details={"status_id": str(status_id)},
                ) from exc
            return {"id": str(status_id), "deleted": True}


__all__ = [
    "DEFAULT_STATUS_SEED",
    "StatusPatch",
    "StatusService",
    "ensure_scope_seeded",
    "render_status",
    "resolve_default_status",
    "resolve_status_in_scope",
    "seed_default_statuses",
    "validate_category",
    "visible_statuses",
]
