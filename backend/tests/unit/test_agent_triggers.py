"""Agent trigger orchestration unit tests (agent.md §3.3, README §6.5/§6.9/§6.11).

Drives ``assign_orchestration_handler`` — the unified entry the outbox
relay calls for ``issue.assigned`` — against real PostgreSQL: enqueue
contract (idempotency key §6.5, reproducible snapshot §6.11, strict
capability typing §6.4, execution.queued §3.6), redelivery idempotence,
the guardrail skip paths (agent.trigger_skipped §3.6), and the
reassignment supersede cancel (§6.9).
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select

from mesh.agent.guardrails import ENQUEUE_EVENT_TYPE, TriggerGuardrailConfig
from mesh.agent.service import AgentService
from mesh.agent.triggers import assign_orchestration_handler
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService

TRIGGER_EVENT_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


async def _make_workspace(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Trigger WS", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _make_owner(session_factory, workspace) -> Member:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Owner"
        )
        session.add(user)
        await session.flush()
        member = Member(workspace_id=workspace.id, member_type="human", user_id=user.id, role="owner")
        session.add(member)
    return member


async def _make_agent(session_factory, workspace, owner, **overrides) -> dict:
    service = AgentService(session_factory)
    defaults = {"name": "小测", "system_instructions": "测试", "model_config": {"temperature": 0.2}}
    defaults.update(overrides)
    return await service.create_agent(actor=owner, workspace_id=workspace.id, **defaults)


async def _make_issue(session_factory, workspace, owner, title="修复登录态丢失") -> dict:
    issue_service = IssueService(session_factory)
    return await issue_service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title=title)
    )


def _assign_payload(*, issue_id: str, agent, action: str = "enqueue") -> dict:
    return {
        "issue_id": issue_id,
        "agent_member_id": str(agent["member"]["id"]),
        "agent_id": agent["id"],
        "trigger": "assign",
        "action": action,
        "trigger_event_id": str(TRIGGER_EVENT_ID),
    }


async def _run_handler(session_factory, workspace, payload, *, config=None):
    """Simulate the relay: claim the event row, run the handler in a txn."""
    async with session_factory() as session, session.begin():
        event = OutboxEvent(
            workspace_id=workspace.id, event_type="issue.assigned", payload=payload
        )
        session.add(event)
        await session.flush()
        event_id = event.id
    async with session_factory() as session, session.begin():
        row = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.workspace_id == workspace.id,
                OutboxEvent.id == event_id,
            )
        )
        await assign_orchestration_handler(session, row, guardrail_config=config)


async def _enqueue_events(session_factory, workspace) -> list:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == workspace.id,
                        OutboxEvent.event_type == ENQUEUE_EVENT_TYPE,
                    )
                )
            )
            .scalars()
            .all()
        )
    return rows


async def _realtime_events(session_factory, name: str) -> list:
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent))).scalars().all()
    return [
        e for e in rows if e.event_type == "realtime.publish" and e.payload["event"] == name
    ]


# --- enqueue contract (§3.3 / §6.5 / §6.11) --------------------------------------


@pytest.mark.unit
async def test_enqueue_writes_execution_event_with_snapshot_and_key(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)

    await _run_handler(session_factory, workspace, _assign_payload(issue_id=issue["id"], agent=agent))

    events = await _enqueue_events(session_factory, workspace)
    assert len(events) == 1
    event = events[0]
    payload = event.payload
    assert payload["intent"] == "enqueue"
    assert payload["agent_id"] == agent["id"]
    assert payload["issue_id"] == issue["id"]
    assert payload["trigger"] == "assign"

    # §6.5: sha256(agent_id | issue_id | trigger_event_id).
    expected_key = hashlib.sha256(
        f"{agent['id']}|{issue['id']}|{TRIGGER_EVENT_ID}".encode()
    ).hexdigest()
    assert payload["idempotency_key"] == expected_key
    assert event.idempotency_key == f"ws:{workspace.id}:{expected_key}"

    # §6.11 reproducible snapshot.
    snapshot = payload["config_snapshot"]
    assert snapshot["agent_config_version_id"] == agent["active_config_version_id"]
    assert snapshot["trigger_event_id"] == str(TRIGGER_EVENT_ID)
    assert snapshot["skill_versions"] == {}
    assert snapshot["repo"] is None
    # §6.4 / §6.11 strict typing (empty until skill.md, but strict arrays).
    # required_capabilities stays EMPTY (claim matching) — the §3.3 broker
    # grants are authorization, not scheduling, and must never pollute it.
    assert payload["required_capabilities"] == []
    assert payload["label_requirements"] == []
    # §3.3 / §2.2: every agent execution freezes the default broker grants
    # (issue read/comment/status + project read); squad grants are absent
    # for a non-squad assign trigger.
    grants = {g["capability"]: g["permission"] for g in snapshot["capability_grants"]}
    assert grants == {
        "issue.read": "read_only",
        "issue.comment": "write",
        "issue.status": "write",
        "project.read": "read_only",
    }
    assert "squad.subtasks" not in grants
    # §2.1 digest covers the final (grants-included) content.
    from mesh.agent.snapshot import compute_snapshot_digest

    assert snapshot["digest"] == compute_snapshot_digest(snapshot)

    # §6.15 untrusted issue context is structurally isolated.
    context = payload["task_spec"]["untrusted_context"]
    assert "UNTRUSTED_DATA_BEGIN" in context["issue"]["title"]
    assert context["notice"]

    # §3.6 step 6: execution.queued on the issue's run channel.
    queued = await _realtime_events(session_factory, "execution.queued")
    assert len(queued) == 1
    assert queued[0].payload["channel"] == f"issue:{issue['id']}:runs"
    assert queued[0].payload["data"]["agent_id"] == agent["id"]


@pytest.mark.unit
async def test_squad_orchestrator_trigger_freezes_squad_grants(session_factory):
    """§2.2 S-05 / §3.3: a squad-dispatched assign carrying squad_role=
    orchestrator freezes the squad broker grants (decompose + roster read)
    INTO the snapshot — and still never touches required_capabilities."""
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)

    payload = _assign_payload(issue_id=issue["id"], agent=agent)
    payload["squad_task_id"] = str(uuid.uuid4())
    payload["squad_role"] = "orchestrator"
    await _run_handler(session_factory, workspace, payload)

    events = await _enqueue_events(session_factory, workspace)
    assert len(events) == 1
    event_payload = events[0].payload
    snapshot = event_payload["config_snapshot"]
    grants = {g["capability"]: g["permission"] for g in snapshot["capability_grants"]}
    assert grants["squad.subtasks"] == "write"
    assert grants["squad.members"] == "read_only"
    assert grants["issue.comment"] == "write"  # defaults still present
    assert event_payload["required_capabilities"] == []
    # The frozen task_spec carries the correlation the claim-time scope
    # widening reads back (squad_role=orchestrator).
    assert event_payload["task_spec"]["squad_role"] == "orchestrator"
    from mesh.agent.snapshot import compute_snapshot_digest

    assert snapshot["digest"] == compute_snapshot_digest(snapshot)


@pytest.mark.unit
async def test_squad_executor_trigger_gets_no_squad_grants(session_factory):
    """Executor/aggregator wakes freeze ONLY the default broker grants."""
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)

    payload = _assign_payload(issue_id=issue["id"], agent=agent)
    payload["squad_task_id"] = str(uuid.uuid4())
    payload["squad_role"] = "executor"
    await _run_handler(session_factory, workspace, payload)

    events = await _enqueue_events(session_factory, workspace)
    grants = {
        g["capability"] for g in events[0].payload["config_snapshot"]["capability_grants"]
    }
    assert "squad.subtasks" not in grants
    assert "squad.members" not in grants
    assert "issue.comment" in grants


@pytest.mark.unit
async def test_snapshot_freezes_budget_and_network_policy(session_factory):
    """§2.1 P0: agent model_config budget/network_policy freeze into the
    AttemptSpec so the daemon's S-07 gate + egress allowlist are reproducible."""
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(
        session_factory, workspace, owner,
        model_config={
            "provider": "claude-code",
            "budget": {"max_cost_usd": "0.50", "max_turns": 2},
            "network_policy": {"allowed_hosts": ["api.example.com"]},
        },
    )
    issue = await _make_issue(session_factory, workspace, owner)
    await _run_handler(session_factory, workspace, _assign_payload(issue_id=issue["id"], agent=agent))

    events = await _enqueue_events(session_factory, workspace)
    snapshot = events[0].payload["config_snapshot"]
    assert snapshot["provider"] == "claude-code"
    assert snapshot["budget"]["max_cost_usd"] == "0.50"
    assert snapshot["budget"]["max_turns"] == 2
    assert snapshot["network_policy"]["allowed_hosts"] == ["api.example.com"]


