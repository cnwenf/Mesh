"""Autopilot console service — rule CRUD / lifecycle / runs / secrets (autopilot.md §3.1).

All writes emit ``autopilot.updated`` on the workspace autopilots channel
(§3.5) through the outbox. Rule validation enforces the spec's named 4xx
codes: ``invalid_cron`` / ``invalid_trigger_config`` (400),
``executor_required`` / ``agent_unavailable`` (422); duplicate names 409.
``next_run_at`` is computed server-side at creation / resume / config
change (the schedule's explicit IANA timezone is mandatory — §2.6).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.autopilot import cron as cron_mod
from mesh.autopilot import runs as runs_mod
from mesh.autopilot import webhook as webhook_mod
from mesh.autopilot.filters import match_filter_config, matched_dimensions
from mesh.autopilot.guardrails import (
    DEFAULT_GUARDRAILS,
    autopilots_channel,
    merge_guardrails,
)
from mesh.db.models.agent import Agent
from mesh.db.models.autopilot import (
    TRIGGER_TYPE_VALUES,
    Autopilot,
    AutopilotArtifact,
    AutopilotRun,
    AutopilotRunAttempt,
    WebhookEvent,
    WebhookSecret,
)
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from mesh.outbox.service import emit_realtime

_ACTION_TYPES = frozenset(
    {"run_agent_prompt", "add_comment", "send_notification", "create_issue", "http_request"}
)


def _now() -> datetime:
    return datetime.now(UTC)


class AutopilotService:
    """Stateless orchestrator over the session factory (house pattern)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], signing_secret: str) -> None:
        self._factory = session_factory
        self._signing_secret = signing_secret

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def _validate_trigger_config(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, trigger_type: str, config: dict
    ) -> None:
        if trigger_type not in TRIGGER_TYPE_VALUES:
            raise ValidationError(
                "unknown trigger type",
                code="invalid_trigger_config",
                details={"trigger_type": trigger_type},
            )
        if trigger_type == "schedule":
            cron_expression = config.get("cron")
            if not cron_expression:
                raise ValidationError(
                    "schedule trigger requires cron",
                    code="invalid_trigger_config",
                    details={"field": "cron"},
                )
            cron_mod.validate_cron(str(cron_expression))
            timezone_name = config.get("timezone")
            if not timezone_name:
                raise ValidationError(
                    "schedule trigger requires an explicit IANA timezone",
                    code="invalid_trigger_config",
                    details={"field": "timezone"},
                )
            cron_mod.validate_timezone(str(timezone_name))
            misfire = config.get("misfire_policy", "run_once")
            if misfire not in cron_mod.MISFIRE_POLICIES:
                raise ValidationError(
                    "invalid misfire_policy",
                    code="invalid_trigger_config",
                    details={"misfire_policy": str(misfire)},
                )
        elif trigger_type == "webhook_received":
            # §3.2: webhook rules MUST reference a configured valid secret.
            secret_id = config.get("secret_id")
            secret = None
            if secret_id:
                secret = await session.scalar(
                    select(WebhookSecret).where(
                        WebhookSecret.workspace_id == workspace_id,
                        WebhookSecret.id == _uuid_or_none(secret_id),
                    )
                )
            if secret is None or secret.status != "active":
                raise BusinessRuleError(
                    "webhook trigger requires a configured valid signing secret",
                    code="webhook_secret_required",
                    details={"field": "secret_id"},
                )

    async def _validate_actions(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        action_config: list,
        executor_agent_id: uuid.UUID | None,
    ) -> None:
        if not isinstance(action_config, list) or not action_config:
            raise BusinessRuleError(
                "action_config must be a non-empty array", code="invalid_request"
            )
        for action in action_config:
            if not isinstance(action, dict):
                raise BusinessRuleError("each action must be an object", code="invalid_request")
            action_type = str(action.get("type") or "")
            if action_type not in _ACTION_TYPES:
                raise BusinessRuleError(
                    "unknown action type", code="invalid_request", details={"type": action_type}
                )
            if action_type == "run_agent_prompt":
                agent_id = _uuid_or_none(action.get("executor_agent_id")) or executor_agent_id
                if agent_id is None:
                    raise BusinessRuleError(
                        "run_agent_prompt requires executor_agent_id",
                        code="executor_required",
                    )
                if not action.get("prompt"):
                    raise BusinessRuleError(
                        "run_agent_prompt requires a prompt", code="invalid_request"
                    )
                agent = await session.scalar(
                    select(Agent).where(
                        Agent.workspace_id == workspace_id, Agent.id == agent_id
                    )
                )
                if agent is None or agent.lifecycle_status != "active":
                    raise BusinessRuleError(
                        "executor agent not found or unavailable", code="agent_unavailable"
                    )
            if action_type == "http_request" and not action.get("url"):
                raise BusinessRuleError(
                    "http_request requires a url", code="invalid_request"
                )

    # ------------------------------------------------------------------
    # Rule CRUD
    # ------------------------------------------------------------------

    async def create_rule(
        self,
        *,
        workspace_id: uuid.UUID,
        creator: Member,
        payload: dict[str, Any],
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            trigger_type = str(payload.get("trigger_type") or "")
            trigger_config = dict(payload.get("trigger_config") or {})
            action_config = list(payload.get("action_config") or [])
            executor_agent_id = _uuid_or_none(payload.get("executor_agent_id"))
            await self._validate_trigger_config(
                session,
                workspace_id=workspace_id,
                trigger_type=trigger_type,
                config=trigger_config,
            )
            await self._validate_actions(
                session,
                workspace_id=workspace_id,
                action_config=action_config,
                executor_agent_id=executor_agent_id,
            )
            status = str(payload.get("status") or "active")
            if status not in ("active", "paused"):
                raise BusinessRuleError("status must be active or paused", code="invalid_request")

            now = _now()
            next_run_at = None
            if trigger_type == "schedule" and status == "active":
                one_time_at = trigger_config.get("one_time_at")
                if one_time_at:
                    next_run_at = cron_mod.as_utc(datetime.fromisoformat(str(one_time_at)))
                else:
                    next_run_at = cron_mod.next_fire_time(
                        str(trigger_config["cron"]), str(trigger_config["timezone"]), after=now
                    )

            rule = Autopilot(
                workspace_id=workspace_id,
                name=str(payload.get("name") or "").strip(),
                description=payload.get("description"),
                trigger_type=trigger_type,
                trigger_config=trigger_config,
                filter_config=dict(payload.get("filter_config") or {}),
                action_config=action_config,
                executor_agent_id=executor_agent_id,
                status=status,
                guardrails=merge_guardrails(payload.get("guardrails")),
                max_retries=int(payload.get("max_retries", 3)),
                retry_backoff=str(payload.get("retry_backoff", "exponential")),
                retry_base_seconds=int(payload.get("retry_base_seconds", 30)),
                retry_max_seconds=int(payload.get("retry_max_seconds", 1800)),
                rate_limit_max=int(payload.get("rate_limit_max", 10)),
                rate_limit_window_seconds=int(payload.get("rate_limit_window_seconds", 3600)),
                concurrency_limit=int(payload.get("concurrency_limit", 1)),
                require_approval=bool(payload.get("require_approval", False)),
                next_run_at=next_run_at,
                created_by=creator.id,
                created_at=now,
                updated_at=now,
            )
            session.add(rule)
            rule_name = rule.name  # capture pre-flush (aborted-txn lazy IO guard)
            try:
                await session.flush()
            except IntegrityError as exc:
                from mesh.db.constraints import violates

                if violates(exc, "uq_autopilot_ws_name"):
                    raise ConflictError(
                        "a rule with this name already exists",
                        code="conflict",
                        details={"name": rule_name},
                    ) from exc
                raise
            await self._emit_autopilot_updated(session, rule=rule, now=now)
            return self._rule_response(rule, stats=None)

    async def list_rules(
        self,
        *,
        workspace_id: uuid.UUID,
        status: str | None = None,
        trigger_type: str | None = None,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        from mesh.api.pagination import decode_cursor, encode_cursor

        limit = max(1, min(limit, 100))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(Autopilot).where(
                Autopilot.workspace_id == workspace_id, Autopilot.deleted_at.is_(None)
            )
            if status:
                stmt = stmt.where(Autopilot.status == status)
            if trigger_type:
                stmt = stmt.where(Autopilot.trigger_type == trigger_type)
            if search:
                stmt = stmt.where(Autopilot.name.ilike(f"%{search}%"))
            stmt = stmt.order_by(Autopilot.created_at.desc(), Autopilot.id.desc())
            if cursor:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    # descending keyset on (created_at, id)
                    (Autopilot.created_at < position.sort_value)
                    | (
                        (Autopilot.created_at == position.sort_value)
                        & (Autopilot.id < position.id)
                    )
                )
            rows = list((await session.execute(stmt.limit(limit + 1))).scalars().all())
            has_more = len(rows) > limit
            rules = rows[:limit]

            # 30-day stats per rule (§3.2 list shape).
            rule_ids = [rule.id for rule in rules]
            stats = await self._run_stats(session, workspace_id=workspace_id, rule_ids=rule_ids)
            last_statuses = await self._last_run_statuses(
                session, workspace_id=workspace_id, rule_ids=rule_ids
            )
            data = [
                self._rule_response(
                    rule,
                    stats=stats.get(rule.id),
                    last_run_status=last_statuses.get(rule.id),
                )
                for rule in rules
            ]
            next_cursor = None
            if has_more and rules:
                last = rules[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            return {"data": data, "next_cursor": next_cursor}

    async def get_rule(self, *, workspace_id: uuid.UUID, rule_id: uuid.UUID) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            rule = await self._load_rule(session, workspace_id=workspace_id, rule_id=rule_id)
            stats = await self._run_stats(session, workspace_id=workspace_id, rule_ids=[rule.id])
            return self._rule_response(rule, stats=stats.get(rule.id))

    async def update_rule(
        self, *, workspace_id: uuid.UUID, rule_id: uuid.UUID, patch: dict[str, Any]
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            rule = await self._load_rule(session, workspace_id=workspace_id, rule_id=rule_id)
            now = _now()

            if "name" in patch and patch["name"] is not None:
                rule.name = str(patch["name"]).strip()
            if "description" in patch:
                rule.description = patch["description"]
            trigger_type = str(patch.get("trigger_type") or rule.trigger_type)
            trigger_config = (
                dict(patch["trigger_config"]) if "trigger_config" in patch else dict(rule.trigger_config)
            )
            filter_config = (
                dict(patch["filter_config"]) if "filter_config" in patch else dict(rule.filter_config)
            )
            action_config = (
                list(patch["action_config"]) if "action_config" in patch else list(rule.action_config)
            )
            executor_agent_id = (
                _uuid_or_none(patch["executor_agent_id"])
                if "executor_agent_id" in patch
                else rule.executor_agent_id
            )
            await self._validate_trigger_config(
                session,
                workspace_id=workspace_id,
                trigger_type=trigger_type,
                config=trigger_config,
            )
            await self._validate_actions(
                session,
                workspace_id=workspace_id,
                action_config=action_config,
                executor_agent_id=executor_agent_id,
            )
            rule.trigger_type = trigger_type
            rule.trigger_config = trigger_config
            rule.filter_config = filter_config
            rule.action_config = action_config
            rule.executor_agent_id = executor_agent_id

            if "guardrails" in patch:
                rule.guardrails = merge_guardrails(patch.get("guardrails"))
            for field in (
                "max_retries",
                "retry_base_seconds",
                "retry_max_seconds",
                "rate_limit_max",
                "rate_limit_window_seconds",
                "concurrency_limit",
            ):
                if field in patch and patch[field] is not None:
                    setattr(rule, field, int(patch[field]))
            if "retry_backoff" in patch and patch["retry_backoff"] is not None:
                rule.retry_backoff = str(patch["retry_backoff"])
            if "require_approval" in patch and patch["require_approval"] is not None:
                rule.require_approval = bool(patch["require_approval"])

            # Recompute the schedule when it changed (or the rule is active).
            if rule.trigger_type == "schedule" and rule.status == "active":
                one_time_at = trigger_config.get("one_time_at")
                if one_time_at:
                    rule.next_run_at = cron_mod.as_utc(datetime.fromisoformat(str(one_time_at)))
                else:
                    rule.next_run_at = cron_mod.next_fire_time(
                        str(trigger_config["cron"]), str(trigger_config["timezone"]), after=now
                    )
            elif rule.trigger_type != "schedule":
                rule.next_run_at = None

            rule.updated_at = now
            # Capture BEFORE the flush: after an IntegrityError the
            # transaction is aborted and attribute access would try lazy IO
            # on a dead session.
            rule_name = rule.name
            try:
                await session.flush()
            except IntegrityError as exc:
                from mesh.db.constraints import violates

                if violates(exc, "uq_autopilot_ws_name"):
                    raise ConflictError(
                        "a rule with this name already exists",
                        code="conflict",
                        details={"name": rule_name},
                    ) from exc
                raise
            await self._emit_autopilot_updated(session, rule=rule, now=now)
            return self._rule_response(rule, stats=None)

    async def delete_rule(self, *, workspace_id: uuid.UUID, rule_id: uuid.UUID) -> None:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            rule = await self._load_rule(session, workspace_id=workspace_id, rule_id=rule_id)
            now = _now()
            rule.deleted_at = now
            rule.status = "archived"
            rule.next_run_at = None
            rule.updated_at = now
            await self._emit_autopilot_updated(session, rule=rule, now=now)

    async def pause_rule(self, *, workspace_id: uuid.UUID, rule_id: uuid.UUID) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            rule = await self._load_rule(session, workspace_id=workspace_id, rule_id=rule_id)
            if rule.status != "active":
                raise ConflictError(
                    "only active rules can be paused",
                    code="invalid_state_transition",
                    details={"status": rule.status},
                )
            now = _now()
            rule.status = "paused"
            rule.next_run_at = None
            rule.updated_at = now
            await self._emit_autopilot_updated(session, rule=rule, now=now)
            return self._rule_response(rule, stats=None)

    async def resume_rule(self, *, workspace_id: uuid.UUID, rule_id: uuid.UUID) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            rule = await self._load_rule(session, workspace_id=workspace_id, rule_id=rule_id)
            if rule.status != "paused":
                raise ConflictError(
                    "only paused rules can be resumed",
                    code="invalid_state_transition",
                    details={"status": rule.status},
                )
            now = _now()
            rule.status = "active"
            guardrails = dict(rule.guardrails or {})
            guardrails["kill_switch_paused"] = False
            rule.guardrails = guardrails
            if rule.trigger_type == "schedule":
                one_time_at = rule.trigger_config.get("one_time_at")
                if one_time_at:
                    rule.next_run_at = cron_mod.as_utc(datetime.fromisoformat(str(one_time_at)))
                else:
                    rule.next_run_at = cron_mod.next_fire_time(
                        str(rule.trigger_config["cron"]),
                        str(rule.trigger_config["timezone"]),
                        after=now,
                    )
            rule.updated_at = now
            await self._emit_autopilot_updated(session, rule=rule, now=now)
            return self._rule_response(rule, stats=None)

    async def preview_schedule(
        self, *, workspace_id: uuid.UUID, rule_id: uuid.UUID, count: int = 5
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            rule = await self._load_rule(session, workspace_id=workspace_id, rule_id=rule_id)
            if rule.trigger_type != "schedule":
                raise BusinessRuleError(
                    "rule is not schedule-triggered", code="invalid_trigger_config"
                )
            config = rule.trigger_config or {}
            upcoming = cron_mod.preview_schedule(
                str(config["cron"]), str(config["timezone"]), count=min(max(count, 1), 10)
            )
            return {
                "cron": config.get("cron"),
                "timezone": config.get("timezone"),
                "next_runs": [moment.isoformat() for moment in upcoming],
            }

    def preview_schedule_params(self, *, cron: str, timezone: str, count: int = 5) -> dict:
        """Stateless cron preview (autopilot.md §4.2 live preview, usable in
        create mode before any rule exists). Validation errors surface as
        400 invalid_cron / invalid_trigger_config from cron_mod."""
        upcoming = cron_mod.preview_schedule(cron, timezone, count=min(max(count, 1), 10))
        return {"cron": cron, "timezone": timezone, "next_runs": [m.isoformat() for m in upcoming]}

    async def list_webhook_events(
        self,
        *,
        workspace_id: uuid.UUID,
        autopilot_id: uuid.UUID | None = None,
        process_status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Inbound event audit trail (autopilot.md §4.1 最近事件)."""
        from mesh.api.pagination import decode_cursor, encode_cursor

        limit = max(1, min(limit, 100))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(WebhookEvent).where(WebhookEvent.workspace_id == workspace_id)
            if autopilot_id is not None:
                stmt = stmt.where(WebhookEvent.autopilot_id == autopilot_id)
            if process_status:
                stmt = stmt.where(WebhookEvent.process_status == process_status)
            stmt = stmt.order_by(WebhookEvent.received_at.desc(), WebhookEvent.id.desc())
            if cursor:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    (WebhookEvent.received_at < position.sort_value)
                    | (
                        (WebhookEvent.received_at == position.sort_value)
                        & (WebhookEvent.id < position.id)
                    )
                )
            rows = list((await session.execute(stmt.limit(limit + 1))).scalars().all())
            has_more = len(rows) > limit
            kept = rows[:limit]
            data = [
                {
                    "id": str(event.id),
                    "autopilot_id": str(event.autopilot_id) if event.autopilot_id else None,
                    "idempotency_key": event.idempotency_key,
                    "event_type": event.event_type,
                    "headers": event.headers,
                    "payload": event.payload,
                    "signature_status": event.signature_status,
                    "process_status": event.process_status,
                    "received_at": _iso(event.received_at),
                }
                for event in kept
            ]
            next_cursor = None
            if has_more and kept:
                last = kept[-1]
                next_cursor = encode_cursor(last.received_at, last.id)
            return {"data": data, "next_cursor": next_cursor}

    # ------------------------------------------------------------------
    # Test run
    # ------------------------------------------------------------------

    async def test_run(
        self,
        *,
        workspace_id: uuid.UUID,
        rule_id: uuid.UUID,
        actor: Member,
        simulate_payload: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> tuple[int, dict]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            rule = await self._load_rule(session, workspace_id=workspace_id, rule_id=rule_id)
            simulate = simulate_payload or {}
            context = {
                "project_id": (simulate.get("issue") or {}).get("project_id"),
                "labels": (simulate.get("issue") or {}).get("labels") or [],
                "priority": (simulate.get("issue") or {}).get("priority"),
                "actor_id": (simulate.get("actor") or {}).get("id"),
                "title": (simulate.get("issue") or {}).get("title") or "",
                "body": (simulate.get("comment") or {}).get("body") or "",
                "payload": (simulate.get("webhook") or {}).get("payload") or simulate.get("payload") or {},
            }
            if dry_run:
                matches = match_filter_config(rule.filter_config, context)
                return 200, {
                    "would_run": matches,
                    "matched_filters": matched_dimensions(rule.filter_config),
                }

            # The kill switch is an emergency stop (§5.3): it blocks manual
            # test runs too (dry_run above is side-effect free and stays
            # available for debugging a paused workspace).
            if (rule.guardrails or {}).get("kill_switch_paused"):
                raise ConflictError(
                    "autopilot kill switch is engaged",
                    code="kill_switch",
                    details={"rule": str(rule.id)},
                )

            now = _now()
            snapshot = {
                "event_id": f"test:{rule.id}:{now.isoformat()}",
                "test": True,
                **simulate,
            }
            webhook_event_id = None
            if rule.trigger_type == "webhook_received":
                # §2.5: skipped signature status exists ONLY for test runs.
                event = WebhookEvent(
                    workspace_id=workspace_id,
                    autopilot_id=rule.id,
                    idempotency_key=f"test:{rule.id}:{now.isoformat()}",
                    event_type=str(simulate.get("event_type") or "test.event"),
                    payload=simulate.get("payload") or {},
                    signature_status="skipped",
                    process_status="dispatched",
                    received_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(event)
                await session.flush()
                webhook_event_id = event.id
            run = await runs_mod.create_run(
                session,
                rule=rule,
                trigger_snapshot=snapshot,
                webhook_event_id=webhook_event_id,
                triggered_by=actor.id,
                is_test=True,
                now=now,
            )
            return 202, {
                "run_id": str(run.id),
                "status": run.status,
                "autopilot_id": str(rule.id),
                "is_test": True,
            }

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def list_runs(
        self,
        *,
        workspace_id: uuid.UUID,
        rule_id: uuid.UUID,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        from mesh.api.pagination import decode_cursor, encode_cursor

        limit = max(1, min(limit, 100))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            # 404 when the rule is invisible in this workspace.
            await self._load_rule(session, workspace_id=workspace_id, rule_id=rule_id)
            stmt = select(AutopilotRun).where(
                AutopilotRun.workspace_id == workspace_id,
                AutopilotRun.autopilot_id == rule_id,
            )
            if status:
                stmt = stmt.where(AutopilotRun.status == status)
            stmt = stmt.order_by(AutopilotRun.created_at.desc(), AutopilotRun.id.desc())
            if cursor:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    (AutopilotRun.created_at < position.sort_value)
                    | (
                        (AutopilotRun.created_at == position.sort_value)
                        & (AutopilotRun.id < position.id)
                    )
                )
            rows = list((await session.execute(stmt.limit(limit + 1))).scalars().all())
            has_more = len(rows) > limit
            kept = rows[:limit]
            next_cursor = None
            if has_more and kept:
                last = kept[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            return {
                "data": [self._run_response(run) for run in kept],
                "next_cursor": next_cursor,
            }

    async def get_run(
        self, *, workspace_id: uuid.UUID, run_id: uuid.UUID, with_detail: bool = True
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            run = await self._load_run(session, workspace_id=workspace_id, run_id=run_id)
            response = self._run_response(run)
            if with_detail:
                attempts = (
                    (
                        await session.execute(
                            select(AutopilotRunAttempt)
                            .where(AutopilotRunAttempt.run_id == run.id)
                            .order_by(AutopilotRunAttempt.attempt_number.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                response["attempts"] = [
                    {
                        "attempt_number": attempt.attempt_number,
                        "status": attempt.status,
                        "execution_id": str(attempt.execution_id) if attempt.execution_id else None,
                        "started_at": _iso(attempt.started_at),
                        "finished_at": _iso(attempt.finished_at),
                        "error": attempt.error,
                        "prompt_tokens": attempt.prompt_tokens,
                        "completion_tokens": attempt.completion_tokens,
                    }
                    for attempt in attempts
                ]
                response["artifacts"] = await self._artifact_rows(session, run_id=run.id)
            return response

    async def list_run_artifacts(self, *, workspace_id: uuid.UUID, run_id: uuid.UUID) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            await self._load_run(session, workspace_id=workspace_id, run_id=run_id)
            return {"data": await self._artifact_rows(session, run_id=run_id), "next_cursor": None}

    async def cancel_run(
        self, *, workspace_id: uuid.UUID, run_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            run = await self._load_run(session, workspace_id=workspace_id, run_id=run_id)
            if run.status in runs_mod.TERMINAL_STATUSES:
                raise ConflictError(
                    "run already finished",
                    code="invalid_state_transition",
                    details={"status": run.status},
                )
            now = _now()
            await runs_mod.transition_run(
                session,
                run,
                "cancelled",
                error={"code": "cancelled", "message": "cancelled by user", "retryable": False},
                now=now,
            )
            return self._run_response(run)

    # ------------------------------------------------------------------
    # Kill switch (§3.1 — workspace-level emergency stop)
    # ------------------------------------------------------------------

    async def kill_switch(
        self, *, workspace_id: uuid.UUID, enabled: bool, reason: str | None
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = _now()
            rules = list(
                (
                    await session.execute(
                        select(Autopilot).where(
                            Autopilot.workspace_id == workspace_id,
                            Autopilot.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            affected = 0
            for rule in rules:
                guardrails = dict(rule.guardrails or DEFAULT_GUARDRAILS)
                if enabled:
                    if rule.status == "active":
                        rule.status = "paused"
                        rule.next_run_at = None
                        guardrails["kill_switch_paused"] = True
                        affected += 1
                    elif rule.status == "paused":
                        guardrails["kill_switch_paused"] = bool(guardrails.get("kill_switch_paused"))
                else:
                    if guardrails.get("kill_switch_paused"):
                        rule.status = "active"
                        guardrails["kill_switch_paused"] = False
                        if rule.trigger_type == "schedule":
                            config = rule.trigger_config or {}
                            one_time_at = config.get("one_time_at")
                            if one_time_at:
                                rule.next_run_at = cron_mod.as_utc(
                                    datetime.fromisoformat(str(one_time_at))
                                )
                            else:
                                rule.next_run_at = cron_mod.next_fire_time(
                                    str(config["cron"]), str(config["timezone"]), after=now
                                )
                        affected += 1
                rule.guardrails = guardrails
                rule.updated_at = now
                await self._emit_autopilot_updated(session, rule=rule, now=now)
            return {
                "kill_switch": enabled,
                "paused_autopilots": affected,
                "reason": reason,
                "updated_at": now.isoformat(),
            }

    # ------------------------------------------------------------------
    # Webhook secrets (§3.1)
    # ------------------------------------------------------------------

    async def create_webhook_secret(
        self, *, workspace_id: uuid.UUID, member: Member, label: str = "default"
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            return await webhook_mod.create_secret(
                session,
                workspace_id=workspace_id,
                member=member,
                label=label,
                signing_secret=self._signing_secret,
            )

    async def rotate_webhook_secret(
        self, *, workspace_id: uuid.UUID, secret_id: uuid.UUID, member: Member
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            return await webhook_mod.rotate_secret(
                session,
                workspace_id=workspace_id,
                secret_id=secret_id,
                member=member,
                signing_secret=self._signing_secret,
            )

    async def list_webhook_secrets(self, *, workspace_id: uuid.UUID) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            rows = (
                (
                    await session.execute(
                        select(WebhookSecret)
                        .where(WebhookSecret.workspace_id == workspace_id)
                        .order_by(WebhookSecret.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return {
                "data": [webhook_mod.public_secret_row(row) for row in rows],
                "next_cursor": None,
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_rule(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, rule_id: uuid.UUID
    ) -> Autopilot:
        rule = await session.scalar(
            select(Autopilot).where(
                Autopilot.workspace_id == workspace_id,
                Autopilot.id == rule_id,
                Autopilot.deleted_at.is_(None),
            )
        )
        if rule is None:
            raise NotFoundError("autopilot rule not found")
        return rule

    async def _load_run(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, run_id: uuid.UUID
    ) -> AutopilotRun:
        run = await session.scalar(
            select(AutopilotRun).where(
                AutopilotRun.workspace_id == workspace_id, AutopilotRun.id == run_id
            )
        )
        if run is None:
            raise NotFoundError("autopilot run not found")
        return run

    async def _run_stats(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        rule_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, dict[str, Any]]:
        if not rule_ids:
            return {}
        since = _now().replace(hour=0, minute=0, second=0, microsecond=0) - _days(30)
        rows = (
            await session.execute(
                select(
                    AutopilotRun.autopilot_id,
                    func.count(),
                    func.count().filter(AutopilotRun.status == "succeeded"),
                )
                .where(
                    AutopilotRun.workspace_id == workspace_id,
                    AutopilotRun.autopilot_id.in_(rule_ids),
                    AutopilotRun.created_at >= since,
                    AutopilotRun.is_test.is_(False),
                )
                .group_by(AutopilotRun.autopilot_id)
            )
        ).all()
        return {
            row[0]: {
                "runs_30d": int(row[1]),
                "success_rate": round(int(row[2]) / int(row[1]), 4) if int(row[1]) else None,
            }
            for row in rows
        }

    async def _artifact_rows(self, session: AsyncSession, *, run_id: uuid.UUID) -> list[dict]:
        artifacts = (
            (
                await session.execute(
                    select(AutopilotArtifact)
                    .where(AutopilotArtifact.run_id == run_id)
                    .order_by(AutopilotArtifact.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "ref_table": artifact.ref_table,
                "ref_id": str(artifact.ref_id),
                "summary": artifact.summary,
                "created_at": _iso(artifact.created_at),
            }
            for artifact in artifacts
        ]

    async def _emit_autopilot_updated(
        self, session: AsyncSession, *, rule: Autopilot, now: datetime
    ) -> None:
        await emit_realtime(
            session,
            workspace_id=rule.workspace_id,
            channel=autopilots_channel(rule.workspace_id),
            event="autopilot.updated",
            data={"autopilot_id": str(rule.id), "status": rule.status},
            idempotency_key=f"autopilot:{rule.id}:updated:{int(now.timestamp() * 1000)}",
        )

    def _rule_response(
        self, rule: Autopilot, *, stats: dict | None, last_run_status: str | None = None
    ) -> dict:
        return {
            "id": str(rule.id),
            "workspace_id": str(rule.workspace_id),
            "name": rule.name,
            "description": rule.description,
            "trigger_type": rule.trigger_type,
            "trigger_config": rule.trigger_config,
            "filter_config": rule.filter_config,
            "action_config": rule.action_config,
            "executor_agent_id": str(rule.executor_agent_id) if rule.executor_agent_id else None,
            "status": rule.status,
            "guardrails": rule.guardrails,
            "max_retries": rule.max_retries,
            "retry_backoff": rule.retry_backoff,
            "retry_base_seconds": rule.retry_base_seconds,
            "retry_max_seconds": rule.retry_max_seconds,
            "rate_limit_max": rule.rate_limit_max,
            "rate_limit_window_seconds": rule.rate_limit_window_seconds,
            "concurrency_limit": rule.concurrency_limit,
            "require_approval": rule.require_approval,
            "next_run_at": _iso(rule.next_run_at),
            "last_run_at": _iso(rule.last_run_at),
            "last_run_status": last_run_status,
            "created_by": str(rule.created_by),
            "created_at": _iso(rule.created_at),
            "updated_at": _iso(rule.updated_at),
            "stats": stats,
        }

    async def _last_run_statuses(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, rule_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """Latest run status per rule (§4.1 列表「上次运行时间与结果」)."""
        if not rule_ids:
            return {}
        ranked = (
            select(
                AutopilotRun.autopilot_id,
                AutopilotRun.status,
                func.row_number()
                .over(
                    partition_by=AutopilotRun.autopilot_id,
                    order_by=(AutopilotRun.created_at.desc(), AutopilotRun.id.desc()),
                ).label("rn"),
            )
            .where(
                AutopilotRun.workspace_id == workspace_id,
                AutopilotRun.autopilot_id.in_(rule_ids),
            )
            .subquery()
        )
        rows = (
            (await session.execute(select(ranked).where(ranked.c.rn == 1))).all()
        )
        return {row[0]: row[1] for row in rows}

    def _run_response(self, run: AutopilotRun) -> dict:
        return {
            "id": str(run.id),
            "autopilot_id": str(run.autopilot_id),
            "workspace_id": str(run.workspace_id),
            "trigger_type": run.trigger_type,
            "trigger_snapshot": run.trigger_snapshot,
            "webhook_event_id": str(run.webhook_event_id) if run.webhook_event_id else None,
            "execution_id": str(run.execution_id) if run.execution_id else None,
            "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
            "cascade_depth": run.cascade_depth,
            "status": run.status,
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "duration_ms": run.duration_ms,
            "retry_count": run.retry_count,
            "error": run.error,
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            # total_tokens is a STORED generated column (SQL-side); derive it
            # here instead of reading the attribute — after a flush the
            # DB-computed value is expired and cannot lazy-load in a sync
            # response builder.
            "total_tokens": (run.prompt_tokens or 0) + (run.completion_tokens or 0),
            "triggered_by": str(run.triggered_by) if run.triggered_by else None,
            "is_test": run.is_test,
            "created_at": _iso(run.created_at),
            "updated_at": _iso(run.updated_at),
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _days(count: int):
    from datetime import timedelta

    return timedelta(days=count)


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


__all__ = ["AutopilotService"]
