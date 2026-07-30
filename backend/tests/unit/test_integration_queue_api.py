"""In-process HTTP coverage for the queue query/operation endpoints (§3.9).

Drives the REAL FastAPI app through httpx ASGITransport: position computation
+ refetch after cancel, the atomic cancel guard (422), triple-based cancel
authorization incl. the cross-provider negative (§5.6 身份三元组授权负向), fixed
orphan exclusion (list/summary vs the audit endpoint), deleted-project snapshot
visibility, project-scoped item visibility, and the summary shape. Queue rows,
identities and projects are seeded directly; the endpoints run the full stack.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from mesh.db.models.integration import (
    ExternalIdentity,
    Integration,
    IntegrationBinding,
    IntegrationMessageQueue,
)
from mesh.db.models.member import Member
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.user import User

pytestmark = pytest.mark.unit

SIGNING_SECRET = "integration-queue-api-signing-secret-0000"
TENANT = "T_QA"


def _settings_kwargs(db_url: str, redis_url: str, **overrides) -> dict:
    base = {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": SIGNING_SECRET,
        "daemon_tls_required": False,
        "storage_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9100"),
        "storage_public_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9100"),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh"),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret"),
        "storage_bucket": "mesh-integration-queue-api-test",
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def app_client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings_kwargs(db_url, redis_url)))
    try:
        await app.state.storage.ensure_bucket()
    except Exception:  # noqa: BLE001 — storage optional in unit context
        pass
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Seeding helpers (HTTP for auth/workspace; direct DB for queue fixtures)
# ---------------------------------------------------------------------------


async def _register(client: httpx.AsyncClient, email: str) -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Queue-Api-Test-123", "display_name": "QA User"},
    )
    token = (
        await client.post("/api/v1/auth/login", json={"email": email, "password": "Queue-Api-Test-123"})
    ).json()["data"]["access_token"]
    return {"token": token, "headers": {"Authorization": f"Bearer {token}"}, "email": email}


async def make_world(client: httpx.AsyncClient, session_factory, suffix: str) -> dict:
    auth = await _register(client, f"qa-{suffix}@example.com")
    ws = (
        await client.post(
            "/api/v1/workspaces",
            json={"name": f"QA {suffix}", "slug": f"qa-{suffix}-{uuid.uuid4().hex[:6]}"},
            headers=auth["headers"],
        )
    ).json()["data"]
    agent = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/agents",
            json={"name": f"qa-agent-{suffix}"},
            headers=auth["headers"],
        )
    ).json()["data"]
    members = (
        await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=auth["headers"])
    ).json()["data"]
    human = next(m for m in members if m.get("member_type") == "human")
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == auth["email"]))
    return {
        **auth,
        "ws_id": ws["id"],
        "agent_id": agent["id"],
        "member_id": human["id"],
        "user_id": user.id,
    }


async def add_member(client, session_factory, world, *, role: str = "member") -> dict:
    auth = await _register(client, f"qa-m-{uuid.uuid4().hex[:8]}@example.com")
    async with session_factory() as session, session.begin():
        user = await session.scalar(select(User).where(User.email == auth["email"]))
        member = Member(
            workspace_id=uuid.UUID(world["ws_id"]),
            member_type="human",
            user_id=user.id,
            role=role,
            status="active",
        )
        session.add(member)
        await session.flush()
        member_id = member.id
        user_id = user.id
    return {**auth, "member_id": member_id, "user_id": user_id}


async def seed_integration(session_factory, world, *, name: str | None = None) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        integration = Integration(
            workspace_id=uuid.UUID(world["ws_id"]),
            kind="im_slack",
            name=name or f"integ-{uuid.uuid4().hex[:8]}",
            config={"team_id": TENANT},
            created_by=uuid.UUID(world["member_id"]),
        )
        session.add(integration)
        await session.flush()
        return integration.id


async def seed_binding(
    session_factory,
    world,
    integration_id,
    *,
    external_ref: str = "C_QA",
    scope: str = "workspace",
    project_id=None,
) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        binding = IntegrationBinding(
            workspace_id=uuid.UUID(world["ws_id"]),
            integration_id=integration_id,
            provider="slack",
            provider_tenant_key=TENANT,
            scope=scope,
            project_id=project_id,
            external_ref=external_ref,
            match_config={},
            bound_agent_id=uuid.UUID(world["agent_id"]),
            status="active",
        )
        session.add(binding)
        await session.flush()
        return binding.id


async def seed_item(
    session_factory,
    world,
    *,
    integration_id,
    binding_id,
    seq: int,
    state: str = "pending",
    conversation_key: str,
    sender_identity_key: str,
    target_agent_id=None,
    project_id_snapshot=None,
    excerpt: str = "message",
    binding_display: str = "room: C_QA",
) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        item = IntegrationMessageQueue(
            workspace_id=uuid.UUID(world["ws_id"]),
            integration_id=integration_id,
            binding_id=binding_id,
            binding_display=binding_display,
            project_id_snapshot=project_id_snapshot,
            conversation_key=conversation_key,
            seq=seq,
            dispatch_mode="serial_conversation",
            state=state,
            message_excerpt=excerpt,
            sender_identity_key=sender_identity_key,
            target_agent_id=target_agent_id,
        )
        session.add(item)
        await session.flush()
        return item.id


async def seed_identity(session_factory, *, provider, tenant, external_user_key, user_id) -> None:
    async with session_factory() as session, session.begin():
        session.add(
            ExternalIdentity(
                provider=provider,
                provider_tenant_key=tenant,
                external_user_key=external_user_key,
                user_id=user_id,
            )
        )


def _by_excerpt(items: list[dict], excerpt: str) -> dict:
    return next(item for item in items if item["message_excerpt"] == excerpt)


# ---------------------------------------------------------------------------
# Position computation + refetch after cancel (§5.6 位置查询与契约)
# ---------------------------------------------------------------------------


async def test_queue_positions_and_refetch_after_cancel(app_client, session_factory):
    world = await make_world(app_client, session_factory, "pos")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_POS")
    conv = f"slack:{TENANT}:C_POS"
    sender = f"slack:{TENANT}:U_OWNER"
    await seed_identity(
        session_factory, provider="slack", tenant=TENANT, external_user_key="U_OWNER",
        user_id=world["user_id"],
    )
    # M1 processing (in-flight, position null), M2/M3 pending behind it.
    await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=1, state="processing", conversation_key=conv, sender_identity_key=sender, excerpt="M1",
    )
    await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=2, state="pending", conversation_key=conv, sender_identity_key=sender, excerpt="M2",
    )
    await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=3, state="pending", conversation_key=conv, sender_identity_key=sender, excerpt="M3",
    )

    base = f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue"
    listed = (await app_client.get(base, headers=world["headers"])).json()["data"]
    assert _by_excerpt(listed, "M1")["position"] is None, "in-flight item has no position"
    assert _by_excerpt(listed, "M2")["position"] == 1
    assert _by_excerpt(listed, "M3")["position"] == 2
    # state field surfaces in-flight states incl. processing.
    assert _by_excerpt(listed, "M1")["state"] == "processing"

    # Cancel M2 (owner has integration:manage) → refetch shifts M3 to 1.
    m2_id = _by_excerpt(listed, "M2")["id"]
    cancel = await app_client.post(f"{base}/{m2_id}:cancel", headers=world["headers"])
    assert cancel.status_code == 200
    assert cancel.json()["data"] == {"id": m2_id, "state": "cancelled"}

    refetched = (await app_client.get(base, headers=world["headers"])).json()["data"]
    assert _by_excerpt(refetched, "M3")["position"] == 1, "M3 moved up after M2 cancelled"
    assert _by_excerpt(refetched, "M2")["state"] == "cancelled"
    assert _by_excerpt(refetched, "M2")["position"] is None
    # The cancellation queued an invalidation notice through the unique write
    # path (a realtime-publish outbox event; the projector materializes the
    # realtime row, which does not run in unit tests).
    async with session_factory() as session:
        from mesh.db.models.outbox import OutboxEvent

        notices = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.payload["event"].astext == "integration.queue_updated"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert notices, "queue_updated invalidation emitted on cancel"
    # Workspace-scoped item → the payload carries the conversation_key (§3.9).
    assert notices[0].payload["data"]["conversation_key"] == conv


async def test_queue_item_fields_sender_and_target(app_client, session_factory):
    world = await make_world(app_client, session_factory, "fields")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_FIELDS")
    conv = f"slack:{TENANT}:C_FIELDS"
    await seed_identity(
        session_factory, provider="slack", tenant=TENANT, external_user_key="U_LINKED",
        user_id=world["user_id"],
    )
    await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                    seq=1, state="pending", conversation_key=conv,
                    sender_identity_key=f"slack:{TENANT}:U_LINKED",
                    target_agent_id=uuid.UUID(world["agent_id"]), excerpt="linked-msg")
    await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                    seq=2, state="pending", conversation_key=conv,
                    sender_identity_key=f"slack:{TENANT}:U_STRANGER", excerpt="unlinked-msg")

    base = f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue"
    listed = (await app_client.get(base, headers=world["headers"])).json()["data"]
    linked = _by_excerpt(listed, "linked-msg")
    assert linked["sender"]["linked"] is True
    assert linked["sender"]["display_name"] == "QA User"  # resolved users.display_name
    assert linked["sender"]["identity_key"] == f"slack:{TENANT}:U_LINKED"
    assert linked["target_agent"] == {"id": world["agent_id"], "name": linked["target_agent"]["name"]}
    assert len(linked["message_excerpt"]) <= 120
    unlinked = _by_excerpt(listed, "unlinked-msg")
    assert unlinked["sender"]["linked"] is False
    assert unlinked["sender"]["display_name"] == f"slack:{TENANT}:U_STRANGER"
    assert unlinked["target_agent"] is None


# ---------------------------------------------------------------------------
# Cancel guard + authorization (§3.9 / §5.6)
# ---------------------------------------------------------------------------


async def test_cancel_non_pending_returns_422(app_client, session_factory):
    world = await make_world(app_client, session_factory, "np422")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_422")
    conv = f"slack:{TENANT}:C_422"
    item_id = await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                              seq=1, state="processing", conversation_key=conv,
                              sender_identity_key=f"slack:{TENANT}:U", excerpt="in-flight")
    base = f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue"
    response = await app_client.post(f"{base}/{item_id}:cancel", headers=world["headers"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "queue_item_not_cancellable"


async def test_cancel_others_pending_without_manage_403(app_client, session_factory):
    world = await make_world(app_client, session_factory, "authz")
    outsider = await add_member(app_client, session_factory, world, role="member")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_AUTHZ")
    conv = f"slack:{TENANT}:C_AUTHZ"
    # The item belongs to the OWNER (sender triple resolves to owner users.id).
    await seed_identity(session_factory, provider="slack", tenant=TENANT,
                        external_user_key="U_OWNER2", user_id=world["user_id"])
    item_id = await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                              seq=1, state="pending", conversation_key=conv,
                              sender_identity_key=f"slack:{TENANT}:U_OWNER2", excerpt="owners-item")
    base = f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue"
    # Outsider (plain member, no integration:manage) cannot cancel it.
    response = await app_client.post(f"{base}/{item_id}:cancel", headers=outsider["headers"])
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "command_forbidden"
    # Item untouched.
    async with session_factory() as session:
        item = await session.get(IntegrationMessageQueue, item_id)
    assert item.state == "pending"
    # The owner (manage) can.
    ok = await app_client.post(f"{base}/{item_id}:cancel", headers=world["headers"])
    assert ok.status_code == 200


async def test_cancel_cross_provider_triple_rejected(app_client, session_factory):
    """§5.6 身份三元组授权负向: same external_user_key string under a different
    provider maps a different user — bare-key resolution would wrongly
    authorize; the full-triple resolution must reject (403)."""
    world = await make_world(app_client, session_factory, "xprov")
    requester = await add_member(app_client, session_factory, world, role="member")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_XPROV")
    # Item sender is a DingTalk user 'foo' (resolves to the owner).
    await seed_identity(session_factory, provider="dingtalk", tenant="dingCORP",
                        external_user_key="foo", user_id=world["user_id"])
    item_id = await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                              seq=1, state="pending", conversation_key="dingtalk:dingCORP:C_XPROV",
                              sender_identity_key="dingtalk:dingCORP:foo", excerpt="ding-item")
    # The requester owns a GitHub identity with the SAME key string 'foo'.
    await seed_identity(session_factory, provider="github", tenant="",
                        external_user_key="foo", user_id=requester["user_id"])
    base = f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue"
    response = await app_client.post(f"{base}/{item_id}:cancel", headers=requester["headers"])
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "command_forbidden"
    async with session_factory() as session:
        assert (await session.get(IntegrationMessageQueue, item_id)).state == "pending"


async def test_cancel_self_pending_allowed_without_manage(app_client, session_factory):
    """A plain member MAY cancel their OWN pending item (triple → same users.id)."""
    world = await make_world(app_client, session_factory, "self")
    member = await add_member(app_client, session_factory, world, role="member")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_SELF")
    conv = f"slack:{TENANT}:C_SELF"
    await seed_identity(session_factory, provider="slack", tenant=TENANT,
                        external_user_key="U_ME", user_id=member["user_id"])
    item_id = await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                              seq=1, state="pending", conversation_key=conv,
                              sender_identity_key=f"slack:{TENANT}:U_ME", excerpt="my-item")
    base = f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue"
    response = await app_client.post(f"{base}/{item_id}:cancel", headers=member["headers"])
    assert response.status_code == 200
    assert response.json()["data"]["state"] == "cancelled"


# ---------------------------------------------------------------------------
# Orphan exclusion: list/summary vs audit endpoint (§3.9 / §5.6 ⑤)
# ---------------------------------------------------------------------------


async def test_orphan_excluded_from_list_but_visible_in_audit(app_client, session_factory):
    world = await make_world(app_client, session_factory, "orphan")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_ORPH")
    conv = f"slack:{TENANT}:C_ORPH"
    # A live item (parent intact) + a terminal orphan (binding_id NULL).
    await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                    seq=1, state="pending", conversation_key=conv,
                    sender_identity_key=f"slack:{TENANT}:U", excerpt="live-item")
    async with session_factory() as session, session.begin():
        session.add(IntegrationMessageQueue(
            workspace_id=uuid.UUID(world["ws_id"]),
            integration_id=None,
            binding_id=None,
            binding_display="room: C_GONE",
            project_id_snapshot=None,
            conversation_key="slack:T_GONE:C_GONE",
            seq=1,
            dispatch_mode="serial_conversation",
            state="done",
            message_excerpt="orphan-item",
            sender_identity_key="slack:T_GONE:U_GONE",
        ))

    # Normal queue list excludes the orphan, includes the live item.
    listed = (
        await app_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue",
            headers=world["headers"],
        )
    ).json()["data"]
    excerpts = {item["message_excerpt"] for item in listed}
    assert "live-item" in excerpts
    assert "orphan-item" not in excerpts, "orphans never appear on the normal queue endpoint"

    # Summary also excludes the orphan conversation.
    summary = (
        await app_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue/summary",
            headers=world["headers"],
        )
    ).json()["data"]
    assert all(entry["conversation_key"] != "slack:T_GONE:C_GONE" for entry in summary)

    # The audit endpoint is the orphan's ONLY read path.
    audit = (
        await app_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integration-queue-audit",
            headers=world["headers"],
        )
    ).json()["data"]
    audit_excerpts = {row["message_excerpt"] for row in audit}
    assert "orphan-item" in audit_excerpts
    assert "live-item" not in audit_excerpts, "live items are not audit rows"
    orphan_row = next(row for row in audit if row["message_excerpt"] == "orphan-item")
    assert orphan_row["binding_display"] == "room: C_GONE"
    assert orphan_row["state"] == "done"


# ---------------------------------------------------------------------------
# Deleted-project snapshot orphan visibility (§3.9 audit 写死)
# ---------------------------------------------------------------------------


async def test_deleted_project_snapshot_orphan_admin_only(app_client, session_factory):
    world = await make_world(app_client, session_factory, "delproj")
    member = await add_member(app_client, session_factory, world, role="member")
    # A physically-deleted private project; an orphan snapshots its id.
    async with session_factory() as session, session.begin():
        project = Project(
            workspace_id=uuid.UUID(world["ws_id"]),
            name="Ghost Project",
            key=f"GP{uuid.uuid4().hex[:4].upper()}",
            visibility="private",
        )
        session.add(project)
        await session.flush()
        project_id = project.id
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
    async with session_factory() as session, session.begin():
        session.add(IntegrationMessageQueue(
            workspace_id=uuid.UUID(world["ws_id"]),
            integration_id=None,
            binding_id=None,
            binding_display="room: C_PROJ_GONE",
            project_id_snapshot=project_id,
            conversation_key="slack:T_P:C_PROJ_GONE",
            seq=1,
            dispatch_mode="serial_conversation",
            state="cancelled",
            message_excerpt="proj-orphan",
            sender_identity_key="slack:T_P:U_P",
        ))

    audit_url = f"/api/v1/workspaces/{world['ws_id']}/integration-queue-audit"
    # Non-admin member: the snapshot project is gone → not visible.
    member_audit = (await app_client.get(audit_url, headers=member["headers"])).json()["data"]
    assert all(row["message_excerpt"] != "proj-orphan" for row in member_audit)
    # Owner (workspace manager): visible as the admin fallback.
    owner_audit = (await app_client.get(audit_url, headers=world["headers"])).json()["data"]
    assert any(row["message_excerpt"] == "proj-orphan" for row in owner_audit)


# ---------------------------------------------------------------------------
# Project-scoped item visibility (§3.9 — private project membership)
# ---------------------------------------------------------------------------


async def test_project_scoped_item_visibility(app_client, session_factory):
    world = await make_world(app_client, session_factory, "projvis")
    member = await add_member(app_client, session_factory, world, role="member")
    integration_id = await seed_integration(session_factory, world)
    async with session_factory() as session, session.begin():
        project = Project(
            workspace_id=uuid.UUID(world["ws_id"]),
            name="Private Proj",
            key=f"PV{uuid.uuid4().hex[:4].upper()}",
            visibility="private",
        )
        session.add(project)
        await session.flush()
        project_id = project.id
    binding_id = await seed_binding(session_factory, world, integration_id,
                                    external_ref="C_PV", scope="project", project_id=project_id)
    conv = f"slack:{TENANT}:C_PV"
    await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                    seq=1, state="pending", conversation_key=conv,
                    sender_identity_key=f"slack:{TENANT}:U", project_id_snapshot=project_id,
                    excerpt="proj-item")
    # A workspace-scoped item is visible to every member regardless of project.
    ws_binding = await seed_binding(session_factory, world, integration_id, external_ref="C_PV_WS")
    await seed_item(session_factory, world, integration_id=integration_id, binding_id=ws_binding,
                    seq=1, state="pending", conversation_key=f"slack:{TENANT}:C_PV_WS",
                    sender_identity_key=f"slack:{TENANT}:U", excerpt="ws-item")

    queue_url = f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue"
    # Non-member of the private project sees only the workspace item.
    member_items = (await app_client.get(queue_url, headers=member["headers"])).json()["data"]
    member_excerpts = {item["message_excerpt"] for item in member_items}
    assert "ws-item" in member_excerpts
    assert "proj-item" not in member_excerpts, "private project item hidden from non-member"

    # Grant project membership → the project item becomes visible.
    async with session_factory() as session, session.begin():
        session.add(ProjectMember(
            workspace_id=uuid.UUID(world["ws_id"]),
            project_id=project_id,
            member_id=member["member_id"],
            role="member",
        ))
    member_items_after = (await app_client.get(queue_url, headers=member["headers"])).json()["data"]
    assert "proj-item" in {item["message_excerpt"] for item in member_items_after}

    # The owner (workspace manager) always sees it.
    owner_items = (await app_client.get(queue_url, headers=world["headers"])).json()["data"]
    assert "proj-item" in {item["message_excerpt"] for item in owner_items}


# ---------------------------------------------------------------------------
# Summary shape (§3.9 queue/summary)
# ---------------------------------------------------------------------------


async def test_summary_shape(app_client, session_factory):
    world = await make_world(app_client, session_factory, "summary")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_SUM")
    conv = f"slack:{TENANT}:C_SUM"
    sender = f"slack:{TENANT}:U"
    await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=1, state="processing", conversation_key=conv, sender_identity_key=sender, excerpt="S1",
    )
    await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=2, state="pending", conversation_key=conv, sender_identity_key=sender, excerpt="S2",
    )
    await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=3, state="pending", conversation_key=conv, sender_identity_key=sender, excerpt="S3",
    )

    summary = (
        await app_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue/summary",
            headers=world["headers"],
        )
    ).json()["data"]
    entry = next(e for e in summary if e["conversation_key"] == conv)
    assert entry["pending_count"] == 2
    assert entry["in_flight"] == [{"id": entry["in_flight"][0]["id"], "state": "processing", "seq": 1}]


async def test_queue_list_filters_state_and_conversation(app_client, session_factory):
    world = await make_world(app_client, session_factory, "filters")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_FILT")
    conv_a = f"slack:{TENANT}:C_FILT_A"
    conv_b = f"slack:{TENANT}:C_FILT_B"
    sender = f"slack:{TENANT}:U"
    await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=1, state="pending", conversation_key=conv_a, sender_identity_key=sender, excerpt="A1",
    )
    await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=1, state="done", conversation_key=conv_b, sender_identity_key=sender, excerpt="B1",
    )

    base = f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/queue"
    pending_resp = await app_client.get(base, params={"state": "pending"}, headers=world["headers"])
    pending_only = pending_resp.json()["data"]
    assert {item["message_excerpt"] for item in pending_only} == {"A1"}
    conv_resp = await app_client.get(base, params={"conversation_key": conv_b}, headers=world["headers"])
    conv_only = conv_resp.json()["data"]
    assert {item["message_excerpt"] for item in conv_only} == {"B1"}


# ---------------------------------------------------------------------------
# Direct service-level tests (logic + branch coverage; the HTTP tests above
# verify routing / envelopes / RBAC end-to-end through the real app)
# ---------------------------------------------------------------------------


def test_split_identity_triple_validation():
    from mesh.integrations.queue_api import split_identity_triple

    assert split_identity_triple("") is None
    assert split_identity_triple("slack:T") is None  # too few segments
    assert split_identity_triple("slack::U") is None  # empty tenant segment
    assert split_identity_triple("slack:T:U:extra") is None  # ':' in third segment
    assert split_identity_triple("slack:T_TEST:U_1") == ("slack", "T_TEST", "U_1")


def test_render_fallbacks_without_sender():
    from mesh.integrations.queue_api import render_audit_row, render_queue_item

    item = IntegrationMessageQueue(
        id=uuid.uuid4(), conversation_key="slack:T:C", seq=1, state="pending",
        dispatch_mode="serial_conversation", message_excerpt="x",
        sender_identity_key="slack:T:U", binding_display="b",
    )
    rendered = render_queue_item(item, position=None, sender=None, target_agent=None)
    assert rendered["sender"]["linked"] is False
    assert rendered["sender"]["identity_key"] == "slack:T:U"
    assert rendered["target_agent"] is None
    audit = render_audit_row(item, sender=None)
    assert audit["sender"]["linked"] is False


def test_path_uuid_invalid_raises():
    from mesh.errors import NotFoundError
    from mesh.integrations.queue_api import _path_uuid

    with pytest.raises(NotFoundError):
        _path_uuid("not-a-uuid", what="integration")


async def _member_obj(session_factory, member_id):
    async with session_factory() as session:
        return await session.get(Member, member_id)


async def _make_member_direct(session_factory, world, *, role: str):
    async with session_factory() as session, session.begin():
        user = User(
            email=f"qa-direct-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Direct Member",
            password_hash="unused",
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=uuid.UUID(world["ws_id"]),
            member_type="human",
            user_id=user.id,
            role=role,
            status="active",
        )
        session.add(member)
        await session.flush()
        return member.id, user.id


async def test_service_list_summary_audit_direct(app_client, session_factory):
    from mesh.integrations import queue_api as qa

    world = await make_world(app_client, session_factory, "direct")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_DIRECT")
    conv = f"slack:{TENANT}:C_DIRECT"
    await seed_identity(session_factory, provider="slack", tenant=TENANT,
                        external_user_key="U_DIR", user_id=world["user_id"])
    await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                    seq=1, state="processing", conversation_key=conv,
                    sender_identity_key=f"slack:{TENANT}:U_DIR",
                    target_agent_id=uuid.UUID(world["agent_id"]), excerpt="D1")
    await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                    seq=2, state="pending", conversation_key=conv,
                    sender_identity_key=f"slack:{TENANT}:U_DIR", excerpt="D2")
    viewer = await _member_obj(session_factory, uuid.UUID(world["member_id"]))
    ws = uuid.UUID(world["ws_id"])

    listed = await qa.list_queue_items(
        session_factory, workspace_id=ws, integration_id=integration_id, viewer=viewer
    )
    assert {i["message_excerpt"] for i in listed["data"]} == {"D1", "D2"}
    assert _by_excerpt(listed["data"], "D2")["position"] == 1
    assert _by_excerpt(listed["data"], "D1")["sender"]["linked"] is True
    assert _by_excerpt(listed["data"], "D1")["target_agent"]["id"] == world["agent_id"]

    summary = await qa.queue_summary(
        session_factory, workspace_id=ws, integration_id=integration_id, viewer=viewer
    )
    entry = next(e for e in summary["data"] if e["conversation_key"] == conv)
    assert entry["pending_count"] == 1
    assert entry["in_flight"][0]["state"] == "processing"

    audit = await qa.list_queue_audit(session_factory, workspace_id=ws, viewer=viewer)
    assert audit["data"] == [], "no orphans yet"


async def test_service_cancel_404_422_403_direct(app_client, session_factory):
    from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError
    from mesh.integrations import queue_api as qa

    world = await make_world(app_client, session_factory, "cancel-direct")
    outsider_id, _outsider_user = await _make_member_direct(session_factory, world, role="member")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_CD")
    await seed_identity(session_factory, provider="slack", tenant=TENANT,
                        external_user_key="U_CD_OWNER", user_id=world["user_id"])
    pending_id = await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=1, state="pending", conversation_key=f"slack:{TENANT}:C_CD",
        sender_identity_key=f"slack:{TENANT}:U_CD_OWNER", excerpt="CD1",
    )
    processing_id = await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=1, state="processing", conversation_key=f"slack:{TENANT}:C_CD2",
        sender_identity_key=f"slack:{TENANT}:U_CD_OWNER", excerpt="CD2",
    )
    # Malformed sender key: triple resolution returns None → never authorized.
    malformed_id = await seed_item(
        session_factory, world, integration_id=integration_id, binding_id=binding_id,
        seq=2, state="pending", conversation_key=f"slack:{TENANT}:C_CD",
        sender_identity_key="malformed-no-colons", excerpt="CD3",
    )
    ws = uuid.UUID(world["ws_id"])
    owner = await _member_obj(session_factory, uuid.UUID(world["member_id"]))
    outsider = await _member_obj(session_factory, outsider_id)

    with pytest.raises(NotFoundError):
        await qa.cancel_queue_item(session_factory, workspace_id=ws, integration_id=integration_id,
                                   item_id=uuid.uuid4(), requester=owner)
    with pytest.raises(ForbiddenError) as exc403:
        await qa.cancel_queue_item(session_factory, workspace_id=ws, integration_id=integration_id,
                                   item_id=pending_id, requester=outsider)
    assert exc403.value.code == "command_forbidden"
    # Malformed sender triple → resolution None → outsider still forbidden.
    with pytest.raises(ForbiddenError):
        await qa.cancel_queue_item(session_factory, workspace_id=ws, integration_id=integration_id,
                                   item_id=malformed_id, requester=outsider)
    with pytest.raises(BusinessRuleError) as exc422:
        await qa.cancel_queue_item(session_factory, workspace_id=ws, integration_id=integration_id,
                                   item_id=processing_id, requester=owner)
    assert exc422.value.code == "queue_item_not_cancellable"
    ok = await qa.cancel_queue_item(session_factory, workspace_id=ws, integration_id=integration_id,
                                    item_id=pending_id, requester=owner)
    assert ok["data"]["state"] == "cancelled"


async def test_service_guest_visibility_and_no_target(app_client, session_factory):
    from mesh.integrations import queue_api as qa

    world = await make_world(app_client, session_factory, "guest")
    guest_id, _guest_user = await _make_member_direct(session_factory, world, role="guest")
    integration_id = await seed_integration(session_factory, world)
    binding_id = await seed_binding(session_factory, world, integration_id, external_ref="C_GUEST")
    # Workspace item with a malformed sender (unlinked) and NO target agent.
    await seed_item(session_factory, world, integration_id=integration_id, binding_id=binding_id,
                    seq=1, state="pending", conversation_key=f"slack:{TENANT}:C_GUEST",
                    sender_identity_key="malformed-no-colons", excerpt="G1")
    # Two orphans: a workspace-level one (visible to all) and one snapshotting a
    # public project the guest has NO grant for (guests see only granted projects).
    async with session_factory() as session, session.begin():
        project = Project(workspace_id=uuid.UUID(world["ws_id"]), name="GPub",
                          key=f"GG{uuid.uuid4().hex[:4].upper()}", visibility="public")
        session.add(project)
        await session.flush()
        project_id = project.id
    async with session_factory() as session, session.begin():
        session.add(IntegrationMessageQueue(
            workspace_id=uuid.UUID(world["ws_id"]), integration_id=None, binding_id=None,
            binding_display="room: G_WS", project_id_snapshot=None,
            conversation_key="slack:TG:CG_WS", seq=1, dispatch_mode="serial_conversation",
            state="done", message_excerpt="G-ws-orphan", sender_identity_key="slack:TG:UG",
        ))
        session.add(IntegrationMessageQueue(
            workspace_id=uuid.UUID(world["ws_id"]), integration_id=None, binding_id=None,
            binding_display="room: G_PROJ", project_id_snapshot=project_id,
            conversation_key="slack:TG:CG_PROJ", seq=1, dispatch_mode="serial_conversation",
            state="done", message_excerpt="G-proj-orphan", sender_identity_key="slack:TG:UG",
        ))
    ws = uuid.UUID(world["ws_id"])
    guest = await _member_obj(session_factory, guest_id)
    owner = await _member_obj(session_factory, uuid.UUID(world["member_id"]))

    # Guest list: guest visibility clause exercised; the workspace item is seen,
    # unlinked sender falls back to the identity key, no target agent → null.
    listed = await qa.list_queue_items(
        session_factory, workspace_id=ws, integration_id=integration_id, viewer=guest
    )
    g1 = _by_excerpt(listed["data"], "G1")
    assert g1["sender"]["linked"] is False
    assert g1["sender"]["display_name"] == "malformed-no-colons"
    assert g1["target_agent"] is None

    # Guest audit: sees the workspace-level orphan, NOT the ungranted project's.
    guest_audit = {r["message_excerpt"] for r in
                   (await qa.list_queue_audit(session_factory, workspace_id=ws, viewer=guest))["data"]}
    assert "G-ws-orphan" in guest_audit
    assert "G-proj-orphan" not in guest_audit

    # Owner (manager) audit: sees both.
    owner_audit = {r["message_excerpt"] for r in
                   (await qa.list_queue_audit(session_factory, workspace_id=ws, viewer=owner))["data"]}
    assert {"G-ws-orphan", "G-proj-orphan"} <= owner_audit
