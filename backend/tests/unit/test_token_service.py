"""Unit tests for the API-token service (auth.md §2.5/§3.2/§5.2/§5.5).

Run against the real migrated PostgreSQL test database. Time is injected so
expiry is deterministic. Members are seeded by raw INSERT (mirrors
test_rbac.py); agent rows set ``agent_id`` directly (the agents-table FK is
deferred to the agent.md increment).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from mesh.auth.tokens import (
    AGENT_TOKEN_PREFIX,
    PAT_TOKEN_PREFIX,
    ResolvedToken,
    TokenService,
)
from mesh.db.models.api_token import ApiToken
from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member
from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError, ValidationError

pytestmark = pytest.mark.unit

START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock(START)


@pytest.fixture
def tokens(session_factory, clock):
    return TokenService(session_factory, clock=clock)


async def _seed_workspace(session_factory) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        return (
            await session.execute(
                text("INSERT INTO workspaces (name, slug) VALUES ('W', :s) RETURNING id"),
                {"s": f"ws-{uuid.uuid4().hex[:12]}"},
            )
        ).scalar_one()


async def _seed_member(
    session_factory, workspace_id: uuid.UUID, role: str, *, member_type="human", status="active"
) -> Member:
    async with session_factory() as session, session.begin():
        if member_type == "human":
            user_id = (
                await session.execute(
                    text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'U') RETURNING id"
                    ),
                    {"e": f"{uuid.uuid4().hex[:12]}@corp.com"},
                )
            ).scalar_one()
            member_id = (
                await session.execute(
                    text(
                        "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                        "VALUES (:ws, 'human', :u, :role, :status) RETURNING id"
                    ),
                    {"ws": workspace_id, "u": user_id, "role": role, "status": status},
                )
            ).scalar_one()
        else:
            owner_id = (
                await session.execute(
                    text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'Owner') RETURNING id"
                    ),
                    {"e": f"{uuid.uuid4().hex[:12]}@corp.com"},
                )
            ).scalar_one()
            # Agent roster rows reference a real agents row (composite FK).
            agent_id = (
                await session.execute(
                    text(
                        "INSERT INTO agents (workspace_id, name, owner_user_id) "
                        "VALUES (:ws, 'Token Agent', :o) RETURNING id"
                    ),
                    {"ws": workspace_id, "o": owner_id},
                )
            ).scalar_one()
            member_id = (
                await session.execute(
                    text(
                        "INSERT INTO members (workspace_id, member_type, agent_id, role, status) "
                        "VALUES (:ws, 'agent', :a, :role, :status) RETURNING id"
                    ),
                    {
                        "ws": workspace_id,
                        "a": agent_id,
                        "role": role,
                        "status": status,
                    },
                )
            ).scalar_one()
    async with session_factory() as session:
        return await session.get(Member, member_id)


# --- creation -----------------------------------------------------------------


class TestCreateToken:
    async def test_create_pat_returns_plaintext_once_and_stores_hash(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        owner = await _seed_member(session_factory, ws, "owner")
        data = await tokens.create_token(
            actor=owner, workspace_id=ws, name="ci", scopes=["issue:read", "comment:write"]
        )
        assert data["token"].startswith(PAT_TOKEN_PREFIX)
        assert data["prefix"] == data["token"][:12]
        assert data["scopes"] == ["comment:write", "issue:read"]
        # DB stores ONLY the hash — never the plaintext.
        async with session_factory() as session:
            row = await session.scalar(select(ApiToken).where(ApiToken.id == data["id"]))
        assert row.token_hash != data["token"]
        assert len(row.token_hash) == 64  # sha256 hex
        assert data["token"].endswith(row.token_hash) is False

    async def test_plaintext_absent_from_list(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        owner = await _seed_member(session_factory, ws, "owner")
        await tokens.create_token(actor=owner, workspace_id=ws, name="ci")
        items = await tokens.list_tokens(actor=owner, workspace_id=ws)
        assert items and "token" not in items[0]

    async def test_role_override_above_holder_role_rejected_422(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        member = await _seed_member(session_factory, ws, "member")
        with pytest.raises(BusinessRuleError) as exc:
            await tokens.create_token(
                actor=member, workspace_id=ws, name="x", role_override="admin"
            )
        assert exc.value.code == "role_override_too_high"
        assert exc.value.status_code == 422

    async def test_role_override_at_or_below_holder_role_ok(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        admin = await _seed_member(session_factory, ws, "admin")
        data = await tokens.create_token(
            actor=admin, workspace_id=ws, name="x", role_override="member"
        )
        assert data["role_override"] == "member"

    async def test_invalid_role_override_value_400(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        owner = await _seed_member(session_factory, ws, "owner")
        with pytest.raises(ValidationError):
            await tokens.create_token(
                actor=owner, workspace_id=ws, name="x", role_override="superuser"
            )

    async def test_invalid_name_rejected(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        owner = await _seed_member(session_factory, ws, "owner")
        with pytest.raises(ValidationError):
            await tokens.create_token(actor=owner, workspace_id=ws, name="   ")

    async def test_inactive_owner_rejected(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        admin = await _seed_member(session_factory, ws, "admin")
        disabled = await _seed_member(session_factory, ws, "member", status="disabled")
        with pytest.raises(BusinessRuleError) as exc:
            await tokens.create_token(
                actor=admin, workspace_id=ws, name="x", owner_member_id=disabled.id
            )
        assert exc.value.code == "owner_not_active"

    async def test_create_for_member_in_other_workspace_not_found(self, tokens, session_factory):
        ws_a = await _seed_workspace(session_factory)
        ws_b = await _seed_workspace(session_factory)
        admin_a = await _seed_member(session_factory, ws_a, "admin")
        member_b = await _seed_member(session_factory, ws_b, "member")
        with pytest.raises(NotFoundError):
            await tokens.create_token(
                actor=admin_a, workspace_id=ws_a, name="x", owner_member_id=member_b.id
            )

    async def test_create_writes_audit_row(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        owner = await _seed_member(session_factory, ws, "owner")
        await tokens.create_token(actor=owner, workspace_id=ws, name="ci")
        async with session_factory() as session:
            actions = (
                (
                    await session.execute(
                        select(AuditLog.action).where(AuditLog.workspace_id == ws)
                    )
                )
                .scalars()
                .all()
            )
        assert "token.created" in actions


# --- agent credentials --------------------------------------------------------


class TestAgentToken:
    async def test_agent_token_prefix_and_default_deny_trigger(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        admin = await _seed_member(session_factory, ws, "admin")
        agent = await _seed_member(session_factory, ws, "member", member_type="agent")
        data = await tokens.create_token(
            actor=admin,
            workspace_id=ws,
            name="runtime",
            scopes=["issue:read", "comment:write", "agent:trigger"],
            owner_member_id=agent.id,
        )
        assert data["token"].startswith(AGENT_TOKEN_PREFIX)
        # Z5 / §5.2: agent credentials drop agent:trigger by default (anti-loop).
        assert "agent:trigger" not in data["scopes"]
        assert "issue:read" in data["scopes"]


# --- listing ------------------------------------------------------------------


class TestListTokens:
    async def test_member_sees_only_own_admin_sees_all(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        admin = await _seed_member(session_factory, ws, "admin")
        member = await _seed_member(session_factory, ws, "member")
        await tokens.create_token(actor=admin, workspace_id=ws, name="a")
        await tokens.create_token(actor=member, workspace_id=ws, name="m")

        own = await tokens.list_tokens(actor=member, workspace_id=ws)
        assert [t["name"] for t in own] == ["m"]
        all_ = await tokens.list_tokens(actor=admin, workspace_id=ws)
        assert {t["name"] for t in all_} == {"a", "m"}


# --- revocation ---------------------------------------------------------------


class TestRevokeToken:
    async def test_holder_revokes_own(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        member = await _seed_member(session_factory, ws, "member")
        data = await tokens.create_token(actor=member, workspace_id=ws, name="m")
        await tokens.revoke_token(actor=member, workspace_id=ws, token_id=data["id"])
        async with session_factory() as session:
            row = await session.get(ApiToken, data["id"])
        assert row.revoked_at is not None

    async def test_non_holder_non_admin_forbidden(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        owner = await _seed_member(session_factory, ws, "owner")
        other = await _seed_member(session_factory, ws, "member")
        data = await tokens.create_token(actor=owner, workspace_id=ws, name="o")
        with pytest.raises(ForbiddenError):
            await tokens.revoke_token(actor=other, workspace_id=ws, token_id=data["id"])

    async def test_admin_revokes_others(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        admin = await _seed_member(session_factory, ws, "admin")
        member = await _seed_member(session_factory, ws, "member")
        data = await tokens.create_token(actor=member, workspace_id=ws, name="m")
        await tokens.revoke_token(actor=admin, workspace_id=ws, token_id=data["id"])
        async with session_factory() as session:
            assert (await session.get(ApiToken, data["id"])).revoked_at is not None

    async def test_revoke_unknown_404(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        owner = await _seed_member(session_factory, ws, "owner")
        with pytest.raises(NotFoundError):
            await tokens.revoke_token(actor=owner, workspace_id=ws, token_id=uuid.uuid4())

    async def test_revoke_writes_audit_row(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        owner = await _seed_member(session_factory, ws, "owner")
        data = await tokens.create_token(actor=owner, workspace_id=ws, name="ci")
        await tokens.revoke_token(actor=owner, workspace_id=ws, token_id=data["id"])
        async with session_factory() as session:
            actions = (
                (
                    await session.execute(
                        select(AuditLog.action).where(AuditLog.workspace_id == ws)
                    )
                )
                .scalars()
                .all()
            )
        assert "token.revoked" in actions


# --- resolution (Bearer → principal) -----------------------------------------


class TestResolvePat:
    async def test_resolve_valid_pat(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        member = await _seed_member(session_factory, ws, "member")
        data = await tokens.create_token(
            actor=member, workspace_id=ws, name="ci", scopes=["issue:read", "comment:write"]
        )
        resolved = await tokens.resolve_pat(token=data["token"])
        assert isinstance(resolved, ResolvedToken)
        assert resolved.workspace_id == ws
        assert resolved.owner_member_id == member.id
        assert resolved.role == "member"
        assert resolved.member_type == "human"
        assert resolved.can("issue:read")
        assert not resolved.can("workspace:billing")

    async def test_resolve_unknown_returns_none(self, tokens):
        assert await tokens.resolve_pat(token=PAT_TOKEN_PREFIX + "bogus") is None

    async def test_resolve_revoked_returns_none(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        member = await _seed_member(session_factory, ws, "member")
        data = await tokens.create_token(actor=member, workspace_id=ws, name="ci")
        await tokens.revoke_token(actor=member, workspace_id=ws, token_id=data["id"])
        assert await tokens.resolve_pat(token=data["token"]) is None

    async def test_resolve_expired_returns_none(self, tokens, session_factory, clock):
        ws = await _seed_workspace(session_factory)
        member = await _seed_member(session_factory, ws, "member")
        data = await tokens.create_token(
            actor=member,
            workspace_id=ws,
            name="ci",
            expires_at=START + timedelta(hours=1),
        )
        assert await tokens.resolve_pat(token=data["token"]) is not None
        clock.advance(hours=2)
        assert await tokens.resolve_pat(token=data["token"]) is None

    async def test_role_override_lowers_effective_role(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        admin = await _seed_member(session_factory, ws, "admin")
        data = await tokens.create_token(
            actor=admin,
            workspace_id=ws,
            name="ci",
            scopes=["issue:read", "workspace:settings"],
            role_override="member",
        )
        resolved = await tokens.resolve_pat(token=data["token"])
        assert resolved.role == "member"
        # workspace:settings is admin-only; a member-override token cannot hold it.
        assert not resolved.can("workspace:settings")
        assert resolved.can("issue:read")

    async def test_scope_intersection_with_role(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        member = await _seed_member(session_factory, ws, "member")
        # member requests an owner-only scope — it is intersected away.
        data = await tokens.create_token(
            actor=member, workspace_id=ws, name="ci", scopes=["issue:read", "workspace:billing"]
        )
        resolved = await tokens.resolve_pat(token=data["token"])
        assert resolved.can("issue:read")
        assert not resolved.can("workspace:billing")

    async def test_use_time_role_override_violation_raises(self, tokens, session_factory):
        ws = await _seed_workspace(session_factory)
        admin = await _seed_member(session_factory, ws, "admin")
        data = await tokens.create_token(
            actor=admin, workspace_id=ws, name="ci", role_override="admin"
        )
        # Downgrade the holder below the override AFTER issuance.
        async with session_factory() as session, session.begin():
            member = await session.get(Member, admin.id)
            member.role = "member"
        with pytest.raises(BusinessRuleError) as exc:
            await tokens.resolve_pat(token=data["token"])
        assert exc.value.code == "role_override_too_high"


# --- C4: token revocation realtime broadcast (§3.7/§5.6) ---------------------


class TestRevocationBroadcast:
    async def test_revoke_token_emits_session_revoked_outbox(self, tokens, session_factory):
        from mesh.db.models.outbox import OutboxEvent

        ws = await _seed_workspace(session_factory)
        member = await _seed_member(session_factory, ws, "member")
        data = await tokens.create_token(
            actor=member, workspace_id=ws, name="ci", scopes=["issue:read"]
        )
        await tokens.revoke_token(actor=member, workspace_id=ws, token_id=data["id"])

        async with session_factory() as session:
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.workspace_id == ws)
                    )
                )
                .scalars()
                .all()
            )
        realtime = [e for e in events if e.event_type == "realtime.publish"]
        assert realtime, "token revocation must publish a realtime outbox event"
        payload = realtime[0].payload
        assert payload["event"] == "session.revoked"
        assert payload["channel"] == f"workspace:{ws}"
        assert payload["data"]["token_id"] == str(data["id"])

    async def test_revoke_idempotent_no_duplicate_broadcast(self, tokens, session_factory):
        from mesh.db.models.outbox import OutboxEvent

        ws = await _seed_workspace(session_factory)
        member = await _seed_member(session_factory, ws, "member")
        data = await tokens.create_token(actor=member, workspace_id=ws, name="ci")
        await tokens.revoke_token(actor=member, workspace_id=ws, token_id=data["id"])
        # Second revoke is a no-op (already revoked) → no second broadcast.
        await tokens.revoke_token(actor=member, workspace_id=ws, token_id=data["id"])
        async with session_factory() as session:
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.workspace_id == ws)
                    )
                )
                .scalars()
                .all()
            )
        broadcasts = [
            e
            for e in events
            if e.event_type == "realtime.publish"
            and e.payload.get("event") == "session.revoked"
        ]
        assert len(broadcasts) == 1
