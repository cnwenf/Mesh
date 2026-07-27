"""Capability normalization unit tests (agent.md §3.3, README §6.4 / §6.11, T28).

Drives the backend's executable implementation of the §3.3 algorithm with
mixed string/object declarations and asserts EVERY normalization semantic:
string entries default to confirm_required, dedup, strictest-permission-wins
(confirm_required > write > read_only), lexicographic ordering, strict
typing of both outputs, and rejection of illegal declarations. The
implementation is line-for-line equivalent to the PL/pgSQL reference in
docs/specs/validation/schema_r2_validation.sql.
"""

from __future__ import annotations

import pytest

from mesh.agent.capabilities import (
    CapabilityInvalidError,
    normalize_capability_declarations,
)
from mesh.agent.snapshot import build_config_snapshot


@pytest.mark.unit
def test_empty_declarations_normalize_to_empty_strict_arrays():
    result = normalize_capability_declarations([])
    assert result == {"required": [], "grants": []}


@pytest.mark.unit
def test_string_entries_default_to_confirm_required():
    result = normalize_capability_declarations(["exec:shell"])
    assert result["required"] == ["exec:shell"]
    assert result["grants"] == [{"capability": "exec:shell", "permission": "confirm_required"}]


@pytest.mark.unit
def test_object_entries_keep_annotated_permission():
    result = normalize_capability_declarations(
        [{"capability": "read:code", "permission": "read_only"}]
    )
    assert result["grants"] == [{"capability": "read:code", "permission": "read_only"}]


@pytest.mark.unit
def test_object_without_permission_defaults_to_confirm_required():
    result = normalize_capability_declarations([{"capability": "net:fetch"}])
    assert result["grants"] == [{"capability": "net:fetch", "permission": "confirm_required"}]


@pytest.mark.unit
def test_mixed_declarations_dedup_sort_and_keep_strictest():
    # T28 core semantic: mixed declarations in arbitrary order.
    declared = [
        {"capability": "ffmpeg", "permission": "write"},
        "exec:shell",
        {"capability": "ffmpeg", "permission": "read_only"},  # weaker → ignored
        "ffmpeg",  # string → confirm_required (strictest) wins
        {"capability": "read:code", "permission": "read_only"},
        "exec:shell",  # duplicate string
    ]
    result = normalize_capability_declarations(declared)
    # required: deduplicated, lexicographically sorted STRING array.
    assert result["required"] == ["exec:shell", "ffmpeg", "read:code"]
    # grants: sorted by capability, strictest permission each.
    assert result["grants"] == [
        {"capability": "exec:shell", "permission": "confirm_required"},
        {"capability": "ffmpeg", "permission": "confirm_required"},
        {"capability": "read:code", "permission": "read_only"},
    ]


@pytest.mark.unit
def test_write_stricter_than_read_only():
    result = normalize_capability_declarations(
        [
            {"capability": "fs:write", "permission": "read_only"},
            {"capability": "fs:write", "permission": "write"},
        ]
    )
    assert result["grants"] == [{"capability": "fs:write", "permission": "write"}]


@pytest.mark.unit
def test_required_is_always_a_pure_string_array():
    # Objects must NEVER leak into the scheduling field — an object there
    # makes the claim JSONB <@ match fail forever (README §6.4 R3).
    result = normalize_capability_declarations(
        ["a", {"capability": "b", "permission": "write"}]
    )
    assert all(isinstance(item, str) for item in result["required"])
    assert all(
        isinstance(item, dict)
        and isinstance(item["capability"], str)
        and item["permission"] in ("read_only", "write", "confirm_required")
        for item in result["grants"]
    )


@pytest.mark.unit
def test_illegal_permission_rejected():
    with pytest.raises(CapabilityInvalidError) as excinfo:
        normalize_capability_declarations(
            [{"capability": "x", "permission": "sudo"}]
        )
    assert excinfo.value.code == "capability_invalid"
    assert "permission must be read_only|write|confirm_required" in str(excinfo.value)


@pytest.mark.unit
def test_non_string_permission_rejected():
    with pytest.raises(CapabilityInvalidError):
        normalize_capability_declarations([{"capability": "x", "permission": 42}])


@pytest.mark.unit
def test_object_without_capability_key_rejected():
    with pytest.raises(CapabilityInvalidError):
        normalize_capability_declarations([{"permission": "write"}])


@pytest.mark.unit
def test_illegal_entry_shape_rejected():
    with pytest.raises(CapabilityInvalidError):
        normalize_capability_declarations([42])


@pytest.mark.unit
def test_non_array_input_rejected():
    with pytest.raises(CapabilityInvalidError):
        normalize_capability_declarations({"capability": "x"})


@pytest.mark.unit
def test_snapshot_builder_derives_both_fields_from_one_normalization():
    import uuid

    parts = build_config_snapshot(
        agent_config_version_id=uuid.uuid4(),
        trigger_event_id=uuid.uuid4(),
        declared_capabilities=["exec:shell", {"capability": "read:code", "permission": "read_only"}],
    )
    snapshot = parts["config_snapshot"]
    assert parts["required_capabilities"] == ["exec:shell", "read:code"]
    assert snapshot["capability_grants"] == [
        {"capability": "exec:shell", "permission": "confirm_required"},
        {"capability": "read:code", "permission": "read_only"},
    ]
    assert snapshot["skill_versions"] == {}
    assert snapshot["repo"] is None
    assert snapshot["trigger_event_id"]


@pytest.mark.unit
def test_snapshot_builder_rejects_illegal_declarations():
    import uuid

    with pytest.raises(CapabilityInvalidError):
        build_config_snapshot(
            agent_config_version_id=None,
            trigger_event_id=uuid.uuid4(),
            declared_capabilities=[{"capability": "x", "permission": "nope"}],
        )
