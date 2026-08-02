"""Integration ledger authorization snapshots (real PostgreSQL/signatures)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mesh.db.models.integration import Integration, IntegrationEvent
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.integrations.inbound import process_inbound
from mesh.integrations.ingest import match_bindings
from mesh.integrations.service import IntegrationService
from mesh.integrations.visibility import resolve_event_visibility
from tests.unit.integrations_support import (
    NOW,
    TEST_SIGNING_SECRET,
    github_request,
    make_binding,
    seed_world,
    slack_request,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("visibility_scope", "project_id_snapshot"),
    [
        ("workspace", uuid.uuid4()),
        ("project", None),
        ("unknown", uuid.uuid4()),
        ("invented", None),
    ],
)
async def test_event_visibility_check_rejects_every_invalid_shape(
    session_factory, visibility_scope, project_id_snapshot
):
    world = await seed_world(session_factory)
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, world["ws"])
            session.add(
                IntegrationEvent(
                    workspace_id=world["ws"],
                    integration_id=world["integ_slack"],
                    external_event_id=f"bad-shape-{visibility_scope}",
                    event_type="message",
                    payload={},
                    signature_status="valid",
                    process_status="received",
                    visibility_scope=visibility_scope,
                    project_id_snapshot=project_id_snapshot,
                )
            )
            await session.flush()


async def test_resolve_event_visibility_snapshots_binding_shape_fail_closed(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        project = Project(
            workspace_id=world["ws"],
            name="Private integration source",
            key=f"VS{uuid.uuid4().hex[:5].upper()}",
            visibility="private",
        )
        session.add(project)
        await session.flush()

    await make_binding(
        session_factory,
        world=world,
        provider="slack",
        provider_tenant_key="T_TEST",
        external_ref="C_DISABLED_WORKSPACE",
        status="disabled",
    )
    await make_binding(
        session_factory,
        world=world,
        provider="github",
        provider_tenant_key="1234567",
        external_ref="acme/private",
        scope="project",
        project_id=project.id,
    )

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        assert await resolve_event_visibility(
            session,
            workspace_id=world["ws"],
            integration_id=world["integ_slack"],
            provider="slack",
            provider_tenant_key="T_TEST",
            external_ref="C_DISABLED_WORKSPACE",
        ) == ("workspace", None)
        assert await resolve_event_visibility(
            session,
            workspace_id=world["ws"],
            integration_id=world["integ_github"],
            provider="github",
            provider_tenant_key="1234567",
            external_ref="acme/private",
        ) == ("project", project.id)
        assert await resolve_event_visibility(
            session,
            workspace_id=world["ws"],
            integration_id=world["integ_github"],
            provider="github",
            provider_tenant_key="1234567",
            external_ref="acme/unmatched",
        ) == ("unknown", None)


async def test_visibility_and_routing_match_the_provider_tenant_identity(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        project = Project(
            workspace_id=world["ws"],
            name="Current tenant source",
            key=f"VT{uuid.uuid4().hex[:5].upper()}",
            visibility="private",
        )
        session.add(project)
        await session.flush()

    await make_binding(
        session_factory,
        world=world,
        provider="slack",
        provider_tenant_key="T_PREVIOUS",
        external_ref="C_SHARED_REF",
    )
    current_binding = await make_binding(
        session_factory,
        world=world,
        provider="slack",
        provider_tenant_key="T_TEST",
        external_ref="C_SHARED_REF",
        scope="project",
        project_id=project.id,
    )

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        integration = await session.get(Integration, world["integ_slack"])
        assert integration is not None
        assert await resolve_event_visibility(
            session,
            workspace_id=world["ws"],
            integration_id=integration.id,
            provider="slack",
            provider_tenant_key="T_TEST",
            external_ref="C_SHARED_REF",
        ) == ("project", project.id)
        matches = await match_bindings(
            session,
            workspace_id=world["ws"],
            integration=integration,
            provider="slack",
            provider_tenant_key="T_TEST",
            external_ref="C_SHARED_REF",
        )
        assert [binding.id for binding in matches] == [current_binding.id]


async def test_deleted_project_snapshots_are_manager_only(session_factory):
    world = await seed_world(session_factory)
    member_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        session.add(
            User(
                id=user_id,
                email=f"visibility-{user_id.hex[:8]}@mesh.test",
                display_name="Visibility member",
                password_hash="unused-in-tests",
            )
        )
        await session.flush()
        member = Member(
            id=member_id,
            workspace_id=world["ws"],
            member_type="human",
            user_id=user_id,
            role="member",
            status="active",
        )
        project = Project(
            workspace_id=world["ws"],
            name="Soon deleted public project",
            key=f"VD{uuid.uuid4().hex[:5].upper()}",
            visibility="public",
        )
        session.add_all([member, project])
        await session.flush()
        session.add(
            IntegrationEvent(
                workspace_id=world["ws"],
                integration_id=world["integ_slack"],
                external_event_id="deleted-project-event",
                event_type="message",
                payload={"sentinel": "deleted project audit"},
                signature_status="valid",
                process_status="received",
                visibility_scope="project",
                project_id_snapshot=project.id,
                received_at=NOW,
            )
        )
    await make_binding(
        session_factory,
        world=world,
        provider="slack",
        provider_tenant_key="T_TEST",
        external_ref="C_SOON_DELETED",
        scope="project",
        project_id=project.id,
        bound_agent=False,
    )

    async with session_factory() as session:
        admin = await session.get(Member, world["member"])
        member = await session.get(Member, member_id)
    service = IntegrationService(session_factory, TEST_SIGNING_SECRET)
    assert (
        len(
            (
                await service.list_events(
                    workspace_id=world["ws"],
                    integration_id=world["integ_slack"],
                    viewer=member,
                )
            ).items
        )
        == 1
    )
    assert (
        len(
            await service.list_bindings(
                workspace_id=world["ws"],
                integration_id=world["integ_slack"],
                viewer=member,
            )
        )
        == 1
    )

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        stored_project = await session.get(Project, project.id)
        stored_project.deleted_at = NOW

    assert not (
        await service.list_events(
            workspace_id=world["ws"],
            integration_id=world["integ_slack"],
            viewer=member,
        )
    ).items
    assert not await service.list_bindings(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        viewer=member,
    )
    assert (
        len(
            (
                await service.list_events(
                    workspace_id=world["ws"],
                    integration_id=world["integ_slack"],
                    viewer=admin,
                )
            ).items
        )
        == 1
    )
    assert (
        len(
            await service.list_bindings(
                workspace_id=world["ws"],
                integration_id=world["integ_slack"],
                viewer=admin,
            )
        )
        == 1
    )


async def test_vcs_ingest_snapshots_project_and_rejection_is_unknown(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        project = Project(
            workspace_id=world["ws"],
            name="Private repository project",
            key=f"VR{uuid.uuid4().hex[:5].upper()}",
            visibility="private",
        )
        session.add(project)
        await session.flush()
    await make_binding(
        session_factory,
        world=world,
        provider="github",
        provider_tenant_key="1234567",
        external_ref="acme/private",
        scope="project",
        project_id=project.id,
        bound_agent=False,
    )

    payload = {
        "action": "opened",
        "repository": {"full_name": "acme/private"},
        "installation": {"id": 1234567},
        "sender": {"login": "octocat"},
        "pull_request": {"number": 9, "title": "change", "state": "open", "merged": False},
    }
    body, headers = github_request(world["secrets"]["github_webhook_secret"], payload, event="pull_request")
    async with session_factory() as session, session.begin():
        status, _response = await process_inbound(
            session,
            kind="vcs_github",
            raw_body=body,
            headers=headers,
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )
    assert status == 200

    rejected_headers = {**headers, "x-github-delivery": "invalid-signature-ledger"}
    rejected_headers["x-hub-signature-256"] = "sha256=" + "0" * 64
    async with session_factory() as session, session.begin():
        rejected_status, _response = await process_inbound(
            session,
            kind="vcs_github",
            raw_body=body,
            headers=rejected_headers,
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )
    assert rejected_status == 401

    async with session_factory() as session:
        rows = (await session.execute(select(IntegrationEvent))).scalars().all()
    project_event = next(row for row in rows if row.signature_status == "valid")
    rejected_event = next(row for row in rows if row.process_status == "rejected")
    assert (project_event.visibility_scope, project_event.project_id_snapshot) == (
        "project",
        project.id,
    )
    assert (rejected_event.visibility_scope, rejected_event.project_id_snapshot) == (
        "unknown",
        None,
    )

    async with session_factory() as session:
        realtime_rows = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")))
            .scalars()
            .all()
        )
    event_frames = [
        row.payload
        for row in realtime_rows
        if row.payload.get("event") == "integration.event_ingested"
        and row.payload.get("data", {}).get("event_id") == str(project_event.id)
    ]
    assert event_frames
    assert {frame["channel"] for frame in event_frames} == {f"project:{project.id}"}
    assert not any(frame["channel"].startswith(("workspace:", "integration:")) for frame in event_frames)


async def test_malformed_project_event_commits_no_realtime_frame(session_factory):
    """A signed event that is rejected must not retain its early project notice."""
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        project = Project(
            workspace_id=world["ws"],
            name="Private malformed event project",
            key=f"VM{uuid.uuid4().hex[:5].upper()}",
            visibility="private",
        )
        session.add(project)
        await session.flush()
    await make_binding(
        session_factory,
        world=world,
        provider="slack",
        provider_tenant_key="T_TEST",
        external_ref="C:PRIVATE",
        scope="project",
        project_id=project.id,
    )
    body, headers = slack_request(
        world["secrets"]["slack_signing_secret"],
        {
            "type": "event_callback",
            "team_id": "T_TEST",
            "event": {
                "type": "message",
                "channel": "C:PRIVATE",
                "user": "U_HUMAN",
                "text": "<@U_BOT> hostile routing segment",
                "event_ts": "1753790400.000901",
            },
        },
    )

    async with session_factory() as session, session.begin():
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
    assert response["process_status"] == "rejected"
    assert response["reason"] == "malformed_payload"

    async with session_factory() as session:
        rejected_event = await session.scalar(
            select(IntegrationEvent).where(IntegrationEvent.external_event_id == "T_TEST:1753790400.000901")
        )
        frames = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")))
            .scalars()
            .all()
        )
    assert rejected_event is not None
    assert (rejected_event.visibility_scope, rejected_event.project_id_snapshot) == (
        "unknown",
        None,
    )
    assert not any(
        row.payload.get("event") == "integration.event_ingested"
        and row.payload.get("data", {}).get("event_id") == str(rejected_event.id)
        for row in frames
    )
