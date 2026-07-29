"""Auth service — the business logic behind the auth routes (auth.md §4.5).

Each public method owns its own transaction (``session_factory() + begin()``)
so it can be exercised directly from unit tests without route plumbing. Time is
injectable (``clock``) and token delivery is injectable (``deliver``) so tests
capture one-time tokens instead of sending real email.

Security invariants enforced here (auth.md §5.x):
- argon2id + constant-time compare; uniform anti-enumeration failures;
- refresh/reset/verification tokens stored as SHA-256 hashes only;
- refresh rotation with replay detection (reusing a rotated token revokes all);
- password change / reset invalidates every refresh session;
- (IP, email) login lockout;
- TOTP MFA with encrypted secret + single-use backup codes.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth import jwt as jwt_mod
from mesh.auth import security
from mesh.auth.audit import write_audit
from mesh.auth.ratelimit import assert_not_locked_out
from mesh.auth.rbac import PERMISSION_MATRIX
from mesh.auth.realtime import SESSION_REVOKED_EVENT, broadcast_user_revocation
from mesh.config import Settings
from mesh.db.models.api_token import ApiToken
from mesh.db.models.member import Member
from mesh.db.models.user import (
    EmailVerificationToken,
    LoginAttempt,
    PasswordResetToken,
    Session,
    User,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    UnauthorizedError,
)
from mesh.outbox.service import emit_realtime

MFA_ISSUER = "Mesh"
MFA_TICKET_TYPE = "mfa"
MFA_TICKET_TTL = timedelta(minutes=5)
MFA_BACKUP_CODE_COUNT = 10
ANTI_ENUMERATION_MESSAGE = "incorrect email or password"


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


async def _maybe_await(value: object) -> None:
    """Await ``value`` when the delivery callback is async (dev Redis mailer)."""
    if inspect.isawaitable(value):
        await value


@dataclass(frozen=True)
class TokenResult:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class RefreshWinner:
    """The sole refresh-rotation winner: carries the ONLY new refresh issued."""

    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class RefreshGrace:
    """A grace-window loser: fresh access ONLY — never a refresh (§3.8)."""

    access_token: str
    expires_in: int


RefreshOutcome = RefreshWinner | RefreshGrace


@dataclass(frozen=True)
class MfaRequiredResult:
    mfa_ticket: str


@dataclass(frozen=True)
class UserUpdate:
    display_name: str | None = None
    avatar_url: str | None = None
    timezone: str | None = None
    settings: dict | None = None


# A delivery callback receives (recipient_email, kind, plaintext_token). Tests
# pass a capturing callable; production wires an email sender.
DeliverToken = Callable[[str, str, str], None]


def _validate_timezone(value: str) -> None:
    """Raise 422 ``invalid_timezone`` unless ``value`` is a valid IANA name."""
    from mesh.validation import validate_iana_timezone

    validate_iana_timezone(value)


def _validate_locale(value: str) -> None:
    from mesh.validation import validate_locale

    validate_locale(value)


def _validate_theme(value: str) -> None:
    from mesh.validation import validate_theme

    validate_theme(value)


def _validate_avatar_url(value: str) -> None:
    # README §6.16: user-controlled URL fields are https-only.
    from mesh.validation import validate_https_url

    validate_https_url(value, field="avatar_url")


def user_to_dict(user: User) -> dict:
    """Render a User row as the API ``data`` payload (auth.md §3.4 / §5.1 R3)."""
    return {
        "id": user.id,
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "status": user.status,
        "timezone": user.timezone,
        "settings": user.settings or {},
        "mfa_enabled": user.mfa_enabled_at is not None,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }


class AuthService:
    """Stateless orchestrator over the auth tables."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        deliver: DeliverToken | None = None,
    ) -> None:
        self._sf = session_factory
        self._settings = settings
        self._clock = clock
        self._deliver = deliver or (lambda *_args: None)

    # -- token issuance --------------------------------------------------------

    def _issue_access(
        self,
        user_id: uuid.UUID,
        *,
        auth_time: datetime | None,
        session_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        scopes: Sequence[str] | None = None,
    ) -> tuple[str, int]:
        """Issue an access JWT bound to ``session_id`` (the ``sid`` claim).

        ``auth_time`` is passed explicitly — ``None`` means NO recent primary
        authentication (silent SSO reuse, or a device session whose approver
        had none) and the claim is omitted so step-up gates fail closed.
        """
        token, _jti = jwt_mod.encode_access_token(
            subject=user_id,
            secret=self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
            ttl=self._settings.access_token_ttl,
            auth_time=auth_time,
            session_id=session_id,
            workspace_id=workspace_id,
            scopes=scopes,
        )
        return token, int(self._settings.access_token_ttl.total_seconds())

    async def _create_session(
        self,
        session: AsyncSession,
        user: User,
        *,
        session_type: str,
        ip_address: str | None,
        user_agent: str | None,
        remember: bool,
        now: datetime,
        authenticated_at: datetime | None,
        workspace_id: uuid.UUID | None = None,
        granted_scopes: list[str] | None = None,
        device_authorization_id: uuid.UUID | None = None,
    ) -> tuple[str, uuid.UUID]:
        """Persist a refresh session (hash only) and return ``(plaintext, id)``.

        ``authenticated_at`` is the caller's EXPLICIT primary-auth moment —
        ``None`` is legitimate (no recent primary auth) and must never default
        to "now": session creation is not authentication (auth.md R6-H3).
        """
        refresh_plain = security.generate_refresh_token()
        ttl = (
            self._settings.remember_refresh_token_ttl
            if remember
            else self._settings.refresh_token_ttl
        )
        row = Session(
            user_id=user.id,
            token_hash=security.hash_token(refresh_plain),
            type=session_type,
            workspace_id=workspace_id,
            granted_scopes=granted_scopes or [],
            device_authorization_id=device_authorization_id,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=now + ttl,
            last_active_at=now,
            authenticated_at=authenticated_at,
        )
        session.add(row)
        await session.flush()  # assigns row.id (the access JWT's sid)
        return refresh_plain, row.id

    async def _issue_tokens(
        self,
        session: AsyncSession,
        user: User,
        *,
        session_type: str,
        ip_address: str | None,
        user_agent: str | None,
        remember: bool,
        now: datetime,
        authenticated_at: datetime | None,
        workspace_id: uuid.UUID | None = None,
        granted_scopes: list[str] | None = None,
        device_authorization_id: uuid.UUID | None = None,
    ) -> TokenResult:
        refresh_token, sid = await self._create_session(
            session,
            user,
            session_type=session_type,
            ip_address=ip_address,
            user_agent=user_agent,
            remember=remember,
            now=now,
            authenticated_at=authenticated_at,
            workspace_id=workspace_id,
            granted_scopes=granted_scopes,
            device_authorization_id=device_authorization_id,
        )
        access_token, expires_in = self._issue_access(
            user.id,
            auth_time=authenticated_at,
            session_id=sid,
            workspace_id=workspace_id,
            scopes=granted_scopes,
        )
        return TokenResult(
            access_token=access_token, refresh_token=refresh_token, expires_in=expires_in
        )

    async def issue_tokens_in_session(
        self,
        session: AsyncSession,
        user: User,
        *,
        session_type: str,
        ip_address: str | None,
        user_agent: str | None,
        now: datetime,
        authenticated_at: datetime | None,
        remember: bool = False,
        workspace_id: uuid.UUID | None = None,
        granted_scopes: list[str] | None = None,
        device_authorization_id: uuid.UUID | None = None,
    ) -> TokenResult:
        """Issue access+refresh INSIDE the caller's transaction.

        The device-code exchange (device_codes.py) must mint the cli session
        atomically with the grant consumption — same transaction, same commit.
        """
        return await self._issue_tokens(
            session,
            user,
            session_type=session_type,
            ip_address=ip_address,
            user_agent=user_agent,
            remember=remember,
            now=now,
            authenticated_at=authenticated_at,
            workspace_id=workspace_id,
            granted_scopes=granted_scopes,
            device_authorization_id=device_authorization_id,
        )

    # -- registration ----------------------------------------------------------

    async def register(
        self, *, email: str, password: str, display_name: str
    ) -> tuple[dict, str | None]:
        """Create a user (status=active) and an email-verification token.

        Returns ``(user_dict, verification_token_or_None)``. The token is passed
        to the delivery callback; it is returned so dev/test callers can use it.
        """
        security.validate_password_strength(password)
        normalized_email = email.lower()
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            existing = await session.scalar(select(User).where(User.email == normalized_email))
            if existing is not None:
                raise ConflictError(
                    "email already registered",
                    code="conflict",
                    details={"field": "email"},
                )
            user = User(
                email=normalized_email,
                display_name=display_name,
                password_hash=security.hash_password(password),
                password_changed_at=now,
                status="active",
            )
            session.add(user)
            await session.flush()
            token = await self._create_email_verification(session, user, now)
        await _maybe_await(self._deliver(normalized_email, "email_verification", token))
        return user_to_dict(user), token

    async def _create_email_verification(
        self, session: AsyncSession, user: User, now: datetime
    ) -> str:
        # Invalidate prior unconsumed verification tokens (single active token).
        await session.execute(
            update(EmailVerificationToken)
            .where(EmailVerificationToken.user_id == user.id)
            .where(EmailVerificationToken.consumed_at.is_(None))
            .values(consumed_at=now)
        )
        token = security.generate_token()
        session.add(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=security.hash_token(token),
                expires_at=now + self._settings.email_verification_ttl,
            )
        )
        return token

    # -- login -----------------------------------------------------------------

    async def login(
        self,
        *,
        email: str,
        password: str,
        remember: bool = False,
        ip_address: str | None = None,
        user_agent: str | None = None,
        session_type: str = "web",
    ) -> TokenResult | MfaRequiredResult:
        normalized_email = email.lower()
        now = _now(self._clock)
        lock_seconds = int(self._settings.login_lock_duration.total_seconds())

        # The failed-login attempt must be COMMITTED (it drives lockout), so the
        # outcome is computed inside the transaction and raised only after the
        # block exits and commits.
        outcome: tuple[str, object]
        async with self._sf() as session, session.begin():
            await assert_not_locked_out(
                session,
                email=normalized_email,
                ip_address=ip_address,
                max_failures=self._settings.login_max_failures,
                lock_seconds=lock_seconds,
                now_epoch=now.timestamp(),
            )
            user = await session.scalar(select(User).where(User.email == normalized_email))

            # Uniform timing: verify against the real hash, or a dummy hash when
            # the account is absent, so response time doesn't enumerate accounts.
            password_hash = user.password_hash if user and user.password_hash else _DUMMY_HASH
            ok = security.verify_password(password, password_hash)

            session.add(
                LoginAttempt(
                    email=normalized_email,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    succeeded=bool(user and ok and user.status == "active"),
                    created_at=now,
                )
            )

            if user is None or not ok or user.status != "active":
                outcome = ("fail", None)
            elif user.mfa_enabled_at is not None:
                # Password step ok; require the second factor before any tokens.
                outcome = ("mfa", self._issue_mfa_ticket(user.id, now))
            else:
                user.last_login_at = now
                outcome = (
                    "tokens",
                    await self._issue_tokens(
                        session,
                        user,
                        session_type=session_type,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        remember=remember,
                        now=now,
                        # Password verification just succeeded — this IS the
                        # primary authentication moment (R6-H3).
                        authenticated_at=now,
                    ),
                )

        kind, payload = outcome
        if kind == "fail":
            # §3.5/§5.1: wrong credentials → 422 invalid_credentials, uniform
            # message whether or not the account exists (anti-enumeration).
            raise BusinessRuleError(ANTI_ENUMERATION_MESSAGE, code="invalid_credentials")
        if kind == "mfa":
            return MfaRequiredResult(mfa_ticket=payload)  # type: ignore[arg-type]
        return payload  # type: ignore[return-value]

    async def verify_mfa(
        self,
        *,
        mfa_ticket: str,
        code: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        remember: bool = False,
        session_type: str = "web",
    ) -> TokenResult:
        """Complete a login by validating the TOTP/backup code for an MFA ticket."""
        now = _now(self._clock)
        user_id = self._decode_mfa_ticket(mfa_ticket)
        async with self._sf() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None or user.status != "active" or user.mfa_enabled_at is None:
                raise UnauthorizedError("invalid or expired token")
            if not self._consume_mfa_code(session, user, code, now):
                raise BusinessRuleError("invalid or expired code", code="invalid_credentials")
            user.last_login_at = now
            tokens = await self._issue_tokens(
                session,
                user,
                session_type=session_type,
                ip_address=ip_address,
                user_agent=user_agent,
                remember=remember,
                now=now,
                # TOTP/backup code just verified — primary authentication.
                authenticated_at=now,
            )
        return tokens

    async def issue_session(
        self,
        *,
        user_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
        remember: bool = False,
        session_type: str = "web",
        authenticated_at: datetime | None,
    ) -> TokenResult:
        """Issue access+refresh for an already-authenticated user (OAuth login).

        The caller has established the user's identity out-of-band (e.g. a
        verified OAuth provider); this stamps last_login and mints tokens.
        ``authenticated_at`` is the caller's verdict on primary-auth freshness
        (R6-H3/R7-H3): a fresh interactive provider login passes ``now()``;
        a silent SSO reuse passes ``None`` — never the callback arrival time.
        """
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None or user.status != "active":
                raise UnauthorizedError("invalid or expired token")
            user.last_login_at = now
            return await self._issue_tokens(
                session,
                user,
                session_type=session_type,
                ip_address=ip_address,
                user_agent=user_agent,
                remember=remember,
                now=now,
                authenticated_at=authenticated_at,
            )

    # -- refresh / logout ------------------------------------------------------

    async def refresh(
        self,
        *,
        presented_token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> RefreshOutcome:
        """Bounded idempotent rotation (auth.md §3.8) — winner-takes-all.

        Two concurrent refreshes with the SAME refresh token (multi-tab /
        multi-process race) both succeed: exactly one UPDATE wins the row
        arbitration and is issued a fresh refresh (the winner — the ONLY
        channel a new refresh plaintext ever leaves by); the loser matches the
        rotated-out ``previous_token_hash`` inside the grace window and is
        issued ONLY a fresh access token — no refresh, no second rotation, no
        DB write — so both converge on the winner's credential instead of
        logging each other out. Outside the window, or for revoked/expired
        sessions, everything is a plain 401: an expired session is never
        resurrected through either path.
        """
        now = _now(self._clock)
        presented_hash = security.hash_token(presented_token)
        grace_seconds = self._settings.refresh_rotation_grace_seconds

        async with self._sf() as session, session.begin():
            # 1) Winner arbitration: conditional rotation, rowcount adjudicates.
            new_refresh_plain = security.generate_refresh_token()
            new_refresh_hash = security.hash_token(new_refresh_plain)
            result = await session.execute(
                update(Session)
                .where(Session.token_hash == presented_hash)
                .where(Session.revoked_at.is_(None))
                .where(Session.expires_at > now)
                .values(
                    token_hash=new_refresh_hash,
                    previous_token_hash=Session.token_hash,
                    rotated_at=now,
                )
            )
            if result.rowcount == 1:
                row = await session.scalar(
                    select(Session).where(Session.token_hash == new_refresh_hash)
                )
                outcome: RefreshOutcome = await self._refresh_tokens(
                    session,
                    row,
                    now=now,
                    new_refresh_plain=new_refresh_plain,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                return outcome

            # 2) Rowcount 0 — re-read to decide WHY.
            current = await session.scalar(
                select(Session).where(Session.token_hash == presented_hash)
            )
            if current is not None:
                # presented == current hash yet the UPDATE matched nothing ⇒
                # the session is revoked or expired (the realistic cause) —
                # hard 401; the predicates are re-checked, never skipped.
                if current.revoked_at is not None or current.expires_at <= now:
                    raise UnauthorizedError("invalid or expired token")
                # The vanishingly rare concurrent rotate-to-identical-value
                # case: predicates hold, so proceed as a normal refresh.
                return await self._refresh_tokens(
                    session,
                    current,
                    now=now,
                    new_refresh_plain=None,  # rotate now
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

            # 3) Grace path: presented matches the rotated-out previous hash.
            previous_holder = await session.scalar(
                select(Session).where(Session.previous_token_hash == presented_hash)
            )
            if (
                previous_holder is not None
                and previous_holder.rotated_at is not None
                and previous_holder.revoked_at is None
                and previous_holder.expires_at > now
                and (now - previous_holder.rotated_at).total_seconds() <= grace_seconds
            ):
                # Access only. No refresh plaintext, no rotation, NO write —
                # the grace path never extends the session's life or scope.
                user = await session.get(User, previous_holder.user_id)
                if user is None or user.status != "active":
                    raise UnauthorizedError("invalid or expired token")
                access_token, expires_in = await self._session_access(
                    session, previous_holder, user, now=now
                )
                return RefreshGrace(access_token=access_token, expires_in=expires_in)

            raise UnauthorizedError("invalid or expired token")

    async def _refresh_tokens(
        self,
        session: AsyncSession,
        row: Session,
        *,
        now: datetime,
        new_refresh_plain: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> RefreshOutcome:
        """Winner branch: rotate (when not already rotated) + issue the pair.

        ``new_refresh_plain=None`` means the row still holds the presented
        hash — rotate it in place first. Renewal scope = the session's fixed
        ``granted_scopes`` ∩ the holder's CURRENT role permissions (a later
        role downgrade narrows renewed tokens; web sessions carry an empty
        list and stay role-based).
        """
        if new_refresh_plain is None:
            new_refresh_plain = security.generate_refresh_token()
            result = await session.execute(
                update(Session)
                .where(Session.id == row.id)
                .where(Session.revoked_at.is_(None))
                .where(Session.expires_at > now)
                .values(
                    token_hash=security.hash_token(new_refresh_plain),
                    previous_token_hash=Session.token_hash,
                    rotated_at=now,
                )
            )
            if result.rowcount != 1:
                raise UnauthorizedError("invalid or expired token")
            row = await session.scalar(select(Session).where(Session.id == row.id))
        user = await session.get(User, row.user_id)
        if user is None or user.status != "active":
            raise UnauthorizedError("invalid or expired token")
        row.last_active_at = now
        access_token, expires_in = await self._session_access(session, row, user, now=now)
        return RefreshWinner(
            access_token=access_token,
            refresh_token=new_refresh_plain,
            expires_in=expires_in,
        )

    async def _session_access(
        self, session: AsyncSession, row: Session, user: User, *, now: datetime
    ) -> tuple[str, int]:
        """Issue an access JWT bound to ``row`` with renewal-time scope (§3.1).

        cli sessions re-intersect their fixed ``granted_scopes`` with the
        holder's CURRENT role permissions (only ever narrows); web sessions
        stay role-based (empty scope claim). ``authenticated_at`` is forwarded
        as-is — possibly None — so step-up reflects the real auth age.
        """
        scopes: list[str] | None = None
        workspace_id = row.workspace_id
        if row.type == "cli" and row.workspace_id is not None:
            # Tenant GUC: the roster read must evaluate RLS against the
            # session's bound workspace under the restricted app role.
            await set_tenant_context(session, row.workspace_id)
            member = await session.scalar(
                select(Member).where(
                    Member.workspace_id == row.workspace_id,
                    Member.user_id == row.user_id,
                    Member.status == "active",
                )
            )
            if member is None:
                # The membership that anchored this device session is gone —
                # it may not mint fresh access tokens.
                raise UnauthorizedError("invalid or expired token")
            role_perms = {p for p, roles in PERMISSION_MATRIX.items() if member.role in roles}
            scopes = sorted(set(row.granted_scopes or []) & role_perms)
        return self._issue_access(
            user.id,
            auth_time=row.authenticated_at,
            session_id=row.id,
            workspace_id=workspace_id,
            scopes=scopes,
        )

    async def logout(
        self,
        *,
        refresh_token: str | None = None,
        session_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """Revoke the session identified by refresh hash OR by ``sid``.

        Transport-agnostic (auth.md §3.1): the route resolves the cookie /
        Bearer transport to one of these locators; ``session_id`` lookups are
        owner-checked when ``user_id`` is given.
        """
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            if refresh_token is not None:
                row = await session.scalar(
                    select(Session).where(
                        Session.token_hash == security.hash_token(refresh_token)
                    )
                )
            elif session_id is not None:
                stmt = select(Session).where(Session.id == session_id)
                if user_id is not None:
                    stmt = stmt.where(Session.user_id == user_id)
                row = await session.scalar(stmt)
            else:
                row = None
            if row is not None and row.revoked_at is None:
                row.revoked_at = now
                # C4: notify live connections (outbox → realtime, §3.7/§5.6).
                await broadcast_user_revocation(session, user_id=row.user_id)

    async def logout_all(self, *, user_id: uuid.UUID) -> int:
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            return await self._revoke_all(session, user_id, now)

    async def _revoke_all(self, session: AsyncSession, user_id: uuid.UUID, now: datetime) -> int:
        result = await session.execute(
            update(Session)
            .where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        revoked = result.rowcount or 0
        if revoked:
            # C4: bulk revocation (logout-all / refresh replay / password change).
            await broadcast_user_revocation(session, user_id=user_id)
        return revoked

    # -- password reset / email verification -----------------------------------

    async def request_password_reset(self, *, email: str) -> str | None:
        """Always succeeds (anti-enumeration); returns the token if a user exists."""
        normalized_email = email.lower()
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            user = await session.scalar(select(User).where(User.email == normalized_email))
            if user is None:
                return None
            # Invalidate prior unconsumed reset tokens.
            await session.execute(
                update(PasswordResetToken)
                .where(PasswordResetToken.user_id == user.id)
                .where(PasswordResetToken.consumed_at.is_(None))
                .values(consumed_at=now)
            )
            token = security.generate_token()
            session.add(
                PasswordResetToken(
                    user_id=user.id,
                    token_hash=security.hash_token(token),
                    expires_at=now + self._settings.password_reset_ttl,
                )
            )
        await _maybe_await(self._deliver(normalized_email, "password_reset", token))
        return token

    async def reset_password(self, *, token: str, new_password: str) -> None:
        """Consume a reset token, set a new password, revoke all sessions."""
        security.validate_password_strength(new_password)
        now = _now(self._clock)
        token_hash = security.hash_token(token)
        async with self._sf() as session, session.begin():
            row = await session.scalar(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
            )
            if row is None or row.consumed_at is not None or row.expires_at < now:
                raise UnauthorizedError("invalid or expired token")
            row.consumed_at = now
            user = await session.get(User, row.user_id)
            if user is None:
                raise UnauthorizedError("invalid or expired token")
            user.password_hash = security.hash_password(new_password)
            user.password_changed_at = now
            await self._revoke_all(session, user.id, now)

    async def change_password(
        self,
        *,
        user_id: uuid.UUID,
        old_password: str,
        new_password: str,
        current_session_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Change the password of an authenticated user (auth.md §3.1/§4.2/§4.5).

        Verifying the old password (argon2id, constant-time) *is* the §5.5
        step-up re-authentication — "recently re-entered the password" is
        exactly what that clause demands, so no additional recent-auth gate is
        applied (R7-M1). The new password is held to the registration strength
        policy; the hash and ``password_changed_at`` are rotated, every *other*
        refresh session is revoked (the session named by the caller's access
        JWT ``sid`` is kept and re-stamped as recently authenticated; an absent
        or unrecognised sid falls back to revoking all), the revocation is
        broadcast (§3.7/§5.6), and an account-level ``user.password_changed``
        audit row is written (§2.6: ``workspace_id`` NULL, actor in metadata).
        """
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None or user.status != "active":
                raise UnauthorizedError("invalid or expired token")
            # Uniform timing for OAuth-only accounts (NULL hash): verify against
            # the dummy hash so failure is indistinguishable from a wrong password.
            password_hash = user.password_hash if user.password_hash else _DUMMY_HASH
            if not security.verify_password(old_password, password_hash):
                raise BusinessRuleError("incorrect password", code="invalid_credentials")
            security.validate_password_strength(new_password)
            user.password_hash = security.hash_password(new_password)
            user.password_changed_at = now

            # Keep the initiating session when identifiable by its sid (R4-H1:
            # the current access names it; the body carries no refresh);
            # revoke everything else.
            keep_session_id: uuid.UUID | None = None
            if current_session_id is not None:
                row = await session.scalar(
                    select(Session).where(
                        Session.id == current_session_id,
                        Session.user_id == user_id,
                        Session.revoked_at.is_(None),
                        Session.expires_at >= now,
                    )
                )
                if row is not None:
                    keep_session_id = row.id
                    # A primary authentication just happened — refresh the step-up
                    # marker (§5.5) so silent refreshes forward a fresh auth_time.
                    row.authenticated_at = now
                    row.last_active_at = now
            revoke_stmt = update(Session).where(
                Session.user_id == user_id, Session.revoked_at.is_(None)
            )
            if keep_session_id is not None:
                revoke_stmt = revoke_stmt.where(Session.id != keep_session_id)
            result = await session.execute(revoke_stmt.values(revoked_at=now))
            revoked = result.rowcount or 0
            if revoked:
                # C4: the other devices must drop (outbox → realtime, §3.7/§5.6).
                await broadcast_user_revocation(session, user_id=user_id)

            # Account-level audit (§2.6): no workspace context; members are
            # workspace-scoped rows, so the actor falls into metadata.
            await write_audit(
                session,
                workspace_id=None,
                actor_member_id=None,
                actor_kind="member",
                action="user.password_changed",
                resource_type="user",
                resource_id=user.id,
                metadata={"user_id": str(user.id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )

    async def verify_email(self, *, token: str) -> None:
        now = _now(self._clock)
        token_hash = security.hash_token(token)
        async with self._sf() as session, session.begin():
            row = await session.scalar(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.token_hash == token_hash
                )
            )
            if row is None or row.consumed_at is not None or row.expires_at < now:
                raise UnauthorizedError("invalid or expired token")
            row.consumed_at = now
            user = await session.get(User, row.user_id)
            if user is None:
                raise UnauthorizedError("invalid or expired token")
            user.email_verified_at = now

    # -- MFA (TOTP + backup codes) ---------------------------------------------

    def _issue_mfa_ticket(self, user_id: uuid.UUID, now: datetime) -> str:
        import jwt as pyjwt

        claims = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + MFA_TICKET_TTL).timestamp()),
            "typ": MFA_TICKET_TYPE,
        }
        token = pyjwt.encode(claims, self._settings.jwt_secret, algorithm=self._settings.jwt_algorithm)
        return token if isinstance(token, str) else token.decode("ascii")

    def _decode_mfa_ticket(self, ticket: str) -> uuid.UUID:
        import jwt as pyjwt

        try:
            # Signature is verified by PyJWT; expiry is checked against the
            # service clock (not wall-clock) so it stays deterministic/testable.
            claims = pyjwt.decode(
                ticket,
                self._settings.jwt_secret,
                algorithms=[self._settings.jwt_algorithm],
                options={"require": ["exp", "sub"], "verify_exp": False},
            )
        except pyjwt.PyJWTError as exc:
            raise UnauthorizedError("invalid or expired token") from exc
        if claims.get("typ") != MFA_TICKET_TYPE:
            raise UnauthorizedError("invalid or expired token")
        if int(claims["exp"]) < int(_now(self._clock).timestamp()):
            raise UnauthorizedError("invalid or expired token")
        try:
            return uuid.UUID(str(claims["sub"]))
        except ValueError as exc:
            raise UnauthorizedError("invalid or expired token") from exc

    async def mfa_setup(self, *, user_id: uuid.UUID) -> dict:
        """Generate a TOTP secret (encrypted at rest) + backup codes; not enabled yet."""
        secret = pyotp.random_base32()
        backup_codes = [
            security.generate_token()[:10].replace("-", "").replace("_", "")
            for _ in range(MFA_BACKUP_CODE_COUNT)
        ]
        encrypted = security.encrypt_secret(secret, self._settings.jwt_secret)
        code_hashes = [security.hash_token(c.upper()) for c in backup_codes]
        async with self._sf() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None:
                raise NotFoundError("user not found")
            user.mfa_secret = encrypted
            user.mfa_backup_codes = code_hashes
            user.mfa_enabled_at = None
            email = user.email
        uri = pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=MFA_ISSUER)
        return {"secret": secret, "otpauth_uri": uri, "backup_codes": backup_codes}

    async def mfa_enable(self, *, user_id: uuid.UUID, code: str) -> None:
        """Confirm the pending secret with a current TOTP code, then enable MFA."""
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None or not user.mfa_secret:
                raise BusinessRuleError("MFA setup not started", code="mfa_not_setup")
            secret = security.decrypt_secret(user.mfa_secret, self._settings.jwt_secret)
            if not pyotp.TOTP(secret).verify(code, valid_window=1):
                raise BusinessRuleError("invalid or expired code", code="invalid_credentials")
            user.mfa_enabled_at = now

    async def mfa_disable(self, *, user_id: uuid.UUID, code: str) -> None:
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None or user.mfa_enabled_at is None:
                raise BusinessRuleError("MFA is not enabled", code="mfa_not_enabled")
            if not self._consume_mfa_code(session, user, code, now):
                raise BusinessRuleError("invalid or expired code", code="invalid_credentials")
            user.mfa_secret = None
            user.mfa_backup_codes = []
            user.mfa_enabled_at = None

    def _consume_mfa_code(
        self, session: AsyncSession, user: User, code: str, now: datetime
    ) -> bool:
        """Validate a TOTP code or a single-use backup code against ``user``."""
        if not user.mfa_secret:
            return False
        secret = security.decrypt_secret(user.mfa_secret, self._settings.jwt_secret)
        if pyotp.TOTP(secret).verify(code, valid_window=1):
            return True
        # Backup codes are stored hashed and consumed on use (one-time).
        code_hash = security.hash_token(code.strip().upper())
        codes = list(user.mfa_backup_codes or [])
        if code_hash in codes:
            codes.remove(code_hash)
            user.mfa_backup_codes = codes
            return True
        return False

    # -- user read / update ----------------------------------------------------

    async def get_user(self, *, user_id: uuid.UUID) -> dict:
        async with self._sf() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise NotFoundError("user not found")
            return user_to_dict(user)

    async def update_user(self, *, user_id: uuid.UUID, patch: UserUpdate) -> dict:
        async with self._sf() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None:
                raise NotFoundError("user not found")
            if patch.display_name is not None:
                user.display_name = patch.display_name
            if patch.avatar_url is not None:
                _validate_avatar_url(patch.avatar_url)
                user.avatar_url = patch.avatar_url
            if patch.timezone is not None:
                _validate_timezone(patch.timezone)
                user.timezone = patch.timezone
            if patch.settings is not None:
                merged = dict(user.settings or {})  # key-level shallow merge
                if "locale" in patch.settings:
                    if patch.settings["locale"] is None:
                        merged.pop("locale", None)  # explicit null = clear preference
                    else:
                        _validate_locale(patch.settings["locale"])
                        merged["locale"] = patch.settings["locale"]
                if "theme" in patch.settings:
                    if patch.settings["theme"] is None:
                        merged.pop("theme", None)  # explicit null = clear preference
                    else:
                        _validate_theme(patch.settings["theme"])
                        merged["theme"] = patch.settings["theme"]
                user.settings = merged
            user.updated_at = _now(self._clock)
            result = user_to_dict(user)
        return result

    # -- step-up re-authentication (auth.md §3.1 ``POST /auth/reauth``) --------

    async def reauth(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        password: str | None = None,
        totp_code: str | None = None,
        method: str | None = None,
    ) -> dict:
        """Refresh the web session's ``authenticated_at`` via fresh primary
        authentication (R6-H3 step-up state machine, web sessions only).

        TOTP-enabled accounts MUST present ``totp_code`` — password alone is
        rejected (``reason=totp_required``, MES-78 LOW-2): holding only the
        password (e.g. phishing capture) must not unlock step-up. The OAuth
        fresh-round-trip branch depends on the §2.4.3 one-time transaction
        table (out of this increment's scope) and fails closed with
        ``reason=oauth_reauth_pending``.
        """
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            row = await session.scalar(
                select(Session)
                .where(
                    Session.id == session_id,
                    Session.user_id == user_id,
                    Session.type == "web",
                    Session.revoked_at.is_(None),
                    Session.expires_at > now,
                )
                .with_for_update()
            )
            if row is None:
                raise UnauthorizedError("invalid or expired token")
            user = await session.get(User, user_id)
            if user is None or user.status != "active":
                raise UnauthorizedError("invalid or expired token")

            if user.mfa_enabled_at is not None:
                # Branch-exclusive: a TOTP account re-auths with TOTP only.
                if not totp_code or not self._verify_totp(user, totp_code):
                    raise BusinessRuleError(
                        "totp code required for re-authentication",
                        code="invalid_credentials",
                        details={"reason": "totp_required"},
                    )
            elif user.password_hash:
                if not password or not security.verify_password(password, user.password_hash):
                    raise BusinessRuleError(
                        "incorrect password", code="invalid_credentials"
                    )
            else:
                # OAuth-only account: the fresh round-trip branch needs the
                # §2.4.3 transaction table — fail closed until it lands.
                raise BusinessRuleError(
                    "oauth re-authentication is not available yet",
                    code="invalid_credentials",
                    details={"reason": "oauth_reauth_pending"},
                )

            row.authenticated_at = now
            row.last_active_at = now
            authenticated_at = now
        return {"status": "ok", "authenticated_at": authenticated_at}

    def _verify_totp(self, user: User, code: str) -> bool:
        """Validate a TOTP code for reauth WITHOUT consuming backup codes
        (re-authentication is not a backup-code event)."""
        if not user.mfa_secret:
            return False
        secret = security.decrypt_secret(user.mfa_secret, self._settings.jwt_secret)
        return pyotp.TOTP(secret).verify(code, valid_window=1)

    # -- credential self-service (auth.md §3.1, review H7) ---------------------

    async def introspect_credential(self, *, principal) -> dict:
        """Metadata for the CURRENT credential — never a plaintext fragment.

        Sessions resolve by the access JWT's ``sid`` (the session-location
        invariant, §1.1); PAT/agent tokens by their ``api_tokens`` row. Powers
        ``mesh auth status`` (prefix masked, scopes/expiry/last-use visible).
        """
        async with self._sf() as session:
            if principal.kind != "session" and principal.workspace_id is not None:
                # api_tokens is tenant-scoped — the GUC makes the read
                # RLS-correct under the restricted app role.
                await set_tenant_context(session, principal.workspace_id)
            if principal.kind == "session":
                if principal.session_id is None:
                    # Pre-increment access JWT without sid — nothing to show.
                    raise UnauthorizedError("invalid or expired token")
                row = await session.get(Session, principal.session_id)
                if row is None or row.revoked_at is not None or row.user_id != principal.subject:
                    raise UnauthorizedError("invalid or expired token")
                member_id = None
                if row.workspace_id is not None:
                    member_id = await session.scalar(
                        select(Member.id).where(
                            Member.workspace_id == row.workspace_id,
                            Member.user_id == row.user_id,
                            Member.status == "active",
                        )
                    )
                user = await session.get(User, row.user_id)
                return {
                    "kind": "session",
                    "token_id": row.id,
                    "prefix": None,
                    "name": user.display_name if user is not None else None,
                    "scopes": sorted(row.granted_scopes or []),
                    "workspace_id": row.workspace_id,
                    "member_id": member_id,
                    "expires_at": row.expires_at,
                    "last_used_at": row.last_active_at,
                }
            token_row = await session.get(ApiToken, principal.token_id)
            if token_row is None or token_row.revoked_at is not None:
                raise UnauthorizedError("invalid or expired token")
            return {
                "kind": principal.kind,
                "token_id": token_row.id,
                # Masked display prefix only — the plaintext existed once, at
                # creation. Nothing more ever leaves the service.
                "prefix": token_row.prefix + "…",
                "name": token_row.name,
                "scopes": sorted(token_row.scopes or []),
                "workspace_id": token_row.workspace_id,
                "member_id": token_row.owner_member_id,
                "expires_at": token_row.expires_at,
                "last_used_at": token_row.last_used_at,
            }

    async def revoke_credential(
        self,
        *,
        principal,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Revoke the CURRENT credential itself (no token id required).

        PAT/agent: ``revoked_at`` now ⇒ per-request hash lookup 401s at once
        (§5.5). Session: refresh dies immediately; the issued access expires
        with its TTL (≤15min, §3.7 — regular routes stay stateless).
        """
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            if principal.kind != "session" and principal.workspace_id is not None:
                await set_tenant_context(session, principal.workspace_id)
            if principal.kind == "session":
                if principal.session_id is None:
                    raise UnauthorizedError("invalid or expired token")
                row = await session.get(Session, principal.session_id)
                if row is None or row.user_id != principal.subject:
                    raise UnauthorizedError("invalid or expired token")
                if row.revoked_at is None:
                    row.revoked_at = now
                    await broadcast_user_revocation(session, user_id=row.user_id)
                return
            token_row = await session.get(ApiToken, principal.token_id)
            if token_row is None:
                raise UnauthorizedError("invalid or expired token")
            if token_row.revoked_at is None:
                token_row.revoked_at = now
                token_row.updated_at = now
                await write_audit(
                    session,
                    workspace_id=token_row.workspace_id,
                    actor_member_id=token_row.owner_member_id,
                    actor_kind="member",
                    action="token.revoked",
                    resource_type="api_token",
                    resource_id=token_row.id,
                    metadata={"name": token_row.name, "self_revoked": True},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                # Live connections bearing this token fail re-auth (§3.7/§5.6).
                await emit_realtime(
                    session,
                    workspace_id=token_row.workspace_id,
                    channel=f"workspace:{token_row.workspace_id}",
                    event=SESSION_REVOKED_EVENT,
                    data={
                        "token_id": str(token_row.id),
                        "owner_member_id": str(token_row.owner_member_id),
                    },
                )

    # -- sessions --------------------------------------------------------------

    async def list_sessions(self, *, user_id: uuid.UUID) -> list[dict]:
        now = _now(self._clock)
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(Session)
                        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
                        .where(Session.expires_at >= now)
                        .order_by(Session.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": row.id,
                    "type": row.type,
                    "user_agent": row.user_agent,
                    "ip_address": str(row.ip_address) if row.ip_address is not None else None,
                    "created_at": row.created_at,
                    "last_active_at": row.last_active_at,
                    "expires_at": row.expires_at,
                    "current": False,
                }
                for row in rows
            ]

    async def revoke_session(self, *, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        now = _now(self._clock)
        async with self._sf() as session:
            row = await session.scalar(
                select(Session).where(Session.id == session_id, Session.user_id == user_id)
            )
            if row is None:
                raise NotFoundError("session not found")
            if row.revoked_at is None:
                row.revoked_at = now
                # C4: notify live connections (outbox → realtime, §3.7/§5.6).
                await broadcast_user_revocation(session, user_id=user_id)
                await session.commit()


# Pre-computed dummy hash so absent-account logins still run an argon2 verify
# (uniform timing, anti-enumeration). Computed once at import.
_DUMMY_HASH = security.hash_password("mesh-dummy-password-for-timing-0")
