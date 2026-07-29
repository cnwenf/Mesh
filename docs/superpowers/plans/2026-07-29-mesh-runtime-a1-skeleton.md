# mesh-runtime A1 Daemon Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the A1 daemon skeleton for `mesh-runtime` — a standalone Python daemon that activates a runtime, heartbeats, claims executions, renews leases, journals attempts, uploads redacted logs, reconciles after crashes, and drives a fake provider through the full attempt state machine with zero real LLM/secrets.

**Architecture:** New top-level `daemon/` package (`mesh_runtime`), fully decoupled from `backend/src/mesh` — the two share ONLY the HTTP `/api/v1/daemon/*` contract (spec §3.1 red line). All loops (heartbeat/claim/lease) take an injectable `Clock`/`sleep` so tests use a fake clock and never really wait. The API client speaks real HTTP semantics via `httpx` against `MockTransport` in tests (real status codes/headers, no network). SQLite journal holds metadata only (0600, never secrets).

**Tech Stack:** Python 3.12, asyncio, httpx, stdlib sqlite3 (via `asyncio.to_thread`), pytest + pytest-asyncio + pytest-cov.

## Global Constraints

- Binary/package/service name is exactly `mesh-runtime` / `mesh_runtime` (spec §4.3 S-12); no `mesh-daemon` alias.
- Daemon NEVER imports `backend/src/mesh` ORM/service (spec §3.1); only the versioned HTTP contract.
- Runtime token (`mesh_rt_`) never enters logs, exceptions, env, SQLite, metrics, or subprocess (spec §2.3/§9.1).
- Journal stores metadata only — IDs, lease_seq, offsets, cleanup bits; never prompt/output/token/secret (spec §2.3).
- Claim backoff (spec §3.1): 204 empty → full-jitter exp 1s→15s, reset on success; network/5xx → 2s→60s; 429 → obey `Retry-After`; 401 → stop claim, enter isolated/fatal. Heartbeat/renew do NOT use claim backoff.
- Log batch (spec §3.9): send when ANY of 64 lines / 256 KiB / 500 ms; `start_offset` over **redacted UTF-8 bytes**; 409 → stop and reconcile.
- Lease (spec §3.1 / design §6.3): renew period = `min(lease_duration/3, 40s)`; single `lease_seq` under one lock; 409 `lease_seq_mismatch`/`attempt_terminal` → kill provider, stop all reports, do not "fix" server.
- Provider fail-closed (spec §1.4): unverified binary/version → runtime `degraded`, no claim; no PATH search beyond configured abs path, no auto-download/upgrade.
- Crash recovery (spec §3.1 / design §7.5): on restart reconcile with server FIRST; never resume old provider or guess from local state.
- UT coverage ≥90% line AND branch on the `mesh_runtime` package (issue quality gate); fake clock, no real waits, no network.
- Git: author/committer `cnwenf <cnwenf@outlook.com>`; commit messages with NO `Co-Authored-By`/co-author lines. Branch names / code / comments reveal no reference product.

## File Structure

```text
daemon/
├── pyproject.toml                # name mesh-runtime, console_script mesh-runtime
├── README.md                     # install/activate/run/doctor usage
├── src/mesh_runtime/
│   ├── __init__.py
│   ├── __main__.py               # `python -m mesh_runtime` -> cli.main
│   ├── cli.py                    # argparse: run | activate | doctor | version
│   ├── config.py                 # DaemonConfig frozen dataclass + load/validate
│   ├── timeutil.py               # Clock protocol, SystemClock, FakeClock, full_jitter
│   ├── errors.py                 # DaemonError hierarchy + classify_http
│   ├── backoff.py                # BackoffPolicy (full-jitter exp) + named presets
│   ├── token_store.py            # FileTokenStore: atomic 0600 write, lstat/open/fstat triple-check
│   ├── redaction.py              # RedactionPipeline: exact + base64/url matchers, hit counts
│   ├── result.py                 # AttemptResult v1 builder + validate (terminal schema §3.9)
│   ├── api.py                    # RuntimeApiClient (httpx) typed methods + error classification
│   ├── journal.py                # Journal (sqlite3 0600): put/get/list_active/mark_cleanup/delete
│   ├── logs.py                   # LogUploader: batching, redacted-byte offset, 409 reconcile, spool
│   ├── inventory.py              # probe provider, inventory hash, capability keys, degraded logic
│   ├── doctor.py                 # actionable local checks -> CheckReport
│   ├── heartbeat.py              # HeartbeatLoop: interval+jitter, downlink cancel dispatch
│   ├── scheduler.py              # ClaimScheduler: semaphore slots, backoff, drain
│   ├── attempt.py                # AttemptSupervisor: lease loop, provider lifecycle, terminal report
│   ├── reconcile.py              # startup reconciliation vs journal + server
│   ├── app.py                    # RuntimeApp: root TaskGroup, signals, graceful shutdown
│   └── providers/
│       ├── __init__.py
│       ├── base.py               # ExecutorAdapter Protocol, events, RunRequest, ProbeResult
│       └── fake.py               # FakeProvider: scripted events + fault injection
└── tests/
    ├── conftest.py               # FakeClock fixture, make_fake_server (httpx MockTransport)
    ├── unit/                     # one test module per source module
    └── contract/
        └── test_state_machine.py # full claim->run->terminal + crash recovery via FakeProvider
```

