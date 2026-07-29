import pytest

from mesh_runtime.providers.base import (
    FinalResult,
    ProbeResult,
    ProviderExited,
    RunRequest,
    SessionStarted,
    TextDelta,
    UsageObserved,
)
from mesh_runtime.providers.fake import FakeProvider


def run_request(**overrides) -> RunRequest:
    base = dict(
        attempt_id="att-1",
        system_prompt="You are a test agent.",
        untrusted_context="issue body here",
        max_turns=3,
        max_budget_usd="0.100000",
        tools_allowlist=("fs.read", "git.diff"),
    )
    base.update(overrides)
    return RunRequest(**base)


class TestRunRequest:
    def test_is_immutable(self):
        import dataclasses

        req = run_request()
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.max_turns = 99  # type: ignore[misc]

    def test_tools_allowlist_is_tuple(self):
        assert run_request().tools_allowlist == ("fs.read", "git.diff")


class TestProbeResult:
    def test_healthy_probe(self):
        probe = ProbeResult(
            available=True,
            name="claude-code",
            version="2.0.0",
            binary_sha256="abc",
            capabilities=("coding_cli.claude_code", "usage.cost"),
            reason=None,
        )
        assert probe.available
        assert probe.version == "2.0.0"

    def test_unavailable_probe_carries_reason(self):
        probe = ProbeResult(
            available=False, name="claude-code", version=None,
            binary_sha256=None, capabilities=(), reason="binary not found",
        )
        assert not probe.available
        assert probe.reason == "binary not found"


class TestFakeProvider:
    async def test_yields_scripted_events_in_order(self):
        events = [
            SessionStarted(session_id="s1", model="claude-sonnet-4"),
            TextDelta(text="hello"),
            FinalResult(summary="done", exit_code=0),
            ProviderExited(exit_code=0),
        ]
        provider = FakeProvider(events=events)
        collected = [e async for e in provider.run(run_request())]
        assert collected == events

    async def test_records_last_request(self):
        provider = FakeProvider(events=[])
        req = run_request(attempt_id="att-xyz")
        _ = [e async for e in provider.run(req)]
        assert provider.last_request is req
        assert provider.run_count == 1

    async def test_probe_returns_configured_result(self):
        probe = ProbeResult(
            available=True, name="claude-code", version="2.0.0",
            binary_sha256="sha", capabilities=("coding_cli.claude_code",), reason=None,
        )
        provider = FakeProvider(events=[], probe_result=probe)
        assert await provider.probe() is probe

    async def test_default_probe_is_available_fake(self):
        provider = FakeProvider(events=[])
        probe = await provider.probe()
        assert probe.available
        assert probe.name == "fake"

    async def test_injected_fault_raises_during_run(self):
        provider = FakeProvider(events=[TextDelta(text="x")], fault=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            _ = [e async for e in provider.run(run_request())]

    async def test_usage_event_carries_cumulative_tokens(self):
        usage = UsageObserved(
            input_tokens=100, output_tokens=50,
            cache_read_tokens=10, cache_creation_tokens=5, cost_usd="0.001000",
        )
        provider = FakeProvider(events=[usage])
        collected = [e async for e in provider.run(run_request())]
        assert collected[0].total_tokens == 165
