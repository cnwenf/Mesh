"""Unit tests for AuthService business logic (auth.md §4.5/§5.x).

Run against the real migrated PostgreSQL test database (session_factory
fixture); time is injected so TTL / lockout / single-use semantics are testable
deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from sqlalchemy import select

from mesh.auth.security import decrypt_secret
from mesh.auth.service import AuthService, MfaRequiredResult, TokenResult, UserUpdate
from mesh.config import load_settings
from mesh.db.models.user import PasswordResetToken, User
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    LockedError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def settings(db_url, redis_url):
    return load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="unit-test-signing-secret",
    )


@pytest.fixture
def clock():
    return Clock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def delivered():
    return []


@pytest.fixture
def service(session_factory, settings, clock, delivered):
    def deliver(email, kind, token):
        delivered.append((email, kind, token))

    return AuthService(session_factory, settings, clock=clock, deliver=deliver)


EMAIL = "li@corp.com"
PASSWORD = "a-strong-passw0rd"


async def _get_user(session_factory, email: str) -> User | None:
    async with session_factory() as session:
        return await session.scalar(select(User).where(User.email == email))


# --- registration ------------------------------------------------------------


class TestRegister:
    async def test_register_creates_active_user_and_delivers_token(
        self, service, session_factory, delivered
    ):
        user, token = await service.register(
            email=EMAIL, password=PASSWORD, display_name="李四"
        )
        assert user["email"] == EMAIL
        assert user["status"] == "active"
        assert user["email_verified"] is False
        assert token is not None
        assert delivered == [(EMAIL, "email_verification", token)]

        stored = await _get_user(session_factory, EMAIL)
        assert stored is not None
        assert stored.password_hash.startswith("$argon2id$")
        assert stored.password_hash != PASSWORD

    async def test_register_normalizes_email_case(self, service, session_factory):
        await service.register(email="Li@Corp.com", password=PASSWORD, display_name="x")
        stored = await _get_user(session_factory, EMAIL)
        assert stored is not None and stored.email == EMAIL

    async def test_register_duplicate_email_conflicts(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        with pytest.raises(ConflictError):
            await service.register(email=EMAIL, password=PASSWORD, display_name="y")

    async def test_register_rejects_weak_password(self, service):
        with pytest.raises(ValidationError):
            await service.register(email=EMAIL, password="weak", display_name="x")


# --- login -------------------------------------------------------------------


class TestLogin:
    async def test_login_success_issues_tokens(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        result = await service.login(email=EMAIL, password=PASSWORD, ip_address="10.0.0.1")
        assert isinstance(result, TokenResult)
        assert result.access_token and result.refresh_token
        assert result.expires_in == 900

    async def test_login_wrong_password_uniform_422(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        with pytest.raises(BusinessRuleError) as exc:
            await service.login(email=EMAIL, password="wrong-pass-1", ip_address="10.0.0.1")
        assert exc.value.code == "invalid_credentials"
        assert exc.value.status_code == 422

    async def test_login_unknown_email_same_error_as_wrong_password(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        with pytest.raises(BusinessRuleError) as exc:
            await service.login(email="ghost@corp.com", password="x", ip_address="10.0.0.1")
        assert exc.value.code == "invalid_credentials"

    async def test_login_lockout_after_threshold(self, service, settings):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        for _ in range(settings.login_max_failures):
            with pytest.raises(BusinessRuleError):
                await service.login(email=EMAIL, password="bad-pass-1", ip_address="10.0.0.9")
        # Next attempt — even with the right password — is locked out (423).
        with pytest.raises(LockedError) as exc:
            await service.login(email=EMAIL, password=PASSWORD, ip_address="10.0.0.9")
        assert exc.value.code == "account_locked"

    async def test_lockout_is_scoped_to_ip_email_tuple(self, service, settings):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        for _ in range(settings.login_max_failures):
            with pytest.raises(BusinessRuleError):
                await service.login(email=EMAIL, password="bad-pass-1", ip_address="10.0.0.9")
        # A different IP for the same email is NOT locked (avoids lockout DoS).
        result = await service.login(email=EMAIL, password=PASSWORD, ip_address="10.0.0.10")
        assert isinstance(result, TokenResult)

    async def test_lockout_expires_after_window(self, service, clock, settings):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        for _ in range(settings.login_max_failures):
            with pytest.raises(BusinessRuleError):
                await service.login(email=EMAIL, password="bad-pass-1", ip_address="10.0.0.9")
        clock.advance(minutes=16)  # past the 15-minute lock window
        result = await service.login(email=EMAIL, password=PASSWORD, ip_address="10.0.0.9")
        assert isinstance(result, TokenResult)


# --- refresh / logout --------------------------------------------------------


class TestRefreshAndLogout:
    async def test_refresh_rotates_and_revokes_old(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        first = await service.login(email=EMAIL, password=PASSWORD)
        second = await service.refresh(refresh_token=first.refresh_token)
        assert second.refresh_token != first.refresh_token
        # The old refresh token is now revoked.
        with pytest.raises(UnauthorizedError):
            await service.refresh(refresh_token=first.refresh_token)

    async def test_refresh_replay_revokes_all_sessions(self, service, session_factory):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        first = await service.login(email=EMAIL, password=PASSWORD)
        second = await service.refresh(refresh_token=first.refresh_token)
        # Replaying the already-rotated token revokes EVERYTHING for the user.
        with pytest.raises(UnauthorizedError):
            await service.refresh(refresh_token=first.refresh_token)
        with pytest.raises(UnauthorizedError):
            await service.refresh(refresh_token=second.refresh_token)

    async def test_refresh_expired_token_rejected(self, service, clock):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        first = await service.login(email=EMAIL, password=PASSWORD)
        clock.advance(days=15)  # past default refresh TTL
        with pytest.raises(UnauthorizedError):
            await service.refresh(refresh_token=first.refresh_token)

    async def test_logout_revokes_only_that_session(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        a = await service.login(email=EMAIL, password=PASSWORD)
        b = await service.login(email=EMAIL, password=PASSWORD)
        user = await _get_user(service._sf, EMAIL)
        assert len(await service.list_sessions(user_id=user.id)) == 2

        await service.logout(user_id=user.id, refresh_token=a.refresh_token)
        # Only one active session remains; the other still refreshes fine.
        # (We don't re-present `a`'s token here — reusing a revoked refresh token
        # is treated as replay and revokes the whole family by design.)
        assert len(await service.list_sessions(user_id=user.id)) == 1
        assert isinstance(await service.refresh(refresh_token=b.refresh_token), TokenResult)
        assert a.refresh_token != b.refresh_token

    async def test_logout_all_revokes_every_session(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        a = await service.login(email=EMAIL, password=PASSWORD)
        b = await service.login(email=EMAIL, password=PASSWORD)
        user = await _get_user(service._sf, EMAIL)
        revoked = await service.logout_all(user_id=user.id)
        assert revoked >= 2
        for token in (a.refresh_token, b.refresh_token):
            with pytest.raises(UnauthorizedError):
                await service.refresh(refresh_token=token)


# --- password reset / email verification -------------------------------------


class TestResetAndVerify:
    async def test_reset_password_single_use_and_revokes_sessions(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        login = await service.login(email=EMAIL, password=PASSWORD)
        token = await service.request_password_reset(email=EMAIL)
        assert token is not None

        await service.reset_password(token=token, new_password="a-new-passw0rd")
        # Old sessions invalidated by the password change.
        with pytest.raises(UnauthorizedError):
            await service.refresh(refresh_token=login.refresh_token)
        # New password works.
        assert isinstance(
            await service.login(email=EMAIL, password="a-new-passw0rd"), TokenResult
        )
        # The reset token is single-use.
        with pytest.raises(UnauthorizedError):
            await service.reset_password(token=token, new_password="another-pass1")

    async def test_reset_expired_token_rejected(self, service, clock):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        token = await service.request_password_reset(email=EMAIL)
        clock.advance(hours=2)  # past the 1h reset TTL
        with pytest.raises(UnauthorizedError):
            await service.reset_password(token=token, new_password="a-new-passw0rd")

    async def test_forgot_password_unknown_email_returns_none(self, service):
        assert await service.request_password_reset(email="ghost@corp.com") is None

    async def test_verify_email_marks_verified_and_single_use(
        self, service, session_factory, delivered
    ):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        verify_token = next(t for (_e, kind, t) in delivered if kind == "email_verification")
        await service.verify_email(token=verify_token)
        stored = await _get_user(session_factory, EMAIL)
        assert stored.email_verified_at is not None
        with pytest.raises(UnauthorizedError):
            await service.verify_email(token=verify_token)

    async def test_verify_email_invalid_token(self, service):
        with pytest.raises(UnauthorizedError):
            await service.verify_email(token="bogus-token")

    async def test_new_reset_token_invalidates_previous(self, service, session_factory):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        first = await service.request_password_reset(email=EMAIL)
        await service.request_password_reset(email=EMAIL)  # supersedes the first
        with pytest.raises(UnauthorizedError):
            await service.reset_password(token=first, new_password="a-new-passw0rd")
        # exactly one unconsumed reset token remains
        async with session_factory() as session:
            user = await _get_user(session_factory, EMAIL)
            count = len(
                (
                    await session.execute(
                        select(PasswordResetToken).where(
                            PasswordResetToken.user_id == user.id,
                            PasswordResetToken.consumed_at.is_(None),
                        )
                    )
                ).scalars().all()
            )
        assert count == 1


# --- MFA ---------------------------------------------------------------------


class TestMfa:
    async def _enable_mfa(self, service) -> tuple[User, str]:
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        setup = await self._setup(service)
        code = pyotp.TOTP(setup["secret"]).now()
        user = await _get_user(service._sf, EMAIL)
        await service.mfa_enable(user_id=user.id, code=code)
        return user, setup

    async def _setup(self, service) -> dict:
        user = await _get_user(service._sf, EMAIL)
        return await service.mfa_setup(user_id=user.id)

    async def test_setup_returns_secret_uri_and_backup_codes(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        setup = await self._setup(service)
        assert setup["secret"]
        assert setup["otpauth_uri"].startswith("otpauth://totp/")
        assert len(setup["backup_codes"]) == 10

    async def test_mfa_secret_stored_encrypted(self, service, session_factory, settings):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        setup = await self._setup(service)
        stored = await _get_user(session_factory, EMAIL)
        assert stored.mfa_secret != setup["secret"]
        assert decrypt_secret(stored.mfa_secret, settings.jwt_secret) == setup["secret"]

    async def test_enable_requires_valid_code(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        await self._setup(service)
        user = await _get_user(service._sf, EMAIL)
        with pytest.raises(BusinessRuleError):
            await service.mfa_enable(user_id=user.id, code="000000")

    async def test_login_requires_mfa_then_verify(self, service):
        user, setup = await self._enable_mfa(service)
        result = await service.login(email=EMAIL, password=PASSWORD)
        assert isinstance(result, MfaRequiredResult)
        code = pyotp.TOTP(setup["secret"]).now()
        tokens = await service.verify_mfa(mfa_ticket=result.mfa_ticket, code=code)
        assert isinstance(tokens, TokenResult)

    async def test_mfa_verify_wrong_code_rejected(self, service):
        _user, _setup = await self._enable_mfa(service)
        result = await service.login(email=EMAIL, password=PASSWORD)
        with pytest.raises(BusinessRuleError):
            await service.verify_mfa(mfa_ticket=result.mfa_ticket, code="000000")

    async def test_backup_code_is_single_use(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        setup = await self._setup(service)
        user = await _get_user(service._sf, EMAIL)
        await service.mfa_enable(user_id=user.id, code=pyotp.TOTP(setup["secret"]).now())
        backup = setup["backup_codes"][0]
        # First use succeeds (via login → verify path).
        login = await service.login(email=EMAIL, password=PASSWORD)
        assert isinstance(
            await service.verify_mfa(mfa_ticket=login.mfa_ticket, code=backup), TokenResult
        )
        # Second use of the SAME backup code fails.
        login2 = await service.login(email=EMAIL, password=PASSWORD)
        with pytest.raises(BusinessRuleError):
            await service.verify_mfa(mfa_ticket=login2.mfa_ticket, code=backup)

    async def test_disable_mfa(self, service, session_factory):
        user, setup = await self._enable_mfa(service)
        code = pyotp.TOTP(setup["secret"]).now()
        await service.mfa_disable(user_id=user.id, code=code)
        stored = await _get_user(session_factory, EMAIL)
        assert stored.mfa_enabled_at is None and stored.mfa_secret is None
        # Login no longer requires MFA.
        assert isinstance(await service.login(email=EMAIL, password=PASSWORD), TokenResult)


# --- user read / update ------------------------------------------------------


class TestUserUpdate:
    async def _user_id(self, service) -> object:
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        return (await _get_user(service._sf, EMAIL)).id

    async def test_update_display_name(self, service):
        uid = await self._user_id(service)
        updated = await service.update_user(
            user_id=uid, patch=UserUpdate(display_name="新名字")
        )
        assert updated["display_name"] == "新名字"

    async def test_update_avatar_requires_https(self, service):
        uid = await self._user_id(service)
        with pytest.raises(ValidationError):
            await service.update_user(
                user_id=uid, patch=UserUpdate(avatar_url="http://insecure/x.png")
            )

    async def test_update_invalid_timezone(self, service):
        uid = await self._user_id(service)
        with pytest.raises(BusinessRuleError) as exc:
            await service.update_user(
                user_id=uid, patch=UserUpdate(timezone="Mars/Olympus_Mons")
            )
        assert exc.value.code == "invalid_timezone"

    async def test_update_valid_timezone(self, service):
        uid = await self._user_id(service)
        updated = await service.update_user(
            user_id=uid, patch=UserUpdate(timezone="Asia/Shanghai")
        )
        assert updated["timezone"] == "Asia/Shanghai"

    async def test_settings_shallow_merge_and_validation(self, service):
        uid = await self._user_id(service)
        await service.update_user(user_id=uid, patch=UserUpdate(settings={"locale": "zh-CN"}))
        updated = await service.update_user(
            user_id=uid, patch=UserUpdate(settings={"theme": "dark"})
        )
        # locale preserved, theme added (key-level shallow merge).
        assert updated["settings"] == {"locale": "zh-CN", "theme": "dark"}

    async def test_settings_unsupported_locale(self, service):
        uid = await self._user_id(service)
        with pytest.raises(BusinessRuleError) as exc:
            await service.update_user(user_id=uid, patch=UserUpdate(settings={"locale": "fr-FR"}))
        assert exc.value.code == "unsupported_locale"

    async def test_settings_invalid_theme(self, service):
        uid = await self._user_id(service)
        # auth.md §3.1/§5.1 + README §9 T32: invalid theme → 422 validation_error.
        with pytest.raises(BusinessRuleError) as exc:
            await service.update_user(
                user_id=uid, patch=UserUpdate(settings={"theme": "neon"})
            )
        assert exc.value.code == "validation_error"

    async def test_settings_explicit_null_clears_key(self, service):
        """Explicit null in settings.locale/theme pops the key (MES-24 清除语义)."""
        uid = await self._user_id(service)
        # Set both keys first.
        await service.update_user(
            user_id=uid, patch=UserUpdate(settings={"locale": "zh-CN", "theme": "dark"})
        )
        # Clear locale with explicit null → key popped, theme preserved.
        cleared = await service.update_user(
            user_id=uid, patch=UserUpdate(settings={"locale": None})
        )
        assert "locale" not in cleared["settings"]
        assert cleared["settings"]["theme"] == "dark"
        # Clear theme with explicit null → both keys gone.
        final = await service.update_user(
            user_id=uid, patch=UserUpdate(settings={"theme": None})
        )
        assert final["settings"] == {}

    async def test_get_user_not_found(self, service):
        import uuid

        with pytest.raises(NotFoundError):
            await service.get_user(user_id=uuid.uuid4())


# --- sessions ----------------------------------------------------------------


class TestSessions:
    async def test_list_and_revoke_sessions(self, service):
        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        a = await service.login(email=EMAIL, password=PASSWORD, user_agent="UA-A")
        await service.login(email=EMAIL, password=PASSWORD, user_agent="UA-B")
        user = await _get_user(service._sf, EMAIL)

        sessions = await service.list_sessions(user_id=user.id)
        assert len(sessions) == 2

        await service.revoke_session(user_id=user.id, session_id=sessions[0]["id"])
        remaining = await service.list_sessions(user_id=user.id)
        assert len(remaining) == 1
        assert a.refresh_token  # silence unused

    async def test_revoke_unknown_session_404(self, service):
        import uuid

        await service.register(email=EMAIL, password=PASSWORD, display_name="x")
        user = await _get_user(service._sf, EMAIL)
        with pytest.raises(NotFoundError):
            await service.revoke_session(user_id=user.id, session_id=uuid.uuid4())
