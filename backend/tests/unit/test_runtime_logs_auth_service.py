"""Log streaming, daemon auth, and RuntimeService lifecycle tests.

Offset continuity + redaction-before-storage (logs), TLS/env-name/token
resolution gates (daemon auth), and the three-stage registration lifecycle
with token revocation linkage (service).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from mesh.db.models.api_token import RUNTIME_TOKEN_PREFIX, ApiToken
from mesh.db.models.runtime import Runtime, RuntimeCredential, TaskLogSegment
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    GoneError,
    UnauthorizedError,
)
from mesh.runtime.credentials import encrypt_credential_value
from mesh.runtime.daemon_auth import (
    validate_env_name,
    resolve_runtime_token,
)
from mesh.runtime.logs import append_log_lines, read_execution_logs
from mesh.runtime.service import RuntimeService, hash_activation_code

from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    issue_runtime_token,
    make_runtime,
    make_settings,
    seed_world,
)

pytestmark = pytest.mark.unit


def _service(session_factory, **overrides) -> RuntimeService:
    return RuntimeService(session_factory, make_settings(**overrides))


async def _claimed_attempt(session_factory, world):
    """Claim one execution; returns (runtime, result)."""
    from mesh.runtime.claim import claim_execution
    from tests.unit.runtime_support import make_execution

    runtime = await make_runtime(session_factory, world["ws_id"])
    await make_execution(session_factory, world["ws_id"], world["agent_id"])
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    return runtime, result


# ---------------------------------------------------------------------------
# logs: offset continuity, redaction, resume
# ---------------------------------------------------------------------------


async def test_log_append_continuity_redaction_and_resume(session_factory, object_storage):
    world = await seed_world(session_factory)
    runtime, result = await _claimed_attempt(session_factory, world)
    attempt_id = uuid.UUID(result.attempt["id"])
    execution_id = uuid.UUID(result.execution["id"])

    # A workspace secret must never reach storage / the wire.
    async with session_factory() as session, session.begin():
        session.add(
            RuntimeCredential(
                workspace_id=world["ws_id"],
                name="LEAKY",
                encrypted_value=encrypt_credential_value("super-secret-42", TEST_JWT_SECRET),
                redact_in_logs=True,
            )
        )

    first = await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["$ run tests", "token=super-secret-42"],
        signing_secret=TEST_JWT_SECRET,
    )
    assert first["redacted_hits"] == 1
    # Offsets count STORED (post-redaction) bytes — resume operates on the
    # content the client actually receives.
    assert first["accepted_end_offset"] == len("$ run tests\n") + len("token=***\n")

    # Wrong offset → 409 (daemon must resync).
    with pytest.raises(ConflictError) as exc:
        await append_log_lines(
            session_factory,
            object_storage,
            attempt_id=attempt_id,
            runtime=runtime,
            lease_seq=1,
            stream="stdout",
            start_offset=999,
            lines=["late"],
            signing_secret=TEST_JWT_SECRET,
        )
    assert exc.value.code == "offset_mismatch"

    # Continue from the accepted offset.
    second = await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stderr",
        start_offset=first["accepted_end_offset"],
        lines=["warning: deprecated"],
        signing_secret=TEST_JWT_SECRET,
    )

    # Resume read from 0 returns both segments in order, redacted.
    page = await read_execution_logs(
        session_factory,
        object_storage,
        workspace_id=world["ws_id"],
        execution_id=execution_id,
        offset=0,
    )
    lines = [item["line"] for item in page["lines"]]
    assert lines == ["$ run tests", "token=***", "warning: deprecated"]
    assert all("super-secret-42" not in item["line"] for item in page["lines"])
    assert page["next_offset"] == second["accepted_end_offset"]

    # Stream filter.
    stderr_page = await read_execution_logs(
        session_factory,
        object_storage,
        workspace_id=world["ws_id"],
        execution_id=execution_id,
        offset=0,
        stream="stderr",
    )
    assert [item["line"] for item in stderr_page["lines"]] == ["warning: deprecated"]

    # Resume from a middle offset skips earlier lines.
    partial = await read_execution_logs(
        session_factory,
        object_storage,
        workspace_id=world["ws_id"],
        execution_id=execution_id,
        offset=first["accepted_end_offset"],
    )
    assert [item["line"] for item in partial["lines"]] == ["warning: deprecated"]


async def test_log_append_empty_lines_noop(session_factory, object_storage):
    world = await seed_world(session_factory)
    runtime, result = await _claimed_attempt(session_factory, world)
    data = await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=uuid.UUID(result.attempt["id"]),
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=[],
        signing_secret=TEST_JWT_SECRET,
    )
    assert data["accepted_end_offset"] == 0
    async with session_factory() as session:
        assert (await session.execute(select(TaskLogSegment))).scalars().all() == []


async def test_log_append_terminal_attempt_rejected(session_factory, object_storage):
    world = await seed_world(session_factory)
    runtime, result = await _claimed_attempt(session_factory, world)
    from mesh.runtime.attempts import transition_attempt

    attempt_id = uuid.UUID(result.attempt["id"])
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="completed",
    )
    with pytest.raises(ConflictError):
        await append_log_lines(
            session_factory,
            object_storage,
            attempt_id=attempt_id,
            runtime=runtime,
            lease_seq=1,
            stream="stdout",
            start_offset=0,
            lines=["post-mortem"],
            signing_secret=TEST_JWT_SECRET,
        )


# ---------------------------------------------------------------------------
# daemon auth: env names, TLS gate, token resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["LD_PRELOAD", "PATH", "PYTHONPATH", "NODE_OPTIONS", "DYLD_INSERT_LIBRARIES",
             "MESH_DAEMON_TOKEN", "MESH_INTERNAL_X", "lower_case", "9STARTS_DIGIT", ""]
)
def test_reserved_env_names_rejected(name):
    with pytest.raises(BusinessRuleError) as exc:
        validate_env_name(name)
    assert exc.value.code == "reserved_env_name"


@pytest.mark.parametrize("name", ["REPO_TOKEN", "CI_API_KEY", "A", "MY_VAR_2"])
def test_valid_env_names_accepted(name):
    validate_env_name(name)  # must not raise


async def test_resolve_runtime_token_paths(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], created_by=world["member_id"])
    plaintext, _ = await issue_runtime_token(session_factory, runtime)

    resolved = await resolve_runtime_token(session_factory, plaintext)
    assert resolved.id == runtime.id

    with pytest.raises(UnauthorizedError):
        await resolve_runtime_token(session_factory, "wrong_prefix_token")
    with pytest.raises(UnauthorizedError):
        await resolve_runtime_token(session_factory, RUNTIME_TOKEN_PREFIX + "x" * 43)

    # Paused runtime → token dead (NEW-L2).
    service = _service(session_factory)
    await service.pause_runtime(workspace_id=world["ws_id"], runtime_id=runtime.id)
    with pytest.raises(UnauthorizedError):
        await resolve_runtime_token(session_factory, plaintext)


def test_tls_guard_refuses_plaintext_when_required():
    from mesh.runtime.daemon_auth import assert_daemon_tls
    from types import SimpleNamespace

    def req(scheme, headers=None, peer="203.0.113.9"):
        return SimpleNamespace(
            url=SimpleNamespace(scheme=scheme),
            headers=headers or {},
            client=SimpleNamespace(host=peer),
        )

    with pytest.raises(ForbiddenError):
        assert_daemon_tls(req("http"), tls_required=True)
    # HTTPS direct passes.
    assert_daemon_tls(req("https"), tls_required=True)
    # M3: X-Forwarded-Proto from a TRUSTED proxy (loopback default) passes...
    assert_daemon_tls(
        req("http", {"x-forwarded-proto": "https"}, peer="127.0.0.1"), tls_required=True
    )
    # ...but the same header from an arbitrary client is spoofed → 403.
    with pytest.raises(ForbiddenError):
        assert_daemon_tls(req("http", {"x-forwarded-proto": "https"}), tls_required=True)
    # Configured proxy list is honored.
    assert_daemon_tls(
        req("http", {"x-forwarded-proto": "https"}, peer="10.9.8.7"),
        tls_required=True,
        trusted_proxies="10.9.8.7",
    )
    assert_daemon_tls(req("http"), tls_required=False)


# ---------------------------------------------------------------------------
# RuntimeService lifecycle: three-stage registration
# ---------------------------------------------------------------------------


async def test_create_activate_flow(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    member_stub = await _member(session_factory, world["member_id"])

    created = await service.create_runtime(
        workspace_id=world["ws_id"],
        member=member_stub,
        name="intranet-build-01",
        labels={"region": "intranet"},
        max_concurrent=4,
    )
    assert created["status"] == "pending"
    code = created["activation"]["code"]
    assert code.startswith("ACT-")
    assert created["activation"]["release"]["artifact_url"]
    # Only the hash is stored.
    async with session_factory() as session:
        runtime = (await session.execute(select(Runtime))).scalar_one()
    assert runtime.activation_token_hash == hash_activation_code(code)
    assert "ACT-" not in (runtime.activation_token_hash or "")

    activated = await service.activate_runtime(
        activation_code=code,
        metadata={
            "hostname": "build-node-7",
            "os": "linux-x86_64",
            "cpu_cores": 8,
            "memory_mb": 32768,
            "capabilities": ["version_control", "python"],
            "labels": {"gpu": "false"},
            "version": "1.4.2",
        },
    )
    assert activated["runtime_id"] == str(runtime.id)
    assert activated["runtime_token"].startswith(RUNTIME_TOKEN_PREFIX)
    async with session_factory() as session:
        runtime = (await session.execute(select(Runtime))).scalar_one()
        token = (await session.execute(select(ApiToken))).scalar_one()
    assert runtime.status == "online"
    assert runtime.activated_at is not None  # non-null = code consumed
    # Hash remains (replay resolves to 410), plaintext is never stored.
    assert runtime.activation_token_hash == hash_activation_code(code)
    assert runtime.hostname == "build-node-7"
    assert runtime.labels == {"region": "intranet", "gpu": "false"}  # merged
    assert runtime.capabilities == ["version_control", "python"]
    assert token.token_hash == hashlib.sha256(
        activated["runtime_token"].encode()
    ).hexdigest()
    assert "runtime" in token.scopes


async def test_activation_expired_and_used_410(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory, runtime_activation_ttl=timedelta(seconds=1))
    member_stub = await _member(session_factory, world["member_id"])
    created = await service.create_runtime(
        workspace_id=world["ws_id"], member=member_stub, name="exp-rt"
    )
    # Force-expire the code.
    from sqlalchemy import update

    async with session_factory() as session, session.begin():
        await session.execute(
            update(Runtime)
            .where(Runtime.id == uuid.UUID(created["id"]))
            .values(activation_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
    with pytest.raises(GoneError) as exc:
        await service.activate_runtime(
            activation_code=created["activation"]["code"], metadata={}
        )
    assert exc.value.code == "activation_expired"

    with pytest.raises(UnauthorizedError):
        await service.activate_runtime(activation_code="ACT-NOPE-NOPE-NOPE", metadata={})


async def test_activated_code_cannot_be_reused(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    member_stub = await _member(session_factory, world["member_id"])
    created = await service.create_runtime(
        workspace_id=world["ws_id"], member=member_stub, name="reuse-rt"
    )
    code = created["activation"]["code"]
    await service.activate_runtime(activation_code=code, metadata={})
    with pytest.raises(GoneError):
        await service.activate_runtime(activation_code=code, metadata={})


async def test_heartbeat_recovers_offline_and_degraded_stops_dispatch(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], status="unavailable", created_by=world["member_id"]
    )
    data = await service.heartbeat(
        runtime=runtime, current_load=1, health="healthy", metrics={"cpu": 10}, inflight=[]
    )
    assert data["server_time"]
    async with session_factory() as session:
        fresh = await session.get(Runtime, runtime.id)
    assert fresh.status == "online"

    await service.heartbeat(
        runtime=runtime, current_load=1, health="degraded", metrics={}, inflight=[]
    )
    async with session_factory() as session:
        fresh = await session.get(Runtime, runtime.id)
    assert fresh.status == "unavailable"  # alive but not dispatchable


async def test_pause_revokes_token_rotate_issues_new(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], created_by=world["member_id"])
    old_plain, old_token = await issue_runtime_token(session_factory, runtime)

    await service.pause_runtime(workspace_id=world["ws_id"], runtime_id=runtime.id)
    async with session_factory() as session:
        revoked = await session.get(ApiToken, old_token.id)
        fresh = await session.get(Runtime, runtime.id)
    assert revoked.revoked_at is not None
    assert fresh.status == "paused"

    rotated = await service.rotate_runtime_token(
        workspace_id=world["ws_id"], runtime_id=runtime.id
    )
    assert rotated["runtime_token"].startswith(RUNTIME_TOKEN_PREFIX)
    assert rotated["runtime_token"] != old_plain
    async with session_factory() as session:
        fresh = await session.get(Runtime, runtime.id)
    assert fresh.runtime_token_hash == hashlib.sha256(
        rotated["runtime_token"].encode()
    ).hexdigest()


async def test_credentials_crud_plaintext_never_returned(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    created = await service.create_credential(
        workspace_id=world["ws_id"],
        name="REPO_TOKEN",
        kind="repo_token",
        scope="execution",
        value="ghp_secret_value",
        env_name="REPO_TOKEN",
        redact_in_logs=True,
        expires_in_seconds=3600,
    )
    assert created["value"] == "***"
    assert "ghp_secret_value" not in str(created)
    listing = await service.list_credentials(workspace_id=world["ws_id"])
    assert len(listing["data"]) == 1
    assert listing["data"][0]["value"] == "***"
    await service.delete_credential(
        workspace_id=world["ws_id"], credential_id=uuid.UUID(created["id"])
    )
    listing = await service.list_credentials(workspace_id=world["ws_id"])
    assert listing["data"] == []


async def test_list_runtimes_filters_and_pagination(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    for i in range(3):
        await make_runtime(session_factory, world["ws_id"], name=f"rt-{i}")
    page = await service.list_runtimes(workspace_id=world["ws_id"], limit=2)
    assert len(page["data"]) == 2
    assert page["next_cursor"] is not None
    page2 = await service.list_runtimes(
        workspace_id=world["ws_id"], limit=2, cursor=page["next_cursor"]
    )
    assert len(page2["data"]) == 1
    assert page2["next_cursor"] is None
    named = await service.list_runtimes(workspace_id=world["ws_id"], search="rt-1")
    assert [r["name"] for r in named["data"]] == ["rt-1"]


async def test_cross_workspace_runtime_invisible(session_factory):
    world_a = await seed_world(session_factory)
    world_b = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(session_factory, world_a["ws_id"])
    from mesh.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await service.get_runtime(workspace_id=world_b["ws_id"], runtime_id=runtime.id)


async def _member(session_factory, member_id):
    from mesh.db.models.member import Member

    async with session_factory() as session:
        member = await session.get(Member, member_id)
        session.expunge(member)
    return member
