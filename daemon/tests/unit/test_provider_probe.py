"""Provider isolation fixture probe tests (runtime-executor.md §1.4 steps 3-4,
§1.5; ISO-09 closure on the REAL binary).

The unit tests exercise the judgment logic hermetically. The real-binary test
runs the actual pinned Claude Code binary against the hostile fixtures (no
python stand-in) and is gated on the binary being present (protected real-LLM
environment); it skips with a clear reason where the binary is absent.
"""

import os
from pathlib import Path

import pytest

from mesh_runtime.provider_probe import (
    FixtureProbeResult,
    _saw_init_record,
    probe_isolation_fixture_sync,
)

# Real pinned binary (protected real-LLM environment); overridable for tests.
REAL_BINARY = os.environ.get(
    "MES101_PROVIDER_PATH", "/opt/mesh/providers/claude/2.1.218/claude"
)


class TestSawInitRecord:
    def test_detects_init_record(self):
        out = '{"type":"system","subtype":"init","session_id":"s"}\n{"type":"assistant"}\n'
        assert _saw_init_record(out) is True

    def test_no_init_record(self):
        assert _saw_init_record('{"type":"assistant"}\n') is False
        assert _saw_init_record("") is False

    def test_malformed_lines_ignored(self):
        out = "not json\n{\"type\":\"system\",\"subtype\":\"init\"}\n"
        assert _saw_init_record(out) is True


class TestFixtureProbeResult:
    def test_isolated_requires_launch_and_no_effect(self):
        assert FixtureProbeResult(True, False, False, False).isolated is True
        # not launched -> cannot verify -> not isolated (fail closed)
        assert FixtureProbeResult(False, False, False, False).isolated is False
        # any hostile effect -> not isolated
        assert FixtureProbeResult(True, True, False, False).isolated is False
        assert FixtureProbeResult(True, False, True, False).isolated is False
        assert FixtureProbeResult(True, False, False, True).isolated is False


@pytest.mark.skipif(
    not Path(REAL_BINARY).exists(),
    reason=f"real provider binary not present at {REAL_BINARY} (protected real-LLM env)",
)
class TestRealBinaryIsolationFixture:
    """ISO-09 closure on the REAL binary: the isolation flags must hold on the
    actual release binary, not a python stand-in (CRITICAL-1 proved flag
    semantics can deviate on a real release)."""

    def test_real_binary_isolation_holds(self):
        result = probe_isolation_fixture_sync(
            REAL_BINARY, drop_uid=(65534 if os.getuid() == 0 else None)
        )
        assert result.launched, f"provider did not launch: {result.detail}"
        assert not result.beacon_connected, "hostile .mcp.json was loaded (beacon)"
        assert not result.hook_fired, "hostile settings hook fired"
        assert not result.claudemd_followed, "CLAUDE.md injection followed"
        assert result.isolated, f"isolation failed: {result.detail}"