Each source file has one responsibility; orchestration modules (`scheduler`, `attempt`, `app`) depend on injected collaborators, never construct them, so they are unit-testable in isolation.

## Interfaces (cross-task contracts)

```python
# timeutil.py
class Clock(Protocol):
    def now(self) -> float: ...            # monotonic seconds
    def utcnow(self) -> datetime: ...
    async def sleep(self, seconds: float) -> None: ...
class FakeClock:                            # manual advance, records sleeps
    async def sleep(self, seconds): ...
    def advance(self, seconds): ...
def full_jitter(base: float, cap: float, attempt: int, rand: Callable[[], float]) -> float

# errors.py
class DaemonError(Exception): ...
class FatalAuthError(DaemonError): ...       # 401 -> stop claim, isolated
class LeaseConflictError(DaemonError): ...   # 409 lease_seq_mismatch/attempt_terminal
class GoneError(DaemonError): ...            # 410 activation expired
class RateLimitedError(DaemonError):         # 429, .retry_after: float|None
    retry_after: float | None
class ServerError(DaemonError): ...          # 5xx / network -> retryable
def classify_response(status: int, body: dict|None, retry_after: float|None) -> None  # raises

# backoff.py
@dataclass(frozen=True)
class BackoffPolicy:
    base: float; cap: float
    def delay(self, attempt: int, rand: Callable[[], float]) -> float   # full jitter
EMPTY_QUEUE = BackoffPolicy(1.0, 15.0)      # 204
NETWORK = BackoffPolicy(2.0, 60.0)          # 5xx/network

# token_store.py
class FileTokenStore:
    def __init__(self, path: Path, uid: int): ...
    async def save(self, token: str) -> None        # atomic, fsync, 0600, dir 0700
    async def load(self) -> str | None              # lstat/open/fstat triple-check, fail-closed
    async def clear(self) -> None

# redaction.py
@dataclass(frozen=True)
class RedactionResult:
    text: str; hit_count: int
class RedactionPipeline:
    def __init__(self, secrets: Sequence[str], rule_version: str): ...
    def redact(self, text: str) -> RedactionResult   # exact + base64 + url-encoded
    def redact_lines(self, lines: list[str]) -> tuple[list[str], int]

# result.py
RESULT_SCHEMA_VERSION = 1
def build_result(*, provider: str, version: str, model: str, session_id: str|None,
                 usage: Usage, exit_code: int, summary: str, termination: str,
                 checkout_id: str|None, diff_ref: str|None,
                 rule_version: str, hit_count: int) -> dict
def validate_result(doc: dict) -> None               # raises ValueError

# api.py
class RuntimeApiClient:
    def __init__(self, base_url: str, token: str|None, *, transport=None, clock: Clock): ...
    async def activate(self, activation_code: str, metadata: dict) -> ActivateResponse
    async def heartbeat(self, runtime_id: str, *, current_load: int, health: str,
                        metrics: dict, inflight: list[str]) -> HeartbeatResponse
    async def claim(self, runtime_id: str) -> ClaimResponse | None        # None on 204
    async def renew_lease(self, attempt_id: str, lease_seq: int) -> LeaseInfo
    async def transition(self, attempt_id: str, *, lease_seq: int, status: str,
                         result: dict|None = None, failure_reason: str|None = None) -> dict
    async def append_logs(self, attempt_id: str, *, lease_seq: int, stream: str,
                          start_offset: int, lines: list[str], sealed: bool = False) -> LogAck
    async def report_checkout(self, attempt_id: str, **fields) -> dict
    async def close(self) -> None

# journal.py
@dataclass(frozen=True)
class JournalEntry:
    attempt_id: str; execution_id: str; runtime_id: str
    lease_seq: int; lease_expires_at: float
    status: str                     # claimed|running|terminal_reported|cleaned
    log_offset_stdout: int; log_offset_stderr: int
    work_dir: str; created_at: float
class Journal:
    async def put(self, entry: JournalEntry) -> None
    async def update(self, attempt_id: str, **fields) -> None
    async def get(self, attempt_id: str) -> JournalEntry | None
    async def list_active(self) -> list[JournalEntry]
    async def delete(self, attempt_id: str) -> None

# logs.py
class LogUploader:
    def __init__(self, api, journal, redactor, *, clock, batch_lines=64,
                 batch_bytes=256*1024, batch_interval=0.5): ...
    async def submit(self, attempt_id: str, stream: str, line: str) -> None
    async def flush(self, attempt_id: str, *, sealed: bool = False) -> None   # drains queue
    async def reconcile(self, attempt_id: str, expected_offset: int) -> None  # 409 handler

# providers/base.py
@dataclass(frozen=True)
class RunRequest:
    attempt_id: str; system_prompt: str; untrusted_context: str
    max_turns: int; max_budget_usd: str; tools_allowlist: tuple[str, ...]
@dataclass(frozen=True)
class ProbeResult:
    available: bool; name: str; version: str|None; binary_sha256: str|None
    capabilities: tuple[str, ...]; reason: str|None
class ExecutorEvent: ...   # SessionStarted/TextDelta/UsageObserved/FinalResult/ProviderExited
class ExecutorAdapter(Protocol):
    name: str
    async def probe(self) -> ProbeResult: ...
    async def run(self, request: RunRequest) -> AsyncIterator[ExecutorEvent]: ...
```

