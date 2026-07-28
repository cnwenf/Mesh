"""Shared fixtures for autopilot-module unit tests (real PostgreSQL, no mocks)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from mesh.autopilot.guardrails import DEFAULT_GUARDRAILS, merge_guardrails
from mesh.autopilot.webhook import generate_credential_pair, hash_token
from mesh.db.models.autopilot import Autopilot, AutopilotRun, WebhookSecret
from mesh.runtime.credentials import encrypt_credential_value

TEST_SIGNING_SECRET = "autopilot-test-signing-secret-000000000000"


def _now() -> datetime:
    return datetime.now(UTC)


async def make_rule(
    session_factory,
    workspace_id: uuid.UUID,
    *,
    created_by: uuid.UUID,
    name: str | None = None,
    trigger_type: str = "schedule",
    trigger_config: dict | None = None,
    filter_config: dict | None = None,
    action_config: list | None = None,
    executor_agent_id: uuid.UUID | None = None,
    status: str = "active",
    guardrails: dict | None = None,
    next_run_at: datetime | None = None,
    require_approval: bool = False,
    rate_limit_max: int = 10,
    rate_limit_window_seconds: int = 3600,
    concurrency_limit: int = 1,
    max_retries: int = 3,
    retry_backoff: str = "exponential",
    retry_base_seconds: int = 30,
    retry_max_seconds: int = 1800,
) -> Autopilot:
    default_trigger = {
        "schedule": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai", "misfire_policy": "run_once"},
        "issue_status_changed": {},
        "issue_created": {},
        "issue_field_changed": {"watch_fields": ["priority"]},
        "comment_created": {},
        "agent_mentioned": {},
        "webhook_received": {},
    }
    # Default action is executor-free (a rule seeded without an agent must
    # still be spec-valid). Tests exercising run_agent_prompt pass it
    # explicitly along with executor_agent_id.
    if executor_agent_id is not None:
        default_actions = [
            {
                "type": "run_agent_prompt",
                "executor_agent_id": str(executor_agent_id),
                "prompt": "handle {{trigger.issue.title}}",
            }
        ]
    else:
        default_actions = [{"type": "send_notification", "message": "autopilot finished"}]
    rule = Autopilot(
        workspace_id=workspace_id,
        name=name or f"rule-{uuid.uuid4().hex[:8]}",
        trigger_type=trigger_type,
        trigger_config=trigger_config if trigger_config is not None else default_trigger[trigger_type],
        filter_config=filter_config or {},
        action_config=action_config if action_config is not None else default_actions,
        executor_agent_id=executor_agent_id,
        status=status,
        guardrails=merge_guardrails(guardrails),
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
        rate_limit_max=rate_limit_max,
        rate_limit_window_seconds=rate_limit_window_seconds,
        concurrency_limit=concurrency_limit,
        require_approval=require_approval,
        next_run_at=next_run_at,
        created_by=created_by,
        created_at=_now(),
        updated_at=_now(),
    )
    async with session_factory() as session, session.begin():
        session.add(rule)
    return rule


async def make_run(
    session_factory,
    rule: Autopilot,
    *,
    status: str = "pending",
    trigger_snapshot: dict | None = None,
    parent_run_id: uuid.UUID | None = None,
    cascade_depth: int = 0,
    is_test: bool = False,
    retry_count: int = 0,
    created_at: datetime | None = None,
    error: dict | None = None,
) -> AutopilotRun:
    run = AutopilotRun(
        workspace_id=rule.workspace_id,
        autopilot_id=rule.id,
        trigger_type=rule.trigger_type,
        trigger_snapshot=trigger_snapshot or {"event_id": f"evt-{uuid.uuid4().hex[:8]}"},
        parent_run_id=parent_run_id,
        cascade_depth=cascade_depth,
        status=status,
        retry_count=retry_count,
        error=error,
        is_test=is_test,
        created_at=created_at or _now(),
        updated_at=_now(),
    )
    async with session_factory() as session, session.begin():
        session.add(run)
    return run


async def make_secret(
    session_factory,
    workspace_id: uuid.UUID,
    *,
    created_by: uuid.UUID,
    label: str = "default",
    status: str = "active",
) -> tuple[WebhookSecret, str, str]:
    """Returns (row, plaintext_token, plaintext_secret)."""
    token, secret = generate_credential_pair()
    row = WebhookSecret(
        workspace_id=workspace_id,
        label=label,
        token_hash=hash_token(token),
        encrypted_secret=encrypt_credential_value(secret, TEST_SIGNING_SECRET),
        status=status,
        created_by=created_by,
        created_at=_now(),
        updated_at=_now(),
    )
    async with session_factory() as session, session.begin():
        session.add(row)
    return row, token, secret


def default_guardrails(**overrides) -> dict:
    merged = dict(DEFAULT_GUARDRAILS)
    merged.update(overrides)
    return merged


def hours(delta: float) -> timedelta:
    return timedelta(hours=delta)
