"""Run executor + execution reconciler (autopilot.md §4.4 / §4.5 / README §6.4).

The worker loop has two reconciling passes over the runs table — both claim
rows with ``FOR UPDATE SKIP LOCKED`` so multiple replicas never touch the
same run:

A. **dispatch** — ``pending`` runs (and ``retrying`` runs whose backoff
   deadline has passed) get a fresh attempt (numbers never reused, §2.4)
   and their action pipeline executed. The first dispatch of a gated rule
   parks the run at ``waiting_approval`` instead (README §6.10);
B. **reconcile** — ``running`` runs observe the terminal state of the
   logical execution their ``run_agent_prompt`` step enqueued
   (``task_executions`` is the lower-layer source of truth, §4.4):
   completed → continue the pipeline past that step; failed/timeout →
   retryable? back off into ``retrying`` : ``failed`` + alert.

Autopilot runs are the UPPER orchestration record; executions are the
lower execution truth. ``succeeded`` ↔ execution ``completed`` (different
names, aligned semantics).

Action side effects are idempotent (README §6.5): every step carries a
stable key ``sha256(run_id | attempt_number | step_index | type)`` — relay
redelivery or a resumed pipeline never doubles a comment / notification /
enqueue. ``run_agent_prompt`` enqueues with the §6.5 key
``sha256(agent_id | issue_id | trigger_event_ref)`` where the trigger ref
is ``run.id:a<attempt>`` — same attempt redelivers dedup, a new retry
attempt gets a NEW execution (§4.4 "入队新 execution").
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import random
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.agent.guardrails import ENQUEUE_EVENT_TYPE
from mesh.agent.snapshot import snapshot_from_agent
from mesh.autopilot import approvals as approvals_mod
from mesh.autopilot import runs as runs_mod
from mesh.autopilot.template import UNTRUSTED_NOTICE, render_template
from mesh.db.models.agent import Agent
from mesh.db.models.autopilot import (
    Autopilot,
    AutopilotArtifact,
    AutopilotRun,
    AutopilotRunAttempt,
)
from mesh.db.models.member import Member
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
from mesh.outbox.service import emit_event

logger = logging.getLogger("mesh.autopilot.executor")

# Outbound HTTP action red lines (§5.3 / README §6.16).
OUTBOUND_SCHEMES = ("https",)
OUTBOUND_TIMEOUT_SECONDS = 10.0

# Execution failure reasons that must NOT retry (config / auth / parameter
# class errors, §4.4 不可重试).
_NON_RETRYABLE_EXECUTION_REASONS = frozenset(
    {"max_retries", "approval_rejected", "approval_expired", "cancelled", "superseded"}
)

_WAIT = object()  # pipeline sentinel: an async step is still in flight


class ActionError(Exception):
    """An action step failed; ``retryable`` drives §4.4 branching."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


def _now() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(rule: Autopilot, retry_index: int, *, rng: random.Random | None = None) -> float:
    """delay = min(base × 2^n, max) × jitter (§4.4 指数退避 + 抖动 + 封顶)."""
    if rule.retry_backoff == "fixed":
        base_delay = float(rule.retry_base_seconds)
    elif rule.retry_backoff == "linear":
        base_delay = rule.retry_base_seconds * (retry_index + 1)
    else:  # exponential
        base_delay = rule.retry_base_seconds * (2**retry_index)
    capped = min(base_delay, float(rule.retry_max_seconds))
    jitter = (rng or random).uniform(0.5, 1.5)
    return max(1.0, capped * jitter)


def _step_idempotency_key(run: AutopilotRun, attempt_number: int, step_index: int, kind: str) -> str:
    """README §6.5-style stable key per (run, attempt, step)."""
    return hashlib.sha256(
        f"{run.id}|{attempt_number}|{step_index}|{kind}".encode()
    ).hexdigest()


