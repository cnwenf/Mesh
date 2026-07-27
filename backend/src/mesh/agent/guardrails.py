"""Agent trigger guardrails (agent.md §3.3 / §6.9, auth.md Z4/Z5).

Every trigger path (assign now; mention / autopilot later) passes the same
guardrail gate before an execution is enqueued. A denied trigger emits
``agent.trigger_skipped`` (agent.md §3.6 — guardrail skips MUST emit) so the
UI can explain why an agent did not start working.

Guards (skeleton per MES-60 scope — lifecycle + membership + per-agent rate
limit + chain depth):

* ``lifecycle`` — only ``active`` agents are triggered; ``paused`` /
  ``disabled`` / ``archived`` skip (agent.md §5.1 暂停/停用拦截);
* ``membership`` — the agent's roster row must be ``active``;
* ``trigger_on_assign`` — the agent-level opt-out for the assign path
  (agent.md §5.1: ``trigger_on_assign=false`` 时不触发);
* ``rate limit`` — at most ``rate_limit`` enqueues per agent inside a
  sliding ``rate_window_seconds`` (counted from outbox enqueue events;
  Redis-based fleet-wide limiting can replace the count source later
  without changing the decision surface);
* ``chain depth`` — agent-initiated triggers carry a ``chain_depth``; once
  it reaches ``max_chain_depth`` the trigger is skipped, which caps
  agent-to-agent amplification loops.

Agent API tokens default-deny ``agent:trigger`` (auth.md Z4/Z5) — that gate
lives in the auth layer; this module consumes the same semantics for
internal paths by refusing skips loudly instead of silently dropping them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.outbox.service import emit_realtime

# Enqueue event type the runtime increment consumes (README §6.6 outbox).
ENQUEUE_EVENT_TYPE = "execution.enqueue"

# Skip reasons (stable keys; rendered by the frontend via error.<reason>).
SKIP_AGENT_NOT_FOUND = "agent_not_found"
SKIP_LIFECYCLE = "lifecycle_not_active"
SKIP_MEMBER_NOT_ACTIVE = "member_not_active"
SKIP_TRIGGER_ON_ASSIGN_DISABLED = "trigger_on_assign_disabled"
SKIP_RATE_LIMITED = "rate_limited"
SKIP_CHAIN_DEPTH = "chain_depth_exceeded"


@dataclass(frozen=True)
class TriggerGuardrailConfig:
    """Guardrail thresholds (platform defaults; workspace-level policy later)."""

    rate_limit: int = 30
    rate_window_seconds: int = 60
    max_chain_depth: int = 5


async def evaluate_assign_trigger(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent: Agent | None,
    member: Member | None,
    trigger: str,
    chain_depth: int = 0,
    config: TriggerGuardrailConfig | None = None,
    now: datetime | None = None,
) -> str | None:
    """Return the skip reason for an assign trigger, or ``None`` when allowed.

    ``trigger`` is the trigger kind (``assign`` today; ``mention`` /
    ``autopilot`` reuse this gate later). ``agent`` / ``member`` may be None
    when the roster row points at an agent that no longer exists.
    """
    cfg = config or TriggerGuardrailConfig()
    if agent is None or agent.deleted_at is not None:
        return SKIP_AGENT_NOT_FOUND
    if agent.lifecycle_status != "active":
        return SKIP_LIFECYCLE
    if member is None or member.status != "active":
        return SKIP_MEMBER_NOT_ACTIVE
    if trigger == "assign" and not agent.trigger_on_assign:
        return SKIP_TRIGGER_ON_ASSIGN_DISABLED
    if chain_depth >= cfg.max_chain_depth:
        return SKIP_CHAIN_DEPTH
    window_anchor = now or datetime.now(UTC)
    window_start = window_anchor - timedelta(seconds=cfg.rate_window_seconds)
    recent = await session.scalar(
        select(func.count(OutboxEvent.id)).where(
            OutboxEvent.workspace_id == workspace_id,
            OutboxEvent.event_type == ENQUEUE_EVENT_TYPE,
            OutboxEvent.created_at >= window_start,
            OutboxEvent.payload["agent_id"].astext == str(agent.id),
        )
    )
    if (recent or 0) >= cfg.rate_limit:
        return SKIP_RATE_LIMITED
    return None


async def emit_trigger_skipped(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    issue_id: uuid.UUID | None,
    trigger: str,
    reason: str,
    trigger_event_id: uuid.UUID | None = None,
    channel: str,
    idempotency_key: str | None = None,
) -> None:
    """Broadcast ``agent.trigger_skipped`` (agent.md §3.6 guardrail event).

    ``idempotency_key`` keeps relay redelivery from emitting the skip event
    twice for the same logical trigger (at-least-once outbox, README §6.5).
    """
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=channel,
        event="agent.trigger_skipped",
        data={
            "agent_id": str(agent_id) if agent_id is not None else None,
            "issue_id": str(issue_id) if issue_id is not None else None,
            "trigger": trigger,
            "reason": reason,
            "trigger_event_id": str(trigger_event_id) if trigger_event_id else None,
        },
        idempotency_key=idempotency_key,
    )


__all__ = [
    "ENQUEUE_EVENT_TYPE",
    "SKIP_AGENT_NOT_FOUND",
    "SKIP_CHAIN_DEPTH",
    "SKIP_LIFECYCLE",
    "SKIP_MEMBER_NOT_ACTIVE",
    "SKIP_RATE_LIMITED",
    "SKIP_TRIGGER_ON_ASSIGN_DISABLED",
    "TriggerGuardrailConfig",
    "evaluate_assign_trigger",
    "emit_trigger_skipped",
]
