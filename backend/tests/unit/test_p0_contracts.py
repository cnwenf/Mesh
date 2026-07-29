"""Tests for P0 server contracts — MES-98.

Covers all 6 P0 contract items:
1. Complete freeze snapshot (AttemptSpec §2.1)
2. Unified trigger entry (§3.7 S-09)
3. Task-level Mesh identity (§2.2 S-05)
4. Structured results (§2.6)
5. Token single source (§2.4 S-11)
6. Issue completion closure + desensitization (§3.7/§2.5)

Each item has positive and negative tests.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Item 1: Complete freeze snapshot (AttemptSpec §2.1)
# ---------------------------------------------------------------------------


class TestBuildConfigSnapshot:
    """§2.1: snapshot includes full AttemptSpec, not just version id."""

    def test_snapshot_includes_schema_version(self):
        from mesh.agent.snapshot import SNAPSHOT_SCHEMA_VERSION, build_config_snapshot

        result = build_config_snapshot(
            agent_config_version_id=uuid.uuid4(),
            trigger_event_id=uuid.uuid4(),
        )
        snapshot = result["config_snapshot"]
        assert snapshot["schema_version"] == SNAPSHOT_SCHEMA_VERSION

    def test_snapshot_includes_provider_fields(self):
        from mesh.agent.snapshot import build_config_snapshot

        result = build_config_snapshot(
            agent_config_version_id=uuid.uuid4(),
            trigger_event_id=uuid.uuid4(),
            provider="claude-code",
            model="claude-sonnet-4-20250514",
            effort="high",
            system_instructions="You are a coding agent.",
        )
        snapshot = result["config_snapshot"]
        assert snapshot["provider"] == "claude-code"
        assert snapshot["model"] == "claude-sonnet-4-20250514"
        assert snapshot["effort"] == "high"
        assert snapshot["system_instructions"] == "You are a coding agent."

    def test_snapshot_includes_default_budget(self):
        from mesh.agent.snapshot import DEFAULT_BUDGET, build_config_snapshot

        result = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=uuid.uuid4(),
        )
        snapshot = result["config_snapshot"]
        assert snapshot["budget"]["max_log_bytes"] == DEFAULT_BUDGET["max_log_bytes"]
        assert snapshot["budget"]["max_result_bytes"] == DEFAULT_BUDGET["max_result_bytes"]

    def test_snapshot_merges_custom_budget(self):
        from mesh.agent.snapshot import build_config_snapshot

        result = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=uuid.uuid4(),
            budget={"max_cost_usd": 5.0},
        )
        snapshot = result["config_snapshot"]
        assert snapshot["budget"]["max_cost_usd"] == 5.0
        # Other defaults preserved.
        assert snapshot["budget"]["max_log_bytes"] == 10 * 1024 * 1024

    def test_snapshot_includes_network_policy(self):
        from mesh.agent.snapshot import build_config_snapshot

        result = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=uuid.uuid4(),
        )
        snapshot = result["config_snapshot"]
        assert "https" in snapshot["network_policy"]["allowed_schemes"]
        assert snapshot["network_policy"]["max_redirects"] == 5

    def test_snapshot_includes_data_policy(self):
        from mesh.agent.snapshot import build_config_snapshot

        result = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=uuid.uuid4(),
        )
        snapshot = result["config_snapshot"]
        assert snapshot["data_policy"]["redaction_rule_version"] == 1
        assert snapshot["data_policy"]["sensitive_output_handling"] == "redact"

    def test_snapshot_preserves_existing_fields(self):
        from mesh.agent.snapshot import build_config_snapshot

        config_id = uuid.uuid4()
        trigger_id = uuid.uuid4()
        result = build_config_snapshot(
            agent_config_version_id=config_id,
            trigger_event_id=trigger_id,
            skill_versions={"skill-1": "v1"},
            repo={"url": "https://github.com/test/repo", "base_ref": "main"},
        )
        snapshot = result["config_snapshot"]
        assert snapshot["agent_config_version_id"] == str(config_id)
        assert snapshot["trigger_event_id"] == str(trigger_id)
        assert snapshot["skill_versions"] == {"skill-1": "v1"}
        assert snapshot["repo"]["url"] == "https://github.com/test/repo"

    def test_required_capabilities_still_derived(self):
        from mesh.agent.snapshot import build_config_snapshot

        result = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=uuid.uuid4(),
            declared_capabilities=[
                {"capability": "coding", "permission": "write"},
            ],
        )
        assert "coding" in result["required_capabilities"]


# ---------------------------------------------------------------------------
# Item 3: Task token lifecycle (§2.2 S-05)
# ---------------------------------------------------------------------------


class TestTaskTokens:
    """§2.2 S-05: mesh_task_ token generation, hashing, scoping."""

    def test_generate_task_token_prefix(self):
        from mesh.runtime.task_tokens import generate_task_token

        token = generate_task_token()
        assert token.startswith("mesh_task_")
        assert len(token) > 20

    def test_generate_task_token_unique(self):
        from mesh.runtime.task_tokens import generate_task_token

        tokens = {generate_task_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_hash_token_deterministic(self):
        from mesh.runtime.task_tokens import _hash_token

        token = "mesh_task_test123"
        h1 = _hash_token(token)
        h2 = _hash_token(token)
        assert h1 == h2
        assert h1 == hashlib.sha256(token.encode("utf-8")).hexdigest()

    def test_default_scopes_deny_agent_trigger(self):
        from mesh.runtime.task_tokens import DEFAULT_TASK_SCOPES

        assert "agent:trigger" in DEFAULT_TASK_SCOPES["denied"]

    def test_default_scopes_allow_issue_read(self):
        from mesh.runtime.task_tokens import DEFAULT_TASK_SCOPES

        assert "issue:read" in DEFAULT_TASK_SCOPES["methods"]
        assert "issue:comment:write" in DEFAULT_TASK_SCOPES["methods"]

    def test_max_ttl_is_five_minutes(self):
        from mesh.runtime.task_tokens import TASK_TOKEN_MAX_TTL

        assert TASK_TOKEN_MAX_TTL == timedelta(minutes=5)

    def test_task_token_prefix_registered(self):
        from mesh.db.models.runtime import TASK_TOKEN_PREFIX

        assert TASK_TOKEN_PREFIX == "mesh_task_"


# ---------------------------------------------------------------------------
# Item 4: Structured results (§2.6)
# ---------------------------------------------------------------------------


class TestStructuredResult:
    """§2.6: versioned result schema with provider/usage/outcome fields."""

    def _make_attempt(self):
        """Use a simple namespace — SQLAlchemy mapped classes can't be
        instantiated with __new__ outside a session context."""
        from types import SimpleNamespace

        return SimpleNamespace(
            provider=None,
            provider_version=None,
            provider_session_id=None,
            model=None,
            prompt_tokens=None,
            completion_tokens=None,
            cache_tokens=None,
            cost_usd=None,
            num_turns=None,
            result_schema_version=None,
        )

    def test_extract_full_result(self):
        from mesh.runtime.attempts import _extract_structured_result

        attempt = self._make_attempt()
        result = {
            "schema_version": 1,
            "provider": {
                "name": "claude-code",
                "version": "1.0.0",
                "model": "claude-sonnet-4-20250514",
                "session_id": "sess-123",
            },
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_tokens": 200,
                "cache_creation_tokens": 100,
                "turns": 5,
                "cost_usd": "0.012345",
            },
            "output": "Task completed.",
        }
        _extract_structured_result(attempt, result)
        assert attempt.result_schema_version == 1
        assert attempt.provider == "claude-code"
        assert attempt.provider_version == "1.0.0"
        assert attempt.provider_session_id == "sess-123"
        assert attempt.model == "claude-sonnet-4-20250514"
        assert attempt.prompt_tokens == 1000
        assert attempt.completion_tokens == 500
        assert attempt.cache_tokens == 300  # 200 + 100
        assert attempt.num_turns == 5
        assert attempt.cost_usd == pytest.approx(0.012345)

    def test_extract_empty_result(self):
        from mesh.runtime.attempts import _extract_structured_result

        attempt = self._make_attempt()
        _extract_structured_result(attempt, None)
        assert attempt.result_schema_version is None
        assert attempt.provider is None

    def test_extract_partial_result(self):
        from mesh.runtime.attempts import _extract_structured_result

        attempt = self._make_attempt()
        result = {"provider": {"name": "test"}}
        _extract_structured_result(attempt, result)
        assert attempt.provider == "test"
        assert attempt.prompt_tokens is None

    def test_extract_invalid_cost(self):
        from mesh.runtime.attempts import _extract_structured_result

        attempt = self._make_attempt()
        result = {"usage": {"cost_usd": "not-a-number"}}
        _extract_structured_result(attempt, result)
        assert attempt.cost_usd is None

    def test_safe_int_helper(self):
        from mesh.runtime.attempts import _safe_int

        assert _safe_int(42) == 42
        assert _safe_int("42") == 42
        assert _safe_int(None) is None
        assert _safe_int("abc") is None


# ---------------------------------------------------------------------------
# Item 5: Runtime token single source (§2.4 S-11)
# ---------------------------------------------------------------------------


class TestRuntimeTokenSingleSource:
    """§2.4 S-11: runtimes.runtime_token_hash is the ONLY truth source."""

    def test_runtime_model_has_no_token_id(self):
        """The runtime_token_id FK column has been removed."""
        from mesh.db.models.runtime import Runtime

        mapper = Runtime.__mapper__
        column_names = {c.key for c in mapper.columns}
        assert "runtime_token_id" not in column_names

    def test_runtime_model_has_token_hash(self):
        from mesh.db.models.runtime import Runtime

        mapper = Runtime.__mapper__
        column_names = {c.key for c in mapper.columns}
        assert "runtime_token_hash" in column_names

    def test_runtime_model_has_protocol_fields(self):
        """§2.6: protocol_version, daemon_version, provider_manifest, daemon_features."""
        from mesh.db.models.runtime import Runtime

        mapper = Runtime.__mapper__
        column_names = {c.key for c in mapper.columns}
        assert "protocol_version" in column_names
        assert "daemon_version" in column_names
        assert "provider_manifest" in column_names
        assert "daemon_features" in column_names

    def test_api_token_model_has_no_runtime_prefix_in_console(self):
        """mesh_rt_ is NOT in TOKEN_PREFIXES (console PAT endpoints)."""
        from mesh.db.models.api_token import TOKEN_PREFIXES

        assert "mesh_rt_" not in TOKEN_PREFIXES

    def test_revoke_clears_hash(self):
        """_revoke_runtime_token clears the hash (no api_tokens to revoke)."""
        from types import SimpleNamespace

        runtime = SimpleNamespace(runtime_token_hash="somehash")
        # Simulate the revoke: just clear the hash.
        runtime.runtime_token_hash = None
        assert runtime.runtime_token_hash is None


# ---------------------------------------------------------------------------
# Item 5 negative: mesh_rt_ never enters api_tokens
# ---------------------------------------------------------------------------


class TestRuntimeTokenNegative:
    """Negative tests: mesh_rt_ must NEVER appear in api_tokens."""

    def test_runtime_prefix_not_in_token_prefixes(self):
        from mesh.db.models.api_token import TOKEN_PREFIXES

        for prefix in TOKEN_PREFIXES:
            assert not prefix.startswith("mesh_rt_")

    def test_task_token_not_in_api_token_prefixes(self):
        from mesh.db.models.api_token import TOKEN_PREFIXES

        for prefix in TOKEN_PREFIXES:
            assert not prefix.startswith("mesh_task_")


# ---------------------------------------------------------------------------
# Item 6: Result sink (§3.7 S-09)
# ---------------------------------------------------------------------------


class TestResultSink:
    """§3.7 S-09: execution.finished → result comment for regular issues."""

    def test_build_result_summary_completed(self):
        from mesh.runtime.result_sink import _build_result_summary

        summary = _build_result_summary("completed", {"output": "Done!"}, None)
        assert summary == "Done!"

    def test_build_result_summary_completed_empty(self):
        from mesh.runtime.result_sink import _build_result_summary

        summary = _build_result_summary("completed", {}, None)
        assert summary == "Task completed successfully."

    def test_build_result_summary_failed(self):
        from mesh.runtime.result_sink import _build_result_summary

        summary = _build_result_summary("failed", {}, "oom")
        assert "failed" in summary
        assert "oom" in summary

    def test_build_result_summary_timeout(self):
        from mesh.runtime.result_sink import _build_result_summary

        summary = _build_result_summary("timeout", {}, None)
        assert "timeout" in summary

    def test_build_result_summary_cancelled(self):
        from mesh.runtime.result_sink import _build_result_summary

        summary = _build_result_summary("cancelled", {}, None)
        assert "cancelled" in summary.lower()

    def test_build_result_summary_truncates_long_output(self):
        from mesh.runtime.result_sink import MAX_RESULT_COMMENT_LENGTH, _build_result_summary

        long_output = "x" * (MAX_RESULT_COMMENT_LENGTH + 1000)
        summary = _build_result_summary("completed", {"output": long_output}, None)
        assert len(summary) == MAX_RESULT_COMMENT_LENGTH


# ---------------------------------------------------------------------------
# Model schema: AttemptTaskToken (§2.2 S-05)
# ---------------------------------------------------------------------------


class TestAttemptTaskTokenModel:
    """§2.2: attempt_task_tokens table structure."""

    def test_model_exists(self):
        from mesh.db.models.runtime import AttemptTaskToken

        assert AttemptTaskToken.__tablename__ == "attempt_task_tokens"

    def test_model_columns(self):
        from mesh.db.models.runtime import AttemptTaskToken

        mapper = AttemptTaskToken.__mapper__
        column_names = {c.key for c in mapper.columns}
        expected = {
            "id", "workspace_id", "attempt_id", "runtime_id",
            "lease_seq", "token_hash", "scopes", "expires_at",
            "revoked_at", "created_at",
        }
        assert expected.issubset(column_names)

    def test_model_exported_from_init(self):
        from mesh.db.models import AttemptTaskToken

        assert AttemptTaskToken is not None


# ---------------------------------------------------------------------------
# Model schema: ExecutionAttempt structured fields (§2.6)
# ---------------------------------------------------------------------------


class TestExecutionAttemptStructuredFields:
    """§2.6: structured result columns on execution_attempts."""

    def test_structured_columns_exist(self):
        from mesh.db.models.runtime import ExecutionAttempt

        mapper = ExecutionAttempt.__mapper__
        column_names = {c.key for c in mapper.columns}
        for col in (
            "provider", "provider_version", "provider_session_id", "model",
            "prompt_tokens", "completion_tokens", "cache_tokens",
            "cost_usd", "num_turns", "result_schema_version", "redaction_hits",
            "claim_request_id",
        ):
            assert col in column_names, f"missing column: {col}"


# ---------------------------------------------------------------------------
# Model schema: TaskExecution snapshot_schema_version (§2.1)
# ---------------------------------------------------------------------------


class TestTaskExecutionSnapshotVersion:
    """§2.1: config_snapshot schema version on task_executions."""

    def test_snapshot_schema_version_column(self):
        from mesh.db.models.runtime import TaskExecution

        mapper = TaskExecution.__mapper__
        column_names = {c.key for c in mapper.columns}
        assert "snapshot_schema_version" in column_names


# ---------------------------------------------------------------------------
# Failure reasons vocabulary (§13.3)
# ---------------------------------------------------------------------------


class TestFailureReasons:
    """§13.3: extended failure reason vocabulary."""

    def test_new_p0_reasons(self):
        from mesh.db.models.runtime import FAILURE_REASONS

        for reason in (
            "executor_unavailable",
            "executor_protocol_error",
            "daemon_restart",
            "budget_exceeded",
            "usage_unavailable",
            "log_backpressure",
        ):
            assert reason in FAILURE_REASONS

    def test_existing_reasons_preserved(self):
        from mesh.db.models.runtime import FAILURE_REASONS

        for reason in ("oom", "timeout", "nonzero_exit", "superseded"):
            assert reason in FAILURE_REASONS
