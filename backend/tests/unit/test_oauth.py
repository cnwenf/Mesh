"""Unit tests for the OAuth round-trip (auth.md §1.2 A5/A6, §4.5)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.auth.oauth import (
    MockOAuthProvider,
    OAuthService,
    code_challenge_s256,
    encode_mock_code,
    generate_code_verifier,
)
from mesh.auth.service import AuthService
from mesh.config import load_settings
from mesh.db.models.user import OAuthIdentity, User
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError

pytestmark = pytest.mark.unit

PASSWORD = "a-strong-passw0rd"


@pytest.fixture
def settings(db_url, redis_url):
    return load_settings(
        database_url=db_url, redis_url=redis_url, auth_mode="dev", jwt_secret="oauth-test-secret"
    )


@pytest.fixture
def auth_service(session_factory, settings):
    return AuthService(session_factory, settings)


@pytest.fixture
def oauth(session_factory, auth_service, redis_client):
    service = OAuthService(session_factory, auth_service, redis_client)
    # M1: the mock provider only accepts whitelisted redirect URIs.
    service.register_provider(
        MockOAuthProvider(allowed_redirect_uris=frozenset({"http://cb"}))
    )
    return service


# --- PKCE --------------------------------------------------------------------


def test_code_verifier_is_high_entropy():
    v = generate_code_verifier()
    assert len(v) >= 43
    assert generate_code_verifier() != v


def test_code_challenge_s256_known_vector():
    # RFC 7636 Appendix B test vector.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


async def test_mock_exchange_decodes_code():
    provider = MockOAuthProvider()
    code = encode_mock_code(sub="s1", email="u1@corp.com", name="U One")
    info = await provider.exchange_code(code=code, code_verifier="v", redirect_uri="r")
    assert info.provider_subject == "s1"
    assert info.email == "u1@corp.com"


async def test_mock_exchange_falls_back_on_bad_code():
    provider = MockOAuthProvider()
    info = await provider.exchange_code(code="not-valid", code_verifier="v", redirect_uri="r")
    assert info.provider_subject == "mock-subject-1"


# --- start / state -----------------------------------------------------------


async def test_unknown_provider_404(oauth):
    with pytest.raises(NotFoundError):
        await oauth.start(provider_name="nope", redirect_uri="http://cb")


async def test_start_stores_state_and_returns_url(oauth, redis_client):
    result = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    assert result["state"]
    assert "state=" + result["state"] in result["authorization_url"]
    assert await redis_client.get(f"mesh:oauth:state:{result['state']}") is not None


async def test_bind_mode_requires_user(oauth):
    with pytest.raises(ValidationError):
        await oauth.start(provider_name="mock", redirect_uri="http://cb", mode="bind")


# --- callback: login or register (A5) ----------------------------------------


async def test_callback_auto_registers_and_issues_tokens(oauth, session_factory):
    start = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    code = encode_mock_code(sub="sub-1", email="new@corp.com", name="New User", email_verified=True)
    result = await oauth.callback(provider_name="mock", code=code, state=start["state"])
    assert result["access_token"] and result["refresh_token"]
    # User created (OAuth-only, verified) + identity bound.
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == "new@corp.com"))
        assert user is not None and user.password_hash is None
        assert user.email_verified_at is not None
        identity = await session.scalar(
            select(OAuthIdentity).where(OAuthIdentity.provider_subject == "sub-1")
        )
        assert identity.user_id == user.id


async def test_callback_second_login_reuses_user(oauth, session_factory):
    code = encode_mock_code(sub="sub-2", email="reuse@corp.com", email_verified=True)
    s1 = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    await oauth.callback(provider_name="mock", code=code, state=s1["state"])
    s2 = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    await oauth.callback(provider_name="mock", code=code, state=s2["state"])
    async with session_factory() as session:
        count = (
            await session.execute(select(User).where(User.email == "reuse@corp.com"))
        ).scalars().all()
        assert len(count) == 1


async def test_callback_binds_to_existing_email_user(oauth, auth_service, session_factory):
    # A password user already exists with this email.
    await auth_service.register(email="existing@corp.com", password=PASSWORD, display_name="E")
    code = encode_mock_code(sub="sub-3", email="existing@corp.com", email_verified=True)
    start = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    await oauth.callback(provider_name="mock", code=code, state=start["state"])
    async with session_factory() as session:
        users = (
            await session.execute(select(User).where(User.email == "existing@corp.com"))
        ).scalars().all()
        assert len(users) == 1
        identity = await session.scalar(
            select(OAuthIdentity).where(OAuthIdentity.provider_subject == "sub-3")
        )
        assert identity.user_id == users[0].id


async def test_callback_bad_state_rejected(oauth):
    code = encode_mock_code(sub="sub-x", email="x@corp.com")
    with pytest.raises(ValidationError) as exc:
        await oauth.callback(provider_name="mock", code=code, state="bogus-state")
    assert exc.value.code == "invalid_oauth_state"


async def test_state_is_one_time(oauth):
    code = encode_mock_code(sub="sub-once", email="once@corp.com", email_verified=True)
    start = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    await oauth.callback(provider_name="mock", code=code, state=start["state"])
    with pytest.raises(ValidationError):
        await oauth.callback(provider_name="mock", code=code, state=start["state"])


# --- bind / unbind (A6) ------------------------------------------------------


async def test_bind_and_unbind_keeps_password_method(oauth, auth_service, session_factory):
    user_dict, _ = await auth_service.register(
        email="binder@corp.com", password=PASSWORD, display_name="B"
    )
    user_id = uuid.UUID(str(user_dict["id"]))
    code = encode_mock_code(sub="sub-bind", email="other@corp.com")
    start = await oauth.start(
        provider_name="mock", redirect_uri="http://cb", mode="bind", user_id=user_id
    )
    result = await oauth.callback(provider_name="mock", code=code, state=start["state"])
    assert result["status"] == "bound"

    identities = await oauth.list_identities(user_id=user_id)
    assert [i["provider"] for i in identities] == ["mock"]

    # Unbind OK because a password remains as a login method.
    await oauth.unbind_identity(user_id=user_id, provider_name="mock")
    assert await oauth.list_identities(user_id=user_id) == []


async def test_unbind_refuses_last_login_method(oauth, session_factory):
    # OAuth-only user (no password) — the identity is the sole login method.
    code = encode_mock_code(sub="sub-only", email="only@corp.com", email_verified=True)
    start = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    await oauth.callback(provider_name="mock", code=code, state=start["state"])
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == "only@corp.com"))
    with pytest.raises(BusinessRuleError) as exc:
        await oauth.unbind_identity(user_id=user.id, provider_name="mock")
    assert exc.value.code == "last_login_method"


async def test_bind_identity_already_bound_to_other_conflicts(
    oauth, auth_service, session_factory
):
    # First account registers + binds sub-shared.
    a_dict, _ = await auth_service.register(email="a@corp.com", password=PASSWORD, display_name="A")
    a_id = uuid.UUID(str(a_dict["id"]))
    code = encode_mock_code(sub="sub-shared", email="shared@corp.com")
    s = await oauth.start(provider_name="mock", redirect_uri="http://cb", mode="bind", user_id=a_id)
    await oauth.callback(provider_name="mock", code=code, state=s["state"])
    # Second account tries to bind the SAME provider subject → conflict.
    from mesh.auth.oauth import OAuthUserInfo

    b_dict, _ = await auth_service.register(email="b@corp.com", password=PASSWORD, display_name="B")
    b_id = uuid.UUID(str(b_dict["id"]))
    with pytest.raises(ConflictError):
        await oauth.bind_identity(
            user_id=b_id,
            provider_name="mock",
            userinfo=OAuthUserInfo(provider_subject="sub-shared", email="shared@corp.com"),
        )


async def test_unbind_unknown_identity_404(oauth, auth_service):
    user_dict, _ = await auth_service.register(
        email="noid@corp.com", password=PASSWORD, display_name="N"
    )
    with pytest.raises(NotFoundError):
        await oauth.unbind_identity(user_id=uuid.UUID(str(user_dict["id"])), provider_name="mock")


# --- security: H1 email_verified gate + M1 redirect_uri allowlist ------------


async def test_unverified_email_does_not_autolink_existing_account(oauth, auth_service):
    """H1: an unverified email must NOT link to an existing account (takeover)."""
    await auth_service.register(email="victim@corp.com", password=PASSWORD, display_name="V")
    # Attacker holds an UNVERIFIED victim@corp.com on the provider.
    code = encode_mock_code(sub="attacker", email="victim@corp.com", email_verified=False)
    start = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    with pytest.raises(BusinessRuleError) as exc:
        await oauth.callback(provider_name="mock", code=code, state=start["state"])
    assert exc.value.code == "oauth_email_not_verified"


async def test_unverified_email_does_not_autoregister(oauth, session_factory):
    """H1: an unverified email must NOT auto-register either."""
    code = encode_mock_code(sub="sub-unv", email="fresh@corp.com", email_verified=False)
    start = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    with pytest.raises(BusinessRuleError) as exc:
        await oauth.callback(provider_name="mock", code=code, state=start["state"])
    assert exc.value.code == "oauth_email_not_verified"
    async with session_factory() as session:
        assert (
            await session.scalar(select(User).where(User.email == "fresh@corp.com"))
        ) is None


async def test_missing_email_cannot_autoregister(oauth):
    code = encode_mock_code(sub="sub-noemail", email="", email_verified=True)
    start = await oauth.start(provider_name="mock", redirect_uri="http://cb")
    with pytest.raises(BusinessRuleError) as exc:
        await oauth.callback(provider_name="mock", code=code, state=start["state"])
    assert exc.value.code == "oauth_email_required"


async def test_redirect_uri_not_in_allowlist_rejected(oauth):
    """M1: a redirect_uri outside the exact-match allowlist is rejected (422)."""
    with pytest.raises(BusinessRuleError) as exc:
        await oauth.start(provider_name="mock", redirect_uri="http://evil.example/cb")
    assert exc.value.code == "redirect_uri_not_allowed"
