"""Final coverage tests: gitlab naming / strict-mode transitions / worker
loop / service extras / owner-role fallback paths (transactional DDL drop —
PostgreSQL rolls back the function drop, restoring it afterwards).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text

from mesh.db.models.integration import IntegrationEvent
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError, NotFoundError
from mesh.integrations import outbound as ob
from mesh.integrations import vcs_links as vl
from mesh.integrations.connectors import NormalizedEvent
from mesh.integrations.inbound import process_inbound
from mesh.integrations.service import IntegrationService
from tests.unit.integrations_support import (
    NOW,
    TEST_SIGNING_SECRET,
    seed_world,
    slack_request,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# vcs naming (gitlab + github push/repository)
# ---------------------------------------------------------------------------


def test_vcs_action_gitlab_variants():
    merged = NormalizedEvent(
        external_event_id="e",
        event_type="Merge Request Hook",
        external_ref="a/b",
        actor_key="u",
        tenant_key="t",
        text="",
        extra={"mr_state": "merged", "action": "merge"},
    )
    assert vl.vcs_action("gitlab", merged) == "merged"
    closed = NormalizedEvent(
        external_event_id="e",
        event_type="Merge Request Hook",
        external_ref="a/b",
        actor_key="u",
        tenant_key="t",
        text="",
        extra={"mr_state": "closed", "action": "close"},
    )
    assert vl.vcs_action("gitlab", closed) == "closed"
    opened = NormalizedEvent(
        external_event_id="e",
        event_type="Merge Request Hook",
        external_ref="a/b",
        actor_key="u",
        tenant_key="t",
        text="",
        extra={"mr_state": "opened", "action": "open"},
    )
    assert vl.vcs_action("gitlab", opened) == "opened"
    assert vl.vcs_action("webhook", merged) is None


def test_external_object_for_variants():
    def event(event_type, external_ref="acme/web", **extra):
        return NormalizedEvent(
            external_event_id="e",
            event_type=event_type,
            external_ref=external_ref,
            actor_key="u",
            tenant_key="t",
            text="",
            extra=extra,
        )

    assert vl.external_object_for("github", event("push", ref="refs/heads/main")) == (
        "branch",
        "acme/web@main",
    )
    assert vl.external_object_for("github", event("issues")) == ("repository", "acme/web")
    assert vl.external_object_for("github", event("pull_request")) == ("repository", "acme/web")
    assert vl.external_object_for("gitlab", event("Merge Request Hook", mr_iid=7)) == (
        "merge_request",
        "acme/web#7",
    )
    assert vl.external_object_for("gitlab", event("Push Hook", ref="refs/heads/dev")) == (
        "branch",
        "acme/web@dev",
    )
    assert vl.external_object_for("gitlab", event("Note Hook")) == ("repository", "acme/web")
    assert vl.external_object_for("github", event("pull_request", external_ref="")) is None


# ---------------------------------------------------------------------------
# strict-mode transition guard
# ---------------------------------------------------------------------------


async def _world_with_statuses(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        todo = IssueStatus(workspace_id=world["ws"], name="Todo", category="todo", is_default=True)
        done = IssueStatus(workspace_id=world["ws"], name="Done", category="done")
        session.add_all([todo, done])
        await session.flush()
        issue = Issue(
            workspace_id=world["ws"],
            identifier_namespace_key="WEB",
            number=1,
            identifier="WEB-1",
            title="t",
            status_id=todo.id,
            state_category="todo",
        )
        session.add(issue)
    return world, todo, done, issue


async def test_strict_mode_blocks_transition(session_factory):
    world, todo, done, issue = await _world_with_statuses(session_factory)
    # Enable strict mode + restrict todo's transitions to nothing.
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        ws = await session.get(Workspace, world["ws"])
        ws.settings = {**(ws.settings or {}), "status_strict_mode": True}
        current = await session.get(IssueStatus, todo.id)
        current.allowed_transitions = []  # empty = unrestricted
    # empty allowed → unrestricted
    event_row = await _event_row(session_factory, world)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        ok = await vl.apply_auto_status(
            session,
            workspace_id=world["ws"],
            issue=issue,
            target="done",
            action="merged",
            external_ref="acme/web#1",
            event_row=event_row,
            now=NOW,
        )
        assert ok is True
    # now configure a real restriction: done→only done allowed from done itself
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        # reset issue to todo with a restrictive allowed_transitions
        reloaded = await session.get(Issue, issue.id)
        reloaded.status_id = todo.id
        reloaded.state_category = "todo"
        current = await session.get(IssueStatus, todo.id)
        current.allowed_transitions = [str(todo.id)]  # only self → done blocked
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        reloaded = await session.get(Issue, issue.id)
        blocked = await vl.apply_auto_status(
            session,
            workspace_id=world["ws"],
            issue=reloaded,
            target="done",
            action="merged",
            external_ref="acme/web#1",
            event_row=event_row,
            now=NOW,
        )
        assert blocked is False, "strict mode must block unlisted transitions"


async def test_find_target_status_missing(session_factory):
    world, todo, done, issue = await _world_with_statuses(session_factory)
    event_row = await _event_row(session_factory, world)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        result = await vl.apply_auto_status(
            session,
            workspace_id=world["ws"],
            issue=issue,
            target="nonexistent",
            action="merged",
            external_ref="a/b#1",
            event_row=event_row,
            now=NOW,
        )
        assert result is False


async def _event_row(session_factory, world):
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        row = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integ_github"],
            external_event_id=f"evt-{uuid.uuid4().hex[:8]}",
            event_type="pull_request",
            payload={},
            signature_status="valid",
        )
        session.add(row)
    return row


# ---------------------------------------------------------------------------
# Delivery worker loop + retry guard
# ---------------------------------------------------------------------------


async def test_worker_run_forever_processes_and_stops(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        from mesh.db.models.integration import WebhookSubscriptionDelivery
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        subscription, _ = await ob.create_subscription(
            session,
            workspace_id=world["ws"],
            creator_member_id=world["member"],
            url="https://hooks.example.com/x",
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
        )
        session.add(
            WebhookSubscriptionDelivery(
                workspace_id=world["ws"],
                subscription_id=subscription.id,
                event_ref="loop-1",
                state="pending",
            )
        )
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    worker = ob.WebhookDeliveryWorker(
        session_factory,
        signing_secret=TEST_SIGNING_SECRET,
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
        resolver=lambda host, port: ["93.184.216.34"],
        poll_interval=0.05,
        clock=lambda: NOW,
    )
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.2)
        stop.set()

    await asyncio.wait_for(asyncio.gather(worker.run_forever(stop), stopper()), timeout=10)
    async with session_factory() as session:
        from mesh.db.models.integration import WebhookSubscriptionDelivery
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        rows = (await session.execute(select(WebhookSubscriptionDelivery))).scalars().all()
        assert all(r.state == "sent" for r in rows)


async def test_retry_non_failed_delivery_rejected(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        from mesh.db.models.integration import WebhookSubscriptionDelivery
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        subscription, _ = await ob.create_subscription(
            session,
            workspace_id=world["ws"],
            creator_member_id=world["member"],
            url="https://hooks.example.com/y",
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
        )
        delivery = WebhookSubscriptionDelivery(
            workspace_id=world["ws"],
            subscription_id=subscription.id,
            event_ref="r-1",
            state="pending",
        )
        session.add(delivery)
        await session.flush()
        with pytest.raises(BusinessRuleError) as excinfo:
            await ob.retry_delivery(
                session,
                workspace_id=world["ws"],
                subscription=subscription,
                delivery_id=delivery.id,
            )
        assert excinfo.value.code == "invalid_request"


# ---------------------------------------------------------------------------
# Service extras (counts, filters, bound-agent clearing)
# ---------------------------------------------------------------------------


async def test_service_event_counts_and_filters(session_factory):
    world = await seed_world(session_factory)
    service = IntegrationService(session_factory, TEST_SIGNING_SECRET)
    async with session_factory() as session:
        from mesh.db.models.member import Member

        member = await session.get(Member, world["member"])
    await service.create_integration(workspace_id=world["ws"], creator=member, kind="im_slack", name="svc-x")
    integrations = await service.list_integrations(workspace_id=world["ws"], kind="im_slack")
    target = next(i for i in integrations.items if i.name == "svc-x")
    count = await service.event_counts_7d(
        workspace_id=world["ws"],
        integration_id=target.id,
        since=NOW - timedelta(days=7),
        viewer=member,
    )
    assert count == 0
    # cursor round-trip
    page1 = await service.list_integrations(workspace_id=world["ws"], limit=1)
    assert page1.next_cursor is not None
    page2 = await service.list_integrations(workspace_id=world["ws"], limit=10, cursor=page1.next_cursor)
    assert all(i.id != page1.items[0].id for i in page2.items)


async def test_service_update_binding_clears_agent(session_factory):
    world = await seed_world(session_factory)
    service = IntegrationService(session_factory, TEST_SIGNING_SECRET)
    rendered = await service.create_binding(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        external_ref="C_CLR",
        bound_agent_id=world["agent"],
    )
    updated = await service.update_binding(
        workspace_id=world["ws"],
        binding_id=uuid.UUID(rendered["id"]),
        bound_agent_id=None,
    )
    assert updated["bound_agent_id"] is None


async def test_service_foreign_workspace_404(session_factory):
    world = await seed_world(session_factory)
    service = IntegrationService(session_factory, TEST_SIGNING_SECRET)
    with pytest.raises(NotFoundError):
        await service.get_integration(workspace_id=uuid.uuid4(), integration_id=world["integ_slack"])


# ---------------------------------------------------------------------------
# Owner-role fallback paths (definer functions dropped inside a transaction
# that rolls back — PostgreSQL DDL is transactional, so the functions are
# restored afterwards).
# ---------------------------------------------------------------------------


async def test_inbound_fallback_without_definer_functions(session_factory):
    world = await seed_world(session_factory)
    from tests.unit.integrations_support import encrypt

    # Give the slack integration a decryptable signing secret.
    signing = "fallback-secret"
    async with session_factory() as session, session.begin():
        from mesh.db.models.integration import Integration
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        integration = await session.get(Integration, world["integ_slack"])
        integration.config = {**integration.config, "signing_secret_ref": encrypt(signing)}
    session = session_factory()
    transaction = await session.begin()
    try:
        await session.execute(text("DROP FUNCTION mesh_integrations_by_kind_config_value(text, text, text)"))
        await session.execute(text("DROP FUNCTION mesh_integrations_active_by_kind(text)"))
        await session.execute(text("DROP FUNCTION mesh_binding_by_external_ref(text, text)"))
        payload = {
            "type": "event_callback",
            "team_id": "T_TEST",
            "event": {"type": "message", "channel": "C_FB", "user": "U", "text": "hi", "event_ts": "2.2"},
        }
        body, headers = slack_request(signing, payload)
        status, response = await process_inbound(
            session,
            kind="im_slack",
            raw_body=body,
            headers=headers,
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )
        assert status == 200
        rows = (
            (
                await session.execute(
                    select(IntegrationEvent).where(IntegrationEvent.external_event_id == "T_TEST:2.2")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        # invalid signature through the fallback path → rejected audit row
        bad_body, bad_headers = slack_request(
            "wrong", {**payload, "event": {**payload["event"], "event_ts": "3.3"}}
        )
        bad_status, _ = await process_inbound(
            session,
            kind="im_slack",
            raw_body=bad_body,
            headers=bad_headers,
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )
        assert bad_status == 401
    finally:
        await transaction.rollback()
        await session.close()


async def test_route_workspace_fallback(session_factory, db_url):
    """_resource_workspace direct-ORM fallback under the owner role."""
    from mesh.integrations.routes import _resource_workspace

    world = await seed_world(session_factory)

    class FakeRequest:
        def __init__(self, sf):
            self.app = type("A", (), {"state": type("S", (), {"session_factory": sf})})()

    session = session_factory()
    transaction = await session.begin()
    await transaction.rollback()
    await session.close()
    # Drop inside a short-lived transaction, call through the fake app
    # (owner role → fallback ORM), then rollback restores the function.
    session = session_factory()
    transaction = await session.begin()
    try:
        await session.execute(text("DROP FUNCTION mesh_integration_workspace_id(uuid)"))
        await transaction.commit()
        workspace_id = await _resource_workspace(
            FakeRequest(session_factory),
            "mesh_integration_workspace_id",
            world["integ_slack"],
        )
        assert workspace_id == world["ws"]
    finally:
        async with session_factory() as restore_session, restore_session.begin():
            await restore_session.execute(
                text(
                    """
                CREATE OR REPLACE FUNCTION mesh_integration_workspace_id(p_id uuid)
                RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
                SET search_path = public AS $$
                  SELECT t.workspace_id FROM integrations t WHERE t.id = p_id
                $$
                """
                )
            )
        await session.close()
