"""Atomic task claim — runtime.md §2.5 (R1 authoritative version).

One transaction, one atomic SUCCESS branch:

1. ``SELECT ... FOR UPDATE`` the runtime row: verify online / not deleted /
   same tenant / ``current_load < max_concurrent`` — VALIDATE ONLY, no
   pre-deduction. The row lock serializes concurrent claims for the same
   runtime.
2. Pick the best matching queued task with ``FOR UPDATE OF e SKIP LOCKED``:
   same tenant (workspace from the RUNTIME ROW, never the request), label
   containment, capability containment (both against the SERVER-STORED
   values read under the runtime row lock), default-runtime affinity.
3. No match → the transaction ends with ZERO writes (current_load untouched,
   T20: no capacity leak).
4. Match → ``current_load + 1`` + execution ``claimed`` + new attempt row
   with a fresh lease, in the same commit.

``FOR UPDATE SKIP LOCKED`` makes concurrent claims across runtimes wait-free
and duplicate-free (T2). ``lease_seq`` starts at 1 and fences every later
report (T10).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, bindparam, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.agent import Agent
from mesh.db.models.runtime import (
    Approval,
    Runtime,
    TaskExecution,
)
from mesh.db.tenant import set_tenant_context
from mesh.outbox.service import emit_realtime
from mesh.runtime.credentials import DeliveredCredential, issue_envelopes
from mesh.runtime.daemon_auth import validate_env_names
from mesh.runtime.task_tokens import issue_task_token


@dataclass(frozen=True)
class ClaimResult:
    """Everything the daemon needs to start work (plaintext credentials here
    ONLY — never again after this response)."""

    execution: dict
    attempt: dict
    credentials: list[dict]


def _execution_payload(row: TaskExecution, resume_context: dict | None = None) -> dict:
    """Claim-time execution snapshot for the daemon.

    ``resume_context`` (H1, §6.10): when this claim follows an APPROVED
    high-risk-tool approval, the frozen resume context (checkpoint ref +
    completed-steps watermark + pending tool call) is delivered so the new
    attempt continues from the approval point instead of starting over.
    """
    return {
        "id": str(row.id),
        "status": "claimed",
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "issue_id": str(row.issue_id) if row.issue_id else None,
        "trigger": row.trigger,
        "config_snapshot": row.config_snapshot,
        "task_spec": row.task_spec,
        "required_capabilities": row.required_capabilities,
        "label_requirements": row.label_requirements,
        "timeout_seconds": row.timeout_seconds,
        "max_attempts": row.max_attempts,
        "resume_context": resume_context,
    }


async def claim_execution(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    runtime: Runtime,
    lease_seconds: int,
    signing_secret: str,
    envelope_ttl: timedelta,
) -> ClaimResult | None:
    """Attempt to claim one task for ``runtime``; None = nothing claimable.

    None covers queue-empty, no label/capability match AND capacity-full —
    in every None case ``current_load`` is provably unchanged (T20).
    """
    workspace_id = runtime.workspace_id
    runtime_id = runtime.id

    async with session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, workspace_id)

            # 1) Lock the runtime row: validate online / tenant / capacity.
            #    Server-stored labels & capabilities are the ONLY match input
            #    (daemon request bodies are never trusted, §2.5 red line).
            locked = (
                await session.execute(
                    select(
                        Runtime.labels,
                        Runtime.capabilities,
                    )
                    .where(
                        Runtime.id == runtime_id,
                        Runtime.workspace_id == workspace_id,
                        Runtime.status == "online",
                        Runtime.deleted_at.is_(None),
                        Runtime.current_load < Runtime.max_concurrent,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if locked is None:
                return None  # offline / full / cross-tenant: nothing changes
            server_labels, server_capabilities = locked

            # 2) Pick the highest-priority, oldest matching queued task.
            # INNER JOIN agents per §2.5 — a claimable execution always has an
            # executor (the enqueue path sets agent_id); agent-less rows are
            # not dispatchable.
            picked_id = (
                await session.execute(
                    select(TaskExecution.id)
                    .select_from(TaskExecution)
                    .join(
                        Agent,
                        and_(
                            Agent.id == TaskExecution.agent_id,
                            Agent.workspace_id == TaskExecution.workspace_id,
                        ),
                    )
                    .where(
                        TaskExecution.status == "queued",
                        TaskExecution.workspace_id == workspace_id,
                        # H1: chat generations are platform-driven (chat-session.md
                        # §4.4) and finalized via the outbox; a runtime must never
                        # claim them (their empty label/cap requirements would
                        # otherwise match any online runtime and lose the
                        # terminal write-back).
                        TaskExecution.trigger != "chat",
                        TaskExecution.label_requirements.op("<@")(
                            bindparam("p_labels", type_=JSONB)
                        ),
                        TaskExecution.required_capabilities.op("<@")(
                            bindparam("p_caps", type_=JSONB)
                        ),
                        or_(
                            Agent.default_runtime_id.is_(None),
                            Agent.default_runtime_id == runtime_id,
                        ),
                    )
                    .order_by(TaskExecution.priority.asc(), TaskExecution.queued_at.asc())
                    .limit(1)
                    .with_for_update(of=TaskExecution, skip_locked=True),
                    {"p_labels": server_labels, "p_caps": server_capabilities},
                )
            ).scalar_one_or_none()

            # 3) Capacity available but nothing matches → zero writes.
            if picked_id is None:
                return None

            # 4) Single atomic success branch: capacity + claimed + attempt.
            await session.execute(
                update(Runtime)
                .where(Runtime.id == runtime_id)
                .values(
                    current_load=Runtime.current_load + 1,
                    last_heartbeat_at=func.now(),
                    updated_at=func.now(),
                )
            )
            await session.execute(
                update(TaskExecution)
                .where(TaskExecution.id == picked_id)
                .values(status="claimed", updated_at=func.now())
            )
            attempt_row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO execution_attempts
                          (workspace_id, execution_id, attempt_number, runtime_id,
                           claimed_by_runtime_id, status, lease_expires_at, lease_seq,
                           claimed_at, working_branch)
                        SELECT :ws, :eid,
                               COALESCE((SELECT MAX(attempt_number)
                                         FROM execution_attempts
                                         WHERE execution_id = :eid), 0) + 1,
                               :rid, :rid, 'claimed',
                               now() + make_interval(secs => :lease_seconds), 1, now(),
                               'agent/' || CAST(:eid AS text) || '/a' ||
                                 CAST(COALESCE((SELECT MAX(attempt_number)
                                                FROM execution_attempts
                                                WHERE execution_id = :eid), 0) + 1 AS text)
                        RETURNING id, attempt_number, lease_expires_at, lease_seq,
                                  working_branch, claimed_at
                        """
                    ),
                    {
                        "ws": workspace_id,
                        "eid": picked_id,
                        "rid": runtime_id,
                        "lease_seconds": lease_seconds,
                    },
                )
            ).mappings().one()

            execution = (
                await session.execute(
                    select(TaskExecution).where(TaskExecution.id == picked_id)
                )
            ).scalar_one()

            # H1 (§6.10): if this claim resumes an APPROVED high-risk-tool
            # approval, deliver the frozen resume_context so the new attempt
            # continues from the approval point.
            approved_summary = (
                await session.execute(
                    select(Approval.action_summary)
                    .where(
                        Approval.workspace_id == workspace_id,
                        Approval.subject_type == "tool_call",
                        Approval.subject_execution_id == execution.id,
                        Approval.status == "approved",
                    )
                    .order_by(Approval.decided_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            resume_context = (
                (approved_summary or {}).get("resume_context")
                if isinstance(approved_summary, dict)
                else None
            )

            # Credential one-shot envelopes (NEW-M1: env names validated at
            # assembly; an illegal name fails the whole claim with 422).
            task_spec = execution.task_spec or {}
            validate_env_names(task_spec.get("env_declarations"))
            raw_ids = task_spec.get("credential_ids") or []
            credential_ids: list[uuid.UUID] = []
            for raw in raw_ids:
                try:
                    credential_ids.append(uuid.UUID(str(raw)))
                except ValueError:
                    continue
            delivered: list[DeliveredCredential] = await issue_envelopes(
                session,
                workspace_id=workspace_id,
                attempt_id=attempt_row["id"],
                credential_ids=credential_ids,
                signing_secret=signing_secret,
                envelope_ttl=envelope_ttl,
            )
            for item in delivered:
                if item.env is not None:
                    validate_env_names([item.env])

            # §2.2 S-05: issue the short-lived task token for this attempt.
            # Plaintext delivered exactly once in this response; only the
            # hash is stored. Token scope pinned to workspace/attempt/agent/
            # issue; ``agent:trigger`` denied by default (anti-loop).
            task_token_plaintext, _task_token_row = await issue_task_token(
                session,
                workspace_id=workspace_id,
                attempt_id=attempt_row["id"],
                runtime_id=runtime_id,
                lease_seq=attempt_row["lease_seq"],
                lease_expires_at=attempt_row["lease_expires_at"],
                issue_id=execution.issue_id,
                agent_id=execution.agent_id,
            )

            # Observability: claim + queue depth (§3.6 channels).
            attempt_id = attempt_row["id"]
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"workspace:{workspace_id}:executions",
                event="execution.claimed",
                data={
                    "execution_id": str(picked_id),
                    "attempt_id": str(attempt_id),
                    "attempt_number": attempt_row["attempt_number"],
                    "runtime_id": str(runtime_id),
                    "runtime_name": runtime.name,
                    "agent_id": str(execution.agent_id) if execution.agent_id else None,
                    "issue_id": str(execution.issue_id) if execution.issue_id else None,
                },
                idempotency_key=f"claim:{attempt_id}:execution-claimed",
            )
            await _emit_queue_depth(session, workspace_id=workspace_id)

        # Transaction committed — build the daemon response.
        return ClaimResult(
            execution=_execution_payload(execution, resume_context=resume_context),
            attempt={
                "id": str(attempt_row["id"]),
                "attempt_number": attempt_row["attempt_number"],
                "working_branch": attempt_row["working_branch"],
                "lease_expires_at": attempt_row["lease_expires_at"].isoformat(),
                "lease_seq": attempt_row["lease_seq"],
                # §2.2 S-05: task token delivered exactly once (claim).
                "task_token": task_token_plaintext,
                "task_token_expires_at": _task_token_row.expires_at.isoformat(),
                "credentials": [
                    {
                        "id": str(item.id),
                        "kind": item.kind,
                        "env": item.env,
                        "value": item.value,
                        "envelope": item.envelope,
                        "expires_at": item.expires_at.isoformat(),
                    }
                    for item in delivered
                ],
            },
            credentials=[],
        )


async def _emit_queue_depth(session: AsyncSession, *, workspace_id: uuid.UUID) -> None:
    """Publish ``queue.depth_changed`` (queue back-pressure signal, §3.6)."""
    depth = (
        await session.execute(
            select(func.count())
            .select_from(TaskExecution)
            .where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.status == "queued",
            )
        )
    ).scalar_one()
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"workspace:{workspace_id}:queue",
        event="queue.depth_changed",
        data={"depth": int(depth)},
    )
