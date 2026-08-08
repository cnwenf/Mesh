"""Config-snapshot parity regression across ALL trigger paths (F-BUDGET-SNAPSHOT).

runtime-executor.md §3.7 S-09: assign, mention, autopilot, integration (VCS
direct + IM queue) and squad must enqueue the SAME complete AttemptSpec
snapshot — provider / model / effort / system instructions / budget /
network policy frozen from the agent's ``model_config`` by the single shared
builder (:func:`mesh.agent.snapshot.snapshot_from_agent`).

Before F-BUDGET-SNAPSHOT four of these paths hand-assembled the builder
arguments and drifted: mention dropped budget + network_policy, autopilot and
both integration paths dropped provider / model / effort / budget /
network_policy. Every daemon run from those paths then fail-closed on the
real-provider gate (runtime-executor.md §3.5 S-07). Each test below seeds an
agent with identifiable budget / network overrides, drives ONE trigger path
against real PostgreSQL, and asserts the enqueued ``config_snapshot`` carries
the full field set. Squad dispatch funnels through the assign handler
(``issue.assigned`` → ``assign_orchestration_handler``) and chat is
platform-driven (runtime claim excludes ``trigger='chat'`` — chat-session.md
§4.4 H1), so the five paths below are the complete enqueue surface.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.agent.service import AgentService
from mesh.agent.snapshot import (
    DEFAULT_BUDGET,
    DEFAULT_NETWORK_POLICY,
    compute_snapshot_digest,
    snapshot_from_agent,
)
from mesh.agent.triggers import assign_orchestration_handler
from mesh.autopilot.executor import dispatch_run
from mesh.comment_inbox.mentions import EXECUTION_ENQUEUE_EVENT
from mesh.comment_inbox.service import CommentService
from mesh.db.models.agent import Agent
from mesh.db.models.integration import IntegrationBinding, IntegrationEvent
from mesh.db.models.issue import IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.integrations.connectors import NormalizedEvent
from mesh.integrations.inbound import _enqueue_vcs_execution
from mesh.integrations.message_queue import build_execution_enqueue_payload
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from tests.unit.autopilot_support import make_rule, make_run
from tests.unit.integrations_support import make_binding
from tests.unit.integrations_support import seed_world as seed_integration_world
from tests.unit.runtime_support import seed_world as seed_runtime_world

pytestmark = pytest.mark.unit

TRIGGER_EVENT_ID = uuid.UUID("87654321-4321-8765-4321-210987654321")
PROVIDER = "claude-code"
MODEL = "claude-sonnet-4-5"
EFFORT = "high"
SYSTEM_INSTRUCTIONS = "parity-test-instructions"
MODEL_CONFIG = {
    "provider": PROVIDER,
    "model": MODEL,
    "reasoning_effort": EFFORT,
    "budget": {"max_cost_usd": "0.50", "max_turns": 2},
    "network_policy": {"allowed_hosts": ["api.example.com"]},
}


def assert_snapshot_complete(snapshot: dict) -> None:
    """The F-BUDGET-SNAPSHOT parity gate, applied identically to every path.

    A missing provider / model / budget is exactly what made the daemon
    fail-closed on every real-provider execution, so each field is asserted
    by value (agent override frozen) plus the §2.1 defaults merged in.
    """
    assert snapshot, "config_snapshot is empty — the path froze nothing"
    assert snapshot["provider"] == PROVIDER
    assert snapshot["model"] == MODEL
    assert snapshot["effort"] == EFFORT
    assert snapshot["system_instructions"] == SYSTEM_INSTRUCTIONS
    budget = snapshot["budget"]
    assert budget["max_cost_usd"] == "0.50"
    assert budget["max_turns"] == 2
    # §2.1 defaults still merged around the overrides.
    assert budget["max_log_bytes"] == DEFAULT_BUDGET["max_log_bytes"]
    network_policy = snapshot["network_policy"]
    assert network_policy["allowed_hosts"] == ["api.example.com"]
    assert network_policy["allowed_schemes"] == DEFAULT_NETWORK_POLICY["allowed_schemes"]
    assert snapshot["digest"] == compute_snapshot_digest(snapshot)
    assert snapshot["trigger_event_id"]


# ---------------------------------------------------------------------------
# The shared builder itself (no DB needed — unsaved Agent instances suffice)
# ---------------------------------------------------------------------------


def _unsaved_agent(**overrides) -> Agent:
    defaults = {
        "workspace_id": uuid.uuid4(),
        "name": "Parity Agent",
        "owner_user_id": uuid.uuid4(),
        "lifecycle_status": "active",
        "system_instructions": SYSTEM_INSTRUCTIONS,
        "model_config": MODEL_CONFIG,
    }
    defaults.update(overrides)
    return Agent(**defaults)


def test_snapshot_from_agent_freezes_full_attemptspec() -> None:
    parts = snapshot_from_agent(_unsaved_agent(), trigger_event_id=TRIGGER_EVENT_ID)
    snapshot = parts["config_snapshot"]
    assert_snapshot_complete(snapshot)
    assert snapshot["trigger_event_id"] == str(TRIGGER_EVENT_ID)
    assert parts["required_capabilities"] == []


def test_snapshot_from_agent_degrades_missing_overrides_to_defaults() -> None:
    agent = _unsaved_agent(model_config={"provider": PROVIDER, "model": MODEL})
    snapshot = snapshot_from_agent(agent, trigger_event_id=TRIGGER_EVENT_ID)["config_snapshot"]
    assert snapshot["provider"] == PROVIDER
    assert snapshot["model"] == MODEL
    assert snapshot["budget"] == DEFAULT_BUDGET
    assert snapshot["network_policy"] == DEFAULT_NETWORK_POLICY


def test_snapshot_from_agent_tolerates_corrupt_model_config() -> None:
    for corrupt in ("garbage", ["broken"], 42):
        agent = _unsaved_agent(model_config=corrupt)
        snapshot = snapshot_from_agent(
            agent, trigger_event_id=TRIGGER_EVENT_ID
        )["config_snapshot"]
        assert snapshot["provider"] is None
        assert snapshot["budget"] == DEFAULT_BUDGET
        assert snapshot["network_policy"] == DEFAULT_NETWORK_POLICY


# ---------------------------------------------------------------------------
# Seeding shared by the DB-backed path tests
# ---------------------------------------------------------------------------


async def _workspace(factory) -> Workspace:
    async with factory() as session, session.begin():
        workspace = Workspace(name="Parity WS", slug=f"parity-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _owner(factory, workspace) -> Member:
    async with factory() as session, session.begin():
        user = User(
            email=f"parity-{uuid.uuid4().hex[:12]}@corp.com",
            password_hash="x",
            display_name="Parity Owner",
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role="owner"
        )
        session.add(member)
    return member


async def _agent_payload(factory, workspace, owner) -> dict:
    """An agent whose model_config carries the parity overrides."""
    service = AgentService(factory)
    return await service.create_agent(
        actor=owner,
        workspace_id=workspace.id,
        name="Parity Agent",
        system_instructions=SYSTEM_INSTRUCTIONS,
        model_config=MODEL_CONFIG,
    )


async def _issue(factory, workspace, owner) -> dict:
    service = IssueService(factory)
    return await service.create_issue(
        actor=owner, workspace_id=workspace.id, body=CreateIssueRequest(title="parity issue")
    )


async def _enqueue_rows(factory) -> list[OutboxEvent]:
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == EXECUTION_ENQUEUE_EVENT
                    )
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def _set_parity_model_config(factory, agent_id: uuid.UUID) -> None:
    """Support-world agents seed without model_config; give them the parity one."""
    async with factory() as session, session.begin():
        agent = await session.scalar(select(Agent).where(Agent.id == agent_id))
        assert agent is not None
        agent.model_config = MODEL_CONFIG
        agent.system_instructions = SYSTEM_INSTRUCTIONS


# ---------------------------------------------------------------------------
# Path 1 — assign (issue.assigned → assign_orchestration_handler)
# ---------------------------------------------------------------------------


async def test_assign_path_snapshot_parity(session_factory) -> None:
    workspace = await _workspace(session_factory)
    owner = await _owner(session_factory, workspace)
    agent = await _agent_payload(session_factory, workspace, owner)
    issue = await _issue(session_factory, workspace, owner)

    payload = {
        "issue_id": issue["id"],
        "agent_member_id": str(agent["member"]["id"]),
        "agent_id": agent["id"],
        "trigger": "assign",
        "action": "enqueue",
        "trigger_event_id": str(TRIGGER_EVENT_ID),
        "actor_user_id": str(owner.user_id),
    }
    async with session_factory() as session, session.begin():
        event = OutboxEvent(
            workspace_id=workspace.id, event_type="issue.assigned", payload=payload
        )
        session.add(event)
        await session.flush()
        event_id = event.id
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(OutboxEvent).where(OutboxEvent.id == event_id))
        await assign_orchestration_handler(session, row)

    enqueues = await _enqueue_rows(session_factory)
    assert len(enqueues) == 1
    assert enqueues[0].payload["trigger"] == "assign"
    assert_snapshot_complete(enqueues[0].payload["config_snapshot"])


# ---------------------------------------------------------------------------
# Path 2 — mention (CommentService → enqueue_agent_executions)
# ---------------------------------------------------------------------------


async def test_mention_path_snapshot_parity(session_factory) -> None:
    workspace = await _workspace(session_factory)
    owner = await _owner(session_factory, workspace)
    agent = await _agent_payload(session_factory, workspace, owner)
    issue = await _issue(session_factory, workspace, owner)

    service = CommentService(session_factory, max_agent_chain_depth=3)
    await service.create_comment(
        workspace_id=workspace.id,
        issue_id=issue["id"],
        author_member=owner,
        body_markdown=f"[@agent](mention://member/{agent['member']['id']}) 请处理",
    )

    enqueues = await _enqueue_rows(session_factory)
    assert len(enqueues) == 1
    assert enqueues[0].payload["trigger"] == "mention"
    assert_snapshot_complete(enqueues[0].payload["config_snapshot"])


# ---------------------------------------------------------------------------
# Path 3 — autopilot (executor dispatch → _enqueue_agent_execution)
# ---------------------------------------------------------------------------


def _autopilot_services(session_factory) -> dict:
    return {
        "session_factory": session_factory,
        "comment_service": CommentService(session_factory, signing_secret="x" * 40),
        "issue_service": IssueService(session_factory),
    }


async def test_autopilot_path_snapshot_parity(session_factory) -> None:
    world = await seed_runtime_world(session_factory)
    await _set_parity_model_config(session_factory, world["agent_id"])
    rule = await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
        action_config=[
            {
                "type": "run_agent_prompt",
                "executor_agent_id": str(world["agent_id"]),
                "prompt": "triage",
            }
        ],
    )
    run = await make_run(
        session_factory,
        rule,
        status="pending",
        trigger_snapshot={"issue": {"id": str(uuid.uuid4()), "title": "parity"}},
    )
    await dispatch_run(
        session_factory,
        run_id=run.id,
        workspace_id=world["ws_id"],
        services=_autopilot_services(session_factory),
        approval_ttl=timedelta(hours=24),
    )

    enqueues = await _enqueue_rows(session_factory)
    assert len(enqueues) == 1
    assert enqueues[0].payload["trigger"] == "autopilot"
    assert_snapshot_complete(enqueues[0].payload["config_snapshot"])


# ---------------------------------------------------------------------------
# Path 4 — integration VCS direct (inbound._enqueue_vcs_execution)
# ---------------------------------------------------------------------------


async def test_inbound_vcs_path_snapshot_parity(session_factory) -> None:
    world = await seed_integration_world(session_factory)
    await _set_parity_model_config(session_factory, world["agent"])
    binding = await make_binding(
        session_factory, world=world, provider="github", external_ref="mesh/mesh"
    )
    event = NormalizedEvent(
        external_event_id=f"evt-{uuid.uuid4().hex[:12]}",
        event_type="pull_request",
        external_ref="mesh/mesh",
        actor_key="dev",
        tenant_key="1234567",
        text="open PR",
        extra={"action": "opened"},
    )
    async with session_factory() as session, session.begin():
        event_row = IntegrationEvent(
            workspace_id=world["ws"],
            integration_id=world["integ_github"],
            external_event_id=event.external_event_id,
            event_type=event.event_type,
            payload={"action": "opened"},
            signature_status="valid",
        )
        session.add(event_row)
        await session.flush()
        await _enqueue_vcs_execution(
            session,
            workspace_id=world["ws"],
            binding=binding,
            event_row=event_row,
            event=event,
            provider="github",
        )

    enqueues = await _enqueue_rows(session_factory)
    assert len(enqueues) == 1
    assert enqueues[0].payload["trigger"] == "integration"
    assert_snapshot_complete(enqueues[0].payload["config_snapshot"])


# ---------------------------------------------------------------------------
# Path 5 — integration IM queue (message_queue.build_execution_enqueue_payload)
# ---------------------------------------------------------------------------


def test_message_queue_path_snapshot_parity() -> None:
    """The payload builder is pure — it only reads attributes, so unsaved
    rows exercise the exact call site the dispatcher uses."""
    workspace_id = uuid.uuid4()
    agent = _unsaved_agent(workspace_id=workspace_id)
    binding = IntegrationBinding(
        id=uuid.uuid4(),
        integration_id=uuid.uuid4(),
        external_ref="C123",
    )
    event_row = IntegrationEvent(id=TRIGGER_EVENT_ID, payload={"text": "hi"})

    payload = build_execution_enqueue_payload(
        agent=agent,
        binding=binding,
        event_row=event_row,
        provider="slack",
        external_event_id="evt-1",
        event_type="message",
        idempotency_key="parity-key",
        queue_item_id=uuid.uuid4(),
    )

    assert payload["trigger"] == "integration"
    assert_snapshot_complete(payload["config_snapshot"])
    assert payload["config_snapshot"]["trigger_event_id"] == str(TRIGGER_EVENT_ID)


# ---------------------------------------------------------------------------
# Direct coverage of the seed helper invariant used by the path tests
# ---------------------------------------------------------------------------


async def test_issue_seed_uses_todo_status(session_factory) -> None:
    """Guard for the shared seed helper: issues land in the workspace todo
    status (seed_default_statuses ran), so the mention/assign path tests
    exercise the production status wiring rather than a bare row."""
    workspace = await _workspace(session_factory)
    owner = await _owner(session_factory, workspace)
    issue = await _issue(session_factory, workspace, owner)
    async with session_factory() as session:
        status = await session.scalar(
            select(IssueStatus).where(IssueStatus.id == uuid.UUID(issue["status_id"]))
        )
    assert status is not None and status.category == "todo"
