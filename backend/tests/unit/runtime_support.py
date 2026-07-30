"""Shared fixtures for runtime-module unit tests (real PostgreSQL, no mocks)."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime

from mesh.config import load_settings
from mesh.db.models.agent import Agent
from mesh.db.models.api_token import RUNTIME_TOKEN_PREFIX
from mesh.db.models.member import Member
from mesh.db.models.runtime import Runtime, TaskExecution
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace

TEST_JWT_SECRET = "runtime-test-signing-secret-00000000000000"


def make_settings(**overrides):
    base = {
        "database_url": os.environ.get(
            "MESH_TEST_DATABASE_URL",
            "postgresql+asyncpg://mesh:mesh@127.0.0.1:5436/mesh_test",
        ),
        "redis_url": os.environ.get("MESH_TEST_REDIS_URL", "redis://127.0.0.1:6394/1"),
        "auth_mode": "dev",
        "jwt_secret": TEST_JWT_SECRET,
        "daemon_tls_required": False,
    }
    base.update(overrides)
    return load_settings(**base)


async def seed_world(session_factory) -> dict:
    """Workspace + human admin member + agent with its roster member row.

    Staged flushes: the models declare bare FK columns (no relationships),
    so the unit of work needs explicit ordering with client-side UUIDs.
    """
    ws_id, user_id, member_id, agent_id, agent_member_id = (uuid.uuid4() for _ in range(5))
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=ws_id, name="RT WS", slug=f"rt-ws-{ws_id.hex[:10]}"))
        session.add(
            User(
                id=user_id,
                email=f"rt-owner-{user_id.hex[:8]}@corp.com",
                display_name="RT Owner",
                password_hash="unused-in-tests",
            )
        )
        await session.flush()
        session.add(
            Agent(
                id=agent_id,
                workspace_id=ws_id,
                name="Agent RT",
                owner_user_id=user_id,
                lifecycle_status="active",
            )
        )
        await session.flush()
        session.add(
            Member(
                id=member_id,
                workspace_id=ws_id,
                member_type="human",
                user_id=user_id,
                role="admin",
                status="active",
            )
        )
        session.add(
            Member(
                id=agent_member_id,
                workspace_id=ws_id,
                member_type="agent",
                agent_id=agent_id,
                role="member",
                status="active",
            )
        )
    return {
        "ws_id": ws_id,
        "user_id": user_id,
        "member_id": member_id,
        "agent_id": agent_id,
        "agent_member_id": agent_member_id,
    }


async def make_runtime(
    session_factory,
    workspace_id: uuid.UUID,
    *,
    name: str | None = None,
    status: str = "online",
    kind: str = "self_hosted",
    labels: dict | None = None,
    capabilities: list | None = None,
    max_concurrent: int = 1,
    current_load: int = 0,
    last_heartbeat_at: datetime | None = None,
    created_by: uuid.UUID | None = None,
) -> Runtime:
    runtime = Runtime(
        workspace_id=workspace_id,
        name=name or f"build-{uuid.uuid4().hex[:6]}",
        kind=kind,
        status=status,
        labels=labels or {},
        capabilities=capabilities or [],
        max_concurrent=max_concurrent,
        current_load=current_load,
        last_heartbeat_at=last_heartbeat_at or datetime.now(UTC),
        created_by=created_by,
    )
    async with session_factory() as session, session.begin():
        session.add(runtime)
        await session.flush()
        session.expunge(runtime)
    return runtime


async def make_execution(
    session_factory,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    *,
    status: str = "queued",
    labels: dict | None = None,
    capabilities: list | None = None,
    priority: int = 100,
    task_spec: dict | None = None,
    config_snapshot: dict | None = None,
    idempotency_key: str | None = None,
    issue_id: uuid.UUID | None = None,
    max_attempts: int = 3,
) -> TaskExecution:
    execution = TaskExecution(
        workspace_id=workspace_id,
        agent_id=agent_id,
        issue_id=issue_id,
        status=status,
        priority=priority,
        label_requirements=labels or {},
        required_capabilities=capabilities or [],
        task_spec=task_spec or {},
        config_snapshot=config_snapshot or {},
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
    )
    async with session_factory() as session, session.begin():
        session.add(execution)
        await session.flush()
        session.expunge(execution)
    return execution


async def fetch_execution_finished_events(
    session_factory, workspace_id: uuid.UUID, execution_id: uuid.UUID
) -> list:
    """All ``execution.finished`` outbox rows for one execution (runtime.md §3.6)."""
    from sqlalchemy import select

    from mesh.db.models.outbox import OutboxEvent

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.workspace_id == workspace_id,
                    OutboxEvent.event_type == "execution.finished",
                )
            )
        ).scalars().all()
    return [
        row
        for row in rows
        if (row.payload or {}).get("execution_id") == str(execution_id)
    ]


async def assert_execution_finished_fanout(
    session_factory,
    workspace_id: uuid.UUID,
    execution_id: uuid.UUID,
    *,
    status: str,
    failure_reason: str | None = None,
) -> None:
    """Assert the §3.6 single terminal fan-out fired with the FULL five-field
    payload ``{execution_id, workspace_id, status, failure_reason, finished_at}``.

    Fails both when the event is absent (a terminal path that never emitted) and
    when it is present but partial (an emitter that drops workspace_id /
    finished_at) — the squad relay / result sink rely on the complete contract.
    """
    rows = await fetch_execution_finished_events(
        session_factory, workspace_id, execution_id
    )
    assert len(rows) == 1, (
        f"expected exactly one execution.finished outbox row for {execution_id}, "
        f"got {len(rows)}"
    )
    payload = rows[0].payload
    assert payload["execution_id"] == str(execution_id)
    assert payload["workspace_id"] == str(workspace_id)
    assert payload["status"] == status
    assert payload["failure_reason"] == failure_reason
    # A terminal transition always carries a finished_at timestamp.
    assert payload["finished_at"] is not None


async def issue_runtime_token(session_factory, runtime: Runtime) -> tuple[str, None]:
    """§2.4 S-11: set runtime_token_hash directly (single source of truth).

    No api_tokens row is created — runtime tokens live ONLY in
    runtimes.runtime_token_hash. Returns (plaintext, None) for backward
    compatibility with callers that unpack a tuple.
    """
    plaintext = RUNTIME_TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    async with session_factory() as session, session.begin():
        from sqlalchemy import update

        await session.execute(
            update(Runtime)
            .where(Runtime.id == runtime.id)
            .values(runtime_token_hash=token_hash)
        )
    runtime.runtime_token_hash = token_hash
    return plaintext, None
