"""Real DB tests for P0 contract modules — MES-98 R4.

Covers task_tokens, redaction (result/diff), result_sink, and
task principal auth with real PostgreSQL (no mocks on contract paths).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.runtime import (
    AttemptTaskToken,
    ExecutionAttempt,
    TaskExecution,
)
from mesh.runtime.task_tokens import (
    _hash_token,
    issue_task_token,
    revoke_attempt_task_tokens,
    validate_task_token,
)
from tests.unit.runtime_support import make_execution, make_runtime, seed_world

# ---------------------------------------------------------------------------
# Task token lifecycle — real DB (§2.2 S-05)
# ---------------------------------------------------------------------------


class TestTaskTokenLifecycleDB:
    """Real DB tests for task token issue/validate/revoke."""

    async def _setup_attempt(self, session_factory, world):
        """Create a runtime + execution + attempt for task token tests."""
        runtime = await make_runtime(
            session_factory, world["ws_id"], created_by=world["member_id"]
        )
        execution = await make_execution(
            session_factory, world["ws_id"], world["agent_id"],
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            attempt = ExecutionAttempt(
                workspace_id=world["ws_id"],
                execution_id=execution.id,
                attempt_number=1,
                runtime_id=runtime.id,
                claimed_by_runtime_id=runtime.id,
                status="running",
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                claimed_at=datetime.now(UTC),
            )
            session.add(attempt)
            await session.flush()
            session.expunge(attempt)
        return runtime, execution, attempt

    async def test_issue_and_validate_task_token(self, session_factory):
        """Issue a task token and validate it — full round-trip."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            plaintext, token_row = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                issue_id=execution.issue_id,
                agent_id=world["agent_id"],
            )
        assert plaintext.startswith("mesh_task_")
        assert token_row.token_hash == _hash_token(plaintext)
        assert token_row.revoked_at is None

        # Validate the token.
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            validated = await validate_task_token(
                session,
                token=plaintext,
                attempt_id=attempt.id,
                lease_seq=1,
                runtime_id=runtime.id,
            )
        assert validated.attempt_id == attempt.id
        assert validated.runtime_id == runtime.id

    async def test_revoke_on_terminal(self, session_factory):
        """Revoke task tokens when attempt goes terminal."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            plaintext, _ = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        # Revoke.
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            count = await revoke_attempt_task_tokens(
                session, attempt_id=attempt.id
            )
        assert count == 1

        # Token should now be rejected.
        from mesh.errors import UnauthorizedError
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            with pytest.raises(UnauthorizedError, match="revoked"):
                await validate_task_token(session, token=plaintext)

    async def test_rotation_revokes_old(self, session_factory):
        """Issuing a new token revokes the old one (rotation on renew)."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            old_plain, _ = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        # Issue a second token (rotation) — advance attempt lease_seq first.
        from sqlalchemy import update as sql_update
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            await session.execute(
                sql_update(ExecutionAttempt)
                .where(ExecutionAttempt.id == attempt.id)
                .values(lease_seq=2)
            )
            new_plain, _ = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=2,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        assert new_plain != old_plain

        # Old token should be revoked.
        from mesh.errors import UnauthorizedError
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            with pytest.raises(UnauthorizedError, match="revoked"):
                await validate_task_token(session, token=old_plain)
            # New token should work.
            validated = await validate_task_token(
                session, token=new_plain, lease_seq=2
            )
            assert validated.lease_seq == 2

    async def test_validate_rejects_wrong_lease_seq(self, session_factory):
        """Token with mismatched lease_seq is rejected."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            plaintext, _ = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        from mesh.errors import UnauthorizedError
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            with pytest.raises(UnauthorizedError, match="lease_seq"):
                await validate_task_token(
                    session, token=plaintext, lease_seq=999
                )

    async def test_validate_rejects_wrong_runtime(self, session_factory):
        """Token with mismatched runtime_id is rejected."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            plaintext, _ = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        from mesh.errors import UnauthorizedError
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            with pytest.raises(UnauthorizedError, match="runtime"):
                await validate_task_token(
                    session, token=plaintext, runtime_id=uuid.uuid4()
                )

    async def test_validate_rejects_terminal_attempt(self, session_factory):
        """Token for a terminal attempt is rejected."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            plaintext, _ = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        # Mark attempt as completed.
        from sqlalchemy import update
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            await session.execute(
                update(ExecutionAttempt)
                .where(ExecutionAttempt.id == attempt.id)
                .values(status="completed")
            )
        from mesh.errors import UnauthorizedError
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            with pytest.raises(UnauthorizedError, match="not in flight"):
                await validate_task_token(session, token=plaintext)

    async def test_scope_denies_agent_trigger(self, session_factory):
        """agent:trigger is in the denied list by default."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            plaintext, _ = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        from mesh.errors import UnauthorizedError
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            with pytest.raises(UnauthorizedError, match="scope"):
                await validate_task_token(
                    session, token=plaintext, required_scope="agent:trigger"
                )

    async def test_scope_allows_issue_read(self, session_factory):
        """issue:read is in the allowed methods by default."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            plaintext, _ = await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            validated = await validate_task_token(
                session, token=plaintext, required_scope="issue:read"
            )
        assert validated is not None

    async def test_only_one_active_token_per_attempt(self, session_factory):
        """Partial unique index: only one non-revoked token per attempt."""
        world = await seed_world(session_factory)
        runtime, execution, attempt = await self._setup_attempt(
            session_factory, world
        )
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            await issue_task_token(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        # Count active tokens.
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            active = (
                await session.execute(
                    select(AttemptTaskToken).where(
                        AttemptTaskToken.attempt_id == attempt.id,
                        AttemptTaskToken.revoked_at.is_(None),
                    )
                )
            ).scalars().all()
        assert len(active) == 1


# ---------------------------------------------------------------------------
# Redaction — real DB (§2.5 S-06)
# ---------------------------------------------------------------------------


class TestRedactionDB:
    """Real DB tests for server-side fallback redaction."""

    async def test_redact_result_cleans_secrets(self, session_factory):
        """redact_result replaces workspace secrets in result dict."""
        from mesh.runtime.redaction import redact_result

        world = await seed_world(session_factory)
        # Create a credential with redact_in_logs=True.
        import os

        from mesh.config import load_settings
        from mesh.db.models.runtime import RuntimeCredential
        from mesh.runtime.credentials import encrypt_credential_value

        settings = load_settings(
            database_url=os.environ.get(
                "MESH_TEST_DATABASE_URL",
                "postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test",
            ),
            redis_url=os.environ.get(
                "MESH_TEST_REDIS_URL", "redis://127.0.0.1:6390/1"
            ),
            jwt_secret="test-redaction-secret-000000000000",
        )
        secret_value = "ghp_SUPER_SECRET_TOKEN_12345"
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            cred = RuntimeCredential(
                workspace_id=world["ws_id"],
                name="TEST_SECRET",
                kind="env",
                scope="execution",
                encrypted_value=encrypt_credential_value(
                    secret_value, settings.jwt_secret
                ),
                env_name="TEST_SECRET",
                redact_in_logs=True,
            )
            session.add(cred)

        # Result containing the secret.
        result = {"output": f"Done! Token is {secret_value}"}
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            redacted, hits = await redact_result(
                session,
                workspace_id=world["ws_id"],
                result=result,
                signing_secret=settings.jwt_secret,
            )
        assert hits > 0
        assert secret_value not in str(redacted)

    async def test_redact_result_clean_passthrough(self, session_factory):
        """Clean result passes through unchanged."""
        import os

        from mesh.config import load_settings
        from mesh.runtime.redaction import redact_result

        world = await seed_world(session_factory)
        settings = load_settings(
            database_url=os.environ.get(
                "MESH_TEST_DATABASE_URL",
                "postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test",
            ),
            redis_url=os.environ.get(
                "MESH_TEST_REDIS_URL", "redis://127.0.0.1:6390/1"
            ),
            jwt_secret="test-redaction-secret-000000000000",
        )
        result = {"output": "All good, no secrets here"}
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            redacted, hits = await redact_result(
                session,
                workspace_id=world["ws_id"],
                result=result,
                signing_secret=settings.jwt_secret,
            )
        assert hits == 0
        assert redacted == result

    async def test_redact_diff_text_cleans_secrets(self, session_factory):
        """redact_diff_text replaces secrets in diff content."""
        import os

        from mesh.config import load_settings
        from mesh.db.models.runtime import RuntimeCredential
        from mesh.runtime.credentials import encrypt_credential_value
        from mesh.runtime.redaction import redact_diff_text

        world = await seed_world(session_factory)
        settings = load_settings(
            database_url=os.environ.get(
                "MESH_TEST_DATABASE_URL",
                "postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test",
            ),
            redis_url=os.environ.get(
                "MESH_TEST_REDIS_URL", "redis://127.0.0.1:6390/1"
            ),
            jwt_secret="test-redaction-secret-000000000000",
        )
        secret_value = "sk-live-SECRET-KEY-99999"
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            cred = RuntimeCredential(
                workspace_id=world["ws_id"],
                name="DIFF_SECRET",
                kind="env",
                scope="execution",
                encrypted_value=encrypt_credential_value(
                    secret_value, settings.jwt_secret
                ),
                env_name="DIFF_SECRET",
                redact_in_logs=True,
            )
            session.add(cred)

        diff = f"+api_key = '{secret_value}'\n+endpoint = 'https://api.example.com'"
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            redacted, hits = await redact_diff_text(
                session,
                workspace_id=world["ws_id"],
                diff=diff,
                signing_secret=settings.jwt_secret,
            )
        assert hits > 0
        assert secret_value not in redacted


# ---------------------------------------------------------------------------
# Result sink — real DB (§3.7 S-09)
# ---------------------------------------------------------------------------


class TestResultSinkDB:
    """Real DB tests for execution result sink."""

    async def test_result_sink_skips_squad(self, session_factory):
        """Result sink skips squad executions."""
        from mesh.db.models.outbox import OutboxEvent
        from mesh.runtime.result_sink import execution_finished_result_sink

        world = await seed_world(session_factory)
        execution = await make_execution(
            session_factory, world["ws_id"], world["agent_id"],
            task_spec={"squad_task_id": str(uuid.uuid4())},
        )
        # Mark execution as completed.
        from sqlalchemy import update
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            await session.execute(
                update(TaskExecution)
                .where(TaskExecution.id == execution.id)
                .values(status="completed")
            )

        event = OutboxEvent(
            workspace_id=world["ws_id"],
            event_type="execution.finished",
            payload={
                "execution_id": str(execution.id),
                "status": "completed",
            },
        )
        async with session_factory() as session:
            result = await execution_finished_result_sink(session, event)
        # Should return None (skipped) for squad executions.
        assert result is None

    async def test_result_sink_skips_issueless(self, session_factory):
        """Result sink skips executions without an issue."""
        from mesh.db.models.outbox import OutboxEvent
        from mesh.runtime.result_sink import execution_finished_result_sink

        world = await seed_world(session_factory)
        execution = await make_execution(
            session_factory, world["ws_id"], world["agent_id"],
            issue_id=None,
        )
        event = OutboxEvent(
            workspace_id=world["ws_id"],
            event_type="execution.finished",
            payload={
                "execution_id": str(execution.id),
                "status": "completed",
            },
        )
        async with session_factory() as session:
            result = await execution_finished_result_sink(session, event)
        assert result is None

    async def test_build_result_summary_variants(self):
        """Result summary builder covers all status variants."""
        from mesh.runtime.result_sink import _build_result_summary

        assert "completed" in _build_result_summary(
            "completed", {"output": "Done!"}, None
        ).lower() or "Done!" in _build_result_summary(
            "completed", {"output": "Done!"}, None
        )
        assert "failed" in _build_result_summary("failed", {}, "oom")
        assert "timeout" in _build_result_summary("timeout", {}, None)
        assert "cancelled" in _build_result_summary("cancelled", {}, None).lower()

    async def test_result_sink_skips_non_assign_triggers(self, session_factory):
        """§3.7 S-09: chat/autopilot/manual triggers don't get result comments."""
        from mesh.db.models.outbox import OutboxEvent
        from mesh.runtime.result_sink import execution_finished_result_sink

        world = await seed_world(session_factory)
        # chat trigger — has independent closure path.
        execution = await make_execution(
            session_factory, world["ws_id"], world["agent_id"],
        )
        # Override trigger to "chat".
        from sqlalchemy import update as sql_update
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            await session.execute(
                sql_update(TaskExecution)
                .where(TaskExecution.id == execution.id)
                .values(trigger="chat", status="completed")
            )
        event = OutboxEvent(
            workspace_id=world["ws_id"],
            event_type="execution.finished",
            payload={
                "execution_id": str(execution.id),
                "status": "completed",
            },
        )
        async with session_factory() as session:
            result = await execution_finished_result_sink(session, event)
        assert result is None

    async def test_result_sink_skips_stub_result(self, session_factory):
        """Completed execution with no output/summary → no result comment."""
        from mesh.db.models.outbox import OutboxEvent
        from mesh.runtime.result_sink import execution_finished_result_sink

        world = await seed_world(session_factory)
        execution = await make_execution(
            session_factory, world["ws_id"], world["agent_id"],
        )
        # Set trigger=assign, status=completed, result={"exit_code": 0}.
        from sqlalchemy import update as sql_update
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            await session.execute(
                sql_update(TaskExecution)
                .where(TaskExecution.id == execution.id)
                .values(
                    trigger="assign",
                    status="completed",
                    result={"exit_code": 0},
                )
            )
        event = OutboxEvent(
            workspace_id=world["ws_id"],
            event_type="execution.finished",
            payload={
                "execution_id": str(execution.id),
                "status": "completed",
            },
        )
        async with session_factory() as session:
            result = await execution_finished_result_sink(session, event)
        # Stub result (no output) → skipped.
        assert result is None


