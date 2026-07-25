"""Realtime authentication and channel authorization."""

from __future__ import annotations

import uuid
from datetime import timedelta

from mesh.auth.jwt import encode_access_token
from mesh.config import DEV_JWT_SECRET
from mesh.db.models.member import Member
from mesh.db.models.realtime import RealtimeChannel
from mesh.db.models.user import User
from mesh.realtime.auth import (
    ChainedAuthenticator,
    DefaultChannelAuthorizer,
    DevTokenAuthenticator,
    JwtPrincipalAuthenticator,
    NullAuthenticator,
    Principal,
)

WS_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
WS_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


async def test_dev_token_authenticator_accepts_workspace_token():
    principal = await DevTokenAuthenticator().authenticate(f"mesh-dev:{WS_A}")
    assert principal is not None
    assert principal.workspace_ids == frozenset({WS_A})
    assert principal.can_access_workspace(WS_A)
    assert not principal.can_access_workspace(WS_B)


async def test_dev_token_authenticator_rejects_bad_tokens():
    authenticator = DevTokenAuthenticator()
    assert await authenticator.authenticate("mesh-dev:not-a-uuid") is None
    assert await authenticator.authenticate("mesh-dev:") is None
    assert await authenticator.authenticate("other-token") is None
    assert await authenticator.authenticate("") is None


async def test_null_authenticator_rejects_everything():
    assert await NullAuthenticator().authenticate(f"mesh-dev:{WS_A}") is None


async def _seed_channel(session_factory, channel: str, workspace_id) -> None:
    async with session_factory() as session, session.begin():
        session.add(RealtimeChannel(channel=channel, workspace_id=workspace_id))


async def test_authorizer_rejects_unknown_channel(session_factory):
    authorizer = DefaultChannelAuthorizer(session_factory)
    principal = Principal(subject="u", workspace_ids=frozenset({WS_A}))
    assert not await authorizer.authorize(principal, "issue:missing")


async def test_authorizer_rejects_cross_tenant_channel(session_factory, workspace_factory):
    ws_a = await workspace_factory(name="A", slug="auth-a")
    ws_b = await workspace_factory(name="B", slug="auth-b")
    await _seed_channel(session_factory, "issue:cross", ws_a.id)
    authorizer = DefaultChannelAuthorizer(session_factory)

    only_b = Principal(subject="u", workspace_ids=frozenset({ws_b.id}))
    assert not await authorizer.authorize(only_b, "issue:cross")

    has_a = Principal(subject="u", workspace_ids=frozenset({ws_b.id, ws_a.id}))
    assert await authorizer.authorize(has_a, "issue:cross")


async def test_authorizer_rejects_invalid_channel_syntax(session_factory):
    authorizer = DefaultChannelAuthorizer(session_factory)
    principal = Principal(subject="u", workspace_ids=frozenset({WS_A}))
    assert not await authorizer.authorize(principal, "Not A Channel")


async def test_authorizer_returns_owning_workspace(session_factory, workspace_factory):
    ws_a = await workspace_factory(name="A", slug="own-a")
    await _seed_channel(session_factory, "issue:own", ws_a.id)
    authorizer = DefaultChannelAuthorizer(session_factory)
    principal = Principal(subject="u", workspace_ids=frozenset({ws_a.id}))
    owner = await authorizer.authorize(principal, "issue:own")
    assert owner == ws_a.id


async def test_authorizer_returns_none_for_other_tenant(session_factory, workspace_factory):
    ws_a = await workspace_factory(name="A", slug="none-a")
    ws_b = await workspace_factory(name="B", slug="none-b")
    await _seed_channel(session_factory, "issue:other", ws_a.id)
    authorizer = DefaultChannelAuthorizer(session_factory)
    only_b = Principal(subject="u", workspace_ids=frozenset({ws_b.id}))
    assert await authorizer.authorize(only_b, "issue:other") is None


async def test_prefix_checker_can_veto(session_factory, workspace_factory):
    ws_a = await workspace_factory(name="A", slug="veto-a")
    await _seed_channel(session_factory, "project:p1", ws_a.id)
    authorizer = DefaultChannelAuthorizer(session_factory)
    authorizer.register_prefix_checker("project", lambda principal, channel: _deny())
    principal = Principal(subject="u", workspace_ids=frozenset({ws_a.id}))
    assert not await authorizer.authorize(principal, "project:p1")
    # Other entities are unaffected.
    await _seed_channel(session_factory, "issue:i1", ws_a.id)
    assert await authorizer.authorize(principal, "issue:i1")


