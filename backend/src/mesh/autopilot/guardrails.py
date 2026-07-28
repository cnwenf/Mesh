"""Trigger-time guardrails — default-ON first-class safety (autopilot.md §2.6 / §5.3).

Every trigger (schedule / domain event / inbound webhook / manual test)
passes this gate BEFORE a run is created. Checks, in order:

1. kill switch — rule-level ``guardrails.kill_switch_paused`` (workspace
   kill switch flips this per rule, §3.1);
2. dedup window — same logical event (``dedup_key``) inside
   ``dedup_window_seconds`` executes only once;
3. rate limit — ``rate_limit_max`` triggers per
   ``rate_limit_window_seconds``; overflow handled per
   ``rate_limit_overflow`` (drop / queue / alert_only) with a critical
   circuit-breaker notification + ``autopilot.rate_limited`` realtime;
4. concurrency — at most ``concurrency_limit`` in-flight runs per rule;
5. agent loop — same ``(executor_agent, trigger target)`` pair inside
   ``agent_loop_window_seconds`` is deduplicated (anti-ping-pong);
6. cascade depth — ``cascade_depth > cascade_max_depth`` refuses to create
   the downstream run (422 ``cascade_depth_exceeded`` on synchronous
   paths);
7. budgets — ``daily_run_budget`` runs and ``daily_token_budget`` tokens
   per UTC day; breach = circuit break + alert.

Denials are structured (:class:`GateDecision`) so each call site maps them
to the right surface: synchronous APIs raise the named 4xx, event paths
drop + audit, webhook paths return the dedup/limit outcome in the bare
JSON contract.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.autopilot import Autopilot, AutopilotRun
from mesh.outbox.service import emit_event, emit_realtime

# autopilot.md §2.6 — guardrails take effect with these defaults AT RULE
# CREATION (not lazily): a rule created without an explicit guardrails block
# is still fully protected.
DEFAULT_GUARDRAILS: dict[str, Any] = {
    "rate_limit_overflow": "drop",
    "dedup_window_seconds": 300,
    "dedup_key_template": "{{trigger.event_id}}",
    "daily_run_budget": 200,
    "daily_token_budget": 2000000,
    "approval_required_actions": ["http_request", "create_issue"],
    "kill_switch_paused": False,
    "agent_loop_detection": True,
    "cascade_max_depth": 3,
    "agent_loop_window_seconds": 60,
}

RATE_LIMIT_OVERFLOW_VALUES = ("drop", "queue", "alert_only")

# In-flight statuses for the concurrency check (waiting_approval holds a
# slot: the run IS coming back).
_IN_FLIGHT_STATUSES = ("pending", "running", "retrying", "waiting_approval")

# Terminal statuses counted by the rate-limit window (a dropped trigger
# never becomes a run, so the window counts created runs).
_TERMINAL_OR_ACTIVE = _IN_FLIGHT_STATUSES + ("succeeded", "failed", "cancelled")

# autopilot_runs.* realtime channels (autopilot.md §3.5).
autopilots_channel = lambda workspace_id: f"workspace:{workspace_id}:autopilots"  # noqa: E731
autopilot_channel = lambda autopilot_id: f"autopilot:{autopilot_id}"  # noqa: E731


def merge_guardrails(provided: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge user guardrails over the default-ON baseline.

    Unknown keys are dropped (the schema validates the known ones); missing
    keys inherit the safe default — protection is never opt-in.
    """
    merged = dict(DEFAULT_GUARDRAILS)
    for key, value in (provided or {}).items():
        if key in DEFAULT_GUARDRAILS and value is not None:
            merged[key] = value
    if merged["rate_limit_overflow"] not in RATE_LIMIT_OVERFLOW_VALUES:
        merged["rate_limit_overflow"] = "drop"
    return merged


@dataclass(frozen=True)
class GateDecision:
    """Outcome of the trigger-time gate.

    ``allowed=False`` carries the stable ``reason`` code; ``http_status``
    tells synchronous call sites how to surface it (429 rate_limited /
    422 cascade_depth_exceeded / 409 conflict-style dedup); ``alert`` marks
    denials that must notify the owner (rate-limit circuit break, budget).
    """

    allowed: bool
    reason: str = ""
    http_status: int = 429
    alert: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


