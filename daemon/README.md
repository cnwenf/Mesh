# mesh-runtime

The Mesh **local execution daemon**. It turns server-queued, frozen
`task_execution` rows into auditable local executions: runtime activation,
heartbeat, atomic claim, lease renewal, attempt journaling, redacted log relay,
provider supervision and crash reconciliation.

This package is **standalone** — it shares only the versioned HTTP contract
(`/api/v1/daemon/*`) with the backend and imports none of `backend/src/mesh`
(see `docs/specs/features/runtime-executor.md` §3.1). The binary and service
name are exactly `mesh-runtime`.

## Status (MES-94 · stage A1 + A2)

Implemented — the daemon **skeleton** (A1) plus the **secure execution
surface** (A2), driving the full attempt state machine and crash recovery:

- `api` — typed client for the whole `/api/v1/daemon/*` surface with
  fail-closed error classification (401 fatal, 409 lease fence, 429
  `Retry-After`, 5xx/network retryable).
- `scheduler` / `heartbeat` — claim loop with the frozen backoff table
  (204 → 1s→15s, 5xx → 2s→60s, full jitter) and an independent jittered
  keepalive that dispatches idempotent cancel downlinks.
- `attempt` — per-attempt supervisor: single-lock `lease_seq` fencing, renew
  loop, provider lifecycle, fenced terminal report; a 409 kills the provider
  and stops all reporting (the server reaper owns the truth).
- `journal` — SQLite (0600, metadata only) crash-recovery ledger; the state
  directory is tightened to 0700 so SQLite's transient `-journal`/`-wal`/`-shm`
  aux files are never world-readable regardless of umask.
- `logs` — redaction-first batching (64 lines / 256 KiB / 500 ms), offsets
  counted in redacted UTF-8 bytes, 409 `offset_mismatch` reconciliation. A
  batch is spooled BEFORE upload and cleared only on server ack, so a crash,
  restart, or transient network blip never loses redacted lines; a mid-stream
  transient failure is retried on the next flush instead of failing the attempt.
- `spool` — durable per-attempt redacted-batch store (0600 files, 0700 dir),
  idempotent `(attempt, stream, start_offset)` replay, frozen-cap backpressure.
- `redaction` / `result` — first-layer redaction pipeline and the versioned
  terminal result schema (decimal-string money, non-negative ints).
- `token_store` — `mesh_rt_` token persistence with the `lstat`/`open`/`fstat`
  triple-check (0600 file, 0700 parent, no symlink) — fail-closed.
- `inventory` / `doctor` — fail-closed binary probing and actionable local
  capability checks.

A2 secure execution surface (real kernel isolation, fail-closed — never a
bare run):

- `sandbox` / `sandbox_init` — per-attempt mount/pid/net/ipc/uts namespaces +
  cgroup2 hard limits (memory/cpu/pids, swap off), pivot_root into a minimal
  read-only root (tmpfs `/tmp` `/home` `/xdg`, fresh `/proc`, `/dev` reduced
  to null/zero/urandom), privilege drop to an unprivileged uid, no default
  network route — the only exit is the per-attempt egress proxy on a veth
  /30. Verified by an EXEC gate: the provider never runs unless the daemon
  has confirmed uid/cgroup/namespaces.
- `provider_env` — S-01: the frozen §1.4 argv template, reserved-env
  scrubbing (second pass after any merge), daemon-owned read-only provider
  configs, and hostile repo-file enumeration (ISO-09: `.mcp.json` /
  `.claude/settings*.json` / hooks / `CLAUDE.md` are plain files, never
  loaded or executed).
- `broker` — S-02: the unique ToolBroker gate. SO_PEERCRED uid + cgroup
  membership + attempt nonce, the §3.3 action→gate table (unknown actions
  fail closed; mount/privilege/daemon-control/cloud-metadata are permanently
  forbidden — approval cannot release them), task-token scope pinning, rate
  limiting, and the `confirm_required` protocol (cancel as
  `awaiting_approval` + resume in a new attempt — the sandbox never parks).
- `egress` / `netguard` — S-04: trusted resolve → filter EVERY answer IP
  (loopback/private/link-local/multicast/reserved/documentation/benchmarking/
  cloud metadata, IPv4-mapped normalized; one forbidden answer refuses the
  request) → pinned connect; HTTP + CONNECT; 3xx never followed (every hop
  re-enters the pipeline).
- `checkout` — §3.2: frozen-URL + allowlist + public-address gates, exact-SHA
  checkout, read-only credentials confined to the git subprocess environment
  (never the remote URL, `.git/config`, or provider env).
- `cleanup` — S-08: ordered, idempotent, whitelist-only teardown (broker →
  revoke → cgroup kill → mounts → artifacts → spool gate → journal bit);
  never follows symlinks, refuses paths outside the attempt root.
- `security` — per-attempt orchestration of the stack; `attempt` / `app`
  wire it into the claim→terminal lifecycle (sandbox failure reports
  `failed/sandbox_violation`).

Proof: `tests/isolation/` runs the ISO-01~14 red-line matrix on REAL
namespaces/cgroups/network — no mocks, no skips (a runner without root
FAILS, never skips). Evidence: `docs/evidence/mes-100/` (ISO matrix junit +
real-server integration: activate → online → claim → sandboxed execution →
redacted reflow).

Still ahead (A3 real provider): the pinned Claude Code adapter (capability
manifest + SHA-256 check, stream-json parsing, hard budget truncation,
session/usage/result reflow).

## Install & run

```bash
cd daemon
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# 1. Create a runtime in the console, get the one-time activation code.
# 2. Put the code in a 0600 file (never on the command line):
install -m 600 /dev/null code.txt && printf '%s' "$CODE" > code.txt

# 3. Write a config (see below), then activate:
mesh-runtime activate --config runtime.toml --activation-code-file code.txt

# 4. Local capability checks:
mesh-runtime doctor --config runtime.toml

# 5. Run the daemon:
mesh-runtime run --config runtime.toml
```

### Config (`runtime.toml`)

```toml
server_url = "https://mesh.example.com"   # https origin only (http loopback + allow_insecure_http for local dev)
state_dir  = "/var/lib/mesh-runtime"       # absolute, 0700 — token, journal, spool
work_dir   = "/var/lib/mesh-runtime/work"  # absolute, 0700 — attempt worktrees
max_concurrent = 1                          # daemon ceiling; server still adjudicates
# provider_path = "/opt/mesh/providers/claude/<version>/claude"  # A3
```

Server-owned values (heartbeat/lease intervals, the authoritative
`max_concurrent`) arrive in protocol responses and are never configured here.

## Test

```bash
cd daemon
pytest --cov=mesh_runtime --cov-report=term-missing
```

Unit tests use a `FakeClock` (no real waits) and a scripted fake server
(`httpx.MockTransport`); contract tests drive the full attempt state machine
and crash reconciliation over the real HTTP contract. No network, no LLM, no
secrets.