def _enqueue_idempotency_key(
    *, agent_id: uuid.UUID, issue_id: uuid.UUID | None, run: AutopilotRun, attempt_number: int
) -> str:
    """§6.5 enqueue key; the trigger ref includes the attempt so a retry
    attempt enqueues a NEW execution while redelivery of the SAME attempt
    dedups."""
    issue_part = str(issue_id) if issue_id is not None else ""
    return hashlib.sha256(
        f"{agent_id}|{issue_part}|{run.id}:a{attempt_number}".encode()
    ).hexdigest()


async def _load_step_outputs(session: AsyncSession, run: AutopilotRun) -> list[dict[str, Any]]:
    """Rebuild the ``steps`` list from recorded agent_output artifacts —
    the resumable pipeline watermark (artifacts ARE the step journal)."""
    artifacts = (
        (
            await session.execute(
                select(AutopilotArtifact)
                .where(
                    AutopilotArtifact.run_id == run.id,
                    AutopilotArtifact.artifact_type == "agent_output",
                )
                .order_by(AutopilotArtifact.created_at.asc(), AutopilotArtifact.id.asc())
            )
        )
        .scalars()
        .all()
    )
    steps: list[dict[str, Any]] = []
    for artifact in artifacts:
        try:
            steps.append(json.loads(artifact.summary or "{}"))
        except json.JSONDecodeError:
            steps.append({"output": artifact.summary})
    return steps