ALLOWED = GateDecision(allowed=True)


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """UTC day [start, end) containing ``now`` — the budget accounting day."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def _recent_run_count(
    session: AsyncSession, *, autopilot_id: uuid.UUID, since: datetime
) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(AutopilotRun)
                .where(
                    AutopilotRun.autopilot_id == autopilot_id,
                    AutopilotRun.created_at >= since,
                    AutopilotRun.is_test.is_(False),
                )
            )
        ).scalar_one()
    )


async def _in_flight_count(session: AsyncSession, *, autopilot_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(AutopilotRun)
                .where(
                    AutopilotRun.autopilot_id == autopilot_id,
                    AutopilotRun.status.in_(_IN_FLIGHT_STATUSES),
                )
            )
        ).scalar_one()
    )


async def _dedup_hit(
    session: AsyncSession,
    *,
    autopilot_id: uuid.UUID,
    dedup_key: str,
    window: timedelta,
    now: datetime,
) -> bool:
    """True when the same logical event already ran inside the window."""
    if not dedup_key:
        return False
    existing = await session.scalar(
        select(AutopilotRun.id)
        .where(
            AutopilotRun.autopilot_id == autopilot_id,
            AutopilotRun.created_at >= now - window,
            AutopilotRun.trigger_snapshot["dedup_key"].astext == dedup_key,
        )
        .limit(1)
    )
    return existing is not None


async def _loop_hit(
    session: AsyncSession,
    *,
    autopilot_id: uuid.UUID,
    executor_agent_id: uuid.UUID | None,
    target_ref: str,
    window: timedelta,
    now: datetime,
) -> bool:
    """Anti-ping-pong: same (executor agent, trigger target) inside window."""
    if executor_agent_id is None or not target_ref:
        return False
    existing = await session.scalar(
        select(AutopilotRun.id)
        .where(
            AutopilotRun.autopilot_id == autopilot_id,
            AutopilotRun.created_at >= now - window,
            AutopilotRun.trigger_snapshot["loop_target"].astext == target_ref,
        )
        .limit(1)
    )
    return existing is not None


async def _daily_stats(
    session: AsyncSession, *, autopilot_id: uuid.UUID, now: datetime
) -> tuple[int, int]:
    start, end = _day_bounds(now)
    row = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(AutopilotRun.total_tokens), 0),
            )
            .select_from(AutopilotRun)
            .where(
                AutopilotRun.autopilot_id == autopilot_id,
                AutopilotRun.created_at >= start,
                AutopilotRun.created_at < end,
                AutopilotRun.is_test.is_(False),
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def emit_rate_limited(
    session: AsyncSession,
    *,
    rule: Autopilot,
    reason: str,
    dropped: int,
    now: datetime,
) -> None:
    """autopilot.rate_limited realtime (§3.5) + critical circuit-break
    notification (§6.13 熔断告警 critical → inbox, pierce quiet hours)."""
    await emit_realtime(
        session,
        workspace_id=rule.workspace_id,
        channel=autopilots_channel(rule.workspace_id),
        event="autopilot.rate_limited",
        data={
            "autopilot_id": str(rule.id),
            "window": rule.rate_limit_window_seconds,
            "dropped": dropped,
            "reason": reason,
        },
        idempotency_key=f"autopilot:{rule.id}:rate-limited:{reason}:{int(now.timestamp())}",
    )
    await emit_event(
        session,
        workspace_id=rule.workspace_id,
        event_type="notification.fanout",
        payload={
            "type": "autopilot_alert",
            "recipient_ids": [str(rule.created_by)],
            "group_key": f"autopilot:{rule.id}:circuit",
            "autopilot_id": str(rule.id),
            "alert_reason": reason,
        },
        idempotency_key=f"autopilot:{rule.id}:circuit-notify:{reason}:{int(now.timestamp())}",
    )


async def evaluate_trigger(
    session: AsyncSession,
    *,
    rule: Autopilot,
    dedup_key: str = "",
    trigger_target_ref: str = "",
    cascade_depth: int = 0,
    now: datetime | None = None,
    emit_alerts: bool = True,
) -> GateDecision:
    """Run the full guardrail gate for one trigger (see module docstring).

    ``emit_alerts=False`` is for dry-run previews that must not side-effect.
    """
    moment = now if now is not None else datetime.now(UTC)
    guardrails = dict(DEFAULT_GUARDRAILS)
    guardrails.update(rule.guardrails or {})

    # 1. Kill switch (rule-level flag; the workspace endpoint flips it per rule).
    if guardrails.get("kill_switch_paused"):
        return GateDecision(
            allowed=False, reason="kill_switch", http_status=409, detail={"rule": str(rule.id)}
        )

    # 2. Dedup window.
    dedup_window = int(guardrails.get("dedup_window_seconds") or 0)
    if dedup_window > 0 and dedup_key:
        if await _dedup_hit(
            session,
            autopilot_id=rule.id,
            dedup_key=dedup_key,
            window=timedelta(seconds=dedup_window),
            now=moment,
        ):
            return GateDecision(
                allowed=False, reason="deduplicated", http_status=200, detail={"key": dedup_key}
            )

    # 3. Rate limit window + overflow policy.
    window_seconds = max(1, rule.rate_limit_window_seconds)
    if rule.rate_limit_max > 0:
        recent = await _recent_run_count(
            session,
            autopilot_id=rule.id,
            since=moment - timedelta(seconds=window_seconds),
        )
        if recent >= rule.rate_limit_max:
            overflow = guardrails.get("rate_limit_overflow", "drop")
            if emit_alerts:
                await emit_rate_limited(
                    session, rule=rule, reason="rate_limit", dropped=1, now=moment
                )
            if overflow == "alert_only":
                return ALLOWED
            if overflow == "queue":
                # Queued triggers still count against concurrency; the run is
                # created pending and the executor serializes it (§4.4).
                return ALLOWED
            return GateDecision(
                allowed=False,
                reason="rate_limited",
                http_status=429,
                alert=True,
                detail={"window": window_seconds, "max": rule.rate_limit_max},
            )

    # 4. Concurrency.
    in_flight = await _in_flight_count(session, autopilot_id=rule.id)
    if in_flight >= max(1, rule.concurrency_limit):
        if emit_alerts:
            await emit_rate_limited(
                session, rule=rule, reason="concurrency", dropped=1, now=moment
            )
        return GateDecision(
            allowed=False,
            reason="concurrency_limited",
            http_status=429,
            detail={"in_flight": in_flight, "limit": rule.concurrency_limit},
        )

    # 5. Agent loop detection.
    if guardrails.get("agent_loop_detection", True):
        loop_window = int(guardrails.get("agent_loop_window_seconds") or 0)
        if loop_window > 0 and await _loop_hit(
            session,
            autopilot_id=rule.id,
            executor_agent_id=rule.executor_agent_id,
            target_ref=trigger_target_ref,
            window=timedelta(seconds=loop_window),
            now=moment,
        ):
            return GateDecision(
                allowed=False, reason="agent_loop_detected", http_status=409
            )

    # 6. Cascade depth.
    max_depth = int(guardrails.get("cascade_max_depth", DEFAULT_GUARDRAILS["cascade_max_depth"]))
    if cascade_depth > max_depth:
        return GateDecision(
            allowed=False,
            reason="cascade_depth_exceeded",
            http_status=422,
            detail={"depth": cascade_depth, "max": max_depth},
        )

    # 7. Daily budgets (runs + tokens).
    runs_today, tokens_today = await _daily_stats(session, autopilot_id=rule.id, now=moment)
    run_budget = int(guardrails.get("daily_run_budget") or 0)
    token_budget = int(guardrails.get("daily_token_budget") or 0)
    if run_budget > 0 and runs_today >= run_budget:
        if emit_alerts:
            await emit_rate_limited(
                session, rule=rule, reason="daily_run_budget", dropped=1, now=moment
            )
        return GateDecision(
            allowed=False, reason="daily_run_budget", http_status=429, alert=True
        )
    if token_budget > 0 and tokens_today >= token_budget:
        if emit_alerts:
            await emit_rate_limited(
                session, rule=rule, reason="daily_token_budget", dropped=1, now=moment
            )
        return GateDecision(
            allowed=False, reason="daily_token_budget", http_status=429, alert=True
        )

    return ALLOWED


__all__ = [
    "ALLOWED",
    "DEFAULT_GUARDRAILS",
    "RATE_LIMIT_OVERFLOW_VALUES",
    "GateDecision",
    "autopilot_channel",
    "autopilots_channel",
    "emit_rate_limited",
    "evaluate_trigger",
    "merge_guardrails",
]