## Task List

### Task 1: Package scaffold + foundations (timeutil, errors, backoff)
- Create `daemon/pyproject.toml` (name `mesh-runtime`, deps httpx, console script, pytest config, cov branch=true source=mesh_runtime), `tests/conftest.py`.
- TDD: `timeutil` (FakeClock advance, full_jitter bounds), `errors` (classify_response for 401/409/410/429/5xx), `backoff` (EMPTY_QUEUE 1→15, NETWORK 2→60, jitter within [0, exp]).
- Commit + push.

### Task 2: config + token_store
- `config.py`: DaemonConfig (server_url https-only, state_dir, work_dir, max_concurrent, provider path), validate absolute paths.
- `token_store.py`: atomic write (temp+rename+fsync), 0600 file/0700 dir, load triple-check (no symlink, regular, owner, mode) fail-closed. TDD incl. negative symlink/world-readable cases.
- Commit + push.

### Task 3: redaction + result schema
- `redaction.py`: exact matcher + base64 + url-encoded, hit counts, never echo secret. TDD cross-chunk/encoded.
- `result.py`: build + validate terminal result v1 (decimal-string cost, non-negative ints). TDD.
- Commit + push.

### Task 4: api client
- `api.py`: typed methods over httpx; `classify_response`; Retry-After parse; bearer header; None-on-204 claim. Tests via `httpx.MockTransport` asserting method/path/body and error mapping. No token leaks in repr.
- Commit + push.

### Task 5: provider contract + fake provider + inventory + doctor
- `providers/base.py` events/RunRequest/ProbeResult/Protocol.
- `providers/fake.py` FakeProvider: scripted event list, injectable failure, records requests.
- `inventory.py`: probe configured abs path only, sha256, capability keys, degraded-on-failure; inventory hash.
- `doctor.py`: checks (token file, state dir perms, provider probe) -> actionable report.
- Commit + push.

### Task 6: journal + logs uploader
- `journal.py`: sqlite3 via to_thread, 0600, metadata-only schema, CRUD + list_active.
- `logs.py`: queue + batching (64/256KiB/500ms via FakeClock), offset over redacted bytes, 409 reconcile (drop confirmed, resend unconfirmed), spool backpressure hook.
- Commit + push.

### Task 7: heartbeat + scheduler + attempt supervisor + reconcile + app/cli
- `heartbeat.py`: interval from server, ±10% jitter, independent loop, idempotent cancel dispatch.
- `scheduler.py`: local semaphore = min(server, config), claim loop with EMPTY_QUEUE/NETWORK backoff, 429 Retry-After, 401 stop, drain.
- `attempt.py`: renew loop (min(lease/3,40s)), single lease lock, 409 -> kill+stop, terminal fenced report, journal transitions.
- `reconcile.py`: on start, for each active journal entry query server; never resume provider; quarantine.
- `app.py`/`cli.py`/`__main__.py`: wire TaskGroup, signal-driven graceful shutdown (stop claim first, grace for in-flight).
- Commit + push.

### Task 8: contract tests + coverage + CI + docs + PR
- `tests/contract/test_state_machine.py`: FakeProvider + fake server drive claim->checkout->running->logs->terminal->journal-cleaned; crash-recovery scenario (restart mid-run -> reconcile, no resume).
- `pytest --cov` ≥90% line+branch; fix gaps.
- `.github/workflows/daemon-ci.yml`; `daemon/README.md`; update root README + runtime-executor.md status note.
- Open PR; post issue result with PR link + coverage evidence.

## Self-Review

- Spec coverage: activate/heartbeat/claim/renew/reconcile (T4,T7), doctor (T5), journal (T6), fake-provider contract (T5,T8), redaction/log offsets (T3,T6), backoff table (T1,T7), lease fencing (T7), crash recovery (T7,T8), fail-closed provider (T5). A2/A3 (real namespace/cgroup sandbox, egress gateway, real Claude Code) are explicitly OUT of this A1 plan per §4.4 ordering.
- Placeholders: none — interfaces above are exact; each task names files, tests, commit.
- Type consistency: names in Interfaces match those referenced in tasks (EMPTY_QUEUE/NETWORK, LeaseConflictError, JournalEntry fields, LogUploader methods).