@pytest.mark.unit
async def test_redelivery_is_idempotent(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)
    payload = _assign_payload(issue_id=issue["id"], agent=agent)

    await _run_handler(session_factory, workspace, payload)
    await _run_handler(session_factory, workspace, payload)  # relay redelivery

    # Exactly one execution.enqueue row — the idempotency key dedupes (§6.5).
    assert len(await _enqueue_events(session_factory, workspace)) == 1


# --- guardrail skips (§3.6 / §6.9) --------------------------------------------------


@pytest.mark.unit
async def test_paused_agent_skips_with_event(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)
    service = AgentService(session_factory)
    await service.transition_lifecycle(
        actor=owner, workspace_id=workspace.id, agent_id=uuid.UUID(agent["id"]), action="pause"
    )

    await _run_handler(
        session_factory, workspace, _assign_payload(issue_id=issue["id"], agent=agent)
    )

    assert await _enqueue_events(session_factory, workspace) == []
    skipped = await _realtime_events(session_factory, "agent.trigger_skipped")
    assert len(skipped) == 1
    data = skipped[0].payload["data"]
    assert data["reason"] == "lifecycle_not_active"
    assert data["agent_id"] == agent["id"]
    assert data["issue_id"] == issue["id"]
    assert skipped[0].payload["channel"] == f"workspace:{workspace.id}:agents"


