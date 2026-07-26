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
``default_status_required``) — and DELETE refuses the scope's LAST default
(409 ``last_default_status``), so a scope can never be drained to zero
defaults (which would 422 every future issue creation in it).

Recategorizing a status flips the state-machine semantics of every issue on
it: the service resyncs each referencing issue in-transaction (denormalized
``state_category`` + ``completed_at`` maintenance + OCC ``version`` bump +
``issue_activity`` trail + ``issue.updated``/``issue.moved`` events), exactly
like a direct per-issue status change, paged by primary key.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.constraints import violates as _violates
from mesh.db.models.issue import STATE_CATEGORY_VALUES, Issue, IssueActivity, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from mesh.outbox.service import emit_realtime

logger = logging.getLogger("mesh.issue.statuses")

# Category-resync page size: a hot status can back thousands of issues; the
# lock/update/event batch is kept bounded so one recategorization cannot
# build a single gigantic write set (issue.md §2.5 rule 1, M-5).
CATEGORY_RESCAN_BATCH_SIZE = 500

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
    stmt = select(func.count()).select_from(IssueStatus).where(IssueStatus.workspace_id == workspace_id)
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
    # The tenant predicate MUST be part of the scope filter: every tenant query
    # carries ``workspace_id`` (db/tenant.py). Omitting it here would let the
    # no-category fallback resolve another workspace's default status (MES-46
    # M1). Leading with the equality also hits ``idx_issue_statuses_scope``.
    scope_filter = and_(
        IssueStatus.workspace_id == workspace_id,
        IssueStatus.project_id.is_(None)
        if project_id is None
        else IssueStatus.project_id.in_((project_id,)) | IssueStatus.project_id.is_(None),
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
        raise BusinessRuleError("no default status in scope", code="default_status_required")
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
        "allowed_transitions": [str(t) for t in (status.allowed_transitions or [])],
        "created_at": status.created_at,
        "updated_at": status.updated_at,
    }


def validate_category(category: str) -> None:
    if category not in STATE_CATEGORY_VALUES:
        raise ValidationError("invalid category", details={"category": category})


def validate_allowed_transitions(value: object) -> list[str]:
    """严格模式「允许的下一步」:目标状态 id 的字符串数组(§4.4,迁移 0009)。"""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError(
            "allowed_transitions must be an array of status id strings",
            details={"allowed_transitions": str(value)[:64]},
        )
    normalized: list[str] = []
    for item in value:
        try:
            normalized.append(str(uuid.UUID(item)))
        except ValueError as exc:
            raise ValidationError(
                "allowed_transitions entries must be status UUIDs",
                details={"entry": item[:64]},
            ) from exc
    return normalized


