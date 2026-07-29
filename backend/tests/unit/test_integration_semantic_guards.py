"""inbound_guards tests — §2.10 three semantic guards + one-shot hint flag."""

from __future__ import annotations

import uuid

import pytest

from mesh.config import load_settings
from mesh.db.models.integration import Integration, IntegrationBinding, IntegrationMessageQueue
from mesh.integrations.inbound_guards import (
    InboundGuardRejected,
    check_inbound_guards,
    rate_limit_hint_allowed,
)

pytestmark = pytest.mark.unit

CONV = "dingtalk:dingsample:cidTEST=="


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


async def _seed_world_with_binding(session_factory) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Workspace + im_dingtalk integration + binding (queue parents).

    ``ck_imq_orphan_terminal`` rejects non-terminal rows without parents
    (delete protection, §2.10), so depth fixtures must seed the real chain.
    """
    from mesh.db.models.member import Member
    from mesh.db.models.user import User
    from mesh.db.models.workspace import Workspace

    ws_id, user_id, member_id = (uuid.uuid4() for _ in range(3))
    integration_id, binding_id = (uuid.uuid4() for _ in range(2))
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=ws_id, name="G WS", slug=f"g-{ws_id.hex[:10]}"))
        session.add(
            User(
                id=user_id,
                email=f"g-{user_id.hex[:8]}@mesh.test",
                display_name="G Admin",
                password_hash="unused",
            )
        )
        await session.flush()
        session.add(
            Member(
                id=member_id,
                workspace_id=ws_id,
                member_type="human",
                user_id=user_id,
                role="admin",
                status="active",
            )
        )
        await session.flush()  # composite FK created_by → members must exist
        session.add(
            Integration(
                id=integration_id,
                workspace_id=ws_id,
                kind="im_dingtalk",
                name="dt-guard",
                created_by=member_id,
                config={"app_key": "dingxxxx", "corp_id": "dingsample"},
            )
        )
        session.add(
            IntegrationBinding(
                id=binding_id,
                workspace_id=ws_id,
                integration_id=integration_id,
                provider="dingtalk",
                provider_tenant_key="dingsample",
                external_ref="cidTEST==",
            )
        )
    return ws_id, integration_id, binding_id


async def _seed_pending(
    session_factory,
    ws_id: uuid.UUID,
    integration_id: uuid.UUID,
    binding_id: uuid.UUID,
    count: int,
    states: list[str] | None = None,
) -> None:
    """Seed queue rows for CONV (states default to ``pending``)."""
    async with session_factory() as session, session.begin():
        for seq in range(1, count + 1):
            state = states[seq - 1] if states else "pending"
            session.add(
                IntegrationMessageQueue(
                    workspace_id=ws_id,
                    integration_id=integration_id,
                    binding_id=binding_id,
                    conversation_key=CONV,
                    seq=seq,
                    dispatch_mode="serial_conversation",
                    state=state,
                )
            )


class TestIdentityWindow:
    async def test_within_limit_passes(self, redis_client, db_session):
        settings = _settings(im_inbound_per_identity_per_min=3)
        for _ in range(3):
            await check_inbound_guards(
                redis_client,
                db_session,
                settings=settings,
                provider="dingtalk",
                tenant_key="dingsample",
                user_key="staff-1",
                conversation_key=CONV,
            )

    async def test_over_limit_rejected_identity(self, redis_client, db_session):
        settings = _settings(im_inbound_per_identity_per_min=2)
        for _ in range(2):
            await check_inbound_guards(
                redis_client,
                db_session,
                settings=settings,
                provider="dingtalk",
                tenant_key="dingsample",
                user_key="staff-2",
                conversation_key=CONV,
            )
        with pytest.raises(InboundGuardRejected) as exc:
            await check_inbound_guards(
                redis_client,
                db_session,
                settings=settings,
                provider="dingtalk",
                tenant_key="dingsample",
                user_key="staff-2",
                conversation_key=CONV,
            )
        assert exc.value.reason == "identity_rate"

    async def test_tenant_dimension_isolated(self, redis_client, db_session):
        """Same user key in different tenants has independent windows."""
        settings = _settings(im_inbound_per_identity_per_min=1)
        await check_inbound_guards(
            redis_client,
            db_session,
            settings=settings,
            provider="dingtalk",
            tenant_key="tenantA",
            user_key="staff-shared",
            conversation_key=CONV,
        )
        # Second tenant — same user key — not limited.
        await check_inbound_guards(
            redis_client,
            db_session,
            settings=settings,
            provider="dingtalk",
            tenant_key="tenantB",
            user_key="staff-shared",
            conversation_key=CONV,
        )


class TestConversationWindow:
    async def test_over_limit_rejected_conversation(self, redis_client, db_session):
        settings = _settings(
            im_inbound_per_identity_per_min=100,
            im_inbound_per_conversation_per_min=2,
        )
        for user in ("u1", "u2"):
            await check_inbound_guards(
                redis_client,
                db_session,
                settings=settings,
                provider="dingtalk",
                tenant_key="dingsample",
                user_key=user,
                conversation_key=CONV,
            )
        with pytest.raises(InboundGuardRejected) as exc:
            await check_inbound_guards(
                redis_client,
                db_session,
                settings=settings,
                provider="dingtalk",
                tenant_key="dingsample",
                user_key="u3",
                conversation_key=CONV,
            )
        assert exc.value.reason == "conversation_rate"


class TestQueueDepth:
    async def test_depth_at_cap_rejected(self, redis_client, session_factory):
        ws_id, integration_id, binding_id = await _seed_world_with_binding(session_factory)
        await _seed_pending(session_factory, ws_id, integration_id, binding_id, count=5)
        settings = _settings(im_queue_max_pending_per_conversation=5)
        async with session_factory() as session:
            with pytest.raises(InboundGuardRejected) as exc:
                await check_inbound_guards(
                    redis_client,
                    session,
                    settings=settings,
                    provider="dingtalk",
                    tenant_key="dingsample",
                    user_key="u1",
                    conversation_key=CONV,
                )
            assert exc.value.reason == "queue_depth"

    async def test_depth_below_cap_passes(self, redis_client, session_factory):
        ws_id, integration_id, binding_id = await _seed_world_with_binding(session_factory)
        await _seed_pending(session_factory, ws_id, integration_id, binding_id, count=4)
        settings = _settings(im_queue_max_pending_per_conversation=5)
        async with session_factory() as session:
            await check_inbound_guards(
                redis_client,
                session,
                settings=settings,
                provider="dingtalk",
                tenant_key="dingsample",
                user_key="u1",
                conversation_key=CONV,
            )

    async def test_non_pending_states_not_counted(self, redis_client, session_factory):
        ws_id, integration_id, binding_id = await _seed_world_with_binding(session_factory)
        await _seed_pending(
            session_factory,
            ws_id,
            integration_id,
            binding_id,
            count=3,
            states=["processing", "done", "cancelled"],
        )
        settings = _settings(im_queue_max_pending_per_conversation=1)
        async with session_factory() as session:
            await check_inbound_guards(
                redis_client,
                session,
                settings=settings,
                provider="dingtalk",
                tenant_key="dingsample",
                user_key="u1",
                conversation_key=CONV,
            )


class TestHintFlag:
    async def test_hint_allowed_once_per_window(self, redis_client):
        assert await rate_limit_hint_allowed(redis_client, conversation_key=CONV) is True
        assert await rate_limit_hint_allowed(redis_client, conversation_key=CONV) is False

    async def test_hint_per_conversation(self, redis_client):
        assert await rate_limit_hint_allowed(redis_client, conversation_key="conv:a") is True
        assert await rate_limit_hint_allowed(redis_client, conversation_key="conv:b") is True
