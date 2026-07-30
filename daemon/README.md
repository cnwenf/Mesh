# mesh-runtime

The Mesh **local execution daemon**. It turns server-queued, frozen
`task_execution` rows into auditable local executions: runtime activation,
heartbeat, atomic claim, lease renewal, attempt journaling, redacted log relay,
provider supervision and crash reconciliation.

This package is **standalone** — it shares only the versioned HTTP contract
(`/api/v1/daemon/*`) with the backend and imports none of `backend/src/mesh`
(see `docs/specs/features/runtime-executor.md` §3.1). The binary and service
name are exactly `mesh-runtime`.

## Status (MES-94 · stage A1 + A2 + A3)

Implemented — the daemon **skeleton** (A1), the **secure execution surface**
(A2) and the **real pinned provider** (A3), driving the full attempt state
machine and crash recovery:

- `api` — typed client for the whole `/api/v1/daemon/*` surface with
  fail-closed error classification (401 fatal, 409 lease fence, 429
  `Retry-After`, 5xx/network retryable).
- `scheduler` / `heartbeat` — claim loop with the frozen backoff table
  (204 → 1s→15s, 5xx → 2s→60s, full jitter) and an independent jittered
  keepalive that dispatches idempotent cancel downlinks.
- `attempt` — per-attempt supervisor: single-lock `lease_seq` fencing, renew
  loop, provider lifecycle, fenced terminal report; a 409 kills the provider
  and stops all reporting (the server reaper owns the truth).
- `journal` — SQLite (metadata only) crash-recovery ledger, pre-created with
  an explicit 0600 (no umask window) in a 0700 state directory, so SQLite's
  transient `-journal`/`-wal`/`-shm` aux files are never world-readable.
- `logs` — redaction-first batching (64 lines / 256 KiB / 500 ms — the
  interval arm runs on an independent timer, so a sparse stream flushes
  without waiting for the next line), offsets counted in redacted UTF-8
  bytes (a single watermark across both streams), 409 `offset_mismatch`
  reconciliation. A batch is spooled BEFORE upload and cleared only on
  server ack; a sealed (terminal) flush replays spooled batches AND the
  in-memory tail, sealing only the true last batch; if it still fails past
  bounded retries the attempt is demoted to `failed/log_flush_failed` —
  never certified completed on incomplete logs.
- `spool` — durable per-attempt redacted-batch store (0600 files, 0700 dir),
  idempotent `(attempt, stream, start_offset)` replay, frozen-cap backpressure.
- `redaction` / `result` — first-layer redaction pipeline and the versioned
  terminal result schema (decimal-string money, non-negative ints).
- `token_store` — `mesh_rt_` token persistence with the `lstat`/`open`/`fstat`
  triple-check (0600 file verified AGAIN through the open fd, 0700 parent, no
  symlink; an oversized file is refused, never truncated) — fail-closed.
- `reconcile` / `residual` — startup reconciliation settles journal rows
  (`daemon_restart` terminal, or a best-effort replay+seal for
  `terminal_seal_pending`) AND reaps crash residuals: per-attempt work/spool
  directories (containment-verified), leftover sandbox cgroups and host-side
  veth links. Reconciliation runs before the first claim — nothing is in
  flight — and failures are reported, never fatal.
- `inventory` / `doctor` — fail-closed binary probing and actionable local
  capability checks.

A2 secure execution surface (real kernel isolation, fail-closed — never a
bare run):

- `sandbox` / `sandbox_init` — per-attempt mount/pid/net/ipc/uts namespaces +
  cgroup2 hard limits (memory/cpu/pids, swap off), pivot_root into a minimal
  read-only root (tmpfs `/tmp` `/home` `/xdg`, fresh `/proc`, `/dev` reduced
  to null/zero/urandom), privilege drop to an unprivileged uid, no default
  network route — the only exit is the per-attempt egress proxy, bound (via
  `IP_FREEBIND`) to that veth's host IP only, never a wildcard address.
  Verified by an EXEC gate: the provider never runs unless the daemon has
  confirmed uid, cgroup and ALL five namespaces (net/mnt/pid/ipc/uts).
  Link /30s are reserved before the sandbox exists so the egress listener
  and the sandbox exit always agree.
