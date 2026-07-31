"""Dispatcher + lease-repair tests — integrations.md §3.9 (T39-9/14/17).

Real PostgreSQL only: FOR UPDATE SKIP LOCKED contention, the partial unique
serial-lane index and the outbox rearm four-state behavior are DB semantics.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC

import pytest
from sqlalchemy import select, text

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
from mesh.db.models.runtime import ExecutionAttempt, TaskExecution
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.integrations.dispatcher import (
    dispatcher_loop,
    make_dispatch_wake_handler,
    rearm_row_key,
    run_dispatcher_pass,
    run_lease_repair_pass,
)
from mesh.integrations.message_queue import execution_idempotency_key
from mesh.outbox.service import scope_idempotency_key

pytestmark = pytest.mark.unit

CORP = "dingsample"
CONV_REF = "cidDISP=="
CONV_KEY = f"dingtalk:{CORP}:{CONV_REF}"


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


async def _seed_world(session_factory):
    ws, user, member, agent = (uuid.uuid4() for _ in range(4))
    integration, binding = (uuid.uuid4() for _ in range(2))
    # The binding external-identity key is GLOBAL (uq_binding_external_identity),
    # so each world needs its own external_ref even across tests in one DB
    # session when multiple worlds coexist (queue_events loops). Items still
    # use the fixed CONV_KEY string — the dispatcher matches on the column.
    ref = f"cidDISP-{ws.hex[:8]}=="
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=ws, name="D WS", slug=f"d-{ws.hex[:10]}"))
        session.add(
            User(
                id=user,
                email=f"d-{user.hex[:8]}@mesh.test",
                display_name="D Admin",
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
                name="D Agent",
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
                name="dt-d",
                created_by=member,
                config={"app_key": "dingxxxx", "corp_id": CORP},
            )
        )
        session.add(
            IntegrationBinding(
                id=binding,
                workspace_id=ws,
                integration_id=integration,
                provider="dingtalk",
                provider_tenant_key=CORP,
                external_ref=ref,
                bound_agent_id=agent,
            )
        )
    return {"ws": ws, "integration": integration, "binding": binding, "agent": agent}


async def _seed_item(
    session_factory,
    world,
    *,
    seq: int,
    state: str = "pending",
    msg_id: str | None = None,
    execution_id: uuid.UUID | None = None,
    lease_expired: bool = False,
    target_agent_id: uuid.UUID | None = "USE_WORLD",
    conv_key: str = CONV_KEY,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed an event + queue item; returns (item_id, event_id)."""
    msg_id = msg_id or f"msg-{uuid.uuid4().hex[:8]}"
    item_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        event = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integration"],
            external_event_id=msg_id,
            event_type="im.message.receive",
            payload={"text": {"content": f"task {seq}"}},
            signature_status="valid",
            process_status="dispatched",
        )
        session.add(event)
        await session.flush()
        session.add(
            IntegrationMessageQueue(
                id=item_id,
                workspace_id=world["ws"],
                integration_id=world["integration"],
                binding_id=world["binding"],
                integration_event_id=event.id,
                conversation_key=conv_key,
                seq=seq,
                dispatch_mode="serial_conversation",
                state=state,
                execution_id=execution_id,
                target_agent_id=(
                    world["agent"] if target_agent_id == "USE_WORLD" else target_agent_id
                ),
                message_excerpt=f"task {seq}",
                sender_identity_key=f"dingtalk:{CORP}:staff001",
                binding_display="dt-d / " + CONV_REF,
                lease_expires_at=(
                    text("now() - interval '60 seconds'") if lease_expired else None
                ),
            )
        )
        return item_id, event.id


async def _seed_execution(
    session_factory, world, *, status: str = "queued", idem: str | None = None, queued_old=False
) -> uuid.UUID:
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id,
                workspace_id=world["ws"],
                agent_id=world["agent"],
                trigger="integration",
                status=status,
                idempotency_key=idem or f"idem-{exec_id.hex[:8]}",
                task_spec={},
                label_requirements={},
                required_capabilities=[],
                config_snapshot={},
            )
        )
        if queued_old:
            await session.flush()
            await session.execute(
                text("UPDATE task_executions SET queued_at = now() - interval '2 hours' WHERE id = :id"),
                {"id": exec_id},
            )
    return exec_id


