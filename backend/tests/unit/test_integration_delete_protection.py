"""Delete protection tests (integrations.md §2.10 / §3.9 / §5.6 ①-⑤).

Covers the success-path closure around ``ck_imq_orphan_terminal`` + SET NULL:

* binding delete without ``force`` + non-terminal items → 409
  ``binding_has_active_queue`` (terminal items do NOT block);
* ``?force=cancel`` drains pending items (→ cancelled, reason audit) then the
  DELETE succeeds and the items survive as ``binding_id IS NULL`` orphan audit
  rows with their ``binding_display`` snapshot intact (T39-8);
* fail-closed negative: a direct (endpoint-bypassing) parent DELETE with a
  non-terminal item is rejected by the CHECK (whole statement rolls back);
* project deletion is guarded the same way — it fails while a project-scoped
  binding has non-terminal items, and succeeds once they are force-drained.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.audit import AuditLog
from mesh.db.models.integration import IntegrationBinding, IntegrationMessageQueue
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.tenant import set_tenant_context
from mesh.errors import ConflictError
from mesh.integrations.service import IntegrationService
from mesh.project.service import ProjectService
from tests.unit.integrations_support import make_binding, seed_world

pytestmark = pytest.mark.unit

TEST_SIGNING_SECRET = "delete-protection-test-signing-secret-00"


def _service(session_factory) -> IntegrationService:
    return IntegrationService(session_factory, TEST_SIGNING_SECRET)


async def _seed_item(
    session_factory,
    *,
    world: dict,
    binding: IntegrationBinding,
    state: str = "pending",
    seq: int = 1,
    conversation_key: str | None = None,
    binding_display: str = "room: C_PROTECT",
    excerpt: str = "protect me",
    execution_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        item = IntegrationMessageQueue(
            workspace_id=world["ws"],
            integration_id=binding.integration_id,
            binding_id=binding.id,
            binding_display=binding_display,
            conversation_key=conversation_key or f"slack:T_TEST:{binding.external_ref}",
            seq=seq,
            dispatch_mode="serial_conversation",
            state=state,
            message_excerpt=excerpt,
            sender_identity_key=f"slack:T_TEST:U_ITEM_{seq}",
            execution_id=execution_id,
        )
        session.add(item)
        await session.flush()
        return item.id


async def _load_items(session_factory, ids: list[uuid.UUID]) -> list[IntegrationMessageQueue]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(IntegrationMessageQueue).where(IntegrationMessageQueue.id.in_(ids))
                )
            )
            .scalars()
            .all()
        )


async def _binding_exists(session_factory, binding_id: uuid.UUID) -> bool:
    async with session_factory() as session:
        return await session.get(IntegrationBinding, binding_id) is not None


# ---------------------------------------------------------------------------
# ① No force + non-terminal → 409; terminal items do not block
# ---------------------------------------------------------------------------


async def test_delete_binding_blocked_by_pending_item_409(session_factory):
    world = await seed_world(session_factory)
    binding = await make_binding(session_factory, world=world, provider="slack", external_ref="C_409")
    await _seed_item(session_factory, world=world, binding=binding, state="pending")
    with pytest.raises(ConflictError) as excinfo:
        await _service(session_factory).delete_binding(
            workspace_id=world["ws"], binding_id=binding.id
        )
    assert excinfo.value.code == "binding_has_active_queue"
    assert await _binding_exists(session_factory, binding.id), "binding NOT deleted on 409"


async def test_delete_binding_terminal_items_do_not_block(session_factory):
    world = await seed_world(session_factory)
    binding = await make_binding(session_factory, world=world, provider="slack", external_ref="C_TERM")
    # Only terminal items — deletion proceeds and orphans them via SET NULL.
    item_id = await _seed_item(session_factory, world=world, binding=binding, state="done")
    await _service(session_factory).delete_binding(workspace_id=world["ws"], binding_id=binding.id)
    assert not await _binding_exists(session_factory, binding.id)
    (item,) = await _load_items(session_factory, [item_id])
    assert item.binding_id is None, "terminal item survived as an orphan audit row"
    assert item.state == "done"


# ---------------------------------------------------------------------------
# ② force=cancel → drain, DELETE succeeds, orphans preserve binding_display
# ---------------------------------------------------------------------------


async def test_delete_binding_force_cancel_orphans_preserved(session_factory):
    world = await seed_world(session_factory)
    binding = await make_binding(session_factory, world=world, provider="slack", external_ref="C_FORCE")
    pending_a = await _seed_item(
        session_factory, world=world, binding=binding, state="pending", seq=1,
        binding_display="room: C_FORCE", excerpt="M1",
    )
    pending_b = await _seed_item(
        session_factory, world=world, binding=binding, state="pending", seq=2,
        binding_display="room: C_FORCE", excerpt="M2",
    )

    await _service(session_factory).delete_binding(
        workspace_id=world["ws"],
        binding_id=binding.id,
        force="cancel",
        force_cancel_wait_seconds=1,
        actor_member_id=world["member"],
    )

    # DELETE actually completed — the parent row is gone.
    assert not await _binding_exists(session_factory, binding.id)
    # Both items survived as terminal orphans with snapshots intact (T39-8).
    items = {item.id: item for item in await _load_items(session_factory, [pending_a, pending_b])}
    assert len(items) == 2
    for item in items.values():
        assert item.binding_id is None, "FK SET NULL turned the item into an orphan"
        assert item.state == "cancelled"
        assert item.finished_at is not None
        assert item.binding_display == "room: C_FORCE", "binding_display snapshot preserved"
        assert item.integration_id is not None, "integration parent untouched"

    # The force path audited reason='binding_deleted'.
    async with session_factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "integration_queue.force_cancel")
        )
    assert audit is not None
    assert audit.metadata_["reason"] == "binding_deleted"
    assert set(audit.metadata_["item_ids"]) == {str(pending_a), str(pending_b)}


async def test_delete_binding_invalid_force_rejected(session_factory):
    world = await seed_world(session_factory)
    binding = await make_binding(session_factory, world=world, provider="slack", external_ref="C_BADF")
    from mesh.errors import BusinessRuleError

    with pytest.raises(BusinessRuleError):
        await _service(session_factory).delete_binding(
            workspace_id=world["ws"], binding_id=binding.id, force="explode"
        )


async def test_delete_integration_force_cancel_drains_all_bindings(session_factory):
    world = await seed_world(session_factory)
    binding = await make_binding(session_factory, world=world, provider="slack", external_ref="C_INTF")
    item_id = await _seed_item(session_factory, world=world, binding=binding, state="pending")
    await _service(session_factory).delete_integration(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        force="cancel",
        force_cancel_wait_seconds=1,
        actor_member_id=world["member"],
    )
    # The binding was force-deleted; the item is a cancelled orphan.
    assert not await _binding_exists(session_factory, binding.id)
    (item,) = await _load_items(session_factory, [item_id])
    assert item.binding_id is None and item.state == "cancelled"
    # The integration itself is soft-deleted.
    from mesh.db.models.integration import Integration

    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_slack"])
    assert integration is not None and integration.deleted_at is not None


async def test_delete_binding_force_cancel_inflight_times_out(session_factory):
    """In-flight (processing) item: the force path requests execution cancel
    (persisted same-txn), waits the bounded window, then forces the survivor
    cancelled with an alert (§3.9 ①: 在途项经 runtime 取消服务, 超时强制)."""
    from mesh.db.models.runtime import TaskExecution

    world = await seed_world(session_factory)
    binding = await make_binding(
        session_factory, world=world, provider="slack", external_ref="C_INFLIGHT"
    )
    # A running execution the processing item is bound to.
    async with session_factory() as session, session.begin():
        execution = TaskExecution(workspace_id=world["ws"], status="running")
        session.add(execution)
        await session.flush()
        execution_id = execution.id
    item_id = await _seed_item(
        session_factory, world=world, binding=binding, state="processing",
        execution_id=execution_id, binding_display="room: C_INFLIGHT",
    )

    await _service(session_factory).delete_binding(
        workspace_id=world["ws"],
        binding_id=binding.id,
        force="cancel",
        force_cancel_wait_seconds=0.2,
        actor_member_id=world["member"],
    )

    # DELETE completed; the item is a forced-cancelled orphan.
    assert not await _binding_exists(session_factory, binding.id)
    (item,) = await _load_items(session_factory, [item_id])
    assert item.binding_id is None
    assert item.state == "cancelled"
    assert item.finished_at is not None
    assert item.binding_display == "room: C_INFLIGHT"
    # The execution cancel intent was persisted (two-phase → still cancelling;
    # the daemon completes the stop on recovery).
    async with session_factory() as session:
        reloaded = await session.get(TaskExecution, execution_id)
    assert reloaded.cancel_requested_at is not None
    assert reloaded.status == "cancelling"


async def test_force_cancel_inflight_sql_fallback(session_factory, monkeypatch):
    """When the in-transaction runtime cancel helper is unavailable, the force
    path falls back to a direct conditional UPDATE persisting the same cancel
    intent (claimed/running → cancelling); the heartbeat downlink finishes it."""
    import mesh.runtime.attempts as attempts_mod
    from mesh.db.models.runtime import TaskExecution

    monkeypatch.setattr(attempts_mod, "request_execution_cancel_tx", None, raising=False)
    world = await seed_world(session_factory)
    binding = await make_binding(
        session_factory, world=world, provider="slack", external_ref="C_FALLBACK"
    )
    async with session_factory() as session, session.begin():
        execution = TaskExecution(workspace_id=world["ws"], status="running")
        session.add(execution)
        await session.flush()
        execution_id = execution.id
    item_id = await _seed_item(
        session_factory, world=world, binding=binding, state="processing",
        execution_id=execution_id,
    )

    await _service(session_factory).delete_binding(
        workspace_id=world["ws"],
        binding_id=binding.id,
        force="cancel",
        force_cancel_wait_seconds=0.2,
        actor_member_id=world["member"],
    )

    (item,) = await _load_items(session_factory, [item_id])
    assert item.state == "cancelled" and item.binding_id is None
    async with session_factory() as session:
        reloaded = await session.get(TaskExecution, execution_id)
    assert reloaded.cancel_requested_at is not None
    assert reloaded.status == "cancelling"


# ---------------------------------------------------------------------------
# ③ fail-closed: direct parent DELETE with a non-terminal item → CHECK rejects
# ---------------------------------------------------------------------------


async def test_direct_parent_delete_with_nonterminal_item_rejected(session_factory):
    world = await seed_world(session_factory)
    binding = await make_binding(session_factory, world=world, provider="slack", external_ref="C_RAW")
    item_id = await _seed_item(session_factory, world=world, binding=binding, state="pending")

    # Bypass the endpoint: a raw parent DELETE would SET NULL the pending item's
    # binding_id, which ck_imq_orphan_terminal rejects (fail-closed, no silent
    # message loss). The whole statement rolls back.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, world["ws"])
            await session.execute(
                text("DELETE FROM integration_bindings WHERE id = :id"),
                {"id": binding.id},
            )

    # Binding still present; the item's parent reference was NOT nulled.
    assert await _binding_exists(session_factory, binding.id)
    (item,) = await _load_items(session_factory, [item_id])
    assert item.binding_id == binding.id
    assert item.state == "pending"


# ---------------------------------------------------------------------------
# ④ project deletion is guarded the same way
# ---------------------------------------------------------------------------


async def _make_project(session_factory, world: dict, *, visibility: str = "private") -> Project:
    async with session_factory() as session, session.begin():
        project = Project(
            workspace_id=world["ws"],
            name=f"Project {uuid.uuid4().hex[:6]}",
            key=f"QP{uuid.uuid4().hex[:4].upper()}",
            visibility=visibility,
        )
        session.add(project)
    return project


async def _admin_member(session_factory, world: dict) -> Member:
    async with session_factory() as session:
        return await session.get(Member, world["member"])


async def test_project_delete_blocked_then_succeeds_after_force(session_factory):
    world = await seed_world(session_factory)
    project = await _make_project(session_factory, world)
    binding = await make_binding(
        session_factory,
        world=world,
        provider="slack",
        external_ref="C_PROJ",
        scope="project",
        project_id=project.id,
    )
    await _seed_item(session_factory, world=world, binding=binding, state="pending")
    actor = await _admin_member(session_factory, world)
    project_service = ProjectService(session_factory)

    # Non-terminal item on a project-scoped binding → project delete fails.
    with pytest.raises(ConflictError) as excinfo:
        await project_service.delete_project(
            actor=actor, workspace_id=world["ws"], project_id=project.id
        )
    assert excinfo.value.code == "binding_has_active_queue"
    async with session_factory() as session:
        assert (await session.get(Project, project.id)).deleted_at is None

    # Force-drain the binding, then the project delete succeeds.
    await _service(session_factory).delete_binding(
        workspace_id=world["ws"],
        binding_id=binding.id,
        force="cancel",
        force_cancel_wait_seconds=1,
        actor_member_id=world["member"],
    )
    result = await project_service.delete_project(
        actor=actor, workspace_id=world["ws"], project_id=project.id
    )
    assert result == {"id": str(project.id), "deleted": True}
    async with session_factory() as session:
        assert (await session.get(Project, project.id)).deleted_at is not None


async def test_project_delete_unaffected_by_other_projects_queue(session_factory):
    """The guard is scoped to the project's OWN project-scoped bindings."""
    world = await seed_world(session_factory)
    project_a = await _make_project(session_factory, world)
    project_b = await _make_project(session_factory, world)
    binding_a = await make_binding(
        session_factory, world=world, provider="slack", external_ref="C_PA",
        scope="project", project_id=project_a.id,
    )
    await _seed_item(session_factory, world=world, binding=binding_a, state="pending")
    actor = await _admin_member(session_factory, world)
    project_service = ProjectService(session_factory)

    # Project B has no queue items → deletes fine even while A is blocked.
    result = await project_service.delete_project(
        actor=actor, workspace_id=world["ws"], project_id=project_b.id
    )
    assert result["deleted"] is True
    # Project A remains blocked.
    with pytest.raises(ConflictError):
        await project_service.delete_project(
            actor=actor, workspace_id=world["ws"], project_id=project_a.id
        )