@pytest.mark.unit
async def test_trigger_on_assign_false_skips(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner, trigger_on_assign=False)
    issue = await _make_issue(session_factory, workspace, owner)

    await _run_handler(
        session_factory, workspace, _assign_payload(issue_id=issue["id"], agent=agent)
    )

    assert await _enqueue_events(session_factory, workspace) == []
    skipped = await _realtime_events(session_factory, "agent.trigger_skipped")
    assert skipped[0].payload["data"]["reason"] == "trigger_on_assign_disabled"


@pytest.mark.unit
async def test_disabled_roster_member_skips(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)
    async with session_factory() as session, session.begin():
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace.id,
                Member.id == uuid.UUID(agent["member"]["id"]),
            )
        )
        member.status = "disabled"

    await _run_handler(
        session_factory, workspace, _assign_payload(issue_id=issue["id"], agent=agent)
    )

    assert await _enqueue_events(session_factory, workspace) == []
    skipped = await _realtime_events(session_factory, "agent.trigger_skipped")
    assert skipped[0].payload["data"]["reason"] == "member_not_active"


@pytest.mark.unit
async def test_rate_limit_guardrail_skips(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)

    # One prior enqueue for this agent inside the window…
    async with session_factory() as session, session.begin():
        session.add(
            OutboxEvent(
                workspace_id=workspace.id,
                event_type=ENQUEUE_EVENT_TYPE,
                payload={"agent_id": agent["id"]},
            )
        )
    # …with a limit of 1 per window → the next trigger is skipped.
    await _run_handler(
        session_factory,
        workspace,
        _assign_payload(issue_id=issue["id"], agent=agent),
        config=TriggerGuardrailConfig(rate_limit=1),
    )
    skipped = await _realtime_events(session_factory, "agent.trigger_skipped")
    assert skipped[0].payload["data"]["reason"] == "rate_limited"
    # Only the pre-seeded enqueue row exists — no new one was written.
    assert len(await _enqueue_events(session_factory, workspace)) == 1


@pytest.mark.unit
async def test_chain_depth_guardrail_skips(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)
    payload = _assign_payload(issue_id=issue["id"], agent=agent)
    payload["chain_depth"] = 5  # default max_chain_depth

    await _run_handler(session_factory, workspace, payload)

    skipped = await _realtime_events(session_factory, "agent.trigger_skipped")
    assert skipped[0].payload["data"]["reason"] == "chain_depth_exceeded"


@pytest.mark.unit
async def test_missing_agent_skips(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)
    payload = _assign_payload(issue_id=issue["id"], agent=agent)
    payload["agent_id"] = str(uuid.uuid4())  # agent vanished

    await _run_handler(session_factory, workspace, payload)

    skipped = await _realtime_events(session_factory, "agent.trigger_skipped")
    assert skipped[0].payload["data"]["reason"] == "agent_not_found"
    assert await _enqueue_events(session_factory, workspace) == []


# --- reassignment supersede (§6.9) ---------------------------------------------------


@pytest.mark.unit
async def test_supersede_writes_cancel_intent_with_distinct_key(session_factory):
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent = await _make_agent(session_factory, workspace, owner)
    issue = await _make_issue(session_factory, workspace, owner)

    await _run_handler(
        session_factory,
        workspace,
        _assign_payload(issue_id=issue["id"], agent=agent, action="supersede"),
    )

    events = await _enqueue_events(session_factory, workspace)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["intent"] == "cancel_in_flight"
    assert payload["failure_reason"] == "superseded"
    # Distinct from the pure §6.5 enqueue key (purpose tag on the row key).
    enqueue_key = hashlib.sha256(
        f"{agent['id']}|{issue['id']}|{TRIGGER_EVENT_ID}".encode()
    ).hexdigest()
    assert events[0].idempotency_key != f"ws:{workspace.id}:{enqueue_key}"


@pytest.mark.unit
async def test_supersede_and_enqueue_coexist_for_reassignment(session_factory):
    """§6.9: reassignment cancels the old agent's runs AND enqueues the new."""
    workspace = await _make_workspace(session_factory)
    owner = await _make_owner(session_factory, workspace)
    agent_a = await _make_agent(session_factory, workspace, owner, name="A")
    agent_b = await _make_agent(session_factory, workspace, owner, name="B")
    issue = await _make_issue(session_factory, workspace, owner)

    # The issue service emits both events for a reassignment; simulate both.
    await _run_handler(
        session_factory,
        workspace,
        _assign_payload(issue_id=issue["id"], agent=agent_a, action="supersede"),
    )
    await _run_handler(
        session_factory,
        workspace,
        _assign_payload(issue_id=issue["id"], agent=agent_b, action="enqueue"),
    )

    events = await _enqueue_events(session_factory, workspace)
    intents = sorted(e.payload["intent"] for e in events)
    assert intents == ["cancel_in_flight", "enqueue"]
    cancel = next(e for e in events if e.payload["intent"] == "cancel_in_flight")
    enqueue = next(e for e in events if e.payload["intent"] == "enqueue")
    assert cancel.payload["agent_id"] == agent_a["id"]
    assert enqueue.payload["agent_id"] == agent_b["id"]