async def _deny() -> bool:
    return False


# --- JWT principal authenticator (session credentials, README §6.16) ---------

JWT_SECRET = DEV_JWT_SECRET
JWT_ALGORITHM = "HS256"


def _issue_token(user_id) -> str:
    token, _jti = encode_access_token(
        subject=user_id,
        secret=JWT_SECRET,
        algorithm=JWT_ALGORITHM,
        ttl=timedelta(minutes=15),
    )
    return token


async def _seed_user(session_factory, user_id, *, status="active") -> None:
    async with session_factory() as session, session.begin():
        session.add(User(id=user_id, email=f"{user_id}@corp.com", display_name="U", status=status))


async def _seed_membership(session_factory, user_id, workspace_id, *, status="active") -> None:
    async with session_factory() as session, session.begin():
        session.add(
            Member(workspace_id=workspace_id, member_type="human", user_id=user_id, status=status)
        )


def _jwt_authenticator(session_factory) -> JwtPrincipalAuthenticator:
    return JwtPrincipalAuthenticator(
        session_factory, jwt_secret=JWT_SECRET, jwt_algorithm=JWT_ALGORITHM
    )


async def test_jwt_authenticator_maps_active_memberships_to_workspaces(
    session_factory, workspace_factory
):
    user_id = uuid.uuid4()
    ws_a = await workspace_factory(name="A", slug="jwt-a")
    ws_b = await workspace_factory(name="B", slug="jwt-b")
    await _seed_user(session_factory, user_id)
    await _seed_membership(session_factory, user_id, ws_a.id)
    await _seed_membership(session_factory, user_id, ws_b.id)

    principal = await _jwt_authenticator(session_factory).authenticate(_issue_token(user_id))

    assert principal is not None
    assert principal.subject == str(user_id)
    assert principal.workspace_ids == frozenset({ws_a.id, ws_b.id})


async def test_jwt_authenticator_ignores_inactive_memberships(session_factory, workspace_factory):
    user_id = uuid.uuid4()
    ws_a = await workspace_factory(name="A", slug="jwt-inactive-a")
    ws_b = await workspace_factory(name="B", slug="jwt-inactive-b")
    await _seed_user(session_factory, user_id)
    await _seed_membership(session_factory, user_id, ws_a.id)
    await _seed_membership(session_factory, user_id, ws_b.id, status="removed")

    principal = await _jwt_authenticator(session_factory).authenticate(_issue_token(user_id))

    assert principal is not None
    assert principal.workspace_ids == frozenset({ws_a.id})


async def test_jwt_authenticator_rejects_invalid_tokens(session_factory):
    authenticator = _jwt_authenticator(session_factory)
    assert await authenticator.authenticate("not-a-jwt") is None
    assert await authenticator.authenticate(f"mesh-dev:{WS_A}") is None
    assert await authenticator.authenticate("") is None
    # Valid JWT shape but signed with a different secret.
    foreign, _ = encode_access_token(
        subject=uuid.uuid4(), secret="other-secret", algorithm=JWT_ALGORITHM, ttl=timedelta(minutes=5)
    )
    assert await authenticator.authenticate(foreign) is None


async def test_jwt_authenticator_rejects_unknown_or_inactive_user(session_factory):
    authenticator = _jwt_authenticator(session_factory)
    # No users row for this subject.
    assert await authenticator.authenticate(_issue_token(uuid.uuid4())) is None
    # Inactive user.
    disabled = uuid.uuid4()
    await _seed_user(session_factory, disabled, status="disabled")
    assert await authenticator.authenticate(_issue_token(disabled)) is None


async def test_chained_authenticator_returns_first_principal(session_factory, workspace_factory):
    user_id = uuid.uuid4()
    await _seed_user(session_factory, user_id)
    jwt = _jwt_authenticator(session_factory)
    chain = ChainedAuthenticator((jwt, DevTokenAuthenticator()))

    # JWT path.
    via_jwt = await chain.authenticate(_issue_token(user_id))
    assert via_jwt is not None and via_jwt.subject == str(user_id)
    # Falls through to dev token when the JWT decode fails.
    via_dev = await chain.authenticate(f"mesh-dev:{WS_A}")
    assert via_dev is not None and via_dev.workspace_ids == frozenset({WS_A})
    # Both fail.
    assert await chain.authenticate("garbage") is None
