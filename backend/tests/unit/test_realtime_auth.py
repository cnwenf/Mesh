"""Realtime authentication and channel authorization."""

from __future__ import annotations

import uuid

from mesh.db.models.realtime import RealtimeChannel
from mesh.realtime.auth import (
    DefaultChannelAuthorizer,
    DevTokenAuthenticator,
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