def _trigger_issue_id(snapshot: dict[str, Any]) -> uuid.UUID | None:
    raw = (snapshot.get("issue") or {}).get("id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except (ValueError, AttributeError, TypeError):
        return None


async def _enqueue_agent_execution(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    rule: Autopilot,
    agent: Agent,
    executor_member: Member,
    prompt: str,
    attempt_number: int,
    outbox_event_id: uuid.UUID,
) -> str:
    """Emit execution.enqueue (trigger='autopilot') — §6.4/§6.5/§6.11."""
    issue_id = _trigger_issue_id(run.trigger_snapshot or {})
    idempotency_key = _enqueue_idempotency_key(
        agent_id=agent.id, issue_id=issue_id, run=run, attempt_number=attempt_number
    )
    # §3.7 S-09 shared snapshot builder. F-BUDGET-SNAPSHOT: this call site
    # previously passed only the version id / trigger ref — provider, model,
    # effort, system instructions, budget and network policy were all absent,
    # so every autopilot-triggered execution failed fail-closed on the daemon.
    snapshot_parts = snapshot_from_agent(agent, trigger_event_id=outbox_event_id)
    await emit_event(
        session,
        workspace_id=run.workspace_id,
        event_type=ENQUEUE_EVENT_TYPE,
        payload={
            "intent": "enqueue",
            "agent_id": str(agent.id),
            "agent_member_id": str(executor_member.id),
            "issue_id": str(issue_id) if issue_id else None,
            "trigger": "autopilot",
            "trigger_event_id": str(outbox_event_id),
            "idempotency_key": idempotency_key,
            "config_snapshot": snapshot_parts["config_snapshot"],
            "required_capabilities": snapshot_parts["required_capabilities"],
            "label_requirements": [],
            "task_spec": {
                "kind": "autopilot_prompt",
                "prompt": prompt,
                "autopilot_run_id": str(run.id),
            },
        },
        idempotency_key=idempotency_key,
    )
    return idempotency_key


# ---------------------------------------------------------------------------
# SSRF-guarded outbound HTTP (§5.3 / README §6.16)
# ---------------------------------------------------------------------------


def _assert_public_target(hostname: str, allowlist: list[str] | None = None) -> None:
    """Refuse private/loopback/link-local/metadata destinations."""
    if allowlist and hostname in allowlist:
        return
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ActionError("invalid_request", f"cannot resolve host: {hostname}", retryable=False) from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        mapped = getattr(address, "ipv4_mapped", None)
        if mapped is not None:
            address = mapped
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ActionError(
                "private_address_forbidden",
                "outbound HTTP may not target private address ranges",
                retryable=False,
            )


async def _perform_http_request(action: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    url = str(action.get("url") or "")
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in OUTBOUND_SCHEMES:
        raise ActionError(
            "invalid_request", "outbound HTTP actions require an https URL", retryable=False
        )
    hostname = httpx.URL(url).host
    _assert_public_target(hostname, [str(h) for h in (action.get("host_allowlist") or [])])
    method = str(action.get("method") or "POST").upper()
    headers = {str(k): str(v) for k, v in (action.get("headers") or {}).items()}
    headers.setdefault("Idempotency-Key", idempotency_key)
    try:
        async with httpx.AsyncClient(
            timeout=OUTBOUND_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            response = await client.request(
                method, url, headers=headers, json=action.get("body")
            )
    except httpx.TimeoutException as exc:
        raise ActionError("timeout", "outbound request timed out", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ActionError("transient", f"outbound request failed: {exc}", retryable=True) from exc
    return {"status_code": response.status_code, "body": response.text[:8192]}


# ---------------------------------------------------------------------------
# The action pipeline
# ---------------------------------------------------------------------------


async def execute_action_pipeline(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    rule: Autopilot,
    attempt: AutopilotRunAttempt,
    services: dict[str, Any],
    now: datetime | None = None,
) -> object:
    """Execute steps sequentially from the recorded watermark.

    Returns ``_WAIT`` when a ``run_agent_prompt`` step is still in flight
    (the reconciler resumes later); otherwise completes the run
    (``succeeded``) or raises :class:`ActionError`.
    """
    moment = now if now is not None else _now()
    actions = [action for action in (rule.action_config or []) if isinstance(action, dict)]
    steps = await _load_step_outputs(session, run)
    snapshot = run.trigger_snapshot or {}

    index = len(steps)
    while index < len(actions):
        action = actions[index]
        action_type = str(action.get("type") or "")
        if action_type == "run_agent_prompt":
            outcome = await _step_run_agent_prompt(
                session,
                run=run,
                rule=rule,
                attempt=attempt,
                action=action,
                steps=steps,
                snapshot=snapshot,
                step_index=index,
                now=moment,
            )
            if outcome is _WAIT:
                return _WAIT
            steps.append(outcome)
        elif action_type == "add_comment":
            steps.append(
                await _step_add_comment(
                    session,
                    run=run,
                    rule=rule,
                    action=action,
                    steps=steps,
                    snapshot=snapshot,
                    step_index=index,
                    attempt_number=attempt.attempt_number,
                    services=services,
                    now=moment,
                )
            )
        elif action_type == "send_notification":
            steps.append(
                await _step_send_notification(
                    session,
                    run=run,
                    rule=rule,
                    action=action,
                    steps=steps,
                    snapshot=snapshot,
                    step_index=index,
                    attempt_number=attempt.attempt_number,
                    now=moment,
                )
            )
        elif action_type == "create_issue":
            steps.append(
                await _step_create_issue(
                    session,
                    run=run,
                    rule=rule,
                    action=action,
                    steps=steps,
                    snapshot=snapshot,
                    step_index=index,
                    attempt_number=attempt.attempt_number,
                    services=services,
                )
            )
        elif action_type == "http_request":
            steps.append(
                await _step_http_request(
                    session,
                    run=run,
                    action=action,
                    steps=steps,
                    snapshot=snapshot,
                    step_index=index,
                    attempt_number=attempt.attempt_number,
                    now=moment,
                )
            )
        else:
            raise ActionError(
                "invalid_request", f"unknown action type: {action_type}", retryable=False
            )
        index += 1

    # Every step done — the run succeeded.
    attempt.status = "succeeded"
    attempt.finished_at = moment
    await runs_mod.transition_run(session, run, "succeeded", now=moment)
    # §6.13 matrix: success = normal, default stays on the run page; the
    # fanout handler decides inbox routing (off unless subscribed).
    await emit_event(
        session,
        workspace_id=run.workspace_id,
        event_type="notification.fanout",
        payload={
            "type": "execution_finished",
            "execution_status": "completed",
            "recipient_ids": [str(rule.created_by)],
            "group_key": f"autopilot:{rule.id}:runs",
            "autopilot_id": str(rule.id),
            "run_id": str(run.id),
        },
        idempotency_key=f"run:{run.id}:finished-ok",
    )
    return None


async def _resolve_executor(
    session: AsyncSession, *, rule: Autopilot, action: dict[str, Any]
) -> tuple[Agent, Member]:
    agent_id = action.get("executor_agent_id") or rule.executor_agent_id
    try:
        agent_uuid = uuid.UUID(str(agent_id)) if agent_id else None
    except (ValueError, TypeError):
        agent_uuid = None
    if agent_uuid is None:
        raise ActionError("executor_required", "run_agent_prompt needs executor_agent_id", retryable=False)
    agent = await session.scalar(
        select(Agent).where(Agent.workspace_id == rule.workspace_id, Agent.id == agent_uuid)
    )
    if agent is None or agent.lifecycle_status != "active":
        raise ActionError("agent_unavailable", "executor agent not found or unavailable", retryable=False)
    member = await session.scalar(
        select(Member).where(Member.workspace_id == rule.workspace_id, Member.agent_id == agent.id)
    )
    if member is None:
        raise ActionError("agent_unavailable", "executor agent has no roster member", retryable=False)
    return agent, member


async def _step_run_agent_prompt(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    rule: Autopilot,
    attempt: AutopilotRunAttempt,
    action: dict[str, Any],
    steps: list[dict[str, Any]],
    snapshot: dict[str, Any],
    step_index: int,
    now: datetime,
) -> Any:
    agent, member = await _resolve_executor(session, rule=rule, action=action)
    prompt = render_template(
        str(action.get("prompt") or ""),
        trigger_snapshot=snapshot,
        steps=steps,
        run_id=run.id,
        now=now,
    )
    prompt = f"{UNTRUSTED_NOTICE}\n\n{prompt}"

    # This attempt's execution (linked by the reconciler, or findable by
    # the stable §6.5 idempotency key after enqueue redelivery).
    execution = None
    if attempt.execution_id is not None:
        execution = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.workspace_id == run.workspace_id,
                TaskExecution.id == attempt.execution_id,
            )
        )
    if execution is None:
        issue_id = _trigger_issue_id(snapshot)
        key = _enqueue_idempotency_key(
            agent_id=agent.id, issue_id=issue_id, run=run, attempt_number=attempt.attempt_number
        )
        execution = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.workspace_id == run.workspace_id,
                TaskExecution.idempotency_key == key,
            )
        )
        if execution is not None:
            attempt.execution_id = execution.id
            run.execution_id = execution.id
            await session.flush()

    if execution is None:
        # First pass for this attempt: enqueue, then wait for the runtime.
        # The run row IS the autopilot-side trigger event (§6.11 audit anchor).
        await _enqueue_agent_execution(
            session,
            run=run,
            rule=rule,
            agent=agent,
            executor_member=member,
            prompt=prompt,
            attempt_number=attempt.attempt_number,
            outbox_event_id=run.id,
        )
        return _WAIT

    status = execution.status
    if status in ("queued", "claimed", "running", "requeued", "awaiting_approval", "cancelling"):
        return _WAIT
    if status == "completed":
        output = (execution.result or {}).get("output") if isinstance(execution.result, dict) else None
        step_record = {"output": output if output is not None else execution.result}
        await runs_mod.record_artifact(
            session,
            run,
            artifact_type="agent_output",
            ref_table="task_executions",
            ref_id=execution.id,
            summary=json.dumps(step_record, ensure_ascii=False, default=str),
            now=now,
        )
        # Best-effort token rollup from the execution result.
        usage = execution.result.get("usage") if isinstance(execution.result, dict) else None
        if isinstance(usage, dict):
            attempt.prompt_tokens = _int_or_none(usage.get("prompt_tokens"))
            attempt.completion_tokens = _int_or_none(usage.get("completion_tokens"))
            run.prompt_tokens = _sum_tokens(run.prompt_tokens, attempt.prompt_tokens)
            run.completion_tokens = _sum_tokens(run.completion_tokens, attempt.completion_tokens)
        return step_record
    # failed / timeout / cancelled → classify per §4.4.
    reason = str(execution.failure_reason or "")
    retryable = reason not in _NON_RETRYABLE_EXECUTION_REASONS and status in ("failed", "timeout")
    raise ActionError(
        "execution_failed_retryable" if retryable else "execution_failed",
        f"execution {status}: {reason or 'unknown'}",
        retryable=retryable,
    )


