"""Member roster service (member.md §3 — feature layer over the roster tables).

Each public method owns its transaction (``session_factory() + begin()``) so it
can be exercised directly from tests without route plumbing. Tenant-bound
transactions set the ``mesh.workspace_id`` GUC up front so every read/write is
correct under RLS (restricted app role) and without it (owner role).

Authorisation model (member.md §3.4): reading the roster needs workspace
membership; role/status changes and adding/removing need ``admin``; a member may
always edit their OWN ``display_override``. Last-owner and agent-owner
protections are server-enforced (UI disabling is never trusted, §5.3).

``members.id`` is the system-wide reference key (README §6.1); removal is a soft
``status='removed'`` so historic references never dangle (member.md §4.4).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import decode_cursor, encode_cursor
from mesh.auth.audit import write_audit
from mesh.auth.rbac import role_satisfies
from mesh.auth.realtime import broadcast_session_revoked
from mesh.db.models.agent import Agent
from mesh.db.models.member import (
    MEMBER_ROLE_VALUES,
    Member,
    MemberProjectAccess,
)
from mesh.db.models.project import Project
from mesh.db.models.user import Session, User
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.member.display import resolve_display_name
from mesh.member.owner_guard import LAST_OWNER_CODE, lock_active_owner_set
from mesh.member.reassign import DEFAULT_REASSIGN_STATUSES, IssueReassigner, NullReassigner
from mesh.outbox.service import emit_realtime
from mesh.validation import LIKE_ESCAPE_CHAR, escape_like
from mesh.workspace.service import WORKSPACE_CHANNEL

_NOT_FOUND = "member not found"
DISPLAY_OVERRIDE_MAX = 80
MEMBER_TYPE_FILTERS = ("all", "human", "agent")
STATUS_FILTERS = ("default", "all", "active", "disabled", "removed")
MEMBER_PROJECT_PERMISSIONS = ("read", "write")


class _Unset:
    """Sentinel distinguishing 'field omitted' from 'field set to null'."""

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<unset>"


UNSET = _Unset()


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _channel(workspace_id: uuid.UUID) -> str:
    return WORKSPACE_CHANNEL.format(workspace_id=workspace_id)


def _human_profile(user: User | None) -> dict | None:
    # users carries a single name column (auth.md owns the table); the spec's
    # ``profile.full_name`` maps onto ``users.display_name`` (member.md §2.4).
    if user is None:
        return None
    return {
        "id": user.id,
        "full_name": user.display_name,
        "email": user.email,
        "avatar_url": user.avatar_url,
    }


def _agent_profile(member: Member, agent: Agent | None = None) -> dict:
    # Roster rows render the agent profile via the agents table (agent.md
    # owns it); a missing agents row (defensive only — the composite FK
    # guarantees it exists) falls back to a null profile body.
    if agent is None:
        return {
            "id": member.agent_id,
            "name": None,
            "description": None,
            "avatar_url": None,
            "is_active": None,
            "role_tag": None,
            "lifecycle_status": None,
        }
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.bio,
        "avatar_url": agent.avatar_url,
        "is_active": agent.lifecycle_status == "active" and agent.deleted_at is None,
        # H-F1:roster 行需要的生命周期与角色标签(§4.2/§4.5/§4.9)。
        "role_tag": agent.role_tag,
        "lifecycle_status": agent.lifecycle_status,
    }


@dataclass(frozen=True)
class MemberPatch:
    """A PATCH members/{id} request — every field optional (unset = keep)."""

    role: str | None = None
    status: str | None = None
    display_override: str | None | _Unset = UNSET


class MemberService:
    """Stateless orchestrator over the member roster tables."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        reassigner: IssueReassigner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._reassigner: IssueReassigner = reassigner or NullReassigner()
        self._clock = clock

    # -- serialization ----------------------------------------------------------

    def render_row(self, member: Member, user: User | None, agent: Agent | None = None) -> dict:
        """Render one roster list item (member.md §3.2)."""
        return {
            "id": member.id,
            "member_type": member.member_type,
            "role": member.role,
            "status": member.status,
            "display_name": resolve_display_name(
                member=member, user=user, agent_name=agent.name if agent is not None else None
            ),
            "joined_at": member.joined_at,
            "profile": (
                _human_profile(user)
                if member.member_type == "human"
                else _agent_profile(member, agent)
            ),
        }

    # -- M-list: roster query + filter projections ------------------------------

    async def list_members(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        member_type: str = "all",
        status: str = "default",
        role: str | None = None,
        q: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        if member_type not in MEMBER_TYPE_FILTERS:
            raise ValidationError(
                "invalid member_type filter",
                details={"member_type": member_type, "allowed": list(MEMBER_TYPE_FILTERS)},
            )
        if status not in STATUS_FILTERS:
            raise ValidationError(
                "invalid status filter",
                details={"status": status, "allowed": list(STATUS_FILTERS)},
            )
        if role is not None and role not in MEMBER_ROLE_VALUES:
            raise ValidationError(
                "invalid role filter",
                details={"role": role, "allowed": list(MEMBER_ROLE_VALUES)},
            )
        limit = max(1, min(limit, 100))

        # NULL-safe sort key: roster orders by joined_at; created_at stands in for
        # the (rare) row without a joined_at so keyset pagination never drops rows.
        sort_expr = func.coalesce(Member.joined_at, Member.created_at)
        stmt = (
            select(Member, User, Agent)
            .outerjoin(User, Member.user_id == User.id)
            .outerjoin(Agent, Member.agent_id == Agent.id)
            .where(Member.workspace_id == workspace_id)
        )
        if not role_satisfies(actor.role, "agent:manage"):
            stmt = stmt.where(
                or_(
                    Member.member_type != "agent",
                    Agent.visibility == "workspace",
                    Agent.owner_user_id == actor.user_id,
                )
            )
        if member_type != "all":
            stmt = stmt.where(Member.member_type == member_type)
        if status == "default":
            # removed is a soft terminal state hidden from the default roster.
            stmt = stmt.where(Member.status.in_(("active", "disabled")))
        elif status != "all":
            stmt = stmt.where(Member.status == status)
        if role is not None:
            stmt = stmt.where(Member.role == role)
        if q:
            # Escaped so user-supplied wildcards match literally (member.md
            # §5.1): a raw ``q=%`` would otherwise hit the whole roster.
            pattern = f"%{escape_like(q)}%"
            stmt = stmt.where(
                or_(
                    Member.display_override.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                    User.display_name.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                    User.email.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                    Agent.name.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                    Agent.role_tag.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                )
            )
        if cursor is not None:
            position = decode_cursor(cursor)
            stmt = stmt.where(
                tuple_(sort_expr, Member.id) > (position.sort_value, position.id)
            )
        stmt = stmt.order_by(sort_expr.asc(), Member.id.asc()).limit(limit + 1)

        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            rows = (await session.execute(stmt)).all()

        items = [self.render_row(member, user, agent) for member, user, agent in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last_member, _, _ = rows[limit - 1]
            sort_value = last_member.joined_at or last_member.created_at
            next_cursor = encode_cursor(sort_value, last_member.id)
        return items, next_cursor

    # -- M-detail ----------------------------------------------------------------

    async def get_member(
        self, *, actor: Member, workspace_id: uuid.UUID, member_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            member, user = await self._load_row(session, workspace_id, member_id)
            agent = await self._agent_for(session, member)
            if agent is not None and (
                agent.visibility == "private"
                and agent.owner_user_id != actor.user_id
                and not role_satisfies(actor.role, "agent:manage")
            ):
                raise NotFoundError(_NOT_FOUND)
            open_issues = await self._reassigner.open_issues_assigned(
                session, workspace_id=workspace_id, member_id=member_id
            )
            detail = self.render_row(member, user, agent)
            detail.update(
                {
                    "display_override": member.display_override,
                    "disabled_at": member.disabled_at,
                    "counts": {"open_issues_assigned": open_issues},
                }
            )
        return detail

    @staticmethod
    async def _agent_for(session: AsyncSession, member: Member) -> Agent | None:
        """Resolve the agents row for an agent roster entry (None for humans)."""
        if member.member_type != "agent" or member.agent_id is None:
            return None
        return await session.scalar(
            select(Agent).where(
                Agent.workspace_id == member.workspace_id, Agent.id == member.agent_id
            )
        )

    async def _load_row(
        self, session: AsyncSession, workspace_id: uuid.UUID, member_id: uuid.UUID
    ) -> tuple[Member, User | None]:
        row = (
            await session.execute(
                select(Member, User)
                .outerjoin(User, Member.user_id == User.id)
                .where(Member.workspace_id == workspace_id, Member.id == member_id)
            )
        ).first()
        if row is None:
            raise NotFoundError(_NOT_FOUND)
        member, user = row
        return member, user

    # -- M-add: join an existing identity to the roster -------------------------

    async def add_member(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        member_type: str,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        role: str = "member",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        if member_type not in ("human", "agent"):
            raise ValidationError(
                "invalid member_type", details={"member_type": member_type}
            )
        if role not in MEMBER_ROLE_VALUES:
            raise ValidationError("invalid role", details={"role": role})

        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            self._require_manage(actor)

            if member_type == "agent":
                # The agents table + creation flow land with the agent.md
                # increment; the endpoint surface exists but agent onboarding is
                # not available yet (member.md §1.3, leader scope note).
                raise BusinessRuleError(
                    "agent onboarding is not available yet",
                    code="agents_not_available",
                )

            if user_id is None:
                raise ValidationError(
                    "user_id is required to add a human member",
                    details={"field": "user_id"},
                )
            # users is a global table (no workspace_id, exempt from RLS).
            user = await session.scalar(select(User).where(User.id == user_id))
            if user is None:
                raise NotFoundError("user not found")
            if user.status != "active":
                raise BusinessRuleError(
                    "cannot add a user who is not active",
                    code="user_not_active",
                    details={"status": user.status},
                )
            if role == "owner" and member_type == "agent":
                raise ConflictError(
                    "agents cannot hold the owner role", code="agent_owner_not_allowed"
                )

            existing = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id, Member.user_id == user_id
                )
            )
            now = _now(self._clock)
            if existing is not None:
                if existing.status == "active":
                    raise ConflictError(
                        "user is already a member of this workspace",
                        code="already_member",
                    )
                # Re-activate a disabled/removed row with the fresh admin grant
                # (mirrors invitation acceptance — one row per user/workspace).
                existing.status = "active"
                existing.role = role
                existing.disabled_at = None
                if existing.joined_at is None:
                    existing.joined_at = now
                existing.updated_at = now
                member = existing
            else:
                member = Member(
                    workspace_id=workspace_id,
                    member_type="human",
                    user_id=user_id,
                    role=role,
                    joined_at=now,
                )
                session.add(member)
            await session.flush()

            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_channel(workspace_id),
                event="member.added",
                data={
                    "member_id": str(member.id),
                    "member_type": "human",
                    "role": member.role,
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="member.added",
                resource_type="member",
                resource_id=member.id,
                metadata={"target_user_id": str(user_id), "role": member.role},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            # Onboarding checklist seeding for new human members (onboarding.md
            # §3.5 R3 main path): same-transaction seed + full historical
            # reconcile (idempotent; agent adds never reach this point).
            from mesh.onboarding.service import seed_for_new_member

            await seed_for_new_member(session, workspace_id=workspace_id, member=member)
            result = self.render_row(member, user)
        return result

    # -- M-update: role / status / display_override -----------------------------

    async def update_member(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
        patch: MemberPatch,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        # Role change reuses the workspace-owned, audited + evented path verbatim
        # (single source for last-owner / agent-owner protection).
        if patch.role is not None:
            from mesh.workspace.members import change_member_role

            await change_member_role(
                self._factory,
                actor=actor,
                workspace_id=workspace_id,
                member_id=member_id,
                new_role=patch.role,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        has_status = patch.status is not None
        has_display = not isinstance(patch.display_override, _Unset)
        if not has_status and not has_display:
            # Role-only (or no-op) change: return the current detail view.
            return await self.get_member(
                actor=actor, workspace_id=workspace_id, member_id=member_id
            )

        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            member, user = await self._load_row(session, workspace_id, member_id)
            if member.status == "removed":
                raise NotFoundError(_NOT_FOUND)

            changes: dict = {}
            audit_action = "member.updated"
            if has_status:
                self._require_manage(actor)
                new_status = patch.status
                if new_status not in ("active", "disabled"):
                    raise ValidationError(
                        "status may only be set to active or disabled "
                        "(removal uses DELETE)",
                        details={"status": new_status},
                    )
                if new_status != member.status:
                    # Lock the target + active owners and re-read the target
                    # from post-lock state (owner_guard.py — TOCTOU): the role
                    # gate and owner count below come from the locked snapshot,
                    # so a concurrent promotion of the target cannot slip past
                    # an unguarded disable, and a concurrent removal becomes a
                    # 404 instead of a double write.
                    active_owners, _locked = await lock_active_owner_set(
                        session, workspace_id=workspace_id, target_id=member.id
                    )
                    if member.status == "removed":
                        raise NotFoundError(_NOT_FOUND)
                    if new_status != member.status:
                        if new_status == "disabled" and member.role == "owner":
                            # Owner invariant (member.md §3.3/§5.3): disabling
                            # the last ACTIVE owner orphans the workspace —
                            # entry is gated on status='active', so zero active
                            # owners can only be fixed by DB intervention.
                            # Same 409 as the demote/remove paths.
                            if active_owners <= 1:
                                raise ConflictError(
                                    "cannot disable the last owner of the workspace",
                                    code=LAST_OWNER_CODE,
                                )
                        now = _now(self._clock)
                        member.status = new_status
                        member.disabled_at = now if new_status == "disabled" else None
                        member.updated_at = now
                        if new_status == "disabled":
                            # MES-78 LOW-1: disabling revokes the member's
                            # workspace-bound cli sessions (same transaction).
                            await self._revoke_member_cli_sessions(
                                session,
                                workspace_id=workspace_id,
                                user_id=member.user_id,
                                now=now,
                            )
                        changes["status"] = new_status
                        audit_action = "member.status_changed"
            if has_display:
                is_self = actor.id == member.id
                if not is_self:
                    self._require_manage(actor)
                new_display = patch.display_override
                if new_display is not None:
                    new_display = new_display.strip()
                    if not 1 <= len(new_display) <= DISPLAY_OVERRIDE_MAX:
                        raise ValidationError(
                            f"display_override must be 1-{DISPLAY_OVERRIDE_MAX} characters",
                            details={"display_override": new_display[:64]},
                        )
                if new_display != member.display_override:
                    member.display_override = new_display
                    member.updated_at = _now(self._clock)
                    changes["display_override"] = new_display
                    if audit_action == "member.updated":
                        audit_action = "member.profile_updated"

            if not changes:
                # §6.9: no field change → no event, no audit.
                result = self.render_row(member, user)
            else:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_channel(workspace_id),
                    event="member.updated",
                    data={"member_id": str(member.id), "changes": changes},
                )
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_member_id=actor.id,
                    actor_kind="member",
                    action=audit_action,
                    resource_type="member",
                    resource_id=member.id,
                    metadata={"changes": changes},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                result = self.render_row(member, user)
        return result

    # -- M-remove: soft removal + optional reassignment -------------------------

    async def _revoke_member_cli_sessions(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID | None,
        now: datetime,
    ) -> int:
        """MES-78 LOW-1 / auth.md §1.1 撤销联动: member removal or disable
        revokes every cli session bound to THIS workspace for the member's
        user, in the SAME transaction — no "stale fixed scope silently
        revives on re-invite" path: re-invitation requires a fresh device
        approval (old refresh stays revoked → 401). Broadcast so live
        connections drop at once (§3.7 ``session.revoked``)."""
        if user_id is None:  # agent members carry no user sessions
            return 0
        result = await session.execute(
            update(Session)
            .where(Session.user_id == user_id)
            .where(Session.workspace_id == workspace_id)
            .where(Session.type == "cli")
            .where(Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        revoked = result.rowcount or 0
        if revoked:
            # Broadcast on the affected workspace's channel directly — the
            # user-global resolver would skip this membership (already
            # removed/disabled in this transaction), and the revocation is
            # workspace-scoped anyway (§3.7 session.revoked).
            await broadcast_session_revoked(
                session, user_id=user_id, workspace_ids=[workspace_id]
            )
        return revoked

    async def remove_member(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
        reassign_to: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            self._require_manage(actor)
            member, _user = await self._load_row(session, workspace_id, member_id)
            if member.status == "removed":
                raise NotFoundError(_NOT_FOUND)

            # Lock the target + active owners and decide from post-lock state
            # (owner_guard.py — TOCTOU): a concurrent promotion of the target
            # cannot slip past the guard, and a concurrent removal becomes a
            # 404 instead of a double removal.
            active_owners, _locked = await lock_active_owner_set(
                session, workspace_id=workspace_id, target_id=member.id
            )
            if member.status == "removed":
                raise NotFoundError(_NOT_FOUND)
            if member.role == "owner" and member.status == "active" and active_owners <= 1:
                # Removing a disabled owner cannot reduce the ACTIVE count;
                # only an active owner needs the last-owner protection.
                raise ConflictError(
                    "cannot remove the last owner of the workspace",
                    code=LAST_OWNER_CODE,
                )

            reassigned = 0
            if reassign_to is not None:
                await self._require_reassign_target(
                    session, workspace_id=workspace_id, target_id=reassign_to,
                    exclude_member_id=member_id,
                )
                reassigned = await self._reassigner.reassign(
                    session,
                    workspace_id=workspace_id,
                    from_member_id=member_id,
                    to_member_id=reassign_to,
                    statuses=list(DEFAULT_REASSIGN_STATUSES),
                )

            member.status = "removed"
            member.updated_at = _now(self._clock)
            # MES-78 LOW-1: revoke the removed member's workspace-bound cli
            # sessions in this same transaction (+ realtime broadcast).
            await self._revoke_member_cli_sessions(
                session,
                workspace_id=workspace_id,
                user_id=member.user_id,
                now=member.updated_at,
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_channel(workspace_id),
                event="member.removed",
                data={"member_id": str(member.id)},
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="member.removed",
                resource_type="member",
                resource_id=member.id,
                metadata={
                    "reassigned_issues": reassigned,
                    "reassign_to": str(reassign_to) if reassign_to else None,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return {"removed": True, "reassigned_issues": reassigned}

    # -- M-reassign: bulk transfer of open issues -------------------------------

    async def reassign_issues(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        from_member_id: uuid.UUID,
        to_member_id: uuid.UUID,
        statuses: list[str] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        # None = default open-issue statuses; an explicit empty list is invalid
        # (reassigning "no statuses" is meaningless).
        effective_statuses = (
            list(statuses) if statuses is not None else list(DEFAULT_REASSIGN_STATUSES)
        )
        if not effective_statuses or not all(
            isinstance(s, str) and s for s in effective_statuses
        ):
            raise ValidationError(
                "statuses must be a non-empty list of strings",
                details={"statuses": effective_statuses},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            self._require_manage(actor)
            source = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id, Member.id == from_member_id
                )
            )
            if source is None:
                raise NotFoundError(_NOT_FOUND)
            await self._require_reassign_target(
                session, workspace_id=workspace_id, target_id=to_member_id,
                exclude_member_id=from_member_id,
            )
            reassigned = await self._reassigner.reassign(
                session,
                workspace_id=workspace_id,
                from_member_id=from_member_id,
                to_member_id=to_member_id,
                statuses=effective_statuses,
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="member.reassigned",
                resource_type="member",
                resource_id=from_member_id,
                metadata={
                    "from_member_id": str(from_member_id),
                    "to_member_id": str(to_member_id),
                    "reassigned_issues": reassigned,
                    "statuses": effective_statuses,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return {"reassigned_issues": reassigned}

    async def _require_reassign_target(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        target_id: uuid.UUID,
        exclude_member_id: uuid.UUID,
    ) -> None:
        if target_id == exclude_member_id:
            raise BusinessRuleError(
                "cannot reassign issues to the member being changed",
                code="reassign_target_invalid",
            )
        target = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id, Member.id == target_id
            )
        )
        if target is None or target.status != "active":
            raise BusinessRuleError(
                "reassignment target must be an active member of this workspace",
                code="reassign_target_invalid",
            )

    # -- M-agents: available agents (interface surface, agents table deferred) ---

    async def list_available_agents(
        self, *, actor: Member, workspace_id: uuid.UUID
    ) -> tuple[list[dict], str | None]:
        self._require_manage(actor)
        # Active, non-deleted agents assignable from this roster (the picker
        # projection of README §6.12's single entry point; creation still
        # lives ONLY behind the roster [+ New Agent] wizard — agent.md §4.2).
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            rows = (
                await session.execute(
                    select(Member, Agent)
                    .join(Agent, Member.agent_id == Agent.id)
                    .where(
                        Member.workspace_id == workspace_id,
                        Member.member_type == "agent",
                        Member.status == "active",
                        Agent.deleted_at.is_(None),
                        Agent.lifecycle_status == "active",
                    )
                    .order_by(Agent.created_at.asc(), Agent.id.asc())
                )
            ).all()
        return [self.render_row(member, None, agent) for member, agent in rows], None

    # -- M12: guest project-level visibility ------------------------------------

    async def list_project_access(
        self, *, actor: Member, workspace_id: uuid.UUID, member_id: uuid.UUID
    ) -> list[dict]:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            self._require_manage(actor)
            await self._load_row(session, workspace_id, member_id)
            rows = (
                await session.execute(
                    select(MemberProjectAccess)
                    .where(
                        MemberProjectAccess.workspace_id == workspace_id,
                        MemberProjectAccess.member_id == member_id,
                    )
                    .order_by(MemberProjectAccess.created_at.asc())
                )
            ).scalars().all()
        return [self._access_to_dict(row) for row in rows]

    async def grant_project_access(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
        project_id: uuid.UUID,
        permission: str = "read",
    ) -> dict:
        if permission not in MEMBER_PROJECT_PERMISSIONS:
            raise ValidationError(
                "permission must be read or write",
                details={"permission": permission, "allowed": list(MEMBER_PROJECT_PERMISSIONS)},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            self._require_manage(actor)
            member, _user = await self._load_row(session, workspace_id, member_id)
            if member.role != "guest":
                # Project sharing only applies to guests; other roles' visibility
                # is decided by their role (member.md §3.1 note).
                raise BusinessRuleError(
                    "project sharing only applies to guest members",
                    code="not_guest_member",
                    details={"role": member.role},
                )
            # The composite FK to projects(workspace_id, id) is live since the
            # project.md increment — validate the project exists (and is not
            # soft-deleted) so grants fail with a clean 404, not an FK error.
            exists = await session.scalar(
                select(Project.id).where(
                    Project.id == project_id,
                    Project.workspace_id == workspace_id,
                    Project.deleted_at.is_(None),
                )
            )
            if exists is None:
                raise NotFoundError("project not found")
            stmt = (
                pg_insert(MemberProjectAccess)
                .values(
                    workspace_id=workspace_id,
                    member_id=member_id,
                    project_id=project_id,
                    permission=permission,
                )
                .on_conflict_do_update(
                    index_elements=["member_id", "project_id"],
                    set_={"permission": permission, "updated_at": _now(self._clock)},
                )
                .returning(MemberProjectAccess)
            )
            row = (await session.execute(stmt)).scalars().one()
            result = self._access_to_dict(row)
        return result

    async def revoke_project_access(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            self._require_manage(actor)
            row = await session.scalar(
                select(MemberProjectAccess).where(
                    MemberProjectAccess.workspace_id == workspace_id,
                    MemberProjectAccess.member_id == member_id,
                    MemberProjectAccess.project_id == project_id,
                )
            )
            if row is None:
                return {"revoked": False}
            await session.delete(row)
        return {"revoked": True}

    @staticmethod
    def _access_to_dict(row: MemberProjectAccess) -> dict:
        return {
            "id": row.id,
            "member_id": row.member_id,
            "project_id": row.project_id,
            "permission": row.permission,
            "created_at": row.created_at,
        }

    # -- /users/me: memberships across workspaces -------------------------------

    async def list_user_memberships(self, *, user: User) -> list[dict]:
        query = (
            "SELECT m.workspace_id, m.role, m.status, m.joined_at, "
            "w.name AS workspace_name, w.slug AS workspace_slug "
            "FROM mesh_my_workspaces(:uid) m "
            "JOIN workspaces w ON w.id = m.workspace_id "
            "WHERE w.deleted_at IS NULL "
            "ORDER BY m.joined_at ASC NULLS LAST, m.workspace_id ASC"
        )
        async with self._factory() as session:
            rows = (await session.execute(text(query), {"uid": user.id})).all()
        return [
            {
                "workspace_id": row.workspace_id,
                "workspace_name": row.workspace_name,
                "workspace_slug": row.workspace_slug,
                "role": row.role,
                "status": row.status,
                "joined_at": row.joined_at,
            }
            for row in rows
        ]

    # -- guards ------------------------------------------------------------------

    @staticmethod
    def _require_manage(actor: Member) -> None:
        if not role_satisfies(actor.role, "workspace:manage_members"):
            raise ForbiddenError("managing members requires the admin role")


__all__ = [
    "DISPLAY_OVERRIDE_MAX",
    "MEMBER_TYPE_FILTERS",
    "MemberPatch",
    "MemberService",
    "STATUS_FILTERS",
    "UNSET",
]