# ---------------------------------------------------------------------------
# Task principal auth — real DB (§2.2 S-05 / auth.md §2.5.1)
# ---------------------------------------------------------------------------


class TestTaskPrincipalDB:
    """Real DB tests for resolve_task_principal dependency."""

    async def test_resolve_task_principal_rejects_non_task_token(self):
        """Non-mesh_task_ tokens are rejected."""
        from unittest.mock import MagicMock

        from mesh.errors import UnauthorizedError
        from mesh.runtime.daemon_auth import resolve_task_principal

        request = MagicMock()
        request.headers = {"authorization": "Bearer mesh_pat_some_token"}
        with pytest.raises(UnauthorizedError, match="not a task token"):
            await resolve_task_principal(request)

    async def test_resolve_task_principal_rejects_missing_bearer(self):
        """Missing bearer token is rejected."""
        from unittest.mock import MagicMock

        from mesh.errors import UnauthorizedError
        from mesh.runtime.daemon_auth import resolve_task_principal

        request = MagicMock()
        request.headers = {}
        with pytest.raises(UnauthorizedError, match="missing bearer"):
            await resolve_task_principal(request)

    async def test_task_token_not_in_api_tokens(self, session_factory):
        """§2.4 S-11 negative: mesh_task_ prefix never enters api_tokens."""
        from mesh.db.models.api_token import ApiToken

        world = await seed_world(session_factory)
        async with session_factory() as session:
            from mesh.db.tenant import set_tenant_context
            await set_tenant_context(session, world["ws_id"])
            tokens = (
                await session.execute(select(ApiToken))
            ).scalars().all()
        for token in tokens:
            assert not token.prefix.startswith("mesh_task_")
            assert not token.prefix.startswith("mesh_rt_")