async def _item(session_factory, item_id) -> IntegrationMessageQueue:
    async with session_factory() as session:
        return await session.get(IntegrationMessageQueue, item_id)


class TestDispatcherPass:
    async def test_dispatches_fifo_head_only(self, session_factory):
        world = await _seed_world(session_factory)
        ids = [await _seed_item(session_factory, world, seq=i) for i in (1, 2, 3)]
        settings = _settings()
        dispatched = await run_dispatcher_pass(session_factory, settings=settings)
        assert dispatched == 1
        first = await _item(session_factory, ids[0][0])
        second = await _item(session_factory, ids[1][0])
        assert first.state == "dispatching"
        assert first.execution_id is None  # bound by the relay consumer, not here
        assert first.lease_expires_at is not None
        assert second.state == "pending"
        # outbox execution.enqueue carries the integration contract fields
        k = execution_idempotency_key(
            agent_id=world["agent"],
            binding_id=world["binding"],
            external_event_id=(
                await _event_msg_id(session_factory, ids[0][1])
            ),
        )
        async with session_factory() as session:
            evt = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                )
            ).scalar_one()
        assert evt.payload["queue_item_id"] == str(first.id)
        assert evt.payload["idempotency_key"] == k
        assert evt.payload["trigger"] == "integration"
        assert evt.idempotency_key == scope_idempotency_key(world["ws"], k)

    async def test_head_transiently_locked_waits_then_dispatches_true_head(
        self, session_factory
    ):
        """§3.9 strict order regression: while the FIFO head is transiently
        row-locked (ack-window write / lease repair / cancel / replica), the
        head selection must WAIT for it — never skip to seq N+1, which would
        dispatch out of order and strand the head behind the active-lane
        unique index until the wrong item's terminal."""
        world = await _seed_world(session_factory)
        ids = [await _seed_item(session_factory, world, seq=i) for i in (1, 2)]
        settings = _settings()

        async def _hold_head_briefly():
            holder = session_factory()
            await holder.begin()
            await holder.execute(
                select(IntegrationMessageQueue)
                .where(IntegrationMessageQueue.id == ids[0][0])
                .with_for_update()
            )
            await asyncio.sleep(0.4)
            await holder.rollback()
            await holder.close()

        holder_task = asyncio.create_task(_hold_head_briefly())
        await asyncio.sleep(0.1)  # let the holder grab the head lock first
        dispatched = await run_dispatcher_pass(session_factory, settings=settings)
        await holder_task

        # Waited out the transient lock, then dispatched the TRUE head.
        assert dispatched == 1
        first = await _item(session_factory, ids[0][0])
        second = await _item(session_factory, ids[1][0])
        assert first.state == "dispatching"
        assert second.state == "pending"

    async def test_lane_occupied_no_dispatch(self, session_factory):
        world = await _seed_world(session_factory)
        await _seed_item(session_factory, world, seq=1, state="processing")
        await _seed_item(session_factory, world, seq=2, state="pending")
        settings = _settings()
        dispatched = await run_dispatcher_pass(session_factory, settings=settings)
        assert dispatched == 0

    async def test_target_unavailable_fails_item(self, session_factory):
        """§2.10: snapshot agent disabled after enqueue → failed(target_unavailable),
        never silently repointed at a retargeted binding agent."""
        world = await _seed_world(session_factory)
        item_id, _ = await _seed_item(session_factory, world, seq=1)
        async with session_factory() as session, session.begin():
            await session.execute(
                text("UPDATE agents SET lifecycle_status = 'paused' WHERE id = :id"),
                {"id": world["agent"]},
            )
        settings = _settings()
        dispatched = await run_dispatcher_pass(session_factory, settings=settings)
        assert dispatched == 1
        item = await _item(session_factory, item_id)
        assert item.state == "failed"
        assert item.finished_at is not None
        # no enqueue event written for the failed item
        async with session_factory() as session:
            enqueues = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                )
            ).scalars().all()
        assert enqueues == []

    async def test_two_conversations_dispatch_concurrently(self, session_factory):
        world = await _seed_world(session_factory)
        await _seed_item(session_factory, world, seq=1)
        # second conversation
        async with session_factory() as session, session.begin():
            binding2 = IntegrationBinding(
                workspace_id=world["ws"],
                integration_id=world["integration"],
                provider="dingtalk",
                provider_tenant_key=CORP,
                external_ref="cidOTHER2==",
                bound_agent_id=world["agent"],
            )
            session.add(binding2)
            await session.flush()
            event = IntegrationEvent(
                workspace_id=world["ws"],
                integration_id=world["integration"],
                external_event_id="msg-other",
                event_type="im.message.receive",
                payload={},
                signature_status="valid",
                process_status="dispatched",
            )
            session.add(event)
            await session.flush()
            session.add(
                IntegrationMessageQueue(
                    workspace_id=world["ws"],
                    integration_id=world["integration"],
                    binding_id=binding2.id,
                    integration_event_id=event.id,
                    conversation_key=f"dingtalk:{CORP}:cidOTHER2==",
                    seq=1,
                    dispatch_mode="serial_conversation",
                    state="pending",
                    target_agent_id=world["agent"],
                )
            )
        settings = _settings()
        dispatched = await run_dispatcher_pass(session_factory, settings=settings)
        assert dispatched == 2


