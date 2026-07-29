"""Invitation service — link lifecycle, caps hardening, atomic accept.

workspace.md §2.3/§2.4/§3.2/§4.4, README §9 T1/T11.

Link lifecycle (``workspace_invitations.status``) and redemption records
(``workspace_invitation_redemptions``) are SEPARATED: a link only ever moves
between active/revoked/expired/exhausted — there is no pending/accepted state.
Each acceptance atomically increments ``used_count`` with a conditional UPDATE
(no application-level check-then-write), inserts one redemption row and one
member row, all in a single transaction.

Tokens are stored as SHA-256 hashes only; the plaintext exists only in the
create response (``invite_link``) and never afterwards.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth.audit import write_audit
from mesh.db.constraints import violates as _violates
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceInvitationRedemption,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime
from mesh.workspace.service import WORKSPACE_CHANNEL

TOKEN_LITERAL_PREFIX = "invtk_"
# Display prefix length — long enough to locate an invitation in a list,
# short enough to stay non-secret (the hash protects the full token).
TOKEN_DISPLAY_PREFIX_LENGTH = 14

INVITATION_ROLES = ("admin", "member", "guest")  # owner is NOT invitable (§2.3)

# Defaults when the caller does not specify (MES-4: never NULL / unlimited).
DEFAULT_MAX_USES = 10
DEFAULT_LIFETIME_HOURS = 168  # 7 days
# Fallback caps when the workspace has not configured its own (§2.3, LOW-2).
FALLBACK_MAX_USES_CAP = 100
FALLBACK_LIFETIME_HOURS_CAP = 720  # 30 days
MAX_BATCH_EMAILS = 50


class _DuplicateAcceptance(Exception):
    """Internal signal: UNIQUE(invitation_id, user_id) hit — the transaction
    must roll back (undoing the used_count increment) and the call becomes a
    no-op returning the existing member (§3.2 idempotency)."""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> str:
    # 24 bytes of entropy → 32 urlsafe chars behind the literal prefix.
    return TOKEN_LITERAL_PREFIX + secrets.token_urlsafe(24)


def _normalize_email(raw: str) -> str:
    try:
        info = validate_email(raw, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValidationError(
            "invalid email address", details={"email": str(raw)[:128]}
        ) from exc
    return info.normalized.lower()


def _effective_status(row: WorkspaceInvitation, *, now: datetime) -> str:
    """Lazy expiry: an active link past its deadline reads as expired (§4.4)."""
    if row.status == "active" and row.expires_at <= now:
        return "expired"
    return row.status


def _invitation_to_dict(row: WorkspaceInvitation, *, invite_link: str | None = None,
                        status_override: str | None = None) -> dict:
    data = {
        "id": row.id,
        "email": row.email,
        "role": row.role,
        "status": status_override or row.status,
        "max_uses": row.max_uses,
        "used_count": row.used_count,
        "expires_at": row.expires_at,
        "token_prefix": row.token_prefix,
        "invited_by": row.invited_by,
        "created_at": row.created_at,
    }
    if invite_link is not None:
        data["invite_link"] = invite_link
    return data


class InvitationService:
    """Stateless orchestrator over the invitation tables."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    # -- create (admin) ----------------------------------------------------------

    async def create_invitations(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        emails: list[str] | None = None,
        role: str = "member",
        max_uses: int | None = None,
        expires_in_hours: int | None = None,
    ) -> list[dict]:
        """Create one link invitation, or one directed invitation per email.

        Explicit ``max_uses`` / ``expires_in_hours`` are bounded by the
        workspace-configurable caps (LOW-2); UNSPECIFIED values take the
        defaults (10 / 168h) and are never cap-rejected (§5.1).
        """
        if role not in INVITATION_ROLES:
            raise ValidationError(
                "invitation role must be one of admin/member/guest (owner is not invitable)",
                details={"role": role},
            )
        if emails is not None:
            if len(emails) > MAX_BATCH_EMAILS:
                raise ValidationError(
                    f"at most {MAX_BATCH_EMAILS} emails per invitation batch",
                    details={"count": len(emails)},
                )
            normalized = [_normalize_email(email) for email in emails]
        else:
            normalized = [None]  # link mode: a single multi-use invitation

        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            settings = (
                await session.scalar(
                    select(Workspace.settings).where(Workspace.id == workspace_id)
                )
                or {}
            )
            uses_cap = settings.get("invitation_max_uses_cap", FALLBACK_MAX_USES_CAP)
            hours_cap = settings.get(
                "invitation_max_lifetime_hours_cap", FALLBACK_LIFETIME_HOURS_CAP
            )

            if max_uses is not None and max_uses > uses_cap:
                raise BusinessRuleError(
                    "max_uses exceeds the workspace-configured cap",
                    code="invitation_limits_exceeded",
                    details={"max_uses": max_uses, "cap": uses_cap},
                )
            if expires_in_hours is not None and expires_in_hours > hours_cap:
                raise BusinessRuleError(
                    "expires_in_hours exceeds the workspace-configured cap",
                    code="invitation_limits_exceeded",
                    details={"expires_in_hours": expires_in_hours, "cap": hours_cap},
                )
            effective_max_uses = max_uses if max_uses is not None else DEFAULT_MAX_USES
            effective_hours = (
                expires_in_hours if expires_in_hours is not None else DEFAULT_LIFETIME_HOURS
            )
            expires_at = datetime.now(UTC) + timedelta(hours=effective_hours)

            results: list[dict] = []
            for email in normalized:
                token = _new_token()
                invitation = WorkspaceInvitation(
                    workspace_id=workspace_id,
                    email=email,
                    token_hash=_hash_token(token),
                    token_prefix=token[:TOKEN_DISPLAY_PREFIX_LENGTH],
                    role=role,
                    invited_by=actor.id,
                    max_uses=effective_max_uses,
                    expires_at=expires_at,
                )
                session.add(invitation)
                try:
                    await session.flush()
                except IntegrityError as exc:
                    if _violates(exc, "uq_ws_invitations_active_email"):
                        raise ConflictError(
                            "an active invitation for this email already exists",
                            code="conflict",
                            details={"email": email},
                        ) from exc
                    raise
                results.append(
                    _invitation_to_dict(
                        invitation, invite_link=f"/invite/{token}"
                    )
                )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="invitation.created",
                resource_type="workspace_invitation",
                metadata={"count": len(results), "role": role},
            )
        return results

    # -- list (admin) --------------------------------------------------------------

    async def list_invitations(
        self, *, workspace_id: uuid.UUID, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[dict], str | None]:
        """List invitations (no token material ever leaves the server)."""
        from mesh.api.pagination import decode_cursor, encode_cursor

        limit = max(1, min(limit, 100))
        stmt = select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id
        )
        if cursor is not None:
            position = decode_cursor(cursor)
            stmt = stmt.where(
                tuple_(
                    WorkspaceInvitation.created_at, WorkspaceInvitation.id
                ) < (position.sort_value, position.id)
            )
        stmt = stmt.order_by(
            WorkspaceInvitation.created_at.desc(), WorkspaceInvitation.id.desc()
        ).limit(limit + 1)

        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            rows = (await session.execute(stmt)).scalars().all()

        now = datetime.now(UTC)
        items = [
            _invitation_to_dict(row, status_override=_effective_status(row, now=now))
            for row in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.created_at, last.id)
        return items, next_cursor

    # -- revoke (admin) --------------------------------------------------------------

    async def revoke_invitation(
        self, *, actor: Member, workspace_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            invitation = await session.scalar(
                select(WorkspaceInvitation).where(
                    WorkspaceInvitation.workspace_id == workspace_id,
                    WorkspaceInvitation.id == invitation_id,
                )
            )
            if invitation is None:
                raise NotFoundError("invitation not found")
            if invitation.status != "active":
                raise ConflictError(
                    "invitation is no longer active",
                    code="conflict",
                    details={"status": invitation.status},
                )
            invitation.status = "revoked"
            invitation.updated_at = datetime.now(UTC)
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="invitation.revoked",
                resource_type="workspace_invitation",
                resource_id=invitation.id,
            )
            result = _invitation_to_dict(invitation)
        return result

    # -- preview (public, limited fields) ----------------------------------------------

    async def preview_invitation(self, *, token: str) -> dict:
        """Public landing-page preview. Never exposes ids beyond validity."""
        row = await self._invitation_by_token(token)
        if row is None:
            return {"valid": False, "reason": "not_found"}
        reason = self._invalid_reason(row)
        if reason is not None:
            return {"valid": False, "reason": reason}
        async with self._factory() as session:
            workspace = await session.scalar(
                select(Workspace).where(
                    Workspace.id == row.workspace_id, Workspace.deleted_at.is_(None)
                )
            )
        if workspace is None:
            # Links of a soft-deleted workspace are not usable; the tenant's
            # existence is not disclosed through the invitation surface.
            return {"valid": False, "reason": "not_found"}
        return {
            "valid": True,
            "workspace_name": workspace.name if workspace else None,
            "workspace_logo_url": workspace.logo_url if workspace else None,
            "role": row.role,
            "expires_at": row.expires_at,
        }

    # -- accept (logged-in) --------------------------------------------------------------

    async def accept_invitation(
        self,
        *,
        user: User,
        token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Join a workspace via an invitation token (§3.2, single transaction).

        Concurrency contract (T11): the conditional UPDATE pushes "usable /
        remaining / unexpired" into the WHERE clause, so a max_uses=1 link
        accepted by two users concurrently admits exactly one; used_count can
        never exceed max_uses. Repeat acceptance by the same user is a no-op
        returning the existing member (UNIQUE(invitation_id, user_id)).
        """
        row = await self._invitation_by_token(token)
        if row is None:
            raise BusinessRuleError(
                "invitation is not valid",
                code="invitation_invalid",
                details={"reason": "not_found"},
            )
        workspace_id = row.workspace_id
        try:
            return await self._accept_in_transaction(
                user=user, row=row, ip_address=ip_address, user_agent=user_agent
            )
        except _DuplicateAcceptance:
            # Same-user race lost: the increment rolled back; return the row
            # the winner created (no-op semantics, §3.2).
            return await self._existing_acceptance_response(
                workspace_id=workspace_id, user=user
            )

    async def _accept_in_transaction(
        self, *, user: User, row, ip_address: str | None, user_agent: str | None
    ) -> dict:
        workspace_id = row.workspace_id
        invitation_id = row.id
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)

            # A soft-deleted workspace is gone: its links must not admit
            # anyone (no member would ever pass the membership gate, and the
            # use would be silently consumed). Same 404-equivalent reason as
            # an unknown workspace — existence of a deleted tenant is not
            # disclosed through invitations.
            workspace = await session.scalar(
                select(Workspace).where(
                    Workspace.id == workspace_id, Workspace.deleted_at.is_(None)
                )
            )
            if workspace is None:
                raise BusinessRuleError(
                    "invitation is not valid",
                    code="invitation_invalid",
                    details={"reason": "not_found"},
                )

            # Idempotency fast path: this user already redeemed THIS link.
            existing = await self._existing_redemption_member(
                session, invitation_id=invitation_id, user_id=user.id
            )
            if existing is not None:
                return self._accept_response(existing, workspace)

            # Atomic conditional increment — the §3.2 SQL, verbatim contract.
            incremented = (
                await session.execute(
                    text(
                        "UPDATE workspace_invitations "
                        "SET used_count = used_count + 1, updated_at = now() "
                        "WHERE id = :invitation_id AND status = 'active' "
                        "AND used_count < max_uses "
                        "AND expires_at > now() "
                        "RETURNING used_count, max_uses, role"
                    ),
                    {"invitation_id": invitation_id},
                )
            ).first()
            if incremented is None:
                # Race-tolerant idempotency (§3.2/§5.1): when the winner of a
                # same-user concurrent accept committed after our fast-path
                # check, the link may now be exhausted — but THIS user's
                # redemption exists, so the call is a no-op, not a failure.
                raced = await self._existing_redemption_member(
                    session, invitation_id=invitation_id, user_id=user.id
                )
                if raced is not None:
                    return self._accept_response(raced, workspace)
                raise BusinessRuleError(
                    "invitation is not valid",
                    code="invitation_invalid",
                    details={"reason": await self._rejection_reason(session, invitation_id)},
                )
            used_count, max_uses, role = incremented

            # Roster entry: reuse an existing row for this user (one row per
            # user per workspace — uq_members_ws_user). A disabled/removed
            # row is reactivated by the fresh admin-issued grant (role taken
            # from the invitation) so the accepted use always yields access —
            # never a silently consumed slot.
            member = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id, Member.user_id == user.id
                )
            )
            member_created = False
            member_rejoined = False
            if member is None:
                member = Member(
                    workspace_id=workspace_id,
                    member_type="human",
                    user_id=user.id,
                    role=role,
                    joined_at=datetime.now(UTC),
                )
                session.add(member)
                await session.flush()
                member_created = True
            elif member.status != "active":
                member.status = "active"
                member.disabled_at = None
                member.role = role
                if member.joined_at is None:
                    member.joined_at = datetime.now(UTC)
                member_rejoined = True
            member_id = member.id
            member_role = member.role
            member_status = member.status

            # Search projection (§2.2): created or reactivated row.
            from mesh.search.projection import sync_member_search_name

            await session.flush()
            await sync_member_search_name(session, member_id)

            # Redemption record — the (invitation_id, user_id) unique index is
            # the idempotency backstop: a losing same-user race rolls the whole
            # transaction back (undoing the increment) and the caller returns
            # the winner's member (no-op).
            session.add(
                WorkspaceInvitationRedemption(
                    invitation_id=invitation_id,
                    user_id=user.id,
                    member_id=member_id,
                    workspace_id=workspace_id,
                )
            )
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_ws_inv_redemptions_inv_user"):
                    raise _DuplicateAcceptance() from exc
                raise

            if used_count >= max_uses:
                # Terminal link state — explicitly set in the same transaction.
                await session.execute(
                    text(
                        "UPDATE workspace_invitations SET status = 'exhausted' "
                        "WHERE id = :invitation_id"
                    ),
                    {"invitation_id": invitation_id},
                )

            channel = WORKSPACE_CHANNEL.format(workspace_id=workspace_id)
            if member_created or member_rejoined:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=channel,
                    event="member.added",
                    data={
                        "member_id": str(member_id),
                        "member_type": "human",
                        "role": member_role,
                    },
                )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=channel,
                event="invitation.redeemed",
                data={
                    "invitation_id": str(invitation_id),
                    "member_id": str(member_id),
                    "used_count": used_count,
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=member_id,
                actor_kind="member",
                action="invitation.accepted",
                resource_type="workspace_invitation",
                resource_id=invitation_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            # Onboarding checklist seeding for new human members (onboarding.md
            # §3.5 R3 main path): same-transaction seed + full historical
            # reconcile, so an invitee entering a mature workspace arrives with
            # steps already evidenced from history. Agent members are never
            # seeded; the call is idempotent for legacy/existing rows.
            from mesh.onboarding.service import seed_for_new_member

            await seed_for_new_member(session, workspace_id=workspace_id, member=member)
            response = self._accept_response(
                {"id": member_id, "role": member_role, "status": member_status},
                workspace,
            )
        return response

    # -- expiry sweep (worker) -------------------------------------------------------

    async def sweep_expired(self, *, workspace_id: uuid.UUID | None = None) -> int:
        """Flip past-due active links to expired (timed complement to the lazy
        checks, §4.4). Runs as the cross-tenant worker role; optionally scoped."""
        query = (
            "UPDATE workspace_invitations "
            "SET status = 'expired', updated_at = now() "
            "WHERE status = 'active' AND expires_at <= now() "
        )
        params: dict = {}
        if workspace_id is not None:
            query += "AND workspace_id = :ws "
            params["ws"] = workspace_id
        query += "RETURNING id"
        async with self._factory() as session, session.begin():
            if workspace_id is not None:
                await set_tenant_context(session, workspace_id)
            rows = (await session.execute(text(query), params)).all()
        return len(rows)

    # -- internals ------------------------------------------------------------------

    async def _invitation_by_token(self, token: str):
        """Resolve an invitation from its plaintext token.

        Goes through the SECURITY DEFINER function: the accepter has no tenant
        context yet (not a member), and the RLS policies stay fail-closed.
        """
        async with self._factory() as session:
            return (
                await session.execute(
                    text("SELECT * FROM mesh_invitation_by_token_hash(:h)"),
                    {"h": _hash_token(token)},
                )
            ).first()

    def _invalid_reason(self, row) -> str | None:
        if row.status != "active":
            return row.status  # revoked | exhausted | expired
        if row.expires_at <= datetime.now(UTC):
            return "expired"
        if row.used_count >= row.max_uses:
            return "exhausted"
        return None

    async def _rejection_reason(self, session: AsyncSession, invitation_id: uuid.UUID) -> str:
        """Distinguish expired/revoked/exhausted after a 0-row atomic UPDATE."""
        current = await session.scalar(
            select(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation_id)
        )
        if current is None:
            return "not_found"
        if current.status != "active":
            return current.status
        if current.expires_at <= datetime.now(UTC):
            return "expired"
        return "exhausted"

    async def _existing_redemption_member(
        self, session: AsyncSession, *, invitation_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict | None:
        row = (
            await session.execute(
                text(
                    "SELECT m.id, m.role, m.status "
                    "FROM workspace_invitation_redemptions r "
                    "JOIN members m ON m.id = r.member_id "
                    "WHERE r.invitation_id = :invitation_id AND r.user_id = :user_id"
                ),
                {"invitation_id": invitation_id, "user_id": user_id},
            )
        ).first()
        if row is None:
            return None
        return {"id": row.id, "role": row.role, "status": row.status}

    async def _existing_acceptance_response(
        self, *, workspace_id: uuid.UUID, user: User
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            member = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id, Member.user_id == user.id
                )
            )
            workspace = await session.scalar(
                select(Workspace).where(Workspace.id == workspace_id)
            )
        return self._accept_response(
            {
                "id": member.id if member else None,
                "role": member.role if member else None,
                "status": member.status if member else None,
            },
            workspace,
        )

    @staticmethod
    def _accept_response(member: dict, workspace) -> dict:
        return {
            "member": {
                "id": member["id"],
                "role": member["role"],
                "status": member["status"],
            },
            "workspace": {
                "id": workspace.id if workspace else None,
                "name": workspace.name if workspace else None,
                "slug": workspace.slug if workspace else None,
            },
        }