@dataclass(frozen=True)
class StatusPatch:
    """Tri-state PATCH payload for issue statuses."""

    name: str | object | None = None  # sentinel-managed by routes (_Unset)
    color: str | object | None = None
    position: float | object | None = None
    category: str | object | None = None
    is_default: bool | object | None = None
    allowed_transitions: list | object | None = None


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
            statuses = await visible_statuses(session, workspace_id=workspace_id, project_id=project_id)
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
        allowed_transitions: list | None = None,
    ) -> dict:
        validate_category(category)
        transitions = validate_allowed_transitions(allowed_transitions or [])
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
                allowed_transitions=transitions,
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
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        status_id: uuid.UUID,
        for_update: bool = False,
    ) -> IssueStatus:
        stmt = select(IssueStatus).where(
            IssueStatus.id == status_id,
            IssueStatus.workspace_id == workspace_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        status = await session.scalar(stmt)
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
            status = await self._load_status(session, workspace_id=workspace_id, status_id=status_id)
            if not is_unset(patch.name) and patch.name != status.name:
                if not isinstance(patch.name, str) or not 1 <= len(patch.name) <= 50:
                    raise ValidationError("name must be 1-50 characters")
                status.name = patch.name
            if not is_unset(patch.color) and patch.color != status.color:
                status.color = patch.color
            if not is_unset(patch.position) and patch.position != status.position:
                status.position = float(patch.position)  # type: ignore[arg-type]
            if not is_unset(patch.allowed_transitions):
                transitions = validate_allowed_transitions(patch.allowed_transitions)
                if transitions != [str(t) for t in (status.allowed_transitions or [])]:
                    status.allowed_transitions = transitions
            if not is_unset(patch.category) and patch.category != status.category:
                validate_category(patch.category)  # type: ignore[arg-type]
                old_category = status.category
                status.category = patch.category  # type: ignore[assignment]
                # Keep EVERY derivation of the category in lockstep (issue.md
                # §2.5 rule 1 forbids the two drifting apart): the plain
                # state_category copy PLUS completed_at maintenance, the OCC
                # version bump and the event/audit trail — the full contract
                # a direct per-issue status change honors (M-5).
                await self._resync_issues_for_category_change(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    status=status,
                    old_category=old_category,
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

    async def _resync_issues_for_category_change(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        status: IssueStatus,
        old_category: str,
    ) -> int:
        """Propagate a status category flip to every referencing issue.

        Each affected issue gets exactly what a direct status change would
        (§3.4 semantics): the denormalized ``state_category``, the
        ``completed_at`` stamp (entering ``done``) or clear (leaving it),
        an OCC ``version`` bump, one ``issue_activity`` trail row and the
        ``issue.updated`` / ``issue.moved`` realtime events — emitted with
        the same visibility rule as the issue path (private-project issues
        hit ONLY their detail channel). Rows are processed in primary-key
        pages under row locks: bounded write sets (M-5 performance note)
        and no lost update against a concurrent per-issue PATCH, which
        locks the same rows (README §9 T12 lock-before-write). Returns the
        number of issues resynchronized.
        """
        # mesh.issue.service imports this module — import at call time.
        from mesh.issue.service import (
            _isoformat,
            _issue_channel,
            _workspace_issues_channel,
        )

        now = _now(self._clock)
        new_category = status.category
        visibility_cache: dict[uuid.UUID, str] = {}
        resynced = 0
        last_id: uuid.UUID | None = None
        while True:
            stmt = (
                select(Issue)
                .where(Issue.workspace_id == workspace_id, Issue.status_id == status.id)
                .order_by(Issue.id.asc())
                .limit(CATEGORY_RESCAN_BATCH_SIZE)
                .with_for_update()
            )
            if last_id is not None:
                stmt = stmt.where(Issue.id > last_id)
            batch = list((await session.execute(stmt)).scalars().all())
            if not batch:
                break
            unknown = {
                issue.project_id
                for issue in batch
                if issue.project_id is not None and issue.project_id not in visibility_cache
            }
            if unknown:
                rows = await session.execute(
                    select(Project.id, Project.visibility).where(
                        Project.workspace_id == workspace_id, Project.id.in_(unknown)
                    )
                )
                visibility_cache.update({pid: vis for pid, vis in rows.all()})
            for issue in batch:
                issue.state_category = new_category
                if new_category == "done" and issue.completed_at is None:
                    issue.completed_at = now
                elif new_category != "done" and issue.completed_at is not None:
                    issue.completed_at = None
                issue.version = issue.version + 1
                issue.updated_at = now
                session.add(
                    IssueActivity(
                        workspace_id=workspace_id,
                        issue_id=issue.id,
                        actor_member_id=actor.id,
                        field="state_category",
                        old_value=old_category,
                        new_value=new_category,
                    )
                )
                project_public = (
                    issue.project_id is None or visibility_cache.get(issue.project_id) == "public"
                )
                updated_payload = {
                    "id": str(issue.id),
                    "changes": {"state_category": new_category},
                    "version": issue.version,
                    "visibility": {
                        "project_id": str(issue.project_id) if issue.project_id is not None else None,
                        "state_category": new_category,
                    },
                    "updated_at": _isoformat(issue.updated_at),
                }
                moved_payload = {
                    "id": str(issue.id),
                    "from": {"state_category": old_category},
                    "to": {"state_category": new_category},
                }
                for event, data in (
                    ("issue.updated", updated_payload),
                    ("issue.moved", moved_payload),
                ):
                    await emit_realtime(
                        session,
                        workspace_id=workspace_id,
                        channel=_issue_channel(issue.id),
                        event=event,
                        data=data,
                    )
                    if project_public:
                        await emit_realtime(
                            session,
                            workspace_id=workspace_id,
                            channel=_workspace_issues_channel(workspace_id),
                            event=event,
                            data=data,
                        )
                resynced += 1
            last_id = batch[-1].id
            await session.flush()
        return resynced

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
                session, workspace_id=workspace_id, status_id=status_id, for_update=True
            )
            # issues.status_id ON DELETE RESTRICT: referenced statuses must
            # be emptied first (issue.md §5.2). Checked explicitly so the
            # in-use error precedes the last-default guard (and a concurrent
            # reference still trips the IntegrityError backstop below).
            references = await session.scalar(
                select(func.count())
                .select_from(Issue)
                .where(Issue.workspace_id == workspace_id, Issue.status_id == status.id)
            )
            if references:
                raise ConflictError(
                    "status is still referenced by issues; migrate them first",
                    code="status_in_use",
                    details={"status_id": str(status_id)},
                )
            if status.is_default:
                other_defaults = await session.scalar(
                    select(func.count())
                    .select_from(IssueStatus)
                    .where(
                        IssueStatus.workspace_id == workspace_id,
                        IssueStatus.project_id.is_(None)
                        if status.project_id is None
                        else IssueStatus.project_id == status.project_id,
                        IssueStatus.is_default.is_(True),
                        IssueStatus.id != status.id,
                    )
                )
                if not other_defaults:
                    # README §6.3: every scope keeps at least one default
                    # status (M-6). Deleting the last one would fail every
                    # future issue creation in the scope with 422 — refuse
                    # the delete instead, in the status_in_use 409 style.
                    raise ConflictError(
                        "cannot delete the last default status of a scope",
                        code="last_default_status",
                        details={"status_id": str(status_id)},
                    )
            await session.delete(status)
            try:
                await session.flush()
            except IntegrityError as exc:
                # Defense in depth: an issue can grab the status between the
                # reference check above and the DELETE.
                raise ConflictError(
                    "status is still referenced by issues; migrate them first",
                    code="status_in_use",
                    details={"status_id": str(status_id)},
                ) from exc
            return {"id": str(status_id), "deleted": True}


__all__ = [
    "CATEGORY_RESCAN_BATCH_SIZE",
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
