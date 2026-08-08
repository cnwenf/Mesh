"""ExecutorAdapter contract + unified event model (design §4.1, spec §1.4).

A provider version ships an IMMUTABLE capability manifest; the daemon probes
it fail-closed (spec §1.4) and only claims tasks whose provider passed. Every
adapter normalises its vendor stream into the events below so the supervisor
stays vendor-agnostic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RunRequest:
    """A frozen, daemon-assembled request. Untrusted content is a SEPARATE
    field from the system prompt so it can never be mistaken for instructions
    (spec §3.7 S-09)."""

    attempt_id: str
    system_prompt: str
    untrusted_context: str
    max_turns: int
    max_budget_usd: str  # decimal string, frozen at enqueue
    tools_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbeResult:
    available: bool
    name: str
    version: str | None
    binary_sha256: str | None
    capabilities: tuple[str, ...] = ()
    reason: str | None = None
    # Immutable manifest expectations remain safe to report when a probe
    # fails; unlike ``capabilities`` they are never used for claim admission.
    required_capabilities: tuple[str, ...] = ()


# -- unified events ----------------------------------------------------------


@dataclass(frozen=True)
class SessionStarted:
    session_id: str
    model: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolRequested:
    name: str
    call_id: str


@dataclass(frozen=True)
class ToolCompleted:
    call_id: str
    outcome: str


@dataclass(frozen=True)
class UsageObserved:
    """Usage (provider-reported); used for live truncation + audit.

    ``terminal`` marks the FINAL cumulative frame for the attempt (the result
    record). Per-message frames on a multi-turn stream are NOT guaranteed to be
    cumulative-monotonic, so the supervisor's regression gate (HIGH-4/MES-190)
    applies to the terminal frame only, compared on a folded, basis-invariant
    context-token total (see ``attempt._context_tokens``); every frame still
    satisfies the §3.5 non-negative / decimal-string invariants.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: str = "0.000000"
    turns: int = 0  # provider-reported turn count (A3 result record carries it)
    terminal: bool = False  # True only for the final cumulative usage frame

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


@dataclass(frozen=True)
class FinalResult:
    summary: str
    exit_code: int
    # Precise termination when the adapter KNOWS it (budget_exceeded/timeout/
    # completed/failed); "" means "derive from exit code" (the A1/A2 path).
    termination: str = ""


@dataclass(frozen=True)
class ProtocolWarning:
    raw_type: str


@dataclass(frozen=True)
class ProviderExited:
    exit_code: int


ExecutorEvent = (
    SessionStarted
    | TextDelta
    | ToolRequested
    | ToolCompleted
    | UsageObserved
    | FinalResult
    | ProtocolWarning
    | ProviderExited
)


@runtime_checkable
class ExecutorAdapter(Protocol):
    """A coding-CLI adapter. Probing is fail-closed; running yields events."""

    name: str

    async def probe(self) -> ProbeResult: ...

    def run(self, request: RunRequest) -> AsyncIterator[ExecutorEvent]: ...


@dataclass
class AdapterRegistry:
    """Holds the adapters the daemon will probe and offer to the server."""

    adapters: list[ExecutorAdapter] = field(default_factory=list)

    def register(self, adapter: ExecutorAdapter) -> None:
        self.adapters.append(adapter)