def _int_or_none(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _sum_tokens(existing: int | None, extra: int | None) -> int | None:
    if extra is None:
        return existing
    return (existing or 0) + extra


async def _step_add_comment(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    rule: Autopilot,
    action: dict[str, Any],
    steps: list[dict[str, Any]],
    snapshot: dict[str, Any],
    step_index: int,
    attempt_number: int,
    services: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    comment_service = services["comment_service"]
    issue_id = _trigger_issue_id(snapshot)
    if issue_id is None:
        raise ActionError(
            "invalid_request", "add_comment needs a trigger issue in the snapshot", retryable=False
        )
    _agent, member = await _resolve_executor(session, rule=rule, action=action)
    content = render_template(
        str(action.get("content") or ""),
        trigger_snapshot=snapshot,
        steps=steps,
        run_id=run.id,
        now=now,
    )
    idem = _step_idempotency_key(run, attempt_number, step_index, "comment")
    try:
        created = await comment_service.create_comment(
            workspace_id=run.workspace_id,
            issue_id=issue_id,
            author_member=member,
            body_markdown=content,
            suppress_triggers=True,  # autopilot comments must not re-trigger agents (loop safety)
            idempotency_key=idem,
        )
    except Exception as exc:  # surface as retryable transient unless clearly invalid
        message = str(exc)
        retryable = "not found" not in message and "forbidden" not in message
        code = "transient" if retryable else "invalid_request"
        raise ActionError(code, message, retryable=retryable) from exc
    comment_id = uuid.UUID(str(created.get("id") or created.get("comment_id") or run.id))
    await runs_mod.record_artifact(
        session,
        run,
        artifact_type="comment",
        ref_table="comments",
        ref_id=comment_id,
        summary="comment posted",
        now=now,
    )
    return {"output": content}


async def _step_send_notification(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    rule: Autopilot,
    action: dict[str, Any],
    steps: list[dict[str, Any]],
    snapshot: dict[str, Any],
    step_index: int,
    attempt_number: int,
    now: datetime,
) -> dict[str, Any]:
    message = render_template(
        str(action.get("message") or action.get("template") or "autopilot run finished"),
        trigger_snapshot=snapshot,
        steps=steps,
        run_id=run.id,
        now=now,
    )
    recipients = [str(rule.created_by)]
    outbox_event = await emit_event(
        session,
        workspace_id=run.workspace_id,
        event_type="notification.fanout",
        payload={
            "type": "autopilot_notice",
            "recipient_ids": recipients,
            "group_key": f"autopilot:{rule.id}:notice",
            "autopilot_id": str(rule.id),
            "run_id": str(run.id),
            "message": message,
        },
        idempotency_key=_step_idempotency_key(run, attempt_number, step_index, "notification"),
    )
    await runs_mod.record_artifact(
        session,
        run,
        artifact_type="notification",
        ref_table="outbox_events",
        ref_id=outbox_event.id,
        summary=message[:280],
        now=now,
    )
    return {"output": message}


async def _step_create_issue(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    rule: Autopilot,
    action: dict[str, Any],
    steps: list[dict[str, Any]],
    snapshot: dict[str, Any],
    step_index: int,
    attempt_number: int,
    services: dict[str, Any],
) -> dict[str, Any]:
    from mesh.issue.schemas import CreateIssueRequest

    issue_service = services["issue_service"]
    session_factory = services["session_factory"]
    title = render_template(
        str(action.get("title") or "autopilot created issue {{run.id}}"),
        trigger_snapshot=snapshot,
        steps=steps,
        run_id=run.id,
    )
    description = render_template(
        str(action.get("description") or ""),
        trigger_snapshot=snapshot,
        steps=steps,
        run_id=run.id,
    )
    # NB: do NOT shadow the pipeline ``session`` parameter here — the issue
    # creation AND the artifact below must commit in the dispatch
    # transaction: the relay matches the new issue's ``issue.created`` event
    # through the issue artifact (cascade lineage, §5.3), so the event row
    # and its lineage anchor must become visible ATOMICALLY — creating the
    # issue in its own transaction would let the relay match the event
    # before the artifact exists and the create_issue ↔ issue_created loop
    # would escape the cascade-depth guard. (The creator lookup stays on a
    # separate session: its FK key-share on the roster row would deadlock
    # with the dispatch's FOR UPDATE from any other connection.)
    async with session_factory() as lookup_session:
        await set_tenant_context(lookup_session, run.workspace_id)
        creator = await lookup_session.scalar(
            select(Member).where(Member.workspace_id == run.workspace_id, Member.id == rule.created_by)
        )
    if creator is None:
        raise ActionError("invalid_request", "rule creator roster row missing", retryable=False)
    request_fields: dict = {"title": title[:500]}
    if description:
        request_fields["description"] = description
    if action.get("project_id"):
        request_fields["project_id"] = str(action["project_id"])
    if action.get("priority"):
        request_fields["priority"] = str(action["priority"])
    body = CreateIssueRequest(**request_fields)
    try:
        # SAVEPOINT so a rejected creation leaves the dispatch transaction
        # usable for the failure/retry bookkeeping that follows.
        async with session.begin_nested():
            created = await issue_service.create_issue_in_session(
                session, actor=creator, workspace_id=run.workspace_id, body=body
            )
    except Exception as exc:
        raise ActionError("transient", f"issue creation failed: {exc}", retryable=True) from exc
    issue_uuid = uuid.UUID(str(created.get("id") or run.id))
    await runs_mod.record_artifact(
        session,
        run,
        artifact_type="issue",
        ref_table="issues",
        ref_id=issue_uuid,
        summary=title[:280],
    )
    return {"output": {"issue_id": str(issue_uuid), "title": title}}


async def _step_http_request(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    action: dict[str, Any],
    steps: list[dict[str, Any]],
    snapshot: dict[str, Any],
    step_index: int,
    attempt_number: int,
    now: datetime,
) -> dict[str, Any]:
    rendered = {
        "url": render_template(
            str(action.get("url") or ""), trigger_snapshot=snapshot, steps=steps, run_id=run.id, now=now
        ),
        "method": action.get("method"),
        "headers": action.get("headers"),
        "host_allowlist": action.get("host_allowlist"),
        "body": action.get("body"),
    }
    idem = _step_idempotency_key(run, attempt_number, step_index, "http")
    result = await _perform_http_request(rendered, idem)
    if result["status_code"] >= 500:
        raise ActionError("transient", f"upstream returned {result['status_code']}", retryable=True)
    await runs_mod.record_artifact(
        session,
        run,
        artifact_type="http_response",
        ref_table="outbound_http",
        ref_id=uuid.uuid5(uuid.NAMESPACE_URL, idem),
        summary=f"HTTP {result['status_code']}",
        now=now,
    )
    return {"output": result}


# ---------------------------------------------------------------------------
# Dispatch / reconcile passes
# ---------------------------------------------------------------------------


async def dispatch_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    services: dict[str, Any],
    approval_ttl: timedelta,
) -> None:
    """One dispatch pass over a single claimed run (own transaction)."""
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        run = (
            await session.execute(
                select(AutopilotRun)
                .where(AutopilotRun.id == run_id, AutopilotRun.workspace_id == workspace_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None or run.status not in ("pending", "retrying"):
            return
        rule = await session.scalar(
            select(Autopilot).where(
                Autopilot.id == run.autopilot_id, Autopilot.workspace_id == workspace_id
            )
        )
        moment = _now()
        if rule is None or rule.deleted_at is not None:
            await runs_mod.transition_run(
                session,
                run,
                "cancelled",
                error={"code": "rule_deleted", "message": "rule no longer exists", "retryable": False},
                now=moment,
            )
            return
        if rule.status != "active":
            # Paused / archived rules do not dispatch queued runs.
            return
        if run.status == "retrying":
            retry_at = (run.error or {}).get("retry_at")
            if retry_at:
                try:
                    if datetime.fromisoformat(str(retry_at)) > moment:
                        return  # backoff deadline not reached
                except ValueError:
                    pass

        # Approval gate — ONLY on first dispatch (§4.4: pending → waiting_approval).
        if run.status == "pending" and run.retry_count == 0:
            required, matched_types = approvals_mod.requires_approval(rule)
            if required:
                await approvals_mod.request_run_approval(
                    session,
                    run=run,
                    rule=rule,
                    requested_by_member_id=run.triggered_by or rule.created_by,
                    action_summary={
                        "action": "autopilot_run",
                        "autopilot_id": str(rule.id),
                        "run_id": str(run.id),
                        "gated_action_types": matched_types,
                        "impact_scope": {"trigger_type": run.trigger_type},
                    },
                    ttl=approval_ttl,
                    now=moment,
                )
                return

        attempt = await runs_mod.new_attempt(session, run, now=moment)
        if run.status == "retrying":
            await runs_mod.transition_run(session, run, "running", now=moment)
        elif run.status == "pending":
            await runs_mod.transition_run(session, run, "running", now=moment)
        try:
            await execute_action_pipeline(
                session, run=run, rule=rule, attempt=attempt, services=services, now=moment
            )
        except ActionError as exc:
            await _handle_action_failure(
                session, run=run, rule=rule, attempt=attempt, exc=exc, now=moment
            )


async def _handle_action_failure(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    rule: Autopilot,
    attempt: AutopilotRunAttempt,
    exc: ActionError,
    now: datetime,
) -> None:
    error = {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
    attempt.status = "failed"
    attempt.error = error
    attempt.finished_at = now
    if exc.retryable and run.retry_count < rule.max_retries:
        delay = backoff_seconds(rule, run.retry_count)
        run.retry_count += 1
        error["backoff_seconds"] = round(delay, 1)
        error["retry_at"] = (now + timedelta(seconds=delay)).isoformat()
        await runs_mod.transition_run(session, run, "retrying", error=error, now=now)
        return
    await runs_mod.transition_run(session, run, "failed", error=error, now=now)
    # §6.13 matrix: failure = critical (inbox + pierce quiet hours + reset).
    await emit_event(
        session,
        workspace_id=run.workspace_id,
        event_type="notification.fanout",
        payload={
            "type": "execution_finished",
            "execution_status": "failed",
            "recipient_ids": [str(rule.created_by)],
            "group_key": f"autopilot:{rule.id}:runs",
            "autopilot_id": str(rule.id),
            "run_id": str(run.id),
            "error": error,
        },
        idempotency_key=f"run:{run.id}:finished-failed",
    )


async def reconcile_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    services: dict[str, Any],
) -> None:
    """Observe the lower-layer execution terminal state for a running run."""
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        run = (
            await session.execute(
                select(AutopilotRun)
                .where(AutopilotRun.id == run_id, AutopilotRun.workspace_id == workspace_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None or run.status != "running":
            return
        attempt = (
            await session.execute(
                select(AutopilotRunAttempt)
                .where(AutopilotRunAttempt.run_id == run.id)
                .order_by(AutopilotRunAttempt.attempt_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if attempt is not None and attempt.status != "running":
            return
        rule = await session.scalar(
            select(Autopilot).where(
                Autopilot.id == run.autopilot_id, Autopilot.workspace_id == workspace_id
            )
        )
        if rule is None:
            return
        moment = _now()

        # Approved resume: the gate parks the run BEFORE any attempt exists
        # (approve → running); start the pipeline now.
        if attempt is None:
            attempt = await runs_mod.new_attempt(session, run, now=moment)
            try:
                await execute_action_pipeline(
                    session, run=run, rule=rule, attempt=attempt, services=services, now=moment
                )
            except ActionError as exc:
                await _handle_action_failure(
                    session, run=run, rule=rule, attempt=attempt, exc=exc, now=moment
                )
            return

        # Link the attempt to its execution once it materializes (the
        # enqueue handler created it in a separate transaction).
        execution = None
        if attempt.execution_id is not None:
            execution = await session.scalar(
                select(TaskExecution).where(
                    TaskExecution.workspace_id == run.workspace_id,
                    TaskExecution.id == attempt.execution_id,
                )
            )
        if execution is None:
            snapshot = run.trigger_snapshot or {}
            actions = [a for a in (rule.action_config or []) if isinstance(a, dict)]
            completed = await _load_step_outputs(session, run)
            if len(completed) < len(actions):
                current = actions[len(completed)]
                if str(current.get("type") or "") == "run_agent_prompt":
                    try:
                        agent, _member = await _resolve_executor(session, rule=rule, action=current)
                    except ActionError:
                        agent = None
                    if agent is not None:
                        key = _enqueue_idempotency_key(
                            agent_id=agent.id,
                            issue_id=_trigger_issue_id(snapshot),
                            run=run,
                            attempt_number=attempt.attempt_number,
                        )
                        execution = await session.scalar(
                            select(TaskExecution).where(
                                TaskExecution.workspace_id == run.workspace_id,
                                TaskExecution.idempotency_key == key,
                            )
                        )
                        if execution is not None:
                            attempt.execution_id = execution.id
                            run.execution_id = execution.id
                            await session.flush()
        if execution is None or execution.status in (
            "queued",
            "claimed",
            "running",
            "requeued",
            "awaiting_approval",
            "cancelling",
        ):
            return  # still in flight (or no async step — dispatch pass owns it)

        if execution.status == "completed":
            # Resume the pipeline past the completed execution step.
            try:
                await execute_action_pipeline(
                    session, run=run, rule=rule, attempt=attempt, services=services, now=moment
                )
            except ActionError as exc:
                await _handle_action_failure(
                    session, run=run, rule=rule, attempt=attempt, exc=exc, now=moment
                )
            return
        if execution.status == "cancelled":
            attempt.status = "cancelled"
            attempt.finished_at = moment
            await runs_mod.transition_run(
                session,
                run,
                "cancelled",
                error={
                    "code": "execution_cancelled",
                    "message": str(execution.failure_reason or "cancelled"),
                    "retryable": False,
                },
                now=moment,
            )
            return
        # failed / timeout
        reason = str(execution.failure_reason or "")
        retryable = reason not in _NON_RETRYABLE_EXECUTION_REASONS
        await _handle_action_failure(
            session,
            run=run,
            rule=rule,
            attempt=attempt,
            exc=ActionError(
                "execution_failed_retryable" if retryable else "execution_failed",
                f"execution {execution.status}: {reason or 'unknown'}",
                retryable=retryable,
            ),
            now=moment,
        )


async def _claim_runs(
    session: AsyncSession, *, statuses: tuple[str, ...], batch: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    rows = (
        await session.execute(
            select(AutopilotRun.id, AutopilotRun.workspace_id)
            .where(AutopilotRun.status.in_(statuses))
            .order_by(AutopilotRun.created_at.asc())
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


async def autopilot_executor_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    services: dict[str, Any],
    interval: float = 2.0,
    approval_ttl: timedelta = timedelta(hours=24),
    batch: int = 25,
    stop: asyncio.Event | None = None,
) -> None:
    """Supervised loop: dispatch pending/retrying, reconcile running."""
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            async with session_factory() as session, session.begin():
                pending = await _claim_runs(
                    session, statuses=("pending", "retrying"), batch=batch
                )
            for run_id, workspace_id in pending:
                await dispatch_run(
                    session_factory,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    services=services,
                    approval_ttl=approval_ttl,
                )
            async with session_factory() as session, session.begin():
                running = await _claim_runs(session, statuses=("running",), batch=batch)
            for run_id, workspace_id in running:
                await reconcile_run(
                    session_factory, run_id=run_id, workspace_id=workspace_id, services=services
                )
        except Exception:  # noqa: BLE001 — supervisor restarts; never poison the loop
            logger.exception("autopilot executor pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


__all__ = [
    "ActionError",
    "autopilot_executor_loop",
    "backoff_seconds",
    "dispatch_run",
    "execute_action_pipeline",
    "reconcile_run",
]
