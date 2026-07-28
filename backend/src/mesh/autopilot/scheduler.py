"""Scan-based schedule worker (autopilot.md §4.5 / README §2.2).

PostgreSQL is the ONLY scheduling source of truth. Every tick:

1. claim due active schedule rules with ``FOR UPDATE SKIP LOCKED`` over the
   partial index ``idx_autopilot_schedule`` (multi-replica safe — rows are
   never double-claimed);
2. advance ``next_run_at`` with an ATOMIC optimistic update
   (``UPDATE ... WHERE id=? AND next_run_at=? RETURNING``) — whoever
   updates successfully owns the fire (belt-and-braces on top of the row
   lock, exactly the §4.5 contract);
3. fire per ``misfire_policy``: slots missed beyond the grace window are
   ``skip`` (advance only), ``run_once`` (one run for the whole window) or
   ``run_all`` (one run per missed slot, capped — overflow is logged, never
   silently dropped);
4. one-time schedules (``one_time_at``) auto-archive after firing.

The scheduler NEVER executes actions — it creates ``pending`` runs (guardrail
gate included); the executor dispatches them. A crashed scheduler loses
nothing: the next scan finds ``next_run_at`` still in the past and the
misfire policy decides the catch-up (§2.2 故障责任).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.autopilot import cron as cron_mod
from mesh.autopilot import runs as runs_mod
from mesh.autopilot.guardrails import evaluate_trigger
from mesh.db.models.autopilot import Autopilot
from mesh.db.tenant import set_tenant_context

logger = logging.getLogger("mesh.autopilot.scheduler")

# Claim SQL — the §4.5 atomic optimistic takeover. The ``next_run_at``
# equality guard means a concurrent replica that already advanced the row
# returns zero rows and fires nothing.
_CLAIM_SQL = text(
    """
    UPDATE autopilots
       SET next_run_at = :new_next, updated_at = :now
     WHERE id = :rule_id
       AND next_run_at = :expected_next
    RETURNING id
    """
)


def _due_rule_ids_query(batch: int) -> text:
    return text(
        """
        SELECT id, workspace_id, next_run_at
          FROM autopilots
         WHERE status = 'active'
           AND trigger_type = 'schedule'
           AND deleted_at IS NULL
           AND next_run_at IS NOT NULL
           AND next_run_at <= now()
         ORDER BY next_run_at ASC
         LIMIT :batch
         FOR UPDATE SKIP LOCKED
        """
    ).bindparams(batch=batch)


async def _fire_schedule_rule(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    rule_id: uuid.UUID,
    workspace_id: uuid.UUID,
    expected_next: datetime,
    grace_seconds: int,
    run_all_cap: int,
    now: datetime,
) -> int:
    """Advance next_run_at atomically and create runs per misfire policy.

    Returns the number of runs created (for observability/tests).
    """
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        rule = (
            await session.execute(
                select(Autopilot)
                .where(Autopilot.id == rule_id, Autopilot.workspace_id == workspace_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if rule is None or rule.status != "active" or rule.trigger_type != "schedule":
            return 0
        if rule.next_run_at is None or rule.next_run_at != expected_next:
            return 0  # another replica already claimed it

        config = rule.trigger_config or {}
        cron_expression = str(config.get("cron") or "")
        # An explicit IANA timezone is a creation-time invariant (autopilot.md
        # §5.1): never fall back to a silent "UTC" — a missing/invalid cron or
        # timezone means corrupted config, which we PARK (claim with
        # next_run_at = NULL, fire nothing) instead of guessing.
        timezone_name = str(config.get("timezone") or "")
        misfire_policy = str(config.get("misfire_policy") or "run_once")
        one_time_at = config.get("one_time_at")

        config_usable = True
        try:
            new_next = cron_mod.next_fire_time(cron_expression, timezone_name, after=now)
        except Exception:  # noqa: BLE001 — invalid config parked by validation elsewhere
            logger.exception("rule %s has unusable schedule config", rule.id)
            new_next = None
            config_usable = False

        if one_time_at:
            new_next = None  # one-shot: archive after this fire

        claimed = (
            await session.execute(
                _CLAIM_SQL,
                {"new_next": new_next, "now": now, "rule_id": rule.id, "expected_next": expected_next},
            )
        ).first()
        if claimed is None:
            return 0  # lost the optimistic race — no fire

        if one_time_at:
            rule.status = "archived"

        if not config_usable:
            return 0  # parked: next_run_at is NULL, nothing fires until repaired

        # Misfire handling: how many slots were missed?
        slots: list[datetime] = [expected_next]
        if misfire_policy == "run_all":
            missed = cron_mod.missed_slots(
                cron_expression,
                timezone_name,
                since=expected_next,
                until=now,
                cap=run_all_cap,
            )
            if len(missed) >= run_all_cap:
                logger.warning(
                    "rule %s misfire run_all capped at %d slots", rule.id, run_all_cap
                )
            slots = [expected_next] + [slot for slot in missed if slot != expected_next]
        elif misfire_policy == "skip":
            late_by = (now - expected_next).total_seconds()
            if late_by > grace_seconds:
                slots = []  # too late — advance only, fire nothing

        created = 0
        for index, slot in enumerate(slots):
            # run_all catch-up slots (every slot past the original due one)
            # bypass ONLY the trigger-time concurrency gate — they are created
            # pending in this one transaction, which the in-flight counter
            # would count as occupied and starve the catch-up under the
            # default concurrency_limit=1 (§4.5 one run per missed slot).
            is_catchup_slot = misfire_policy == "run_all" and index > 0
            decision = await evaluate_trigger(
                session,
                rule=rule,
                dedup_key=f"schedule:{rule.id}:{slot.isoformat()}",
                now=now,
                bypass_concurrency=is_catchup_slot,
            )
            if not decision.allowed:
                continue
            await runs_mod.create_run(
                session,
                rule=rule,
                trigger_snapshot={
                    "event_id": f"schedule:{rule.id}:{slot.isoformat()}",
                    "dedup_key": f"schedule:{rule.id}:{slot.isoformat()}",
                    "scheduled_for": slot.isoformat(),
                    "fired_at": now.isoformat(),
                    "schedule": {"cron": cron_expression, "timezone": timezone_name},
                },
                now=now,
            )
            created += 1
        rule.last_run_at = now
        return created


async def autopilot_scheduler_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval: float = 15.0,
    batch: int = 50,
    grace_seconds: int = 300,
    run_all_cap: int = 50,
    stop: asyncio.Event | None = None,
    clock=None,
) -> None:
    """Supervised scheduler loop (README §2.2 调度 worker)."""
    stop = stop or asyncio.Event()
    now_fn = clock or (lambda: datetime.now(UTC))
    while not stop.is_set():
        try:
            async with session_factory() as session, session.begin():
                due = (await session.execute(_due_rule_ids_query(batch))).all()
            for rule_id, workspace_id, next_run_at in due:
                await _fire_schedule_rule(
                    session_factory,
                    rule_id=rule_id,
                    workspace_id=workspace_id,
                    expected_next=next_run_at,
                    grace_seconds=grace_seconds,
                    run_all_cap=run_all_cap,
                    now=now_fn(),
                )
        except Exception:  # noqa: BLE001 — supervisor restarts the loop
            logger.exception("autopilot scheduler pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


__all__ = ["autopilot_scheduler_loop"]
