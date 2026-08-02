"""Round-3 coverage: service edge paths, delivery transport errors, card
400 paths, vcs project-scoped fallback + stale refresh, missing-signature
inbound."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from mesh.db.models.integration import IntegrationBinding, IntegrationEvent, VcsLink
from mesh.db.models.issue import Issue, IssueStatus
from mesh.errors import BusinessRuleError, NotFoundError
from mesh.integrations import outbound as ob
from mesh.integrations import vcs_links as vl
from mesh.integrations.cards import handle_card_callback
from mesh.integrations.connectors import NormalizedEvent
from mesh.integrations.inbound import process_inbound
from mesh.integrations.service import IntegrationService, render_event
from tests.unit.integrations_support import (
    NOW,
    TEST_SIGNING_SECRET,
    seed_world,
    slack_request,
)

pytestmark = pytest.mark.unit


def make_service(session_factory):
    return IntegrationService(session_factory, TEST_SIGNING_SECRET)


async def _member(session_factory, world):
    from mesh.db.models.member import Member

    async with session_factory() as session:
        return await session.get(Member, world["member"])


# ---------------------------------------------------------------------------
# Service edge paths
# ---------------------------------------------------------------------------


async def test_update_integration_invalid_status_and_404(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    with pytest.raises(BusinessRuleError):
        await service.update_integration(
            workspace_id=world["ws"],
            integration_id=world["integ_slack"],
            status="exploded",
        )
    with pytest.raises(NotFoundError):
        await service.update_integration(workspace_id=world["ws"], integration_id=uuid.uuid4(), name="x")
    with pytest.raises(NotFoundError):
        await service.delete_integration(workspace_id=world["ws"], integration_id=uuid.uuid4())


async def test_update_integration_name_conflict(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    member = await _member(session_factory, world)
    await service.create_integration(workspace_id=world["ws"], creator=member, kind="im_slack", name="name-a")
    second = await service.create_integration(
        workspace_id=world["ws"], creator=member, kind="im_slack", name="name-b"
    )
    from mesh.errors import ConflictError

    with pytest.raises(ConflictError):
        await service.update_integration(
            workspace_id=world["ws"],
            integration_id=uuid.UUID(second["integration"]["id"]),
            name="name-a",
        )


async def test_decrypt_integration_secret_failure_modes(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    integration = await service.get_integration(workspace_id=world["ws"], integration_id=world["integ_slack"])
    assert service.decrypt_integration_secret(integration) is None  # no secret_ref
    async with session_factory() as session, session.begin():
        from mesh.db.models.integration import Integration

        row = await session.get(Integration, world["integ_slack"])
        row.secret_ref = "not-a-fernet-token"
    integration = await service.get_integration(workspace_id=world["ws"], integration_id=world["integ_slack"])
    assert service.decrypt_integration_secret(integration) is None  # undecryptable


async def test_list_events_signature_filter_and_render(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        event = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integ_slack"],
            external_event_id="sig-1",
            event_type="message",
            payload={"a": 1},
            signature_status="invalid",
            process_status="rejected",
        )
        session.add(event)
    page = await service.list_events(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        viewer=await _member(session_factory, world),
        signature_status="invalid",
    )
    assert len(page.items) == 1
    rendered = render_event(page.items[0])
    assert rendered["signature_status"] == "invalid"
    assert rendered["payload"] == {"a": 1}


async def test_create_binding_provider_mismatch_and_foreign_integration(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    with pytest.raises(NotFoundError):
        await service.create_binding(
            workspace_id=world["ws"],
            integration_id=uuid.uuid4(),
            external_ref="C_X",
        )
    # webhook_outbound kind has no bindings
    member = await _member(session_factory, world)
    outbound_integration = await service.create_integration(
        workspace_id=world["ws"], creator=member, kind="webhook_outbound", name="ob-1"
    )
    with pytest.raises(BusinessRuleError):
        await service.create_binding(
            workspace_id=world["ws"],
            integration_id=uuid.UUID(outbound_integration["integration"]["id"]),
            external_ref="C_Y",
        )


# ---------------------------------------------------------------------------
# Delivery worker: transport-level error
# ---------------------------------------------------------------------------


async def test_delivery_transport_error_records_failure(session_factory):
    world = await seed_world(session_factory)

    def raise_handler(request):
        raise httpx.ConnectError("connection refused")

    async with session_factory() as session, session.begin():
        from mesh.db.models.integration import WebhookSubscriptionDelivery
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        subscription, _ = await ob.create_subscription(
            session,
            workspace_id=world["ws"],
            creator_member_id=world["member"],
            url="https://unreachable.example.com/x",
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
        )
        delivery = WebhookSubscriptionDelivery(
            workspace_id=world["ws"],
            subscription_id=subscription.id,
            event_ref="net-err-1",
            state="pending",
        )
        session.add(delivery)
    worker = ob.WebhookDeliveryWorker(
        session_factory,
        signing_secret=TEST_SIGNING_SECRET,
        max_attempts=3,
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(raise_handler)),
        resolver=lambda host, port: ["93.184.216.34"],
        clock=lambda: NOW,
    )
    await worker.run_once()
    async with session_factory() as session:
        from mesh.db.models.integration import WebhookSubscriptionDelivery
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        row = (await session.execute(select(WebhookSubscriptionDelivery))).scalars().first()
        assert row.state == "pending"
        assert row.attempts == 1
        assert row.last_error == "ConnectError"
        assert row.response_status is None


async def test_delivery_unresolvable_host(session_factory):
    world = await seed_world(session_factory)

    def broken_resolver(host, port):
        raise OSError("DNS failure")

    async with session_factory() as session, session.begin():
        from mesh.db.models.integration import WebhookSubscriptionDelivery
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        subscription, _ = await ob.create_subscription(
            session,
            workspace_id=world["ws"],
            creator_member_id=world["member"],
            url="https://no-dns.example.com/x",
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
        )
        session.add(
            WebhookSubscriptionDelivery(
                workspace_id=world["ws"],
                subscription_id=subscription.id,
                event_ref="dns-1",
                state="pending",
            )
        )
    worker = ob.WebhookDeliveryWorker(
        session_factory,
        signing_secret=TEST_SIGNING_SECRET,
        max_attempts=1,
        resolver=broken_resolver,
        clock=lambda: NOW,
    )
    await worker.run_once()
    async with session_factory() as session:
        from mesh.db.models.integration import WebhookSubscriptionDelivery
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        row = (await session.execute(select(WebhookSubscriptionDelivery))).scalars().first()
        assert row.state == "failed"
        assert row.last_error == "ssrf_blocked"


# ---------------------------------------------------------------------------
# Cards: malformed payload paths (400)
# ---------------------------------------------------------------------------


async def test_card_missing_clicker_or_action_400(session_factory):
    world = await seed_world(session_factory)
    body, headers = slack_request(
        world["secrets"]["slack_signing_secret"],
        {"type": "block_actions", "team": {"id": "T_TEST"}},  # no user, no actions
    )
    async with session_factory() as session, session.begin():
        status, resp = await handle_card_callback(
            session,
            session_factory,
            kind="im_slack",
            raw_body=body,
            headers=headers,
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )
    assert status == 400
    assert resp["error"]["code"] == "invalid_request"


async def test_card_unsupported_kind_401(session_factory):
    await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        status, resp = await handle_card_callback(
            session,
            session_factory,
            kind="vcs_github",
            raw_body=b"{}",
            headers={},
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )
    assert status == 401


# ---------------------------------------------------------------------------
# vcs_links: project-scoped fallback + delete 404 + non-merge refresh
# ---------------------------------------------------------------------------


async def _project_world(session_factory):
    world = await seed_world(session_factory)
    from mesh.db.models.project import Project

    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        project = Project(workspace_id=world["ws"], name="P", key="WEB", visibility="public")
        session.add(project)
        await session.flush()
        # workspace-level done status (project has none)
        done = IssueStatus(workspace_id=world["ws"], name="Done", category="done")
        todo = IssueStatus(workspace_id=world["ws"], name="Todo", category="todo", is_default=True)
        session.add_all([done, todo])
        await session.flush()
        issue = Issue(
            workspace_id=world["ws"],
            project_id=project.id,
            identifier_namespace_key="WEB",
            number=5,
            identifier="WEB-5",
            title="t",
            status_id=todo.id,
            state_category="todo",
        )
        session.add(issue)
    return world, issue, done


async def test_find_target_status_project_fallback_to_workspace(session_factory):
    world, issue, done = await _project_world(session_factory)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        found = await vl._find_target_status(
            session,
            workspace_id=world["ws"],
            project_id=issue.project_id,
            target="done",
        )
        assert found is not None and found.id == done.id
        by_name = await vl._find_target_status(
            session,
            workspace_id=world["ws"],
            project_id=issue.project_id,
            target="Done",
        )
        assert by_name is not None


async def test_delete_link_404(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        with pytest.raises(NotFoundError):
            await vl.delete_link(session, workspace_id=world["ws"], link_id=uuid.uuid4(), now=NOW)


async def test_ingest_non_merge_event_refreshes_state_not_stale(session_factory):
    world = await seed_world(session_factory)
    from mesh.db.models.integration import Integration

    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_github"])
    # binding without auto_status_map
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        session.add(
            IntegrationBinding(
                workspace_id=world["ws"],
                integration_id=world["integ_github"],
                provider="github",
                provider_tenant_key="1234567",
                external_ref="acme/web",
                match_config={},
                bound_agent_id=world["agent"],
            )
        )
    event = NormalizedEvent(
        external_event_id="evt-open-1",
        event_type="pull_request",
        external_ref="acme/web",
        actor_key="dev",
        tenant_key="1234567",
        text="WEB-77 open",
        extra={
            "action": "opened",
            "pr_number": 77,
            "pr_title": "WEB-77 open",
            "pr_state": "open",
            "pr_merged": False,
        },
    )
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        # issue WEB-77 must exist for linking
        todo = IssueStatus(workspace_id=world["ws"], name="T", category="todo", is_default=True)
        session.add(todo)
        await session.flush()
        session.add(
            Issue(
                workspace_id=world["ws"],
                identifier_namespace_key="WEB",
                number=77,
                identifier="WEB-77",
                title="t",
                status_id=todo.id,
                state_category="todo",
            )
        )
        event_row = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integ_github"],
            external_event_id=event.external_event_id,
            event_type="pull_request",
            payload={},
            signature_status="valid",
        )
        session.add(event_row)
        await session.flush()
        result = await vl.ingest_vcs_event(
            session,
            workspace_id=world["ws"],
            integration=integration,
            provider="github",
            event=event,
            event_row=event_row,
            now=NOW,
        )
    assert result["links_created"] == 1
    assert result["issues_transitioned"] == 0
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        link = (await session.execute(select(VcsLink))).scalars().first()
        assert link.status == "active"


# ---------------------------------------------------------------------------
# inbound: missing-signature path (no header at all)
# ---------------------------------------------------------------------------


async def test_inbound_missing_signature_header(session_factory):
    await seed_world(session_factory)
    payload = {
        "type": "event_callback",
        "team_id": "T_TEST",
        "event": {"type": "message", "channel": "C", "user": "U", "text": "x", "event_ts": "9.9"},
    }
    body = json.dumps(payload).encode()
    async with session_factory() as session, session.begin():
        status, resp = await process_inbound(
            session,
            kind="im_slack",
            raw_body=body,
            headers={},
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )
    assert status == 401
    assert resp["error"]["code"] == "invalid_signature"


async def test_inbound_rejects_webhook_outbound_kind(session_factory):
    await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        status, _ = await process_inbound(
            session,
            kind="webhook_outbound",
            raw_body=b"{}",
            headers={},
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )
    assert status == 401
