"""message_queue core tests — §2.10 enqueue protocol, §3.8 ack window.

Every test runs against real PostgreSQL: the advisory-lock seq protocol,
clock_timestamp() window ordering and the partial-unique indexes are DB
behaviors no mock can stand in for.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from mesh.config import load_settings
from mesh.db.models.agent import Agent
from mesh.db.models.integration import (
    Integration,
    IntegrationBinding,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.integrations import inbound as inbound_mod
from mesh.integrations.ack import position_hint
from mesh.integrations.connectors import NormalizedEvent
from mesh.integrations.inbound_guards import InboundGuardRejected
from mesh.integrations.message_queue import (
    enqueue_message,
    execution_idempotency_key,
)

pytestmark = pytest.mark.unit

CORP = "dingsample"
CONV_REF = "cidTEST=="
CONV_KEY = f"dingtalk:{CORP}:{CONV_REF}"


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


async def _seed(
    session_factory, *, inbound_queue: str = "serial_conversation", ack_template: str | None = None
):
    """ws + member + agent + im_dingtalk integration + binding."""
    ws, user, member, agent = (uuid.uuid4() for _ in range(4))
    integration, binding = (uuid.uuid4() for _ in range(2))
    config = {"app_key": "dingxxxx", "corp_id": CORP, "inbound_queue": inbound_queue}
    if ack_template is not None:
        config["ack_template"] = ack_template
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=ws, name="Q WS", slug=f"q-{ws.hex[:10]}"))
        session.add(
            User(
                id=user,
                email=f"q-{user.hex[:8]}@mesh.test",
                display_name="Q Admin",
                password_hash="unused",
            )
        )
        await session.flush()
        session.add(
            Member(
                id=member,
                workspace_id=ws,
                member_type="human",
                user_id=user,
                role="admin",
                status="active",
            )
        )
        session.add(
            Agent(
                id=agent,
                workspace_id=ws,
                name="Q Agent",
                owner_user_id=user,
                lifecycle_status="active",
            )
        )
        await session.flush()
        session.add(
            Integration(
                id=integration,
                workspace_id=ws,
                kind="im_dingtalk",
                name="dt-q",
                created_by=member,
                config=config,
            )
        )
        session.add(
            IntegrationBinding(
                id=binding,
                workspace_id=ws,
                integration_id=integration,
                provider="dingtalk",
                provider_tenant_key=CORP,
                external_ref=CONV_REF,
                bound_agent_id=agent,
            )
        )
    return {"ws": ws, "user": user, "integration": integration, "binding": binding, "agent": agent}


async def _event(session_factory, world, *, msg_id: str, payload: dict | None = None):
    async with session_factory() as session, session.begin():
        row = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integration"],
            external_event_id=msg_id,
            event_type="im.message.receive",
            payload=payload or {"text": {"content": "do the thing"}},
            signature_status="valid",
            process_status="received",
        )
        session.add(row)
        await session.flush()
        return row.id


def _normalized(msg_id: str, text_: str = "do the thing", actor: str = "staff001") -> NormalizedEvent:
    return NormalizedEvent(
        external_event_id=msg_id,
        event_type="im.message.receive",
        external_ref=CONV_REF,
        actor_key=actor,
        tenant_key=CORP,
        text=text_,
    )


async def _load(world, integration_id, binding_id, event_id):
    return integration_id, binding_id, event_id


async def _enqueue(
    session_factory, world, *, msg_id: str, text_: str = "do the thing", actor: str = "staff001"
):
    settings = _settings()
    event_id = await _event(session_factory, world, msg_id=msg_id)
    async with session_factory() as session, session.begin():
        integration = await session.get(Integration, world["integration"])
        binding = await session.get(IntegrationBinding, world["binding"])
        event_row = await session.get(IntegrationEvent, event_id)
        return await enqueue_message(
            session,
            settings=settings,
            integration=integration,
            binding=binding,
            event_row=event_row,
            event=_normalized(msg_id, text_=text_, actor=actor),
            provider="dingtalk",
        )


async def _items(session_factory, world) -> list[IntegrationMessageQueue]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(IntegrationMessageQueue)
                    .where(IntegrationMessageQueue.conversation_key == CONV_KEY)
                    .order_by(IntegrationMessageQueue.seq)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


class TestSeqProtocol:
    async def test_serial_pending_with_snapshots(self, session_factory):
        world = await _seed(session_factory)
        result = await _enqueue(session_factory, world, msg_id="m-1", text_="hello\nworld")
        assert result.item.seq == 1
        assert result.item.state == "pending"
        assert result.item.dispatch_mode == "serial_conversation"
        assert result.item.target_agent_id == world["agent"]
        assert result.item.binding_id == world["binding"]
        assert result.item.integration_id == world["integration"]
        assert result.item.project_id_snapshot is None
        # §2.10: 去控制符/换行 — newlines are stripped, not spaced
        assert result.item.message_excerpt == "helloworld"
        assert result.item.sender_identity_key == f"dingtalk:{CORP}:staff001"
        assert result.item.binding_display.startswith("dt-q / ")
        assert result.item.ack_window_at is not None

    async def test_seq_monotonic_per_conversation(self, session_factory):
        world = await _seed(session_factory)
        for i in range(1, 4):
            await _enqueue(session_factory, world, msg_id=f"m-{i}")
        items = await _items(session_factory, world)
        assert [i.seq for i in items] == [1, 2, 3]
        assert all(i.state == "pending" for i in items)

    async def test_conversations_independent(self, session_factory):
        world = await _seed(session_factory)
        await _enqueue(session_factory, world, msg_id="m-1")
        # second conversation on the same binding
        async with session_factory() as session, session.begin():
            binding2 = IntegrationBinding(
                workspace_id=world["ws"],
                integration_id=world["integration"],
                provider="dingtalk",
                provider_tenant_key=CORP,
                external_ref="cidOTHER==",
                bound_agent_id=world["agent"],
            )
            session.add(binding2)
        event_id = await _event(session_factory, world, msg_id="m-2")
        settings = _settings()
        async with session_factory() as session, session.begin():
            integration = await session.get(Integration, world["integration"])
            event_row = await session.get(IntegrationEvent, event_id)
            ev = NormalizedEvent(
                external_event_id="m-2",
                event_type="im.message.receive",
                external_ref="cidOTHER==",
                actor_key="staff001",
                tenant_key=CORP,
                text="other conv",
            )
            result = await enqueue_message(
                session,
                settings=settings,
                integration=integration,
                binding=binding2,
                event_row=event_row,
                event=ev,
                provider="dingtalk",
            )
        assert result.item.seq == 1  # own conversation, own numbering


class TestAckWindow:
    async def test_first_is_leader_writes_im_send(self, session_factory):
        world = await _seed(session_factory)
        result = await _enqueue(session_factory, world, msg_id="m-1")
        assert result.leader is True
        assert result.item.ack_leader_id == result.item.id  # self-reference
        async with session_factory() as session:
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.event_type == "im.send")
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1
        assert events[0].payload["kind"] == "ack"
        assert events[0].payload["queue_item_id"] == str(result.item.id)
        expected_key = f"ws:{world['ws']}:" + hashlib.sha256(
            f"{result.item.id}|ack".encode()
        ).hexdigest()
        assert events[0].idempotency_key == expected_key

    async def test_second_in_window_is_follower_no_event(self, session_factory):
        world = await _seed(session_factory)
        first = await _enqueue(session_factory, world, msg_id="m-1")
        second = await _enqueue(session_factory, world, msg_id="m-2")
        assert second.leader is False
        assert second.item.ack_leader_id == first.item.id
        async with session_factory() as session:
            count = len(
                
                    (
                        await session.execute(
                            select(OutboxEvent).where(OutboxEvent.event_type == "im.send")
                        )
                    )
                    .scalars()
                    .all()
                
            )
        assert count == 1  # follower wrote nothing external

    async def test_outside_window_new_leader(self, session_factory):
        world = await _seed(session_factory, )
        first = await _enqueue(session_factory, world, msg_id="m-1")
        # push the leader's window into the past (beyond 5s)
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE integration_message_queue SET ack_window_at = now() - interval '30 seconds' "
                    "WHERE id = :id"
                ),
                {"id": first.item.id},
            )
        second = await _enqueue(session_factory, world, msg_id="m-2")
        assert second.leader is True
        assert second.item.ack_leader_id == second.item.id

    async def test_window_uses_lock_ordered_time_not_enqueued_at(self, session_factory):
        """T39-16: a row enqueued EARLIER (enqueued_at) but locking LATER must
        fall into the existing window by ack_window_at — never create a second
        leader because its transaction started first."""
        world = await _seed(session_factory)
        first = await _enqueue(session_factory, world, msg_id="m-1")
        # Make the leader's enqueued_at NEWER than the next item will be —
        # the inverse of acquisition order. Window judgment must ignore it.
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE integration_message_queue SET enqueued_at = now() + interval '10 seconds' "
                    "WHERE id = :id"
                ),
                {"id": first.item.id},
            )
        second = await _enqueue(session_factory, world, msg_id="m-2")
        items = await _items(session_factory, world)
        assert items[1].enqueued_at < items[0].enqueued_at  # inversion present
        assert second.leader is False  # still a follower by lock-ordered time
        assert second.item.ack_leader_id == first.item.id

    async def test_ack_disabled_no_leader_no_events(self, session_factory):
        world = await _seed(session_factory, ack_template="")
        r1 = await _enqueue(session_factory, world, msg_id="m-1")
        r2 = await _enqueue(session_factory, world, msg_id="m-2")
        assert r1.leader is False and r2.leader is False
        assert r1.item.ack_leader_id is None
        assert r2.item.ack_leader_id is None
        async with session_factory() as session:
            count = len(
                
                    (
                        await session.execute(
                            select(OutboxEvent).where(OutboxEvent.event_type == "im.send")
                        )
                    )
                    .scalars()
                    .all()
                
            )
        assert count == 0


class TestDispatchModeSnapshot:
    async def test_parallel_direct_dispatch(self, session_factory):
        world = await _seed(session_factory, inbound_queue="parallel")
        result = await _enqueue(session_factory, world, msg_id="m-1")
        assert result.dispatched is True
        assert result.item.state == "dispatching"
        assert result.item.dispatch_mode == "parallel"
        assert result.item.lease_expires_at is not None
        async with session_factory() as session:
            evt = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                    )
                )
                .scalar_one_or_none()
            )
        assert evt is not None
        assert evt.payload["trigger"] == "integration"
        assert evt.payload["queue_item_id"] == str(result.item.id)
        k = execution_idempotency_key(
            agent_id=world["agent"], binding_id=world["binding"], external_event_id="m-1"
        )
        assert evt.payload["idempotency_key"] == k
        assert evt.idempotency_key == f"ws:{world['ws']}:{k}"

    async def test_drain_then_switch_forces_serial(self, session_factory):
        """Mode flipped to parallel but a serial item is still in flight →
        new item snapshots serial_conversation and stays pending."""
        world = await _seed(session_factory)  # serial
        first = await _enqueue(session_factory, world, msg_id="m-1")
        async with session_factory() as session, session.begin():
            row = await session.get(IntegrationMessageQueue, first.item.id)
            row.state = "processing"  # in-flight serial residue
        # flip the integration to parallel
        async with session_factory() as session, session.begin():
            integration = await session.get(Integration, world["integration"])
            config = dict(integration.config)
            config["inbound_queue"] = "parallel"
            integration.config = config
        second = await _enqueue(session_factory, world, msg_id="m-2")
        assert second.item.dispatch_mode == "serial_conversation"
        assert second.item.state == "pending"
        assert second.dispatched is False

    async def test_parallel_after_serial_drained(self, session_factory):
        world = await _seed(session_factory)
        first = await _enqueue(session_factory, world, msg_id="m-1")
        async with session_factory() as session, session.begin():
            row = await session.get(IntegrationMessageQueue, first.item.id)
            row.state = "done"  # serial lane drained (terminal)
            integration = await session.get(Integration, world["integration"])
            config = dict(integration.config)
            config["inbound_queue"] = "parallel"
            integration.config = config
        second = await _enqueue(session_factory, world, msg_id="m-2")
        assert second.item.dispatch_mode == "parallel"
        assert second.item.state == "dispatching"

    async def test_target_snapshot_not_retroactive(self, session_factory):
        world = await _seed(session_factory)
        await _enqueue(session_factory, world, msg_id="m-1")
        # retarget the binding after enqueue
        async with session_factory() as session, session.begin():
            new_agent = Agent(
                workspace_id=world["ws"],
                name="Q Agent 2",
                owner_user_id=world["user"],
                lifecycle_status="active",
            )
            session.add(new_agent)
            await session.flush()
            binding = await session.get(IntegrationBinding, world["binding"])
            binding.bound_agent_id = new_agent.id
            new_agent_id = new_agent.id
        items = await _items(session_factory, world)
        assert items[0].target_agent_id == world["agent"]  # snapshot holds
        assert items[0].target_agent_id != new_agent_id


class TestGuardsAndDedupe:
    async def test_depth_guard_under_lock(self, session_factory):
        world = await _seed(session_factory, )
        settings = _settings(im_queue_max_pending_per_conversation=2)
        for i in (1, 2):
            await _enqueue(session_factory, world, msg_id=f"m-{i}")
        event_id = await _event(session_factory, world, msg_id="m-3")
        async with session_factory() as session, session.begin():
            integration = await session.get(Integration, world["integration"])
            binding = await session.get(IntegrationBinding, world["binding"])
            event_row = await session.get(IntegrationEvent, event_id)
            with pytest.raises(InboundGuardRejected) as exc:
                await enqueue_message(
                    session,
                    settings=settings,
                    integration=integration,
                    binding=binding,
                    event_row=event_row,
                    event=_normalized("m-3"),
                    provider="dingtalk",
                )
        assert exc.value.reason == "queue_depth"

    async def test_duplicate_event_not_requeued(self, session_factory):
        world = await _seed(session_factory)
        await _enqueue(session_factory, world, msg_id="m-1")
        # same integration_event_id again → uq_imq_event
        settings = _settings()
        async with session_factory() as session, session.begin():
            integration = await session.get(Integration, world["integration"])
            binding = await session.get(IntegrationBinding, world["binding"])
            event_row = (
                (
                    await session.execute(
                        select(IntegrationEvent).where(
                            IntegrationEvent.external_event_id == "m-1"
                        )
                    )
                )
                .scalar_one()
            )
            with pytest.raises(IntegrityError):
                await enqueue_message(
                    session,
                    settings=settings,
                    integration=integration,
                    binding=binding,
                    event_row=event_row,
                    event=_normalized("m-1"),
                    provider="dingtalk",
                )


class TestHelpers:
    async def test_position_hint_counts_smaller_pending(self, session_factory):
        world = await _seed(session_factory)
        for i in (1, 2, 3):
            await _enqueue(session_factory, world, msg_id=f"m-{i}")
        items = await _items(session_factory, world)
        async with session_factory() as session:
            pos3 = await position_hint(session, item=items[2])
            pos1 = await position_hint(session, item=items[0])
        assert pos1 == 1
        assert pos3 == 3

    def test_idempotency_key_formula_matches_inbound(self):
        agent_id, binding_id = uuid.uuid4(), uuid.uuid4()
        assert execution_idempotency_key(
            agent_id=agent_id, binding_id=binding_id, external_event_id="evt-1"
        ) == inbound_mod.enqueue_idempotency_key(
            agent_id=agent_id, binding_id=binding_id, external_event_id="evt-1"
        )

    async def test_queue_updated_emitted(self, session_factory):
        world = await _seed(session_factory)
        await _enqueue(session_factory, world, msg_id="m-1")
        async with session_factory() as session:
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "realtime.publish"
                        )
                    )
                )
                .scalars()
                .all()
            )
        queue_events = [e for e in events if e.payload.get("event") == "integration.queue_updated"]
        assert len(queue_events) == 1
        data = queue_events[0].payload["data"]
        assert data["conversation_key"] == CONV_KEY
        assert data["integration_id"] == str(world["integration"])
        assert "scope" not in data  # workspace-level shape
