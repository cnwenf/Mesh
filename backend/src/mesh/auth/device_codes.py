"""Device-code authorization service (auth.md §2.4.2 / §3.1.1, cli.md §3.2).

Server-side authority for CLI device login: code issuance (HMAC-SHA256 keyed
by the server pepper — NEVER bare SHA-256, the low-entropy user_code would
fall to offline dictionary attack), the irreversible state machine, the
browser-side confirm/approve/deny contract, and the single-consumption token
exchange with its fixed lock order (authorization row → roster row → consume
→ session, all one transaction — member removal / role change linearize on
the same roster row lock, so a stale "active" read can never mint a session).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth import security
from mesh.auth.audit import write_audit
from mesh.auth.rbac import PERMISSION_MATRIX
from mesh.auth.service import AuthService, TokenResult
from mesh.config import Settings
from mesh.db.models.member import Member
from mesh.db.models.user import DeviceAuthorization, Session, User
from mesh.db.models.workspace import Workspace
from mesh.errors import NotFoundError, UnauthorizedError, ValidationError

VERIFICATION_URI_PATH = "/device"
CLIENT_NAME = "Mesh CLI"

# Single-code brute-force ceiling (auth.md §2.4.2 / §5.5 ⑤): more than this
# many recorded violations against ONE code voids it (status='invalidated').
FAILED_ATTEMPTS_INVALIDATE_THRESHOLD = 5

ACTIVE_STATUSES = ("pending", "approved")

# Human-readable scope enumeration for the confirmation page (auth.md §3.1.1:
# full human-readable list of the INTERSECTED scopes is shown pre-approval).
SCOPE_DESCRIPTIONS: dict[str, str] = {
    "issue:read": "Read issues, comments and history",
    "issue:write": "Create and update issues, change status, move on boards",
    "comment:write": "Post and edit comments",
    "project:manage": "Manage projects, cycles and milestones",
    "agent:trigger": "Trigger agent runs",
    "agent:manage": "Manage agent configuration",
    "token:manage": "Manage access tokens",
    "workspace:settings": "Manage workspace settings",
    "workspace:manage_members": "Manage workspace members",
    "chat:write": "Chat with agents",
    "autopilot:manage": "Manage automation rules",
}


def describe_scope(scope: str) -> dict:
    return {"scope": scope, "description": SCOPE_DESCRIPTIONS.get(scope, scope)}


# Named device-flow error codes (auth.md §3.5 registry). All are 400-class
# except slow_down (429) — the CLI maps them to its exit-code contract.
class AuthorizationPendingError(ValidationError):
    code = "authorization_pending"
    message = "the device authorization is still pending"


class AccessDeniedError(ValidationError):
    code = "access_denied"
    message = "the device authorization was denied"


class ExpiredTokenError(ValidationError):
    code = "expired_token"
    message = "the device code has expired"


class InvalidGrantError(ValidationError):
    code = "invalid_grant"
    message = "the device code is unknown, consumed or invalidated"


@dataclass(frozen=True)
class ConsumedGrant:
    """The token endpoint's success payload (auth.md §3.1.1)."""

    tokens: TokenResult
    workspace_id: uuid.UUID
    workspace_slug: str
    scopes: list[str]


