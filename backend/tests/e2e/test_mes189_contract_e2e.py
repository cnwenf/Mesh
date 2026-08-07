"""MES-189 real-stack contract e2e — six alignment contracts over the REAL
stack (uvicorn API subprocess as the RLS ``mesh_app`` role + real PostgreSQL
+ real Redis + real outbox relay with the production handler set).

Contracts asserted (docs/audits/mes185-interface-alignment-audit.md §4.1,
parity checklist rows closed by MES-189):

1. inline approval — daemon requests approval → ``review_requested`` inbox
   notification carries ``approval_id`` → console approve persists the
   decision on the unified ``approvals`` entity and requeues the execution;
2. notification auto-archive — read + expired groups are swept out of the
   main inbox view (``archive_read_expired_notifications``);
3. email open token — the signed one-time link marks the notification read
   over unauthenticated HTTP (comment-inbox.md §4.4);
4. squad export — GET /squads/{id}/export returns the markdown archive
   (squad.md §4.6);
5. skill bulk bind — one installation binds to many agents with per-item
   conflict markers on repeat (skill.md 批量操作);
6. assign guardrail — a non-owner assigning a PRIVATE agent is skipped with
   ``visibility_private`` and enqueues nothing (agent.md §3.5/§3.6).

A PASS verdict plus the asserted database states are written to
``docs/evidence/mes-189/real-stack-contract.json`` once all six contracts
hold (MES-188 evidence convention).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import select, update

from mesh.comment_inbox.notifications import issue_email_open_token
from mesh.config import load_settings
from mesh.db.engine import create_engine_from_settings, create_session_factory
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.realtime import RealtimeEvent
from mesh.db.models.runtime import Approval, Runtime, TaskExecution
from mesh.db.models.user import User
from mesh.workers.main import build_relay
from mesh.workers.notification_archive import archive_read_expired_notifications

pytestmark = pytest.mark.e2e

PASSWORD = "a-strong-passw0rd"

EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "evidence" / "mes-189" / "real-stack-contract.json"
)

CONTRACT_KEYS = (
    "inline_approval",
    "notification_auto_archive",
    "email_open_token_mark_read",
    "squad_export",
    "skill_bulk_bind",
    "assign_guardrail_private_agent",
)

CONTRACT_RESULTS: dict[str, dict] = {}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def evidence_writer():
    yield
    if set(CONTRACT_KEYS) <= set(CONTRACT_RESULTS):
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "verdict": "PASS",
            "issue": "MES-189",
            "contracts": {key: CONTRACT_RESULTS[key] for key in CONTRACT_KEYS},
            "provider_credentials_redacted": True,
        }
        EVIDENCE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client: httpx.AsyncClient, email: str, name: str = "MES189") -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _create_workspace(client: httpx.AsyncClient, token: str, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "MES189 WS", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["data"]


async def _create_issue(client: httpx.AsyncClient, token: str, ws_id: str, title: str) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json={"title": title}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _invite_accept(
    client: httpx.AsyncClient, owner_token: str, ws_id: str, email: str, role: str = "member"
) -> str:
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": role},
        headers=_auth(owner_token),
    )
    token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    joiner = await _register_and_login(client, email)
    await client.post("/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner))
    return joiner


async def _member_id_by_email(session_factory, email: str) -> uuid.UUID:
    async with session_factory() as session:
        row = await session.execute(
            select(Member.id).join(User, Member.user_id == User.id).where(User.email == email)
        )
        return row.scalar()


@pytest_asyncio.fixture
async def relay(db_url, redis_url):
    """The production relay (all handlers) over the test services."""
    settings = load_settings(database_url=db_url, redis_url=redis_url, auth_mode="dev")
    engine = create_engine_from_settings(settings)
    factory = create_session_factory(engine)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    from mesh.api.app import build_object_storage
    from mesh.auth.mailer import build_mailer
    from mesh.realtime.pubsub import RedisFanOut

    relay_instance = build_relay(
        settings,
        factory,
        RedisFanOut(redis_client),
        build_object_storage(settings),
        mailer=build_mailer(settings, redis_client),
    )
    yield relay_instance
    await redis_client.aclose()
    await engine.dispose()


async def _drain(relay, cycles: int = 10) -> None:
    """Run the relay until the outbox is quiet (handlers enqueue follow-ups)."""
    for _ in range(cycles):
        processed = await relay.run_once()
        if processed == 0:
            break
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# runtime / daemon helpers (T21 approval protocol, runtime.md §5)
# ---------------------------------------------------------------------------


async def _create_agent(client: httpx.AsyncClient, token: str, ws_id: str, name: str) -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/agents", json={"name": name}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _activated_runtime(client: httpx.AsyncClient, token: str, ws_id: str) -> tuple[dict, str]:
    created = await client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes",
        json={"name": "mes189-rt", "kind": "self_hosted", "labels": {}, "max_concurrent": 1},
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    runtime = created.json()["data"]
    activated = await client.post(
        "/api/v1/daemon/runtimes:activate",
        json={
            "activation_code": runtime["activation"]["code"],
            "metadata": {
                "hostname": "mes189-host",
                "os": "linux-x86_64",
                "cpu_cores": 4,
                "memory_mb": 8192,
                "capabilities": ["python"],
            },
        },
    )
    assert activated.status_code == 200, activated.text
    return runtime, activated.json()["data"]["runtime_token"]


async def _enqueue_execution(session_factory, ws_id: str, *, agent_id: str, issue_id: str) -> str:
    """Insert an execution.enqueue outbox event — the REAL production entry."""
    key = f"mes189-{uuid.uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(
            OutboxEvent(
                workspace_id=uuid.UUID(ws_id),
                event_type="execution.enqueue",
                payload={
                    "intent": "enqueue",
                    "agent_id": agent_id,
                    "issue_id": issue_id,
                    "trigger": "manual",
                    "trigger_event_id": str(uuid.uuid4()),
                    "idempotency_key": key,
                    "config_snapshot": {},
                    "required_capabilities": [],
                    "label_requirements": {},
                    "task_spec": {},
                },
                idempotency_key=key,
                status="pending",
            )
        )
    return key


async def _execution_by_key(session_factory, ws_id: str, key: str) -> TaskExecution:
    async with session_factory() as session:
        row = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.workspace_id == uuid.UUID(ws_id),
                TaskExecution.idempotency_key == key,
            )
        )
    assert row is not None, f"execution {key} never materialized"
    return row


# ---------------------------------------------------------------------------
# contract 1 — inline approval: inbox approval_id + decision persisted
# ---------------------------------------------------------------------------


async def test_inline_approval_persists_and_inbox_carries_approval_id(
    api_client, relay, session_factory
):
    alice = await _register_and_login(api_client, "mes189-alice@corp.example")
    ws = await _create_workspace(api_client, alice, f"mes189-ap-{uuid.uuid4().hex[:8]}")
    issue = await _create_issue(api_client, alice, ws["id"], "批准高风险工具调用")
    agent = await _create_agent(api_client, alice, ws["id"], "执行者")
    runtime, daemon_token = await _activated_runtime(api_client, alice, ws["id"])

    idem = await _enqueue_execution(
        session_factory, ws["id"], agent_id=agent["id"], issue_id=issue["id"]
    )
    await _drain(relay)
    execution = await _execution_by_key(session_factory, ws["id"], idem)

    claimed = await api_client.post(
        f"/api/v1/daemon/runtimes/{runtime['id']}/executions:claim",
        json={"diagnostics": {}},
        headers=_auth(daemon_token),
    )
    assert claimed.status_code == 200, claimed.text
    attempt = claimed.json()["data"]["attempt"]
    started = await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=_auth(daemon_token),
    )
    assert started.status_code == 200, started.text

    approval_resp = await api_client.post(
        f"/api/v1/daemon/executions/{execution.id}/approvals",
        json={
            "lease_seq": 1,
            "attempt_id": attempt["id"],
            "action_summary": {"action": "exec:shell", "capability": "exec:shell"},
            "resume_context": {"checkpoint_ref": "ckpt-mes189", "completed_steps": 1},
        },
        headers=_auth(daemon_token),
    )
    assert approval_resp.status_code == 200, approval_resp.text
    approval = approval_resp.json()["data"]
    assert approval["status"] == "pending"

    # fan-out → inbox: review_requested carries approval_id (comment-inbox §4.2)
    await _drain(relay)
    async with session_factory() as session:
        notifications = (
            (
                await session.execute(
                    select(Notification).where(Notification.type == "review_requested")
                )
            )
            .scalars()
            .all()
        )
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.priority == "critical"
    assert notification.payload["approval_id"] == approval["id"]

    alice_member = await _member_id_by_email(session_factory, "mes189-alice@corp.example")
    assert notification.recipient_id == alice_member

    listing = await api_client.get(
        "/api/v1/inbox", params={"workspace_id": ws["id"]}, headers=_auth(alice)
    )
    assert listing.status_code == 200
    items = listing.json()["data"]
    review_items = [item for item in items if item["type"] == "review_requested"]
    assert len(review_items) == 1
    assert review_items[0]["approval_id"] == approval["id"]

    # inline approve (console JWT) → decision persisted on approvals entity
    approve = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/approvals/{approval['id']}/approve",
        json={"comment": "proceed"},
        headers=_auth(alice),
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["data"]["execution_status"] == "queued"

    async with session_factory() as session:
        stored_approval = await session.get(Approval, uuid.UUID(approval["id"]))
        stored_exec = await session.get(TaskExecution, execution.id)
    assert stored_approval.status == "approved"
    assert stored_approval.decided_by_member_id == alice_member
    assert stored_exec.status == "queued"

    CONTRACT_RESULTS["inline_approval"] = {
        "verdict": "PASS",
        "workspace_id": ws["id"],
        "execution_id": str(execution.id),
        "database": {
            "approval_status": stored_approval.status,
            "decided_by_member_id_matches_recipient": True,
            "execution_requeued": stored_exec.status == "queued",
            "inbox_notification_type": notification.type,
            "inbox_payload_approval_id": True,
        },
    }


# ---------------------------------------------------------------------------
# contract 2 — read + expired notifications auto-archive
# ---------------------------------------------------------------------------


async def test_read_expired_notifications_auto_archive(api_client, relay, session_factory):
    alice = await _register_and_login(api_client, "mes189-arch-alice@corp.example")
    ws = await _create_workspace(api_client, alice, f"mes189-ar-{uuid.uuid4().hex[:8]}")
    bob = await _invite_accept(
        api_client, alice, ws["id"], "mes189-arch-bob@corp.example"
    )
    issue = await _create_issue(api_client, alice, ws["id"], "归档巡检")
    # bob comments → alice (reporter) receives the notification (§6.13)
    comment = await api_client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "bob pings alice"},
        headers=_auth(bob),
    )
    assert comment.status_code == 201, comment.text
    await _drain(relay)

    listing = await api_client.get(
        "/api/v1/inbox", params={"workspace_id": ws["id"]}, headers=_auth(alice)
    )
    items = listing.json()["data"]
    assert items, "expected alice to have an inbox notification"
    notification_id = items[0]["id"]

    read = await api_client.post(
        f"/api/v1/inbox/{notification_id}/read",
        params={"workspace_id": ws["id"]},
        headers=_auth(alice),
    )
    assert read.status_code == 200, read.text

    # backdate the read group past the retention window (time-travel, not wait)
    backdated = datetime.now(UTC) - timedelta(days=2)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Notification)
            .where(Notification.id == uuid.UUID(notification_id))
            .values(created_at=backdated, updated_at=backdated)
        )

    archived_count = await archive_read_expired_notifications(
        session_factory, retention=timedelta(hours=12), now=datetime.now(UTC)
    )
    assert archived_count == 1

    async with session_factory() as session:
        row = await session.scalar(
            select(Notification).where(Notification.id == uuid.UUID(notification_id))
        )
    assert row.archived_at is not None
    assert row.read_at is not None

    # main view hides archived rows; archived=true returns exactly this one
    main = await api_client.get(
        "/api/v1/inbox", params={"workspace_id": ws["id"]}, headers=_auth(alice)
    )
    assert main.json()["data"] == []
    archived_view = await api_client.get(
        "/api/v1/inbox",
        params={"workspace_id": ws["id"], "archived": "true"},
        headers=_auth(alice),
    )
    assert [item["id"] for item in archived_view.json()["data"]] == [notification_id]

    CONTRACT_RESULTS["notification_auto_archive"] = {
        "verdict": "PASS",
        "workspace_id": ws["id"],
        "database": {
            "sweep_archived_count": archived_count,
            "archived_at_set": True,
            "read_at_set": True,
            "main_view_hides_archived": True,
            "archived_view_returns_row": True,
        },
    }


# ---------------------------------------------------------------------------
# contract 3 — email open token marks the notification read over HTTP
# ---------------------------------------------------------------------------


async def test_email_open_token_marks_read_over_http(api_client, relay, session_factory, db_url, redis_url):
    alice = await _register_and_login(api_client, "mes189-mail-alice@corp.example")
    ws = await _create_workspace(api_client, alice, f"mes189-em-{uuid.uuid4().hex[:8]}")
    bob = await _invite_accept(
        api_client, alice, ws["id"], "mes189-mail-bob@corp.example"
    )
    issue = await _create_issue(api_client, alice, ws["id"], "邮件打开链接")
    comment = await api_client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "ping for open token"},
        headers=_auth(bob),
    )
    assert comment.status_code == 201, comment.text
    await _drain(relay)

    alice_member = await _member_id_by_email(session_factory, "mes189-mail-alice@corp.example")
    async with session_factory() as session:
        notification = await session.scalar(
            select(Notification).where(Notification.recipient_id == alice_member)
        )
    assert notification is not None
    assert notification.read_at is None

    # the signed link IS the credential — same dev signing key as the server
    settings = load_settings(database_url=db_url, redis_url=redis_url, auth_mode="dev")
    token = issue_email_open_token(
        settings,
        notification_id=notification.id,
        workspace_id=uuid.UUID(ws["id"]),
        recipient_member_id=alice_member,
    )

    opened = await api_client.get(f"/api/v1/inbox/{notification.id}/open", params={"token": token})
    assert opened.status_code == 200, opened.text
    frame = opened.json()["data"]
    assert frame["read_at"] is not None

    async with session_factory() as session:
        reread = await session.scalar(
            select(Notification).where(Notification.id == notification.id)
        )
    assert reread.read_at is not None

    # anti-oracle: tampered token and swapped notification id both → 404
    bad = await api_client.get(
        f"/api/v1/inbox/{notification.id}/open", params={"token": token + "x"}
    )
    assert bad.status_code == 404
    other_id = uuid.uuid4()
    swapped = await api_client.get(f"/api/v1/inbox/{other_id}/open", params={"token": token})
    assert swapped.status_code == 404

    CONTRACT_RESULTS["email_open_token_mark_read"] = {
        "verdict": "PASS",
        "workspace_id": ws["id"],
        "database": {
            "read_at_set_via_token": True,
            "unauthenticated_endpoint": True,
            "tampered_token_404": True,
            "wrong_notification_404": True,
        },
    }


# ---------------------------------------------------------------------------
# contract 4 — squad export markdown archive over HTTP
# ---------------------------------------------------------------------------


async def test_squad_export_markdown_over_http(api_client, session_factory):
    owner = await _register_and_login(api_client, "mes189-squad@corp.example")
    ws = await _create_workspace(api_client, owner, f"mes189-sq-{uuid.uuid4().hex[:8]}")
    roster = await api_client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=_auth(owner))
    owner_member = next(m for m in roster.json()["data"] if m["member_type"] == "human")

    squad_name = "归档导出小队"
    created = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads",
        json={
            "name": squad_name,
            "members": [{"member_id": owner_member["id"], "role": "leader"}],
        },
        headers=_auth(owner),
    )
    assert created.status_code == 201, created.text
    squad = created.json()["data"]

    issue = await _create_issue(api_client, owner, ws["id"], "导出素材任务")
    task = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/tasks",
        json={"issue_id": issue["id"]},
        headers=_auth(owner),
    )
    assert task.status_code == 202, task.text

    exported = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/export", headers=_auth(owner)
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/markdown")
    assert "attachment" in exported.headers.get("content-disposition", "")
    body = exported.text
    assert f"# 小队归档：{squad_name}" in body
    assert "## 任务清单（1）" in body
    assert "导出素材任务" in body
    assert "## 任务消息（" in body
    assert "## 时间线（" in body

    # non-members cannot export (workspace membership gate)
    stranger = await _register_and_login(api_client, "mes189-stranger@corp.example")
    denied = await api_client.get(
        f"/api/v1/workspaces/{ws['id']}/squads/{squad['id']}/export", headers=_auth(stranger)
    )
    assert denied.status_code in (403, 404)

    CONTRACT_RESULTS["squad_export"] = {
        "verdict": "PASS",
        "workspace_id": ws["id"],
        "squad_id": squad["id"],
        "database": {
            "status_code": 200,
            "content_type_markdown": True,
            "header_and_sections_present": True,
            "non_member_denied": True,
        },
    }


# ---------------------------------------------------------------------------
# contract 5 — skill bulk bind with per-item conflict markers
# ---------------------------------------------------------------------------


async def test_skill_bulk_bind_and_conflict_markers(api_client, session_factory):
    owner = await _register_and_login(api_client, "mes189-skill@corp.example")
    ws = await _create_workspace(api_client, owner, f"mes189-sk-{uuid.uuid4().hex[:8]}")

    skill = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/skills",
            json={"name": "mes189-skill", "summary": "contract"},
            headers=_auth(owner),
        )
    ).json()["data"]["id"]
    version_id = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/skills/{skill}/versions",
            json={"version": "1.0.0", "instructions": "do the thing", "publish": True},
            headers=_auth(owner),
        )
    ).json()["data"]["id"]
    installation_id = (
        await api_client.post(
            f"/api/v1/workspaces/{ws['id']}/skill-installations",
            json={"skill_id": skill, "skill_version_id": version_id, "scope": "workspace"},
            headers=_auth(owner),
        )
    ).json()["data"]["id"]

    agent_ids = [
        (
            await api_client.post(
                f"/api/v1/workspaces/{ws['id']}/agents",
                json={"name": f"bulk-{i}", "system_instructions": "x"},
                headers=_auth(owner),
            )
        ).json()["data"]["id"]
        for i in range(2)
    ]

    first = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/skills/bulk-bind",
        json={"skill_installation_id": installation_id, "agent_ids": agent_ids},
        headers=_auth(owner),
    )
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert {b["agent_id"] for b in data["bound"]} == set(agent_ids)
    assert data["errors"] == []

    again = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/skills/bulk-bind",
        json={"skill_installation_id": installation_id, "agent_ids": agent_ids},
        headers=_auth(owner),
    )
    assert again.status_code == 200
    data2 = again.json()["data"]
    assert data2["bound"] == []
    assert {e["agent_id"] for e in data2["errors"]} == set(agent_ids)
    assert all(e["code"] == "conflict" for e in data2["errors"])

    # DB persistence: each agent's binding list carries the skill
    for agent_id in agent_ids:
        rows = await api_client.get(
            f"/api/v1/workspaces/{ws['id']}/agents/{agent_id}/skills", headers=_auth(owner)
        )
        assert rows.status_code == 200
        assert len(rows.json()["data"]) == 1

    CONTRACT_RESULTS["skill_bulk_bind"] = {
        "verdict": "PASS",
        "workspace_id": ws["id"],
        "skill_installation_id": installation_id,
        "database": {
            "bound_count": len(data["bound"]),
            "repeat_conflict_markers": len(data2["errors"]),
            "bindings_persisted_per_agent": True,
        },
    }


# ---------------------------------------------------------------------------
# contract 6 — private agent assign guardrail (visibility_private skip)
# ---------------------------------------------------------------------------


async def test_private_agent_assign_guardrail_blocks_non_owner(api_client, relay, session_factory):
    alice = await _register_and_login(api_client, "mes189-guard-alice@corp.example")
    ws = await _create_workspace(api_client, alice, f"mes189-gd-{uuid.uuid4().hex[:8]}")
    private_agent = await api_client.post(
        f"/api/v1/workspaces/{ws['id']}/agents",
        json={"name": "私有执行者", "visibility": "private"},
        headers=_auth(alice),
    )
    assert private_agent.status_code == 201, private_agent.text
    agent = private_agent.json()["data"]
    agent_member_id = agent["member"]["id"]

    bob = await _invite_accept(
        api_client, alice, ws["id"], "mes189-guard-bob@corp.example", role="admin"
    )

    # bob assigns alice's private agent → guardrail MUST skip, no execution
    bob_issue = await _create_issue(api_client, bob, ws["id"], "bob 试图分派私有 agent")
    assigned = await api_client.patch(
        f"/api/v1/issues/{bob_issue['id']}",
        json={"assignee_id": agent_member_id},
        headers=_auth(bob),
    )
    assert assigned.status_code == 200, assigned.text
    await _drain(relay)

    async with session_factory() as session:
        skipped = (
            (
                await session.execute(
                    select(RealtimeEvent).where(RealtimeEvent.event == "agent.trigger_skipped")
                )
            )
            .scalars()
            .all()
        )
        enqueues = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                )
            )
            .scalars()
            .all()
        )
        executions = (
            (await session.execute(select(TaskExecution))).scalars().all()
        )
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "visibility_private"
    assert skipped[0].payload["issue_id"] == bob_issue["id"]
    assert enqueues == []
    assert executions == []

    # positive control: the OWNER assigning the same private agent passes
    alice_issue = await _create_issue(api_client, alice, ws["id"], "owner 自己分派私有 agent")
    own = await api_client.patch(
        f"/api/v1/issues/{alice_issue['id']}",
        json={"assignee_id": agent_member_id},
        headers=_auth(alice),
    )
    assert own.status_code == 200, own.text
    await _drain(relay)

    async with session_factory() as session:
        owner_enqueues = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
                )
            )
            .scalars()
            .all()
        )
        owner_executions = (await session.execute(select(TaskExecution))).scalars().all()
    assert len(owner_enqueues) == 1
    assert owner_enqueues[0].payload["agent_id"] == agent["id"]
    assert len(owner_executions) == 1

    CONTRACT_RESULTS["assign_guardrail_private_agent"] = {
        "verdict": "PASS",
        "workspace_id": ws["id"],
        "agent_id": agent["id"],
        "database": {
            "non_owner_skipped_reason": "visibility_private",
            "non_owner_enqueued_executions": 0,
            "owner_assign_enqueued": True,
            "owner_execution_materialized": len(owner_executions) == 1,
        },
    }