# ---------------------------------------------------------------------------
# Snapshot digest (§2.1)
# ---------------------------------------------------------------------------


class TestSnapshotDigest:
    """AttemptSpec digest computation and verification."""

    def test_digest_deterministic(self):
        from mesh.agent.snapshot import build_config_snapshot

        r1 = build_config_snapshot(
            agent_config_version_id=uuid.uuid4(),
            trigger_event_id=uuid.uuid4(),
            provider="claude-code",
            model="claude-sonnet-4-20250514",
        )
        r2 = build_config_snapshot(
            agent_config_version_id=uuid.UUID(
                r1["config_snapshot"]["agent_config_version_id"]
            ),
            trigger_event_id=uuid.UUID(
                r1["config_snapshot"]["trigger_event_id"]
            ),
            provider="claude-code",
            model="claude-sonnet-4-20250514",
        )
        assert r1["config_snapshot"]["digest"] == r2["config_snapshot"]["digest"]

    def test_digest_changes_with_content(self):
        from mesh.agent.snapshot import build_config_snapshot

        tid = uuid.uuid4()
        r1 = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=tid,
            provider="claude-code",
        )
        r2 = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=tid,
            provider="different-provider",
        )
        assert r1["config_snapshot"]["digest"] != r2["config_snapshot"]["digest"]

    def test_digest_is_sha256_hex(self):
        from mesh.agent.snapshot import build_config_snapshot

        result = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=uuid.uuid4(),
        )
        digest = result["config_snapshot"]["digest"]
        assert len(digest) == 64  # SHA-256 hex length
        int(digest, 16)  # valid hex