class DeviceCodeService:
    """Stateless orchestrator over ``device_authorizations`` + sessions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        auth_service: AuthService | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sf = session_factory
        self._settings = settings
        self._clock = clock
        self._auth = auth_service or AuthService(session_factory, settings, clock=clock)

    def _now(self) -> datetime:
        return self._clock() if self._clock is not None else datetime.now(UTC)

    def _pepper(self) -> str:
        # Fail closed: production startup already refuses a missing pepper
        # (validate_auth_settings); this guards dev misconfiguration at use.
        if not self._settings.device_code_pepper:
            raise ValidationError(
                "device code issuance is unavailable",
                code="internal_error",
                details={"reason": "device_code_pepper_not_configured"},
            )
        return self._settings.device_code_pepper

    def _hash(self, code: str) -> str:
        return security.hmac_token(code, self._pepper())

    # -- issuance --------------------------------------------------------------

    async def create_code(
        self,
        *,
        client_id: str,
        scopes: list[str] | None,
        ip_address: str | None = None,
    ) -> dict:
        """Issue a pending grant; plaintext codes exist only in this response."""
        now = self._now()
        ttl = self._settings.device_code_ttl
        device_code = security.generate_device_code()
        requested = sorted({s for s in (scopes or []) if s})

        async with self._sf() as session, session.begin():
            pepper = self._pepper()

            async def _active_taken(candidate: str) -> bool:
                # Partial unique index covers active codes only.
                existing = await session.scalar(
                    select(DeviceAuthorization.id).where(
                        DeviceAuthorization.user_code_hash
                        == security.hmac_token(candidate, pepper),
                        DeviceAuthorization.status.in_(ACTIVE_STATUSES),
                    )
                )
                return existing is not None

            user_code = await security.generate_user_code(_active_taken)
            row = DeviceAuthorization(
                device_code_hash=security.hmac_token(device_code, pepper),
                user_code_hash=security.hmac_token(user_code, pepper),
                status="pending",
                requested_scopes=requested,
                request_ip=ip_address,
                expires_at=now + ttl,
            )
            session.add(row)
            # Account-less audit (§2.6): actor falls into metadata.
            await write_audit(
                session,
                workspace_id=None,
                actor_member_id=None,
                actor_kind="system",
                action="auth.device_code_issued",
                resource_type="device_authorization",
                resource_id=row.id,
                metadata={
                    "client_id": client_id,
                    "request_ip": ip_address,
                    "requested_scopes": requested,
                },
                ip_address=ip_address,
            )
            await session.flush()
            grant_id = row.id

        return {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": VERIFICATION_URI_PATH,
            "verification_uri_complete": f"{VERIFICATION_URI_PATH}?user_code={user_code}",
            "expires_in": int(ttl.total_seconds()),
            "interval": self._settings.device_poll_interval,
            "_id": grant_id,  # internal convenience for tests/audit correlation
        }

    # -- confirmation page data --------------------------------------------------

    async def confirm_data(self, *, user_code: str, approver: User) -> dict:
        """Confirmation-page payload for a logged-in approver (GET /auth/device).

        A miss (unknown / consumed / expired) is the SAME generic 404 — the
        hit result must not probe code state (anti-enumeration, §3.1.1).
        """
        now = self._now()
        async with self._sf() as session:
            row = await session.scalar(
                select(DeviceAuthorization).where(
                    DeviceAuthorization.user_code_hash == self._hash(user_code),
                    DeviceAuthorization.status == "pending",
                    DeviceAuthorization.expires_at > now,
                )
            )
            if row is None:
                raise NotFoundError("code not found")
            requested = list(row.requested_scopes or [])
            # The approver's workspaces with their role — the page branches on
            # 0 / 1 / many (auth.md §3.1.1 workspace selection contract).
            member_rows = (
                (
                    await session.execute(
                        select(Member, Workspace)
                        .join(Workspace, Workspace.id == Member.workspace_id)
                        .where(
                            Member.user_id == approver.id,
                            Member.status == "active",
                            Member.member_type == "human",
                            Workspace.deleted_at.is_(None),
                        )
                    )
                )
                .all()
            )
            workspaces = [
                {
                    "id": member.workspace_id,
                    "slug": ws.slug,
                    "name": ws.name,
                    "my_role": member.role,
                }
                for member, ws in member_rows
            ]
            return {
                "client_name": CLIENT_NAME,
                "requested_scopes": [describe_scope(s) for s in requested],
                "workspaces": workspaces,
            }

    # -- approve / deny ----------------------------------------------------------

    async def _locate_web_session(
        self, session: AsyncSession, *, user_id: uuid.UUID, sid: uuid.UUID, now: datetime, for_update: bool
    ) -> Session:
        """Session-location invariant (auth.md §1.1): web, owner, alive."""
        stmt = select(Session).where(
            Session.id == sid,
            Session.user_id == user_id,
            Session.type == "web",
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await session.scalar(stmt)
        if row is None:
            raise UnauthorizedError("invalid or expired token")
        return row

    async def approve(
        self,
        *,
        user_code: str,
        workspace_id: uuid.UUID,
        approver_user_id: uuid.UUID,
        approver_sid: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Bind the entered user_code to the approver in the chosen workspace.

        The approval binds ONLY the code typed into the page (RFC 8628 §5.5
        phishing defence), the workspace must be one where the approver is an
        ACTIVE member (the roster row is the authority — FOR UPDATE so a
        concurrent removal linearizes here), and the approver's web session is
        re-verified under the location invariant (a revoked session inside the
        access TTL window cannot approve, R7-H1). Its ``authenticated_at`` is
        snapshotted — the device session's step-up eligibility can only come
        from the approver's REAL authentication moment (R6-H3).
        """
        now = self._now()
        async with self._sf() as session, session.begin():
            # 1) Approver session invariant FIRST, under lock — a revoked
            # session inside the access TTL window cannot approve (R7-H1), and
            # its authenticated_at is the snapshot source. The lock order
            # (sessions → members) matches the truncation/maintenance order so
            # approval can never deadlock with table maintenance.
            web_session = await self._locate_web_session(
                session, user_id=approver_user_id, sid=approver_sid, now=now, for_update=True
            )
            # 2) Roster authority: active member of the chosen workspace.
            member = await session.scalar(
                select(Member)
                .where(
                    Member.workspace_id == workspace_id,
                    Member.user_id == approver_user_id,
                    Member.status == "active",
                    Member.member_type == "human",
                )
                .with_for_update()
            )
            if member is None:
                from mesh.errors import ForbiddenError

                raise ForbiddenError("not an active member of this workspace")
            # 3) Server-enforced scope intersection (requested ∩ role perms).
            role_perms = {p for p, roles in PERMISSION_MATRIX.items() if member.role in roles}
            code_hash = self._hash(user_code)
            target = await session.scalar(
                select(DeviceAuthorization).where(
                    DeviceAuthorization.user_code_hash == code_hash,
                    DeviceAuthorization.status == "pending",
                    DeviceAuthorization.expires_at > now,
                )
            )
            if target is None:
                # Not pending: echo the current state of an existing grant
                # (idempotent — never overwrite a concurrent transition);
                # only a truly unknown code is a 404.
                any_row = await session.scalar(
                    select(DeviceAuthorization).where(
                        DeviceAuthorization.user_code_hash == code_hash
                    )
                )
                if any_row is None:
                    raise NotFoundError("code not found")
                return {"status": any_row.status}
            granted = sorted(set(target.requested_scopes or []) & role_perms)
            # 4) Atomic conditional transition — exactly one concurrent
            # approve/deny wins; the loser sees 0 rows and does NOT overwrite.
            result = await session.execute(
                update(DeviceAuthorization)
                .where(
                    DeviceAuthorization.id == target.id,
                    DeviceAuthorization.status == "pending",
                    DeviceAuthorization.expires_at > now,
                )
                .values(
                    status="approved",
                    granted_scopes=granted,
                    approved_by_user_id=approver_user_id,
                    workspace_id=workspace_id,
                    approved_authenticated_at=web_session.authenticated_at,
                    approved_at=now,
                )
            )
            if result.rowcount == 1:
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_member_id=member.id,
                    actor_kind="member",
                    action="auth.device_approved",
                    resource_type="device_authorization",
                    resource_id=target.id,
                    metadata={
                        "requested_scopes": list(target.requested_scopes or []),
                        "granted_scopes": granted,
                        "member_id": str(member.id),
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                return {"status": "approved", "granted_scopes": granted}
            # Lost the race (denied/expired/consumed concurrently) — echo the
            # current state without overwriting the other transition.
            await session.refresh(target)
            return {"status": target.status}

    async def deny(
        self,
        *,
        user_code: str,
        denier_user_id: uuid.UUID,
        denier_sid: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Deny the entered code (web session + same atomic conditional update)."""
        now = self._now()
        async with self._sf() as session, session.begin():
            await self._locate_web_session(
                session, user_id=denier_user_id, sid=denier_sid, now=now, for_update=False
            )
            code_hash = self._hash(user_code)
            target = await session.scalar(
                select(DeviceAuthorization).where(
                    DeviceAuthorization.user_code_hash == code_hash,
                    DeviceAuthorization.status == "pending",
                    DeviceAuthorization.expires_at > now,
                )
            )
            if target is None:
                # Idempotent echo of an already-transitioned grant; a truly
                # unknown code is the only 404.
                any_row = await session.scalar(
                    select(DeviceAuthorization).where(
                        DeviceAuthorization.user_code_hash == code_hash
                    )
                )
                if any_row is None:
                    raise NotFoundError("code not found")
                return {"status": any_row.status}
            result = await session.execute(
                update(DeviceAuthorization)
                .where(
                    DeviceAuthorization.id == target.id,
                    DeviceAuthorization.status == "pending",
                    DeviceAuthorization.expires_at > now,
                )
                .values(
                    status="denied",
                    approved_by_user_id=denier_user_id,  # records the denier
                    denied_at=now,
                )
            )
            if result.rowcount == 1:
                await write_audit(
                    session,
                    workspace_id=None,
                    actor_member_id=None,
                    actor_kind="system",
                    action="auth.device_denied",
                    resource_type="device_authorization",
                    resource_id=target.id,
                    metadata={"denier_user_id": str(denier_user_id)},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                return {"status": "denied"}
            await session.refresh(target)
            return {"status": target.status}

    # -- token exchange (poll → consume) -----------------------------------------

    async def exchange(
        self,
        *,
        device_code: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> ConsumedGrant:
        """Poll endpoint success path: fixed-lock-order single consumption.

        ① lock the authorization row (FOR UPDATE) → ② lock the approver's
        roster row (member removal / role change linearize on this SAME lock)
        → ③ re-intersect granted scopes with the role READ UNDER THE LOCK
        (only ever narrows) → ④ conditional consume (rowcount witness) →
        ⑤ mint the cli session (workspace/granted_scopes fixed; authenticated_at
        inherited from the approval snapshot, never the consumption moment) →
        ⑥ audit — one commit.
        """
        now = self._now()
        pepper = self._pepper()
        code_hash = security.hmac_token(device_code, pepper)

        # Everything happens in ONE committed transaction — including the
        # terminal transitions (lazy expiry, invalidation on lost race): the
        # outcome is resolved inside, committed, then raised afterwards, so a
        # voided grant stays voided.
        phase = "ok"
        payload: ConsumedGrant | None = None
        async with self._sf() as session, session.begin():
            # ① Authorization row under lock; map state to the named codes.
            row = await session.scalar(
                select(DeviceAuthorization)
                .where(DeviceAuthorization.device_code_hash == code_hash)
                .with_for_update()
            )
            if row is None:
                phase = "invalid_grant"
            elif row.expires_at <= now and row.status in ACTIVE_STATUSES:
                row.status = "expired"  # lazy expiry transition (reaper too)
                phase = "expired"
            elif row.status == "pending":
                phase = "pending"
            elif row.status == "denied":
                phase = "denied"
            elif row.status == "expired":
                phase = "expired"
            elif row.status in ("consumed", "invalidated"):
                phase = "invalid_grant"
            else:
                # status == "approved" — consume under the fixed lock order.
                # ② Roster row under lock: the approver must still be an
                # ACTIVE member of the bound workspace.
                member = await session.scalar(
                    select(Member)
                    .where(
                        Member.workspace_id == row.workspace_id,
                        Member.user_id == row.approved_by_user_id,
                        Member.status == "active",
                    )
                    .with_for_update()
                )
                if member is None:
                    # Lost the consume-vs-remove race: void the grant, never mint.
                    row.status = "invalidated"
                    row.invalidated_at = now
                    await write_audit(
                        session,
                        workspace_id=row.workspace_id,
                        actor_member_id=None,
                        actor_kind="system",
                        action="auth.device_invalidated",
                        resource_type="device_authorization",
                        resource_id=row.id,
                        metadata={"reason": "approver_no_longer_active_member"},
                    )
                    phase = "denied"
                else:
                    # ③ Re-intersect under the lock — role read AFTER acquiring
                    # the lock, so a concurrent downgrade narrows the scope.
                    role_perms = {
                        p for p, roles in PERMISSION_MATRIX.items() if member.role in roles
                    }
                    issued = sorted(set(row.granted_scopes or []) & role_perms)

                    # ④ Conditional consumption — atomic single-use witness.
                    result = await session.execute(
                        update(DeviceAuthorization)
                        .where(
                            DeviceAuthorization.id == row.id,
                            DeviceAuthorization.status == "approved",
                            DeviceAuthorization.consumed_at.is_(None),
                        )
                        .values(status="consumed", consumed_at=now)
                    )
                    if result.rowcount != 1:
                        phase = "invalid_grant"
                    else:
                        # ⑤ Mint the cli session bound to the approved workspace.
                        user = await session.get(User, row.approved_by_user_id)
                        if user is None or user.status != "active":
                            phase = "denied"
                        else:
                            tokens = await self._auth.issue_tokens_in_session(
                                session,
                                user,
                                session_type="cli",
                                ip_address=ip_address,
                                user_agent=user_agent,
                                now=now,
                                authenticated_at=row.approved_authenticated_at,  # R6-H3
                                workspace_id=row.workspace_id,
                                granted_scopes=issued,
                                device_authorization_id=row.id,
                            )
                            # ⑥ Audit.
                            await write_audit(
                                session,
                                workspace_id=row.workspace_id,
                                actor_member_id=member.id,
                                actor_kind="member",
                                action="auth.device_consumed",
                                resource_type="device_authorization",
                                resource_id=row.id,
                                metadata={"issued_scopes": issued},
                                ip_address=ip_address,
                                user_agent=user_agent,
                            )
                            workspace = await session.get(Workspace, row.workspace_id)
                            slug = workspace.slug if workspace is not None else ""
                            payload = ConsumedGrant(
                                tokens=tokens,
                                workspace_id=row.workspace_id,
                                workspace_slug=slug,
                                scopes=issued,
                            )

        # Transaction committed — now surface the outcome.
        if phase == "ok" and payload is not None:
            return payload
        if phase == "pending":
            raise AuthorizationPendingError()
        if phase == "denied":
            raise AccessDeniedError("the approval is no longer valid")
        if phase == "expired":
            raise ExpiredTokenError()
        raise InvalidGrantError()

    # -- brute-force protection + expiry sweep -----------------------------------

    async def register_poll_violation(self, *, device_code: str) -> None:
        """Count a rate-limit violation against the code; >5 voids it (§5.5 ④⑤).

        Unknown codes cannot be counted (no row to attach to) — the IP-global
        limiter bounds that case.
        """
        now = self._now()
        async with self._sf() as session, session.begin():
            row = await session.scalar(
                select(DeviceAuthorization)
                .where(DeviceAuthorization.device_code_hash == self._hash(device_code))
                .with_for_update()
            )
            if row is None or row.status not in ACTIVE_STATUSES:
                return
            row.failed_attempts = (row.failed_attempts or 0) + 1
            if row.failed_attempts > FAILED_ATTEMPTS_INVALIDATE_THRESHOLD:
                row.status = "invalidated"
                row.invalidated_at = now
                await write_audit(
                    session,
                    workspace_id=None,
                    actor_member_id=None,
                    actor_kind="system",
                    action="auth.device_invalidated",
                    resource_type="device_authorization",
                    resource_id=row.id,
                    metadata={
                        "reason": "poll_violations_exceeded",
                        "failed_attempts": row.failed_attempts,
                    },
                )

    async def sweep_expired(self) -> int:
        """Reaper: pending/approved grants past TTL → expired (terminal)."""
        now = self._now()
        async with self._sf() as session, session.begin():
            result = await session.execute(
                update(DeviceAuthorization)
                .where(
                    DeviceAuthorization.status.in_(ACTIVE_STATUSES),
                    DeviceAuthorization.expires_at <= now,
                )
                .values(status="expired")
            )
            return result.rowcount or 0
