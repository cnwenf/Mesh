"""Runtime lifecycle service — console side (runtime.md §3.1 / §5.1).

Covers: three-stage registration (shadow row + one-time activation code +
daemon activation), heartbeat / health, pause / resume / decommission with
token revocation linkage (NEW-L2), token rotation, credential management
(plaintext IN only), executions & queue-depth observability.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.config import Settings
from mesh.db.models.api_token import DISPLAY_PREFIX_LEN, RUNTIME_TOKEN_PREFIX, ApiToken
from mesh.db.models.member import Member
from mesh.db.models.runtime import (
    ExecutionAttempt,
    ExecutionCredential,
    RepoCheckout,
    Runtime,
    RuntimeCredential,
    RuntimeHeartbeat,
    TaskExecution,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    GoneError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime
from mesh.runtime.credentials import encrypt_credential_value

# Activation code alphabet without ambiguous glyphs (0/O, 1/I/L).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_GROUP_COUNT = 3
CODE_GROUP_LEN = 4

_RUNTIME_NOT_FOUND = "runtime not found"
_EXECUTION_NOT_FOUND = "execution not found"
_CREDENTIAL_NOT_FOUND = "credential not found"

# Terminal / offline transitions that revoke the daemon token (NEW-L2).
_TOKEN_REVOKING_STATUSES = frozenset({"paused", "decommissioned"})

MAX_LABEL_KEY = 64
MAX_LABEL_VALUE = 256
MAX_LABELS = 32
MAX_CAPABILITIES = 64


def _now() -> datetime:
    return datetime.now(UTC)


def generate_activation_code() -> str:
    """``ACT-XXXX-XXXX-XXXX`` — shown once; only its hash is stored."""
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_GROUP_LEN))
        for _ in range(CODE_GROUP_COUNT)
    ]
    return "ACT-" + "-".join(groups)


def hash_activation_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _validate_labels(labels: dict | None) -> dict:
    if labels is None:
        return {}
    if not isinstance(labels, dict):
        raise BusinessRuleError("labels must be an object", code="invalid_labels")
    if len(labels) > MAX_LABELS:
        raise BusinessRuleError("too many labels", code="invalid_labels")
    for key, value in labels.items():
        if not isinstance(key, str) or not (1 <= len(key) <= MAX_LABEL_KEY):
            raise BusinessRuleError("invalid label key", code="invalid_labels")
        if not isinstance(value, str) or len(value) > MAX_LABEL_VALUE:
            raise BusinessRuleError("invalid label value", code="invalid_labels")
    return dict(labels)


def _render_runtime(runtime: Runtime, *, include_activation: dict | None = None) -> dict:
    data = {
        "id": str(runtime.id),
        "workspace_id": str(runtime.workspace_id),
        "name": runtime.name,
        "kind": runtime.kind,
        "status": runtime.status,
        "capabilities": runtime.capabilities,
        "labels": runtime.labels,
        "hostname": runtime.hostname,
        "os": runtime.os,
        "cpu_cores": runtime.cpu_cores,
        "memory_mb": runtime.memory_mb,
        "max_concurrent": runtime.max_concurrent,
        "current_load": runtime.current_load,
        "last_heartbeat_at": (
            runtime.last_heartbeat_at.isoformat() if runtime.last_heartbeat_at else None
        ),
        "heartbeat_interval_seconds": runtime.heartbeat_interval_seconds,
        "lease_grace_seconds": runtime.lease_grace_seconds,
        "version": runtime.version,
        "activated_at": runtime.activated_at.isoformat() if runtime.activated_at else None,
        "created_at": runtime.created_at.isoformat() if runtime.created_at else None,
    }
    if include_activation is not None:
        data["activation"] = include_activation
    return data


def _render_attempt(attempt: ExecutionAttempt, runtime_name: str | None = None) -> dict:
    return {
        "id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "runtime_id": str(attempt.runtime_id) if attempt.runtime_id else None,
        "runtime_name": runtime_name,
        "status": attempt.status,
        "lease_seq": attempt.lease_seq,
        "claimed_at": attempt.claimed_at.isoformat() if attempt.claimed_at else None,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        "working_branch": attempt.working_branch,
        "failure_reason": attempt.failure_reason,
        "result": attempt.result,
    }


def _render_execution(execution: TaskExecution, attempts: list[dict] | None = None) -> dict:
    data = {
        "id": str(execution.id),
        "workspace_id": str(execution.workspace_id),
        "agent_id": str(execution.agent_id) if execution.agent_id else None,
        "issue_id": str(execution.issue_id) if execution.issue_id else None,
        "trigger": execution.trigger,
        "status": execution.status,
        "priority": execution.priority,
        "task_spec": execution.task_spec,
        "label_requirements": execution.label_requirements,
        "required_capabilities": execution.required_capabilities,
        "config_snapshot": execution.config_snapshot,
        "max_attempts": execution.max_attempts,
        "queued_at": execution.queued_at.isoformat() if execution.queued_at else None,
        "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
        "timeout_seconds": execution.timeout_seconds,
        "failure_reason": execution.failure_reason,
        "result": execution.result,
        "cancel_requested_at": (
            execution.cancel_requested_at.isoformat() if execution.cancel_requested_at else None
        ),
    }
    if attempts is not None:
        data["attempts"] = attempts
    return data


class RuntimeService:
    """Stateless orchestrator bound to a session factory (house pattern)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], settings: Settings):
        self._sf = session_factory
        self._settings = settings

    # -- registration & activation -------------------------------------------

    async def create_runtime(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        name: str,
        kind: str = "self_hosted",
        labels: dict | None = None,
        max_concurrent: int = 1,
    ) -> dict:
        code = generate_activation_code()
        now = _now()
        activation = {
            "code": code,
            "expires_at": (now + self._settings.runtime_activation_ttl).isoformat(),
            "release": {
                "version": self._settings.runtime_release_version,
                "artifact_url": self._settings.runtime_release_artifact_url,
                "sha256": self._settings.runtime_release_sha256,
                "signature_url": self._settings.runtime_release_signature_url,
                "signing_key_url": self._settings.runtime_release_signing_key_url,
            },
            "activate_hint": (
                "mesh-runtime activate --activation-file ./activation.txt   "
                "# activation code via restricted file/stdin, never a CLI argument"
            ),
        }
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = Runtime(
                workspace_id=workspace_id,
                name=name.strip(),
                kind=kind,
                status="pending",
                labels=_validate_labels(labels),
                max_concurrent=max_concurrent,
                activation_token_hash=hash_activation_code(code),
                activation_expires_at=now + self._settings.runtime_activation_ttl,
                created_by=member.id,
            )
            session.add(runtime)
            await session.flush()
            return _render_runtime(runtime, include_activation=activation)

    async def activate_runtime(self, *, activation_code: str, metadata: dict) -> dict:
        """Daemon: exchange the one-time code for the long-lived runtime token.

        Plaintext token appears ONLY in this response; the server stores the
        hash. Expired / already-used codes → 410 (create a fresh runtime).
        """
        code_hash = hash_activation_code(activation_code)
        async with self._sf() as session, session.begin():
            # SECURITY DEFINER bootstrap read (workspace unknown until here).
            from sqlalchemy import text

            row = (
                await session.execute(
                    text(
                        "SELECT id, workspace_id, status, activation_expires_at, "
                        "activated_at, deleted_at "
                        "FROM mesh_runtime_by_activation_hash(:h)"
                    ),
                    {"h": code_hash},
                )
            ).mappings().one_or_none()
            if row is None:
                raise UnauthorizedError("invalid activation code")
            if row["deleted_at"] is not None:
                raise GoneError("activation expired", code="activation_expired")
            now = _now()
            if row["activated_at"] is not None or (
                row["activation_expires_at"] is not None
                and row["activation_expires_at"] < now
            ):
                raise GoneError("activation expired", code="activation_expired")

            await set_tenant_context(session, row["workspace_id"])
            runtime = await session.get(Runtime, row["id"])
            if runtime is None or runtime.status != "pending":
                raise GoneError("activation expired", code="activation_expired")

            meta = metadata or {}
            capabilities = meta.get("capabilities") or []
            if not isinstance(capabilities, list):
                capabilities = []
            capabilities = [str(c) for c in capabilities if isinstance(c, str)][:MAX_CAPABILITIES]
            labels = _validate_labels(meta.get("labels") or {})
            merged_labels = {**runtime.labels, **labels}

            runtime.hostname = str(meta.get("hostname") or runtime.hostname)
            runtime.os = str(meta.get("os") or runtime.os)
            runtime.cpu_cores = _as_positive_int(meta.get("cpu_cores")) or runtime.cpu_cores
            runtime.memory_mb = _as_positive_int(meta.get("memory_mb")) or runtime.memory_mb
            runtime.capabilities = capabilities
            runtime.labels = merged_labels
            runtime.version = str(meta.get("version") or runtime.version)
            runtime.status = "online"
            runtime.activated_at = now  # non-null = code consumed (replay → 410)
            runtime.last_heartbeat_at = now
            # The hash stays (used codes resolve to a 410, §5.1; plaintext is
            # never stored and the row can no longer be activated).
            runtime.updated_at = now

            # Issue the long-lived daemon token (hash-only storage).
            if runtime.created_by is None:
                raise BusinessRuleError(
                    "runtime has no registered owner for token issuance",
                    code="runtime_owner_missing",
                )
            plaintext = RUNTIME_TOKEN_PREFIX + secrets.token_urlsafe(32)
            token_row = ApiToken(
                workspace_id=runtime.workspace_id,
                owner_member_id=runtime.created_by,
                name=f"runtime:{runtime.name}",
                token_hash=hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
                prefix=plaintext[:DISPLAY_PREFIX_LEN],
                scopes=["runtime"],
            )
            session.add(token_row)
            await session.flush()
            runtime.runtime_token_id = token_row.id
            runtime.runtime_token_hash = token_row.token_hash

            await emit_realtime(
                session,
                workspace_id=runtime.workspace_id,
                channel=f"workspace:{runtime.workspace_id}:runtimes",
                event="runtime.activated",
                data={"runtime_id": str(runtime.id), "name": runtime.name},
                idempotency_key=f"runtime:{runtime.id}:activated",
            )
            await emit_realtime(
                session,
                workspace_id=runtime.workspace_id,
                channel=f"workspace:{runtime.workspace_id}:runtimes",
                event="runtime.online",
                data={"runtime_id": str(runtime.id), "name": runtime.name},
                idempotency_key=f"runtime:{runtime.id}:online:{now.isoformat()}",
            )
            return {
                "runtime_id": str(runtime.id),
                "runtime_token": plaintext,
                "heartbeat_interval_seconds": runtime.heartbeat_interval_seconds,
            }

    # -- heartbeat ------------------------------------------------------------

    async def heartbeat(
        self,
        *,
        runtime: Runtime,
        current_load: int,
        health: str,
        metrics: dict,
        inflight: list[str],
    ) -> dict:
        # F8: daemon-reported inflight is DIAGNOSTIC (server-side attempt
        # rows stay the capacity authority) — but validated and persisted in
        # the heartbeat detail row for drift auditing, never ignored.
        for entry in inflight:
            try:
                uuid.UUID(str(entry))
            except (ValueError, TypeError):
                raise ValidationError(
                    "inflight entries must be attempt UUIDs",
                    code="invalid_request",
                    details={"inflight": entry},
                ) from None
        now = _now()
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, runtime.workspace_id)
            row = await session.get(Runtime, runtime.id)
            if row is None or row.deleted_at is not None:
                raise UnauthorizedError("invalid runtime token")
            previous = row.status
            row.last_heartbeat_at = now
            row.updated_at = now
            if health == "degraded":
                # Alive process, broken environment: stop dispatch, keep the
                # troubleshooting window (§5.1).
                row.status = "unavailable"
                await emit_realtime(
                    session,
                    workspace_id=row.workspace_id,
                    channel=f"workspace:{row.workspace_id}:runtimes",
                    event="runtime.degraded",
                    data={"runtime_id": str(row.id), "name": row.name},
                    idempotency_key=f"runtime:{row.id}:degraded:{now.isoformat()}",
                )
            elif previous == "unavailable":
                row.status = "online"
                await emit_realtime(
                    session,
                    workspace_id=row.workspace_id,
                    channel=f"workspace:{row.workspace_id}:runtimes",
                    event="runtime.online",
                    data={"runtime_id": str(row.id), "name": row.name},
                    idempotency_key=f"runtime:{row.id}:online:{now.isoformat()}",
                )
            session.add(
                RuntimeHeartbeat(
                    workspace_id=row.workspace_id,
                    runtime_id=row.id,
                    current_load=current_load,
                    metrics={**(metrics or {}), "inflight_reported": len(inflight)},
                    health=health,
                )
            )

            # Downlink commands ride the heartbeat response (§4.8: ≤15s).
            commands: list[dict] = []
            cancelling = (
                await session.execute(
                    select(ExecutionAttempt)
                    .where(
                        ExecutionAttempt.runtime_id == row.id,
                        ExecutionAttempt.status == "cancelling",
                    )
                    .order_by(ExecutionAttempt.updated_at.desc())
                    .limit(20)
                )
            ).scalars().all()
            for attempt in cancelling:
                commands.append(
                    {
                        "type": "cancel_execution",
                        "execution_id": str(attempt.execution_id),
                        "attempt_id": str(attempt.id),
                        "grace_seconds": 15,
                    }
                )
            return {"server_time": now.isoformat(), "commands": commands}

    # -- listing / detail ------------------------------------------------------

    async def list_runtimes(
        self,
        *,
        workspace_id: uuid.UUID,
        status: str | None = None,
        kind: str | None = None,
        labels: dict | None = None,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        limit = max(1, min(limit, 200))
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(Runtime).where(
                Runtime.workspace_id == workspace_id, Runtime.deleted_at.is_(None)
            )
            if status:
                stmt = stmt.where(Runtime.status == status)
            if kind:
                stmt = stmt.where(Runtime.kind == kind)
            if labels:
                # H3 (§3.1): runtime must carry ALL requested labels (JSONB
                # containment — the same operator claim matching uses).
                stmt = stmt.where(Runtime.labels.op("@>")(labels))
            if search:
                # Escape LIKE metacharacters (review L1): user search terms
                # must match literally, not as wildcards.
                escaped = (
                    search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                stmt = stmt.where(Runtime.name.ilike(f"%{escaped}%", escape="\\"))
            if cursor:
                decoded = _decode_cursor(cursor)
                if decoded is not None:
                    stmt = stmt.where(
                        or_(
                            Runtime.created_at < decoded["created_at"],
                            (Runtime.created_at == decoded["created_at"])
                            & (Runtime.id < decoded["id"]),
                        )
                    )
            stmt = stmt.order_by(Runtime.created_at.desc(), Runtime.id.desc()).limit(limit + 1)
            rows = (await session.execute(stmt)).scalars().all()
            has_more = len(rows) > limit
            items = rows[:limit]
            next_cursor = (
                _encode_cursor(items[-1]) if has_more and items else None
            )
            return {
                "data": [_render_runtime(r) for r in items],
                "next_cursor": next_cursor,
            }

    async def get_runtime(self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID) -> dict:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            heartbeats = (
                await session.execute(
                    select(RuntimeHeartbeat)
                    .where(RuntimeHeartbeat.runtime_id == runtime_id)
                    .order_by(RuntimeHeartbeat.created_at.desc())
                    .limit(120)
                )
            ).scalars().all()
            data = _render_runtime(runtime)
            data["recent_heartbeats"] = [
                {
                    "created_at": h.created_at.isoformat(),
                    "health": h.health,
                    "current_load": h.current_load,
                    "metrics": h.metrics,
                }
                for h in heartbeats
            ]
            return data

    async def patch_runtime(
        self,
        *,
        workspace_id: uuid.UUID,
        runtime_id: uuid.UUID,
        name: str | None,
        labels: dict | None,
        max_concurrent: int | None,
    ) -> dict:
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            if name is not None:
                runtime.name = name.strip()
            if labels is not None:
                runtime.labels = _validate_labels(labels)
            if max_concurrent is not None:
                runtime.max_concurrent = max_concurrent
            runtime.updated_at = _now()
            await session.flush()
            return _render_runtime(runtime)

    async def pause_runtime(self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID) -> dict:
        return await self._lifecycle(workspace_id, runtime_id, target="paused")

    async def resume_runtime(self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID) -> dict:
        return await self._lifecycle(workspace_id, runtime_id, target="online")

    async def decommission_runtime(
        self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> dict:
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            runtime.status = "decommissioned"
            runtime.deleted_at = _now()
            runtime.updated_at = _now()
            await self._revoke_runtime_token(session, runtime)
            return _render_runtime(runtime)

    async def rotate_runtime_token(
        self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> dict:
        """Issue a new daemon token; plaintext shown ONLY here; old revoked."""
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            if runtime.created_by is None:
                raise BusinessRuleError(
                    "runtime has no registered owner for token issuance",
                    code="runtime_owner_missing",
                )
            await self._revoke_runtime_token(session, runtime)
            plaintext = RUNTIME_TOKEN_PREFIX + secrets.token_urlsafe(32)
            token_row = ApiToken(
                workspace_id=workspace_id,
                owner_member_id=runtime.created_by,
                name=f"runtime:{runtime.name}",
                token_hash=hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
                prefix=plaintext[:DISPLAY_PREFIX_LEN],
                scopes=["runtime"],
            )
            session.add(token_row)
            await session.flush()
            runtime.runtime_token_id = token_row.id
            runtime.runtime_token_hash = token_row.token_hash
            runtime.updated_at = _now()
            return {
                "runtime_id": str(runtime.id),
                "runtime_token": plaintext,
                "prefix": token_row.prefix,
            }

    async def _lifecycle(
        self, workspace_id: uuid.UUID, runtime_id: uuid.UUID, *, target: str
    ) -> dict:
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            runtime.status = target
            runtime.updated_at = _now()
            if target in _TOKEN_REVOKING_STATUSES:
                await self._revoke_runtime_token(session, runtime)
            event = "runtime.paused" if target == "paused" else "runtime.online"
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"workspace:{workspace_id}:runtimes",
                event=event,
                data={"runtime_id": str(runtime.id), "name": runtime.name},
                idempotency_key=f"runtime:{runtime.id}:{target}:{_now().isoformat()}",
            )
            return _render_runtime(runtime)

    async def _revoke_runtime_token(self, session: AsyncSession, runtime: Runtime) -> None:
        if runtime.runtime_token_id is None:
            return
        await session.execute(
            update(ApiToken)
            .where(ApiToken.id == runtime.runtime_token_id, ApiToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )

    async def _get_runtime_row(
        self, session: AsyncSession, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> Runtime:
        runtime = (
            await session.execute(
                select(Runtime).where(
                    Runtime.id == runtime_id,
                    Runtime.workspace_id == workspace_id,
                    Runtime.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if runtime is None:
            raise NotFoundError(_RUNTIME_NOT_FOUND)
        return runtime

    # -- executions observability ----------------------------------------------

    async def list_executions(
        self,
        *,
        workspace_id: uuid.UUID,
        runtime_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        issue_id: uuid.UUID | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        limit = max(1, min(limit, 200))
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(TaskExecution).where(TaskExecution.workspace_id == workspace_id)
            if runtime_id is not None:
                stmt = stmt.where(
                    TaskExecution.id.in_(
                        select(ExecutionAttempt.execution_id).where(
                            ExecutionAttempt.runtime_id == runtime_id
                        )
                    )
                )
            if agent_id is not None:
                stmt = stmt.where(TaskExecution.agent_id == agent_id)
            if issue_id is not None:
                stmt = stmt.where(TaskExecution.issue_id == issue_id)
            if status:
                stmt = stmt.where(TaskExecution.status == status)
            if cursor:
                decoded = _decode_cursor(cursor)
                if decoded is not None:
                    stmt = stmt.where(
                        or_(
                            TaskExecution.queued_at < decoded["created_at"],
                            (TaskExecution.queued_at == decoded["created_at"])
                            & (TaskExecution.id < decoded["id"]),
                        )
                    )
            stmt = stmt.order_by(
                TaskExecution.queued_at.desc(), TaskExecution.id.desc()
            ).limit(limit + 1)
            rows = (await session.execute(stmt)).scalars().all()
            has_more = len(rows) > limit
            items = rows[:limit]
            next_cursor = (
                _encode_cursor_execution(items[-1]) if has_more and items else None
            )
            return {
                "data": [_render_execution(e) for e in items],
                "next_cursor": next_cursor,
            }

    async def get_execution(
        self, *, workspace_id: uuid.UUID, execution_id: uuid.UUID
    ) -> dict:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            execution = (
                await session.execute(
                    select(TaskExecution).where(
                        TaskExecution.id == execution_id,
                        TaskExecution.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if execution is None:
                raise NotFoundError(_EXECUTION_NOT_FOUND)
            attempts = (
                await session.execute(
                    select(ExecutionAttempt)
                    .where(ExecutionAttempt.execution_id == execution_id)
                    .order_by(ExecutionAttempt.attempt_number.asc())
                )
            ).scalars().all()
            runtime_names: dict[uuid.UUID, str] = {}
            runtime_ids = {a.runtime_id for a in attempts if a.runtime_id}
            if runtime_ids:
                rows = (
                    await session.execute(
                        select(Runtime.id, Runtime.name).where(Runtime.id.in_(runtime_ids))
                    )
                ).all()
                runtime_names = {row[0]: row[1] for row in rows}
            rendered_attempts = [
                _render_attempt(a, runtime_names.get(a.runtime_id) if a.runtime_id else None)
                for a in attempts
            ]
            data = _render_execution(execution, attempts=rendered_attempts)
            data["retry_count"] = max(len(attempts) - 1, 0)
            # Credential metadata — values are NEVER rendered (*** forever).
            if attempts:
                creds = (
                    await session.execute(
                        select(ExecutionCredential, RuntimeCredential)
                        .join(
                            RuntimeCredential,
                            RuntimeCredential.id == ExecutionCredential.credential_id,
                        )
                        .where(
                            ExecutionCredential.attempt_id.in_([a.id for a in attempts])
                        )
                    )
                ).all()
                data["credentials"] = [
                    {
                        "id": str(ec.credential_id),
                        "name": rc.name,
                        "kind": rc.kind,
                        "attempt_id": str(ec.attempt_id),
                        "injected_at": ec.injected_at.isoformat(),
                        "revoked_at": ec.revoked_at.isoformat() if ec.revoked_at else None,
                        "value": "***",
                    }
                    for ec, rc in creds
                ]
                checkout = (
                    await session.execute(
                        select(RepoCheckout).where(
                            RepoCheckout.attempt_id == attempts[-1].id
                        )
                    )
                ).scalar_one_or_none()
                if checkout is not None:
                    data["checkout"] = {
                        "repo_url": checkout.repo_url,
                        "base_ref": checkout.base_ref,
                        "working_branch": checkout.working_branch,
                        "commit_sha": checkout.commit_sha,
                        "status": checkout.status,
                        "diff_ref": checkout.diff_ref,
                    }
            return data

    # -- credentials ------------------------------------------------------------

    async def create_credential(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        kind: str,
        scope: str,
        value: str,
        env_name: str | None,
        redact_in_logs: bool,
        expires_in_seconds: int | None,
    ) -> dict:
        if env_name is not None:
            # NEW-M1: reserved loader/runtime names are rejected at the gate.
            from mesh.runtime.daemon_auth import validate_env_name

            validate_env_name(env_name)
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            credential = RuntimeCredential(
                workspace_id=workspace_id,
                name=name.strip(),
                kind=kind,
                scope=scope or "execution",
                encrypted_value=encrypt_credential_value(
                    value, self._settings.jwt_secret
                ),
                env_name=env_name,
                redact_in_logs=redact_in_logs,
                expires_at=(
                    _now() + timedelta(seconds=expires_in_seconds)
                    if expires_in_seconds
                    else None
                ),
            )
            session.add(credential)
            await session.flush()
            return self._render_credential(credential)

    async def list_credentials(self, *, workspace_id: uuid.UUID) -> dict:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            rows = (
                await session.execute(
                    select(RuntimeCredential)
                    .where(
                        RuntimeCredential.workspace_id == workspace_id,
                        RuntimeCredential.deleted_at.is_(None),
                    )
                    .order_by(RuntimeCredential.created_at.desc())
                )
            ).scalars().all()
            return {"data": [self._render_credential(c) for c in rows], "next_cursor": None}

    async def delete_credential(self, *, workspace_id: uuid.UUID, credential_id: uuid.UUID) -> None:
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            credential = (
                await session.execute(
                    select(RuntimeCredential).where(
                        RuntimeCredential.id == credential_id,
                        RuntimeCredential.workspace_id == workspace_id,
                        RuntimeCredential.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if credential is None:
                raise NotFoundError(_CREDENTIAL_NOT_FOUND)
            credential.deleted_at = _now()
            credential.updated_at = _now()

    @staticmethod
    def _render_credential(credential: RuntimeCredential) -> dict:
        return {
            "id": str(credential.id),
            "name": credential.name,
            "kind": credential.kind,
            "scope": credential.scope,
            "env_name": credential.env_name,
            "redact_in_logs": credential.redact_in_logs,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            "created_at": credential.created_at.isoformat() if credential.created_at else None,
            # Plaintext never leaves the server (§6.16).
            "value": "***",
        }


def _as_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


# --- opaque cursors (base64url JSON, house style) ---------------------------

import base64  # noqa: E402
import json  # noqa: E402


def _encode_cursor(runtime: Runtime) -> str:
    payload = {"created_at": runtime.created_at.isoformat(), "id": str(runtime.id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _encode_cursor_execution(execution: TaskExecution) -> str:
    payload = {"created_at": execution.queued_at.isoformat(), "id": str(execution.id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: str) -> dict | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        payload = json.loads(raw)
        return {
            "created_at": datetime.fromisoformat(payload["created_at"]),
            "id": uuid.UUID(payload["id"]),
        }
    except (ValueError, KeyError, TypeError):
        return None
