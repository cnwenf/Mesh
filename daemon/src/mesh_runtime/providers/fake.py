"""FakeProvider — the A1 stand-in for a real coding CLI.

Drives the full attempt state machine and crash-recovery contract tests with
NO real LLM and NO secrets (spec §4.4 A1 gate). It replays a scripted event
list, records the request it was handed, and can inject a fault to exercise
failure paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from mesh_runtime.providers.base import (
    ExecutorEvent,
    ProbeResult,
    RunRequest,
)

_FAKE_PROBE = ProbeResult(
    available=True,
    name="fake",
    version="0.0.0-fake",
    binary_sha256=None,
    capabilities=("coding_cli.fake", "usage.cost"),
    reason=None,
)


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        *,
        events: list[ExecutorEvent],
        probe_result: ProbeResult | None = None,
        fault: BaseException | None = None,
    ) -> None:
        self._events = list(events)
        self._probe_result = probe_result or _FAKE_PROBE
        self._fault = fault
        self.last_request: RunRequest | None = None
        self.run_count = 0

    async def probe(self) -> ProbeResult:
        return self._probe_result

    async def run(self, request: RunRequest) -> AsyncIterator[ExecutorEvent]:
        self.last_request = request
        self.run_count += 1
        if self._fault is not None:
            raise self._fault
        for event in self._events:
            yield event
