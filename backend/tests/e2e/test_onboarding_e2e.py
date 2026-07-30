"""Onboarding T34 — four REAL scenarios end-to-end (README §9 T34, onboarding.md §5).

Real uvicorn (mesh_app RLS) + real API calls + real outbox relay (production
handler set incl. the onboarding consumer) + real PostgreSQL:

① enrollment seeding — workspace creation / invitation redeem seed the
   checklist in the same transaction (step 1 completed); agent members are
   never seeded;
② mature-workspace reconcile — an invitee into a workspace with agent /
   issues / historical executions gets steps 2–3 completed WITH evidence,
   while step 4 stays pending because they never triggered an execution
   themselves (R4 — no batch completion, no fabricated evidence);
③ unread never completes — the agent reply exists and its notification is
   delivered, but until the member reads it the final step stays pending and
   aha is not set;
④ strict trigger ownership — the wrong member reading the reply does NOT
   complete their final step; the trigger member reading it DOES, with the
   persisted {execution_id, comment_id, notification_id, trigger_member_id}
   evidence and the one-shot onboarding.completed event.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import select

from mesh.config import load_settings
from mesh.db.engine import create_engine_from_settings, create_session_factory
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification
from mesh.db.models.onboarding import (
    STEP_CREATE_FIRST_ISSUE,
    STEP_CREATE_WORKSPACE,
    STEP_DISPATCH_OR_MENTION_AGENT,
    STEP_INVITE_MEMBER_OR_ADD_AGENT,
    STEP_SEE_AGENT_REPLY_IN_INBOX,
    OnboardingState,
    OnboardingStateStep,
)
from mesh.db.models.realtime import RealtimeEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.models.user import User
from mesh.workers.main import build_relay
from tests.unit.runtime_support import valid_result_v1

pytestmark = pytest.mark.e2e

PASSWORD = "a-strong-passw0rd"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _daemon(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- setup helpers (real API) ----------------------------------------------------


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _create_workspace(client, token: str, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Onb T34", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _invite_accept(client, owner_token: str, ws_id: str, email: str) -> str:
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": "member"},
        headers=_auth(owner_token),
    )
    assert inv.status_code == 201, inv.text
    token = await _register_and_login(client, email)
    accept_token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    resp = await client.post(
        "/api/v1/invitations/accept", json={"token": accept_token}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return token


async def _create_agent(client, token: str, ws_id: str, name: str = "助手") -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/agents",
        json={
            "name": name,
            "system_instructions": "你是测试助手。",
            "model_config": {"model_tier": "balanced", "temperature": 0.2},
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_issue(client, token: str, ws_id: str, assignee_id: str | None = None) -> dict:
    body: dict = {"title": "接入登录模块"}
    if assignee_id is not None:
        body["assignee_id"] = assignee_id
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _post_comment(client, token: str, issue_id: str, body: str) -> dict:
    resp = await client.post(
        f"/api/v1/issues/{issue_id}/comments", json={"body_markdown": body}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _activated_runtime(client, token: str, ws_id: str) -> tuple[str, str]:
    created = await client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes",
        json={"name": "runner-01", "kind": "self_hosted", "labels": {}, "max_concurrent": 2},
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    runtime = created.json()["data"]
    activated = await client.post(
        "/api/v1/daemon/runtimes:activate",
        json={"activation_code": runtime["activation"]["code"], "metadata": {}},
    )
    assert activated.status_code == 200, activated.text
    return runtime["id"], activated.json()["data"]["runtime_token"]


async def _run_execution_to_completion(client, runtime_id: str, daemon_token: str) -> dict:
    """Real daemon claim → running → completed (runtime.md §3)."""
    claimed = await client.post(
        f"/api/v1/daemon/runtimes/{runtime_id}/executions:claim",
        json={"diagnostics": {}},
        headers=_daemon(daemon_token),
    )
    assert claimed.status_code == 200, claimed.text
    attempt = claimed.json()["data"]["attempt"]
    running = await client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=_daemon(daemon_token),
    )
    assert running.status_code == 200, running.text
    done = await client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "completed", "result": valid_result_v1()},
        headers=_daemon(daemon_token),
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["execution_status"] == "completed"
    return attempt


async def _mark_read(client, token: str, ws_id: str, notification_id: str) -> None:
    resp = await client.post(
        f"/api/v1/inbox/{notification_id}/read",
        params={"workspace_id": ws_id},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text


async def _onboarding_state(client, token: str, ws_id: str) -> dict:
    resp = await client.get(
        "/api/v1/onboarding/state", params={"workspace_id": ws_id}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _step(state_payload: dict, key: str) -> dict:
    return next(s for s in state_payload["steps"] if s["step_key"] == key)


# --- DB-level helpers (real database, owner role) ---------------------------------


async def _member_id_by_email(factory, email: str) -> uuid.UUID:
    async with factory() as session:
        return (
            await session.execute(
                select(Member.id).join(User, Member.user_id == User.id).where(User.email == email)
            )
        ).scalar_one()


async def _steps_by_key(factory, state_id: uuid.UUID) -> dict[str, OnboardingStateStep]:
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(OnboardingStateStep).where(OnboardingStateStep.state_id == state_id)
                )
            )
            .scalars()
            .all()
        )
    return {row.step_key: row for row in rows}


async def _state_by_member(factory, member_id: uuid.UUID) -> OnboardingState | None:
    async with factory() as session:
        return await session.scalar(
            select(OnboardingState).where(OnboardingState.member_id == member_id)
        )


async def _notifications_for(factory, member_id: uuid.UUID) -> list[Notification]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(Notification).where(Notification.recipient_id == member_id)
                )
            )
            .scalars()
            .all()
        )


async def _drain(relay, passes: int = 12) -> None:
    """Run relay passes until the outbox is quiescent (production polls
    continuously; tests drain explicitly between API steps)."""
    for _ in range(passes):
        result = await relay.run_once()
        if result.claimed == 0:
            break
        await asyncio.sleep(0.05)


# --- fixtures ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def owner_factory(db_url, redis_url):
    engine = create_engine_from_settings(load_settings(database_url=db_url, redis_url=redis_url))
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def relay(db_url, redis_url):
    """The PRODUCTION handler set (workers/main.py::build_relay) — includes
    the projector, notification fanout, execution enqueue and the onboarding
    consumer chained on realtime.publish."""
    settings = load_settings(database_url=db_url, redis_url=redis_url, auth_mode="dev")
    engine = create_engine_from_settings(settings)
    factory = create_session_factory(engine)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    from mesh.api.app import build_object_storage

    r = build_relay(settings, factory, None, build_object_storage(settings), mailer=None)
    yield r
    await redis_client.aclose()
    await engine.dispose()


# --- T34① enrollment seeding --------------------------------------------------------


async def test_t34_1_enrollment_seeding(api_client, owner_factory, relay, db_url, redis_url):
    tag = uuid.uuid4().hex[:8]
    owner_email = f"t34a-owner-{tag}@e2e.mesh"
    owner = await _register_and_login(api_client, owner_email)
    ws = await _create_workspace(api_client, owner, f"t34a-{tag}")

    # Workspace creation seeded the owner's checklist (same transaction),
    # step 1 completed in-transaction.
    state = await _onboarding_state(api_client, owner, ws["id"])
    assert _step(state, STEP_CREATE_WORKSPACE)["status"] == "completed"
    assert _step(state, STEP_CREATE_WORKSPACE)["completed_via"] == "auto"
    assert state["progress"]["completed"] == 1

    # Invitation redeem seeds the invitee's checklist in the accept
    # transaction (before any GET).
    invitee_email = f"t34a-inv-{tag}@e2e.mesh"
    invitee = await _invite_accept(api_client, owner, ws["id"], invitee_email)
    invitee_mid = await _member_id_by_email(owner_factory, invitee_email)
    seeded = await _state_by_member(owner_factory, invitee_mid)
    assert seeded is not None  # seeded at redemption, NOT by a GET
    steps = await _steps_by_key(owner_factory, seeded.id)
    assert steps[STEP_CREATE_WORKSPACE].status == "completed"

    # Agent creation never seeds a checklist (T34①).
    agent = await _create_agent(api_client, owner, ws["id"])
    agent_state = await _state_by_member(owner_factory, uuid.UUID(agent["member"]["id"]))
    assert agent_state is None

    # Drain the member.added events → step 2 completes for both humans.
    await _drain(relay)
    state = await _onboarding_state(api_client, owner, ws["id"])
    assert _step(state, STEP_INVITE_MEMBER_OR_ADD_AGENT)["status"] == "completed"
    assert _step(state, STEP_CREATE_FIRST_ISSUE)["status"] == "pending"
    assert invitee is not None


# --- T34② mature workspace reconcile -------------------------------------------------


async def test_t34_2_mature_workspace_reconcile(api_client, owner_factory, relay):
    tag = uuid.uuid4().hex[:8]
    owner_email = f"t34b-owner-{tag}@e2e.mesh"
    owner = await _register_and_login(api_client, owner_email)
    ws = await _create_workspace(api_client, owner, f"t34b-{tag}")
    agent = await _create_agent(api_client, owner, ws["id"])
    runtime_id, daemon_token = await _activated_runtime(api_client, owner, ws["id"])
    issue = await _create_issue(api_client, owner, ws["id"], assignee_id=agent["member"]["id"])
    await _drain(relay)  # assign orchestration → execution.enqueue → execution.queued

    # Real execution completed by the real daemon.
    await _run_execution_to_completion(api_client, runtime_id, daemon_token)

    # The owner's own history: steps 2–4 completed with evidence.
    owner_mid = await _member_id_by_email(owner_factory, owner_email)
    owner_state = await _state_by_member(owner_factory, owner_mid)
    owner_steps = await _steps_by_key(owner_factory, owner_state.id)
    assert owner_steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].status == "completed"
    assert owner_steps[STEP_CREATE_FIRST_ISSUE].status == "completed"
    assert owner_steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "completed"
    assert owner_steps[STEP_DISPATCH_OR_MENTION_AGENT].evidence["trigger_member_id"] == str(
        owner_mid
    )
    execution_id = uuid.UUID(owner_steps[STEP_DISPATCH_OR_MENTION_AGENT].evidence["execution_id"])
    async with owner_factory() as session:
        execution = await session.get(TaskExecution, execution_id)
    assert execution is not None  # evidence points at a real execution

    # A NEW member invited into this mature workspace: reconcile completes
    # steps 2–3 from workspace facts, but step 4 stays pending because the
    # invitee never triggered an execution (R4 — never batched from the
    # workspace's executions, never fabricated).
    late_email = f"t34b-late-{tag}@e2e.mesh"
    await _invite_accept(api_client, owner, ws["id"], late_email)
    late_mid = await _member_id_by_email(owner_factory, late_email)
    late_state = await _state_by_member(owner_factory, late_mid)
    assert late_state is not None
    late_steps = await _steps_by_key(owner_factory, late_state.id)
    assert late_steps[STEP_CREATE_WORKSPACE].status == "completed"
    assert late_steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].status == "completed"
    assert "member_added_id" in late_steps[STEP_INVITE_MEMBER_OR_ADD_AGENT].evidence
    assert late_steps[STEP_CREATE_FIRST_ISSUE].status == "completed"
    assert late_steps[STEP_CREATE_FIRST_ISSUE].evidence["issue_id"] == str(issue["id"])
    # R4 core assertion: pending, empty evidence.
    assert late_steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "pending"
    assert late_steps[STEP_DISPATCH_OR_MENTION_AGENT].evidence == {}
    assert late_steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "pending"
    assert late_state.aha_reached_at is None
    assert daemon_token and runtime_id


# --- T34③ + ④ unread / wrong-trigger / trigger-member aha -----------------------------


async def _aha_world(api_client, owner_factory, relay, tag: str):
    """Owner A (trigger), participant B, agent, completed execution, agent
    reply comment fanned out to both inboxes. Returns the context dict."""
    owner_email = f"t34cd-owner-{tag}@e2e.mesh"
    buddy_email = f"t34cd-buddy-{tag}@e2e.mesh"
    owner = await _register_and_login(api_client, owner_email)
    ws = await _create_workspace(api_client, owner, f"t34cd-{tag}")
    buddy = await _invite_accept(api_client, owner, ws["id"], buddy_email)
    agent = await _create_agent(api_client, owner, ws["id"])
    runtime_id, daemon_token = await _activated_runtime(api_client, owner, ws["id"])
    issue = await _create_issue(api_client, owner, ws["id"])
    # B participates → subscribed to comment_created on this issue.
    await _post_comment(api_client, buddy, issue["id"], "我也关注这个任务")
    await _drain(relay)

    # Assign → real execution → real completion.
    patched = await api_client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"assignee_id": agent["member"]["id"]},
        headers=_auth(owner),
    )
    assert patched.status_code == 200, patched.text
    await _drain(relay)  # assign orchestration → enqueue → execution.queued (→ onboarding step 4)
    await _run_execution_to_completion(api_client, runtime_id, daemon_token)

    # Agent reply — real CommentService path (same mechanism the runtime
    # uses): real comment row, real outbox events, real notification fanout.
    from mesh.comment_inbox.service import CommentService

    comment_service = CommentService(owner_factory)
    async with owner_factory() as session:
        agent_member = await session.get(Member, uuid.UUID(agent["member"]["id"]))
    reply = await comment_service.create_comment(
        workspace_id=uuid.UUID(ws["id"]),
        issue_id=uuid.UUID(issue["id"]),
        author_member=agent_member,
        body_markdown="已完成登录模块接入,请查收。",
    )
    await _drain(relay)  # fanout → notifications rows

    owner_mid = await _member_id_by_email(owner_factory, owner_email)
    buddy_mid = await _member_id_by_email(owner_factory, buddy_email)
    execution = await _the_execution(owner_factory, ws["id"])
    return {
        "ws": ws,
        "owner": owner,
        "buddy": buddy,
        "owner_mid": owner_mid,
        "buddy_mid": buddy_mid,
        "issue": issue,
        "reply_id": uuid.UUID(reply["id"]),
        "execution": execution,
    }


async def _the_execution(factory, ws_id: str) -> TaskExecution:
    async with factory() as session:
        return await session.scalar(
            select(TaskExecution).where(TaskExecution.workspace_id == uuid.UUID(ws_id))
        )


async def _reply_notification(factory, member_id: uuid.UUID, comment_id: uuid.UUID) -> Notification:
    """The inbox row for the agent reply — direct comment_id or the
    aggregated group row whose payload.latest_comment_id is the reply."""
    async with factory() as session:
        direct = await session.scalar(
            select(Notification).where(
                Notification.recipient_id == member_id,
                Notification.comment_id == comment_id,
            )
        )
        if direct is not None:
            return direct
        rows = (
            (
                await session.execute(
                    select(Notification).where(Notification.recipient_id == member_id)
                )
            )
            .scalars()
            .all()
        )
    for row in rows:
        if (row.payload or {}).get("latest_comment_id") == str(comment_id):
            return row
    return None


async def test_t34_3_unread_never_completes(api_client, owner_factory, relay):
    tag = uuid.uuid4().hex[:8]
    world = await _aha_world(api_client, owner_factory, relay, tag)

    # The agent reply notification EXISTS in the owner's inbox but is unread.
    notif = await _reply_notification(owner_factory, world["owner_mid"], world["reply_id"])
    assert notif is not None
    assert notif.read_at is None

    # Even after the relay is fully drained: final step pending, no aha.
    await _drain(relay)
    state = await _onboarding_state(api_client, world["owner"], world["ws"]["id"])
    assert _step(state, STEP_DISPATCH_OR_MENTION_AGENT)["status"] == "completed"  # step 4 done
    assert _step(state, STEP_SEE_AGENT_REPLY_IN_INBOX)["status"] == "pending"
    assert state["aha_reached_at"] is None


async def test_t34_4_strict_trigger_ownership(api_client, owner_factory, relay):
    tag = uuid.uuid4().hex[:8]
    world = await _aha_world(api_client, owner_factory, relay, tag)
    ws_id = world["ws"]["id"]

    # B reads THEIR copy of the agent-reply notification first.
    b_notif = await _reply_notification(owner_factory, world["buddy_mid"], world["reply_id"])
    assert b_notif is not None
    await _mark_read(api_client, world["buddy"], ws_id, str(b_notif.id))
    await _drain(relay)  # notification.read consumed by the onboarding chain

    # B is NOT the trigger member → B's final step stays pending (T34④),
    # B's step 4 also stays pending (never triggered an execution, R4).
    b_state_row = await _state_by_member(owner_factory, world["buddy_mid"])
    b_steps = await _steps_by_key(owner_factory, b_state_row.id)
    assert b_steps[STEP_SEE_AGENT_REPLY_IN_INBOX].status == "pending"
    assert b_steps[STEP_SEE_AGENT_REPLY_IN_INBOX].evidence == {}
    assert b_steps[STEP_DISPATCH_OR_MENTION_AGENT].status == "pending"
    assert b_state_row.aha_reached_at is None

    # The trigger member reads → final step completes, aha set ONCE,
    # four-tuple evidence persisted (T34④).
    a_notif = await _reply_notification(owner_factory, world["owner_mid"], world["reply_id"])
    assert a_notif is not None
    await _mark_read(api_client, world["owner"], ws_id, str(a_notif.id))
    await _drain(relay)

    a_state_row = await _state_by_member(owner_factory, world["owner_mid"])
    a_steps = await _steps_by_key(owner_factory, a_state_row.id)
    final = a_steps[STEP_SEE_AGENT_REPLY_IN_INBOX]
    assert final.status == "completed"
    assert final.completed_via == "auto"
    assert final.evidence == {
        "execution_id": str(world["execution"].id),
        "comment_id": str(world["reply_id"]),
        "notification_id": str(a_notif.id),
        "trigger_member_id": str(world["owner_mid"]),
    }
    assert a_state_row.aha_reached_at is not None
    # B unaffected by the trigger member's aha.
    b_state_row = await _state_by_member(owner_factory, world["buddy_mid"])
    assert b_state_row.aha_reached_at is None

    # onboarding.progress + onboarding.completed persisted via the unique
    # outbox → projector path (README §6.6/§6.7), member-private channel.
    async with owner_factory() as session:
        events = list(
            (
                await session.execute(
                    select(RealtimeEvent).where(
                        RealtimeEvent.channel == f"member:{world['owner_mid']}:onboarding"
                    )
                )
            )
            .scalars()
            .all()
        )
    names = [e.event for e in events]
    assert names.count("onboarding.completed") == 1
    assert "onboarding.progress" in names
    completed = next(e for e in events if e.event == "onboarding.completed")
    assert completed.payload["state_id"] == str(a_state_row.id)
    assert completed.seq >= 1

    # Aha is set exactly once: reading again (redelivery-safe) never moves it.
    first_aha = a_state_row.aha_reached_at
    await _mark_read(api_client, world["owner"], ws_id, str(a_notif.id))
    await _drain(relay)
    a_state_row = await _state_by_member(owner_factory, world["owner_mid"])
    assert a_state_row.aha_reached_at == first_aha
    async with owner_factory() as session:
        replayed = (
            await session.execute(
                select(RealtimeEvent).where(
                    RealtimeEvent.channel == f"member:{world['owner_mid']}:onboarding",
                    RealtimeEvent.event == "onboarding.completed",
                )
            )
        ).scalars().all()
    assert len(replayed) == 1  # one-shot event
