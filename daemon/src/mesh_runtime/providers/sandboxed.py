"""SandboxedProcessAdapter — runs the pinned provider binary INSIDE the real
namespace/cgroup sandbox (S-01/S-03 foundation).

A2 streams plain stdout/stderr lines as ``TextDelta`` events — enough for the
fake/demo providers and the ISO matrix payloads. The pinned Claude Code
adapter (A3) layers stream-json parsing on top of this same sandbox seam.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from mesh_runtime.errors import DaemonError
from mesh_runtime.providers.base import (
    ExecutorEvent,
    FinalResult,
    ProbeResult,
    RunRequest,
    SessionStarted,
    TextDelta,
)
from mesh_runtime.sandbox import SandboxManager, SandboxSpec, SandboxUnavailableError


class SandboxLaunchError(DaemonError):
    """The sandbox could not be provisioned/verified for this attempt.
    The supervisor maps this to failed/sandbox_violation — never bare run."""


class SandboxedProcessAdapter:
    name = "sandboxed"

    def __init__(
        self,
        *,
        sandbox_manager: SandboxManager,
        spec_builder: Callable[[RunRequest], SandboxSpec],
        provider_name: str = "fake",
        provider_version: str = "0.0.0-fake",
        model: str = "fake-model",
    ) -> None:
        self._manager = sandbox_manager
        self._spec_builder = spec_builder
        self._provider_name = provider_name
        self._provider_version = provider_version
        self._model = model
        self._attempt_id = ""

    async def probe(self) -> ProbeResult:
        caps = await SandboxManager.probe_capabilities(
            cgroup_base=self._manager.cgroup_base, state_root=self._manager.state_root
        )
        if caps.get("sandbox") == "linux_ns":
            return ProbeResult(
                available=True,
                name=self._provider_name,
                version=self._provider_version,
                binary_sha256=None,
                capabilities=("sandbox.linux_ns", "egress.gateway", "broker.unix"),
                reason=None,
            )
        return ProbeResult(
            available=False,
            name=self._provider_name,
            version=self._provider_version,
            binary_sha256=None,
            capabilities=(),
            reason=str(caps.get("reason", "sandbox unavailable")),
            required_capabilities=(
                "sandbox.linux_ns",
                "egress.gateway",
                "broker.unix",
            ),
        )

    async def run(self, request: RunRequest) -> AsyncIterator[ExecutorEvent]:
        self._attempt_id = request.attempt_id
        try:
            spec = self._spec_builder(request)
            handle = await self._manager.provision(spec)
        except SandboxUnavailableError as exc:
            raise SandboxLaunchError(str(exc)) from exc
        yield SessionStarted(session_id=f"sandbox:{request.attempt_id}", model=self._model)
        proc = handle.proc
        summary_lines: list[str] = []
        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                summary_lines.append(text)
                yield TextDelta(text=text)
            await asyncio.wait_for(proc.wait(), timeout=30.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        exit_code = proc.returncode if proc.returncode is not None else 124
        summary = "\n".join(summary_lines[-8:])[:4000]
        yield FinalResult(summary=summary, exit_code=exit_code)

    async def destroy(self) -> None:
        await self._manager.destroy_attempt(self._attempt_id)