- `provider_env` — S-01: the frozen §1.4 argv template, reserved-env
  scrubbing (second pass after any merge: reserved prefixes, the proxy
  family, and generic `_TOKEN`/`_SECRET`/`_KEY`/`_CREDENTIAL`/`_PASSWORD`
  suffixes), daemon-owned read-only provider configs, and hostile repo-file
  enumeration (ISO-09: `.mcp.json` / `.claude/settings*.json` / hooks /
  `CLAUDE.md` are plain files, never loaded or executed).
- `broker` — S-02: the unique ToolBroker gate. SO_PEERCRED uid + cgroup
  membership + attempt nonce, the §3.3 action→gate table (unknown actions
  fail closed; mount/privilege/daemon-control/cloud-metadata are permanently
  forbidden — approval cannot release them), task-token scope pinning, rate
  limiting, idempotency keys on write actions (a repeated key replays the
  cached result instead of re-executing), and the `confirm_required`
  protocol (cancel as `awaiting_approval` + resume in a new attempt — the
  sandbox never parks).
- `egress` / `netguard` — S-04: trusted resolve → filter EVERY answer IP
  (loopback/private/link-local/multicast/reserved/documentation/benchmarking/
  cloud metadata, IPv4-mapped normalized; one forbidden answer refuses the
  request) → pinned connect; HTTP + CONNECT; 3xx never followed (every hop
  re-enters the pipeline) and the frozen `max_redirects` is a real per-attempt
  hop cap — past it, further 3xx are refused, not relayed.
- `checkout` — §3.2: frozen-URL + allowlist gates, exact-SHA checkout
  (`base_sha` missing → fail-closed, never a moving-ref fetch), and — on
  platform-managed runtimes — trusted resolution of the repo host with
  all-answer IP filtering and the fetch PINNED to the verified IPs
  (`http.curloptResolve`), closing the rebinding window before the
  read-only credential (confined to the git subprocess environment, never
  the remote URL / `.git/config` / provider env) is used.
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

B lifeline layer (MES-95, delivered): the protected `real_llm` workflow
(`.github/workflows/real-llm.yml` — manual/schedule only, protected
self-hosted runner, `concurrency` serialized, secrets-only credentials) runs
the two real-gate scripts on top of A3: single-agent `real_llm_e2e.py` and
the multi-agent squad `real_llm_squad_e2e.py` (leader + 2 members: the leader
REALLY decomposes via the `squad.members` / `squad.subtasks` broker tools,
members really execute and report via `issue.comment`, the leader aggregator
really summarizes via `issue.comment` + `issue.status`; issue ends `done`
with real per-execution session/usage/cost reflow).

A3 real pinned provider (a real coding CLI inside the A2 sandbox):

- `manifest` — the immutable §1.4 capability manifest (TOML encoding of the
  spec's fields: `provider` / `version` / `binary_sha256` / `required_flags` /
  `hard_limits`). Loading is fail-closed on any doubt; `hard_limits.usd_budget`
  / `wall_timeout` must be `true` (a real provider with no hard budget is
  refused, §3.5). `required_flags` must stay inside the frozen §1.4 argv set
  and never include a §1.5 load-expanding flag.
- `providers/claude_code` — the pinned Claude Code adapter. `probe()`
  re-verifies absolute path / owner / mode / SHA-256 / exact version and that
  every required flag is advertised by a bare-env `--help` read (the
  `--flag[-suffix]` help shorthand is expanded to its concrete flags); the
  result caches on `(dev, ino, mtime, size)` and is invalidated the instant the
  binary's inode/mtime/hash changes (§1.4 step 5). `run()` builds the frozen
  §1.4 argv (prompt via stdin only — never argv, never a shell), writes the
  three daemon-owned read-only configs into the attempt run dir, builds the
  sandbox env FROM EMPTY plus the administrator-owned provider credentials
  (§5.4.7), and parses the vendor `stream-json` stream into unified events.
  The broker socket and egress proxy are resolved at `run()` time (security
  starts after adapter construction), never captured eagerly.
