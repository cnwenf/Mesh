"""Unit tests — §2.2 S-05 squad task scopes + §3.3 frozen broker grants.

Hermetic (no DB, no HTTP): the pure helpers that widen task-token scopes for
orchestrator attempts and inject the broker capability grants into the frozen
AttemptSpec (digest included).
"""

from __future__ import annotations

from mesh.agent.snapshot import build_config_snapshot, compute_snapshot_digest
from mesh.agent.triggers import (
    DEFAULT_BROKER_GRANTS,
    ORCHESTRATOR_BROKER_GRANTS,
    _inject_broker_grants,
)
from mesh.runtime.task_tokens import (
    DEFAULT_TASK_SCOPES,
    SQUAD_ORCHESTRATOR_METHODS,
    squad_role_of_task_spec,
)


class TestSquadRoleOfTaskSpec:
    def test_orchestrator_role(self):
        assert squad_role_of_task_spec({"squad_role": "orchestrator"}) == "orchestrator"

    def test_executor_role(self):
        assert squad_role_of_task_spec({"squad_role": "executor"}) == "executor"

    def test_non_dict_is_none(self):
        assert squad_role_of_task_spec(None) is None
        assert squad_role_of_task_spec("orchestrator") is None
        assert squad_role_of_task_spec([]) is None

    def test_missing_or_empty_role_is_none(self):
        assert squad_role_of_task_spec({}) is None
        assert squad_role_of_task_spec({"squad_role": ""}) is None
        assert squad_role_of_task_spec({"squad_role": 3}) is None


class TestBrokerGrantsInjection:
    def _snapshot(self) -> dict:
        return build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=None,
            provider="claude-code",
            model="m",
            system_instructions="s",
        )["config_snapshot"]

    def test_default_grants_for_plain_execution(self):
        snap = self._snapshot()
        _inject_broker_grants(snap, squad_role=None)
        caps = {g["capability"]: g["permission"] for g in snap["capability_grants"]}
        for grant in DEFAULT_BROKER_GRANTS:
            assert caps[grant["capability"]] == grant["permission"]
        assert "squad.members" not in caps
        assert "squad.subtasks" not in caps

    def test_orchestrator_gets_squad_grants(self):
        snap = self._snapshot()
        _inject_broker_grants(snap, squad_role="orchestrator")
        caps = {g["capability"]: g["permission"] for g in snap["capability_grants"]}
        for grant in (*DEFAULT_BROKER_GRANTS, *ORCHESTRATOR_BROKER_GRANTS):
            assert caps[grant["capability"]] == grant["permission"]
        assert caps["squad.subtasks"] == "write"
        assert caps["squad.members"] == "read_only"

    def test_executor_and_aggregator_get_no_squad_grants(self):
        for role in ("executor", "aggregator"):
            snap = self._snapshot()
            _inject_broker_grants(snap, squad_role=role)
            caps = {g["capability"] for g in snap["capability_grants"]}
            assert "squad.subtasks" not in caps
            assert "squad.members" not in caps

    def test_digest_recomputed_over_final_content(self):
        snap = self._snapshot()
        old_digest = snap["digest"]
        _inject_broker_grants(snap, squad_role="orchestrator")
        assert snap["digest"] != old_digest
        assert snap["digest"] == compute_snapshot_digest(snap)

    def test_injection_is_deterministic(self):
        a = self._snapshot()
        b = self._snapshot()
        _inject_broker_grants(a, squad_role="orchestrator")
        _inject_broker_grants(b, squad_role="orchestrator")
        assert a["capability_grants"] == b["capability_grants"]
        assert a["digest"] == b["digest"]

    def test_grants_sorted_by_capability(self):
        snap = self._snapshot()
        _inject_broker_grants(snap, squad_role="orchestrator")
        caps = [g["capability"] for g in snap["capability_grants"]]
        assert caps == sorted(caps)

    def test_existing_grants_win_on_collision(self):
        snap = self._snapshot()
        snap["capability_grants"] = [{"capability": "issue.read", "permission": "write"}]
        _inject_broker_grants(snap, squad_role=None)
        match = [g for g in snap["capability_grants"] if g["capability"] == "issue.read"]
        assert len(match) == 1
        assert match[0]["permission"] == "write"  # pre-existing entry kept

    def test_required_capabilities_never_polluted(self):
        """Grants are NOT scheduling requirements — required_capabilities
        (claim matching) must stay untouched by broker grant injection."""
        parts = build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=None,
            declared_capabilities=[],
        )
        _inject_broker_grants(parts["config_snapshot"], squad_role="orchestrator")
        assert parts["required_capabilities"] == []


class TestTaskScopeWideningConstants:
    def test_default_scopes_unchanged(self):
        assert "squad:task:decompose" not in DEFAULT_TASK_SCOPES["methods"]
        assert "squad:task:read" not in DEFAULT_TASK_SCOPES["methods"]
        assert DEFAULT_TASK_SCOPES["denied"] == ["agent:trigger"]

    def test_orchestrator_methods_are_additive(self):
        assert SQUAD_ORCHESTRATOR_METHODS == ("squad:task:read", "squad:task:decompose")