async def _event_msg_id(session_factory, event_id) -> str:
    async with session_factory() as session:
        row = await session.get(IntegrationEvent, event_id)
        return row.external_event_id


class TestLeaseRepair:
    async def test_branch1_terminal_event_lost_backfill(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="completed")
        item_id, _ = await _seed_item(
            session_factory, world, seq=1, state="processing",
            execution_id=exec_id, lease_expired=True,
        )
        settings = _settings()
        handled = await run_lease_repair_pass(session_factory, settings=settings)
        assert handled == 1
        item = await _item(session_factory, item_id)
        assert item.state == "done"
        assert item.finished_at is not None
        # wake emitted so the next item dispatches without the tick
        async with session_factory() as session:
            wakes = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "imq.dispatch_wake")
                )
            ).scalars().all()
        assert len(wakes) == 1

    async def test_branch1_cancelling_to_cancelled_with_feedback(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="cancelled")
        item_id, _ = await _seed_item(
            session_factory, world, seq=1, state="cancelling",
            execution_id=exec_id, lease_expired=True,
        )
        settings = _settings()
        await run_lease_repair_pass(session_factory, settings=settings)
        item = await _item(session_factory, item_id)
        assert item.state == "cancelled"
        async with session_factory() as session:
            sends = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "im.send")
                )
            ).scalars().all()
        assert len(sends) == 1
        assert sends[0].payload["stage"] == "stopped"
        assert "已停止任务" in sends[0].payload["text"]

    async def test_branch2_inflight_renews_aligned_to_attempt(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="running")
        async with session_factory() as session, session.begin():
            session.add(
                ExecutionAttempt(
                    workspace_id=world["ws"],
                    execution_id=exec_id,
                    attempt_number=1,
                    status="running",
                    lease_seq=1,
                    lease_expires_at=text("now() + interval '2000 seconds'"),
                )
            )
        item_id, _ = await _seed_item(
            session_factory, world, seq=1, state="processing",
            execution_id=exec_id, lease_expired=True,
        )
        settings = _settings()
        await run_lease_repair_pass(session_factory, settings=settings)
        item = await _item(session_factory, item_id)
        assert item.state == "processing"  # NOT failed — long task preserved
        async with session_factory() as session:
            renewed = (await session.get(IntegrationMessageQueue, item_id)).lease_expires_at
        # aligned to the attempt lease (~+2000s), well beyond the 300s buffer
        from datetime import datetime

        delta = (renewed - datetime.now(UTC)).total_seconds()
        assert delta > 1500

    async def test_branch3_queued_within_stuck_renews(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="queued")
        item_id, _ = await _seed_item(
            session_factory, world, seq=1, state="dispatching",
            execution_id=exec_id, lease_expired=True,
        )
        settings = _settings(im_queue_max_stuck_seconds=3600)
        await run_lease_repair_pass(session_factory, settings=settings)
        item = await _item(session_factory, item_id)
        assert item.state == "dispatching"
        assert item.lease_expires_at is not None

    async def test_branch4_queued_beyond_stuck_fails(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(
            session_factory, world, status="queued", queued_old=True
        )
        item_id, _ = await _seed_item(
            session_factory, world, seq=1, state="dispatching",
            execution_id=exec_id, lease_expired=True,
        )
        settings = _settings(im_queue_max_stuck_seconds=3600)
        await run_lease_repair_pass(session_factory, settings=settings)
        item = await _item(session_factory, item_id)
        assert item.state == "failed"  # dispatch_stuck — never re-dispatched
        async with session_factory() as session:
            enqueues = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                )
            ).scalars().all()
        assert len(enqueues) == 0

    async def _seed_original_event(
        self, session_factory, world, item_id, msg_id, *, status: str, old: bool = False
    ) -> str:
        """Insert the original enqueue outbox row under its scoped K; return K."""
        item = await _item(session_factory, item_id)
        k = execution_idempotency_key(
            agent_id=item.target_agent_id, binding_id=item.binding_id, external_event_id=msg_id
        )
        async with session_factory() as session, session.begin():
            session.add(
                OutboxEvent(
                    workspace_id=world["ws"],
                    event_type="execution.enqueue",
                    payload={"idempotency_key": k, "queue_item_id": str(item_id)},
                    idempotency_key=scope_idempotency_key(world["ws"], k),
                    status=status,
                )
            )
            if old:
                await session.flush()
                await session.execute(
                    text(
                        "UPDATE outbox_events SET created_at = now() - interval '30 seconds' "
                        "WHERE idempotency_key = :k"
                    ),
                    {"k": scope_idempotency_key(world["ws"], k)},
                )
            if status == "published":
                await session.flush()
                await session.execute(
                    text(
                        "UPDATE outbox_events SET published_at = now() "
                        "WHERE idempotency_key = :k"
                    ),
                    {"k": scope_idempotency_key(world["ws"], k)},
                )
        return k

    async def _derived_rows(self, session_factory, world, k, item_id):
        k2 = scope_idempotency_key(world["ws"], rearm_row_key(original_key=k, item_id=item_id))
        async with session_factory() as session:
            return (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.idempotency_key == k2)
                    )
                )
                .scalars()
                .all()
            )

    async def test_branch5a_pending_within_sla_no_new_event(self, session_factory):
        world = await _seed_world(session_factory)
        item_id, event_id = await _seed_item(
            session_factory, world, seq=1, state="dispatching", lease_expired=True
        )
        msg_id = await _event_msg_id(session_factory, event_id)
        k = await self._seed_original_event(
            session_factory, world, item_id, msg_id, status="pending", old=False
        )
        settings = _settings(outbox_consume_sla_seconds=120)
        await run_lease_repair_pass(session_factory, settings=settings)
        assert await self._derived_rows(session_factory, world, k, item_id) == []
        item = await _item(session_factory, item_id)
        assert item.state == "dispatching"  # lease renewed, waits for relay

    async def test_branch5a_past_sla_derives(self, session_factory):
        world = await _seed_world(session_factory)
        item_id, event_id = await _seed_item(
            session_factory, world, seq=1, state="dispatching", lease_expired=True
        )
        msg_id = await _event_msg_id(session_factory, event_id)
        k = await self._seed_original_event(
            session_factory, world, item_id, msg_id, status="pending", old=True
        )
        settings = _settings(outbox_consume_sla_seconds=2)
        await run_lease_repair_pass(session_factory, settings=settings)
        derived = await self._derived_rows(session_factory, world, k, item_id)
        assert len(derived) == 1
        assert derived[0].payload["idempotency_key"] == k  # execution-level key preserved
        assert derived[0].payload["queue_item_id"] == str(item_id)

    async def test_branch5b_failed_conditional_rearm(self, session_factory):
        world = await _seed_world(session_factory)
        item_id, event_id = await _seed_item(
            session_factory, world, seq=1, state="dispatching", lease_expired=True
        )
        msg_id = await _event_msg_id(session_factory, event_id)
        k = await self._seed_original_event(
            session_factory, world, item_id, msg_id, status="failed"
        )
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE outbox_events SET delivery_attempts = 5 "
                    "WHERE idempotency_key = :k"
                ),
                {"k": scope_idempotency_key(world["ws"], k)},
            )
        settings = _settings()
        await run_lease_repair_pass(session_factory, settings=settings)
        async with session_factory() as session:
            original = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.idempotency_key == scope_idempotency_key(world["ws"], k)
                    )
                )
            ).scalar_one()
        assert original.status == "pending"
        assert original.delivery_attempts == 0
        assert original.published_at is None
        assert await self._derived_rows(session_factory, world, k, item_id) == []

    async def test_branch5c_published_kept_and_derived(self, session_factory):
        world = await _seed_world(session_factory)
        item_id, event_id = await _seed_item(
            session_factory, world, seq=1, state="dispatching", lease_expired=True
        )
        msg_id = await _event_msg_id(session_factory, event_id)
        k = await self._seed_original_event(
            session_factory, world, item_id, msg_id, status="published"
        )
        settings = _settings()
        await run_lease_repair_pass(session_factory, settings=settings)
        async with session_factory() as session:
            original = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.idempotency_key == scope_idempotency_key(world["ws"], k)
                    )
                )
            ).scalar_one()
        assert original.status == "published"  # original KEPT (audit retention)
        derived = await self._derived_rows(session_factory, world, k, item_id)
        assert len(derived) == 1
        assert derived[0].payload["idempotency_key"] == k

    async def test_branch5d_missing_derives(self, session_factory):
        world = await _seed_world(session_factory)
        item_id, event_id = await _seed_item(
            session_factory, world, seq=1, state="dispatching", lease_expired=True
        )
        msg_id = await _event_msg_id(session_factory, event_id)
        k = execution_idempotency_key(
            agent_id=world["agent"], binding_id=world["binding"], external_event_id=msg_id
        )
        settings = _settings()
        await run_lease_repair_pass(session_factory, settings=settings)
        derived = await self._derived_rows(session_factory, world, k, item_id)
        assert len(derived) == 1
        assert derived[0].payload["queue_item_id"] == str(item_id)

    async def test_rearm_key_formula_matches_t39(self):
        # T39-9/14: sha256(K | 'rearm' | item_id) hex
        import hashlib

        k = "mes82-enqueue-key-1"
        item_id = "item-id"
        assert rearm_row_key(original_key=k, item_id=item_id) == hashlib.sha256(
            f"{k}|rearm|{item_id}".encode()
        ).hexdigest()


class TestLoop:
    async def test_wake_handler_sets_event(self):
        wake = asyncio.Event()
        handler = make_dispatch_wake_handler(wake)
        await handler(None, None)
        assert wake.is_set()

    async def test_dispatcher_loop_honors_stop(self, session_factory):
        settings = _settings(im_dispatch_tick_seconds=0.05)
        wake = asyncio.Event()
        stop = asyncio.Event()

        async def _stop_soon():
            await asyncio.sleep(0.1)
            stop.set()
            wake.set()

        await asyncio.wait_for(
            asyncio.gather(
                dispatcher_loop(session_factory, settings=settings, wake=wake, stop=stop),
                _stop_soon(),
            ),
            timeout=15,
        )