- `stream_json` — strict §3.9 parser: a fixed schema whitelist
  (system/init, assistant text/tool_use/usage, user tool_result, the terminal
  result record); unknown / malformed / oversized records are dropped with a
  diagnostic reason (never raised, never relayed); `thinking` blocks never
  become events (§3.7).
- `budget` — S-07 daemon-layer enforcement: the effective limit is the
  STRICTER of the frozen snapshot and the daemon caps; provider-reported usage
  and wall/idle clocks are checked live and a violation TERM→KILLs the
  provider, terminating with the frozen `budget_exceeded` / `timeout`
  vocabulary. `UsageObserved.turns` and the result record's `total_cost_usd`
  flow into the schema-v1 terminal result.
- `sandbox_init` additionally bind-mounts the host's read-only public CA trust
  store (`/etc/ssl/certs`) so the provider can verify the TLS certificate of
  its pinned egress destination (public roots only — no host config, no
  secrets).

Proof: `tests/integration/real_llm_e2e.py` drives the real gate over the
public API (no `psql` seed): register → workspace → agent (frozen budget +
network policy) → runtime → daemon activate → **online** → assign → real claim
→ the pinned binary executes a real LLM call inside the namespace/cgroup
sandbox → logs / session / usage / result reflow with the provider credential
redacted. Evidence: `docs/evidence/mes-101/real-llm-e2e.json`.

Proof (squad lifeline): `tests/integration/real_llm_squad_e2e.py` drives the
full multi-agent chain over the public API: register → workspace → 3 agents
(leader + 2 workers, frozen budget/network policy) → squad → runtime → daemon
activate → **online** → dynamic-nonce issue assigned to the squad → leader
orchestrator run REALLY decomposes into 2 subtasks through the task broker
(`squad_members` + `squad_subtasks`, server-verified orchestrator identity) →
both members claim and execute with real LLM calls, each posting its nonce
report through `issue_comment` → all children terminal → leader aggregator
run posts the summary and marks the issue `done` → root task `done`,
assignment `completed`, relay writeback comment. Asserted: every execution
completed with real usage (tokens/cost/session, distinct sessions), logs
non-empty and credential-redacted, the nonce visible in every run's output,
zero credential leak anywhere. Operator prerequisites are the same as
`real_llm_e2e.py` (`MES95_*` env vars fall back to `MES101_*`): pinned binary
+ `manifest.toml` + 0600 `provider.env`, root (real sandbox), a live local
stack (API + worker). Evidence: `docs/evidence/mes-95/real-llm-squad-e2e.json`
(first full real run: 4 executions / 4 distinct sessions / 45,408 tokens /
0.188556 USD / issue done).

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
# A3 real provider (omit all three to stay on the A1/A2 fake provider):
# provider_path        = "/opt/mesh/providers/claude/<version>/claude"  # pinned binary (absolute)
# provider_manifest    = "/etc/mesh-runtime/claude-manifest.toml"       # §1.4 capability manifest
# provider_env_file    = "/etc/mesh-runtime/provider.env"               # 0600 provider credentials (0700 parent)
# sandbox_memory_bytes = 2147483648   # per-attempt cgroup ceilings (daemon local;
# sandbox_pids_max     = 256          # the frozen snapshot may be stricter, never looser)
```

The provider credential file is `KEY=VALUE` lines (e.g. `ANTHROPIC_API_KEY=…`);
every name is re-validated against the §3.8 reserved set and every value joins
the redaction secret set, so credentials never reach logs, results, argv,
stdin, or the journal (§5.4.7). Generate a manifest's pinned values with
`mesh-runtime manifest hash --binary <path>`.

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
