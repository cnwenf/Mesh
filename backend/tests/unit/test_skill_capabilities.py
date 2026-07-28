"""Capability grant-subset tests (skill.md §5.3 权限最小化)."""

from __future__ import annotations

import pytest

from mesh.errors import BusinessRuleError
from mesh.skill.capabilities import assert_grants_subset_of_required, capability_keys


class TestCapabilityKeys:
    def test_mixed_declaration_shapes(self) -> None:
        keys = capability_keys(
            ["read:code", {"capability": "exec:shell", "permission": "read_only"}]
        )
        assert keys == {"read:code", "exec:shell"}

    def test_malformed_input_yields_empty(self) -> None:
        assert capability_keys("not-a-list") == set()
        assert capability_keys([123, {"nope": True}]) == set()


class TestSubsetCheck:
    def test_equal_sets_pass(self) -> None:
        assert_grants_subset_of_required(["a", "b"], ["a", "b"])

    def test_subset_passes(self) -> None:
        assert_grants_subset_of_required(["a"], ["a", "b"])

    def test_object_grant_vs_string_declaration_passes(self) -> None:
        # read_only (autonomy 1) vs bare-string default confirm_required (2) =
        # tightening → allowed.
        assert_grants_subset_of_required(
            [{"capability": "exec:shell", "permission": "read_only"}],
            ["exec:shell", "net:outbound"],
        )

    def test_undeclared_grant_refused(self) -> None:
        with pytest.raises(BusinessRuleError) as exc_info:
            assert_grants_subset_of_required(
                ["exec:shell", "net:outbound"], ["exec:shell"]
            )
        assert exc_info.value.code == "capability_not_declared"
        assert exc_info.value.details == {"undeclared": ["net:outbound"]}

    def test_empty_grants_always_pass(self) -> None:
        assert_grants_subset_of_required([], ["exec:shell"])

    # --- HIGH-3: permission-level ESCALATION must be refused ------------------

    def test_escalation_read_only_to_write_refused(self) -> None:
        # Declare read_only, grant write → widens the surface → refused.
        with pytest.raises(BusinessRuleError) as exc_info:
            assert_grants_subset_of_required(
                [{"capability": "exec:shell", "permission": "write"}],
                [{"capability": "exec:shell", "permission": "read_only"}],
            )
        assert exc_info.value.code == "capability_not_declared"
        assert exc_info.value.details["escalated"][0]["capability"] == "exec:shell"

    def test_escalation_bare_to_write_refused(self) -> None:
        # Bare declared = confirm_required (autonomy 2); write = 3 → escalation.
        with pytest.raises(BusinessRuleError) as exc_info:
            assert_grants_subset_of_required(
                [{"capability": "exec:shell", "permission": "write"}], ["exec:shell"]
            )
        assert exc_info.value.code == "capability_not_declared"
        assert exc_info.value.details["escalated"]

    def test_escalation_confirm_required_to_write_refused(self) -> None:
        with pytest.raises(BusinessRuleError):
            assert_grants_subset_of_required(
                [{"capability": "exec:shell", "permission": "write"}],
                [{"capability": "exec:shell", "permission": "confirm_required"}],
            )

    def test_tightening_write_to_read_only_allowed(self) -> None:
        # Declared write (3), granted read_only (1) → tightening → allowed.
        assert_grants_subset_of_required(
            [{"capability": "exec:shell", "permission": "read_only"}],
            [{"capability": "exec:shell", "permission": "write"}],
        )

    def test_equal_permission_passes(self) -> None:
        assert_grants_subset_of_required(
            [{"capability": "exec:shell", "permission": "write"}],
            [{"capability": "exec:shell", "permission": "write"}],
        )
