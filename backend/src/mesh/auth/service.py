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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth import jwt as jwt_mod
from mesh.auth import security
from mesh.auth.ratelimit import assert_not_locked_out
from mesh.config import Settings
from mesh.db.models.user import (
    EmailVerificationToken,
    LoginAttempt,
    PasswordResetToken,
    Session,
    User,
)
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)

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
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
    except Exception as exc:  # ZoneInfoNotFoundError / ValueError
        raise BusinessRuleError(
            "unsupported timezone",
            code="invalid_timezone",
            details={"timezone": value},
        ) from exc


def _validate_locale(value: str) -> None:
    from mesh.config import SUPPORTED_LOCALES

    if value not in SUPPORTED_LOCALES:
        raise BusinessRuleError(
            "unsupported locale",
            code="unsupported_locale",
            details={"locale": value, "supported": list(SUPPORTED_LOCALES)},
        )


def _validate_theme(value: str) -> None:
    from mesh.config import SUPPORTED_THEMES

    if value not in SUPPORTED_THEMES:
        # auth.md §3.1/§5.1 + README §9 T32: invalid theme is auth-canonical
        # 422 validation_error (NOT 400).
        raise BusinessRuleError(
            "unsupported theme",
            code="validation_error",
            details={"theme": value, "supported": list(SUPPORTED_THEMES)},
        )


def _validate_avatar_url(value: str) -> None:
    # README §6.16: only https URLs are acceptable for user-supplied links.
    if not value.startswith("https://"):
        raise ValidationError(
            "avatar_url must be an https URL",
            code="validation_error",
            details={"avatar_url": value[:128]},
        )


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

    def _issue_access(self, user_id: uuid.UUID) -> tuple[str, int]:
        token, _jti = jwt_mod.encode_access_token(
            subject=user_id,
            secret=self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
            ttl=self._settings.access_token_ttl,
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
    ) -> str:
        """Persist a refresh session (hash only) and return the plaintext token."""
        refresh_plain = security.generate_token()
        ttl = (
            self._settings.remember_refresh_token_ttl
            if remember
            else self._settings.refresh_token_ttl
        )
        session.add(
            Session(
                user_id=user.id,
                token_hash=security.hash_token(refresh_plain),
                type=session_type,
                ip_address=ip_address,
                user_agent=user_agent,
                expires_at=now + ttl,
                last_active_at=now,
            )
        )
        return refresh_plain

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
    ) -> TokenResult:
        access_token, expires_in = self._issue_access(user.id)
        refresh_token = await self._create_session(
            session,
            user,
            session_type=session_type,
            ip_address=ip_address,
            user_agent=user_agent,
            remember=remember,
            now=now,
        )
        return TokenResult(
            access_token=access_token, refresh_token=refresh_token, expires_in=expires_in
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
            )
        return tokens

    # -- refresh / logout ------------------------------------------------------

    async def refresh(
        self,
        *,
        refresh_token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        session_type: str = "web",
    ) -> TokenResult:
        now = _now(self._clock)
        token_hash = security.hash_token(refresh_token)
        # Replay revocation must COMMIT before we raise, so the outcome is
        # resolved inside the transaction and raised after it exits.
        outcome: tuple[str, object]
        async with self._sf() as session, session.begin():
            row = await session.scalar(select(Session).where(Session.token_hash == token_hash))
            if row is None or row.expires_at < now:
                outcome = ("invalid", None)
            elif row.revoked_at is not None:
                # Replay: a rotated token was reused. Revoke everything for safety.
                await self._revoke_all(session, row.user_id, now)
                outcome = ("replay", None)
            else:
                user = await session.get(User, row.user_id)
                if user is None or user.status != "active":
                    outcome = ("invalid", None)
                else:
                    # Rotate: revoke the presented token, issue a fresh pair.
                    row.revoked_at = now
                    outcome = (
                        "tokens",
                        await self._issue_tokens(
                            session,
                            user,
                            session_type=session_type,
                            ip_address=ip_address,
                            user_agent=user_agent,
                            remember=False,
                            now=now,
                        ),
                    )
        kind, payload = outcome
        if kind == "invalid":
            raise UnauthorizedError("invalid or expired token")
        if kind == "replay":
            raise UnauthorizedError("invalid or expired token", details={"reason": "replay"})
        return payload  # type: ignore[return-value]

    async def logout(self, *, user_id: uuid.UUID, refresh_token: str) -> None:
        now = _now(self._clock)
        token_hash = security.hash_token(refresh_token)
        async with self._sf() as session, session.begin():
            row = await session.scalar(
                select(Session).where(Session.token_hash == token_hash, Session.user_id == user_id)
            )
            if row is not None and row.revoked_at is None:
                row.revoked_at = now

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
        return result.rowcount or 0

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
                if "locale" in patch.settings and patch.settings["locale"] is not None:
                    _validate_locale(patch.settings["locale"])
                    merged["locale"] = patch.settings["locale"]
                if "theme" in patch.settings and patch.settings["theme"] is not None:
                    _validate_theme(patch.settings["theme"])
                    merged["theme"] = patch.settings["theme"]
                user.settings = merged
            user.updated_at = _now(self._clock)
            result = user_to_dict(user)
        return result

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
                await session.commit()


# Pre-computed dummy hash so absent-account logins still run an argon2 verify
# (uniform timing, anti-enumeration). Computed once at import.
_DUMMY_HASH = security.hash_password("mesh-dummy-password-for-timing-0")
