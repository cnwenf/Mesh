# MES-62 Runtime Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full runtime module (runtime.md five chapters): runtime registry + activation, execution/attempt dual-layer queue with atomic SKIP LOCKED claim, lease fencing + reaper self-healing, daemon machine API, console API, log streaming, credential fencing, checkout whitelist, approvals skeleton, frontend pages — all backed by real e2e tests for red-line items T2/T3/T4/T10/T16/T20/T21.

**Architecture:** Backend `src/mesh/runtime/` package (models in `src/mesh/db/models/runtime.py`, migration `0018_runtime.py`). Claim is a single Postgres transaction: lock runtime row (validate online/capacity, no pre-deduct) → `FOR UPDATE SKIP LOCKED` pick matching task (tenant + label `<@` + capability `<@` + default_runtime) → only then `current_load+1` + status→claimed + insert attempt, one commit; no match → rollback (T20). Reaper worker task sweeps expired leases (attempt→reclaimed, execution→queued or failed(max_retries), lease_seq++, idempotent `GREATEST(load-1,0)` release). Daemon API authenticated by `mesh_rt_` token hash matched to `runtimes.runtime_token_hash`; workspace always server-side. Realtime via existing outbox→projector path (`emit_realtime`), events already in vocab. `execution.enqueue` outbox consumer registered in `workers/main.py::build_relay` completes the MES-60 contract.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, PostgreSQL 16 (RLS, SKIP LOCKED, advisory locks), Redis pub/sub, MinIO (log segments), pytest + pytest-cov (≥90% branch), Playwright (frontend real e2e).

## Global Constraints

- Spec authority: `docs/specs/features/runtime.md` + README §6.4/§6.5/§6.6/§6.7/§6.10/§6.11/§6.13/§6.16. Never deviate; report spec gaps in the issue, don't self-amend.
- Coverage: pytest-cov global `fail_under = 90` (branch); new module code ≥90%.
- Real e2e: real uvicorn subprocess + httpx against `mesh_app` RLS role; red-line tests T2/T3/T4/T10/T16/T20/T21 mandatory with real concurrency.
- Git: author/committer `cnwenf <cnwenf@outlook.com>`, NEVER any Co-Authored-By line, `core.hooksPath /dev/null`, rebase on latest main before PR.
- Anonymization: no reference-source leakage anywhere (code/comments/docs/commits/branch names).
- Response envelope: `{"data": ...}` / `{"data": [...], "next_cursor": ...}` / `{"error": {"code","message","details"}}`; errors via `src/mesh/errors.py` classes.
- Realtime: only via `emit_realtime(...)`; never insert `realtime_events` directly. Event names already registered in vocab — no new names needed.
- Multi-tenancy: composite FKs `(workspace_id, x_id)` for all tenant refs; RLS policies on all new tables; claim reads workspace from server-side runtime row, never request body.
- Secrets: `encrypt_secret`/`decrypt_secret` (Fernet, key from jwt_secret) for `runtime_credentials.encrypted_value`; plaintext only ever in claim/refetch responses; full-channel redaction (logs/comments/attachments) via `redact_in_logs` blacklist.

## File Structure

### Backend — create

| File | Responsibility |
|---|---|
| `backend/migrations/versions/0018_runtime.py` | DDL: `runtimes`, `task_executions`, `execution_attempts`, `task_log_segments`, `repo_checkouts`, `runtime_credentials`, `execution_credentials`, `runtime_heartbeats`, `approvals` (tool_call FKs live; autopilot/squad FKs deferred) + §2.4 indexes + RLS + grants + deferred `agents.(workspace_id, default_runtime_id)` composite FK + `mesh_runtime_by_token_hash` SECURITY DEFINER bootstrap fn |
| `backend/src/mesh/db/models/runtime.py` | ORM models for all 9 tables + status tuple constants + transition maps |
| `backend/src/mesh/runtime/__init__.py` | Package marker |
| `backend/src/mesh/runtime/schemas.py` | Pydantic request models (console + daemon) |
| `backend/src/mesh/runtime/service.py` | `RuntimeService(session_factory)`: runtime CRUD, create→activation code, activate (daemon), heartbeat, pause/resume/decommission, token rotate, credential CRUD, executions listing, queue depth |
| `backend/src/mesh/runtime/claim.py` | `claim_execution(session_factory, runtime, lease_seconds)` — the §2.5 atomic transaction; returns ClaimResult or None |
| `backend/src/mesh/runtime/attempts.py` | Attempt state machine: `transition_attempt` (PATCH), `renew_lease`, terminal-transition guard + idempotent capacity release, execution-level transitions (cancel/freeze) |
| `backend/src/mesh/runtime/enqueue.py` | `enqueue_execution_handler(session, outbox_event)` — consumes `execution.enqueue` (intent enqueue/cancel_in_flight), inserts `task_executions` with idempotency_key, emits `execution.queued` + `queue.depth_changed` |
| `backend/src/mesh/runtime/reaper.py` | `run_reaper_pass(session_factory, settings)`: lease-expired + heartbeat-lost sweeps; reclaim→requeue/failed(max_retries); approval expiry sweep; runtime offline marking; emits `execution.requeued`/`execution.failed`/`runtime.offline` |
| `backend/src/mesh/runtime/credentials.py` | Envelope create/revoke/refetch (TTL, per-attempt binding, refetch cap 3), redaction scanner (`redact_secrets(text, blacklist)`) shared for logs/comments/attachments |
| `backend/src/mesh/runtime/checkout.py` | `allowed_repos` whitelist check (from `workspaces.settings["allowed_repos"]`), SSRF guard (RFC1918/link-local/metadata rejection for platform_managed), checkout row lifecycle |
| `backend/src/mesh/runtime/logs.py` | Log append (offset continuity, segment sealing→object storage), read (offset range), `execution:{id}:logs` channel emission with `execution.log` frames |
| `backend/src/mesh/runtime/daemon_auth.py` | `resolve_runtime_token(token) -> Runtime` via `mesh_runtime_by_token_hash`; TLS enforcement middleware check for `/api/v1/daemon/`; env-name validator (NEW-M1) |
| `backend/src/mesh/runtime/routes.py` | Console API router (§3.1) |
| `backend/src/mesh/runtime/daemon_routes.py` | Machine API router (§3.2) incl. approvals request endpoint |
| `backend/src/mesh/runtime/channels.py` | `register_execution_checkers(authorizer, session_factory)` for `execution:{id}` / `execution:{id}:logs` channels |
| `backend/src/mesh/runtime/approvals.py` | Approval creation (daemon), decide (console): approve→execution queued + resume_context; reject/expire→cancelled; partial-unique pending enforcement |

### Backend — modify

| File | Change |
|---|---|
| `backend/src/mesh/db/models/__init__.py` | Export new models |
| `backend/src/mesh/api/app.py` | Instantiate `RuntimeService`, include routers, register execution channel checkers, daemon TLS middleware |
| `backend/src/mesh/realtime/app.py` | Register execution channel checkers |
| `backend/src/mesh/workers/main.py` | Register `execution.enqueue` handler in `build_relay`; add `runtime-reaper` TaskSpec |
| `backend/src/mesh/config.py` | Settings: `runtime_activation_ttl` (15m), `runtime_lease_seconds` (120), `runtime_reaper_interval` (5s), `runtime_heartbeat_timeout_multiplier` (3), `runtime_envelope_ttl` (2h), `runtime_credential_refetch_limit` (3), `runtime_release_*` (artifact placeholders), `runtime_log_segment_bytes` (64KB), `daemon_tls_required` |
| `backend/src/mesh/auth/security.py` | Add `RUNTIME_TOKEN_PREFIX = "mesh_rt_"` (+ activation code generator) |

### Tests — create

| File | Covers |
|---|---|
| `backend/tests/unit/test_runtime_models.py` | Model constraints via real DDL (CHECK rejections: status enums, non-string capability elements, attempt_number unique) |
| `backend/tests/unit/test_runtime_service.py` | CRUD, activation lifecycle (410 expired/used), pause/resume token revocation, queue depth |
| `backend/tests/unit/test_runtime_claim.py` | T20 no-match rollback, label/capability matching, default_runtime constraint, capacity pre-check |
| `backend/tests/unit/test_runtime_claim_concurrency.py` | T2 (3 runtimes race 1 task → exactly 1 winner), T3 (5 claims vs max_concurrent=2 → exactly 2, terminal→zero) |
| `backend/tests/unit/test_runtime_attempts.py` | State machine transitions (legal/illegal 409/422), lease renew lease_seq++, T10 stale lease_seq 409, idempotent release |
| `backend/tests/unit/test_runtime_enqueue.py` | execution.enqueue handler: idempotent insert, cancel_in_flight supersede, capability/label normalization passthrough |
| `backend/tests/unit/test_runtime_reaper.py` | T4 (reclaim→requeue audit preserved, new attempt #N+1), max_retries→failed, approval expiry sweep, offline marking |
| `backend/tests/unit/test_runtime_credentials.py` | Encrypt/decrypt roundtrip, envelope refetch cap 3, freeze revocation, redaction scanner |
| `backend/tests/unit/test_runtime_checkout.py` | T16 whitelist 403, SSRF private/metadata rejection, platform_managed vs self_hosted |
| `backend/tests/unit/test_runtime_approvals.py` | T21 full protocol: running→awaiting_approval (attempt cancelled, lease ended, load released)→approve→queued→new attempt with resume_context; reject→cancelled; single pending per subject |
| `backend/tests/unit/test_runtime_logs.py` | Offset continuity, segment sealing to storage, resume read, redaction in stream |
| `backend/tests/e2e/test_runtime_e2e.py` | Real server: activation flow over HTTP, daemon claim/report cycle, console list/cancel, TLS middleware, env-name 422, cross-runtime 403, T2/T3/T10/T20 over concurrent HTTP, log REST resume |

### Frontend — create (delegated, after backend API frozen)

| File | Responsibility |
|---|---|
| `frontend/src/features/runtimes/types.ts` | Readonly interfaces mirroring API |
| `frontend/src/features/runtimes/api.ts` | Path helpers + fetch wrappers + channel helpers |
| `frontend/src/features/runtimes/RuntimesPage.tsx` | List: status dot + load bar + heartbeat age + queue depth (§4.1) |
| `frontend/src/features/runtimes/RuntimeDetailPage.tsx` | Monitoring: metadata, inflight, history, pause/resume/rotate (§4.2) |
| `frontend/src/features/runtimes/RegisterRuntimeWizard.tsx` | 3-step bootstrap: create→signed-package commands→wait `runtime.activated` via WS (§4.3) |
| `frontend/src/features/runtimes/ExecutionDetailPage.tsx` | Live log stream (WS primary/SSE fallback/resume), cancel, credentials tab `***`, diff tab (§4.4) |
| `frontend/src/features/runtimes/runtimes.css` | Token-only styles |
| `frontend/src/features/runtimes/__tests__/*.test.ts(x)` | Vitest unit tests |
| `frontend/e2e/real-runtimes.spec.ts` + `playwright.runtimes.config.ts` | Real UI walkthrough |

### Frontend/docs — modify

`frontend/src/App.tsx` (routes), `frontend/src/shell/Sidebar.tsx` (automation entry), `frontend/src/i18n/catalogs/{en,zh-CN}.json` (`runtimes.*` keys), `frontend/scripts/verify-perfile-coverage.mjs` (add `src/features/runtimes/`), root `README.md` (status row), `backend/README.md` (module bullet + endpoints), `CHANGELOG.md` (0.14.0), `docs/specs/validation/schema_r2_validation.sql` (runtime DDL behavior assertions).

## Tasks

### Task 1: Migration 0018 + ORM models (TDD)
- Write migration DDL per §2.1/§2.2/§2.4: all 9 tables, CHECKs (status enums; `jsonb_is_string_array`-style CHECK on `required_capabilities` and `runtimes.capabilities`; `max_concurrent>=0`, `max_attempts>=1`), composite FKs, partial indexes, `uq_*_ws_id` on all tenant tables, RLS policies + grants, `mesh_runtime_by_token_hash(hash)` SECURITY DEFINER fn, deferred `agents.default_runtime_id` composite FK, approvals table with deferred autopilot/squad columns (no FKs yet), partial unique `uq_approvals_pending_execution`.
- ORM models + constants + transition maps; export in `db/models/__init__.py`.
- Tests: `test_runtime_models.py` — CHECK rejections via raw inserts (bad status, non-string capability element, duplicate attempt_number, cross-tenant composite FK rejection), RLS fail-closed under `mesh_app` role.
- Commit: `feat(runtime): 数据模型与迁移 0018(runtimes/task_executions/execution_attempts/approvals,MES-62 §2)`

### Task 2: Config + token prefix + RuntimeService core (TDD)
- Settings fields; `RUNTIME_TOKEN_PREFIX`; activation code generator (`ACT-XXXX-XXXX-XXXX`, hash-only storage).
- `RuntimeService`: create (pending + hashed activation + `activation_expires_at` + signed release info), list (cursor pagination, status/kind/labels filters), get, patch (name/labels/max_concurrent), pause/resume (token revoke linkage NEW-L2), decommission/soft-delete, rotate token, queue_depth.
- Tests: `test_runtime_service.py` incl. 410 paths, token revocation on pause.
- Commit: `feat(runtime): runtime 生命周期服务(三段式注册/激活码哈希/暂停即吊销 token,MES-62 §3.1)`

### Task 3: Daemon auth + activation + heartbeat (TDD)
- `daemon_auth.py`: `mesh_runtime_by_token_hash` bootstrap lookup → runtime row (status/deleted checks → 401), TLS guard (non-TLS to `/api/v1/daemon/` → 403 `tls_required`, bypassed when `daemon_tls_required=false` for local dev/test), env-name validator (NEW-M1: reject `LD_*`, `PATH`, `PYTHON*`, `NODE_OPTIONS`, `DYLD_*`, `MESH_DAEMON_*`, `MESH_INTERNAL_*`; allow `^[A-Z][A-Z0-9_]{0,63}$`).
- `POST /daemon/runtimes:activate` (410 expired/used, metadata merge, token once), `POST /daemon/runtimes/{id}:heartbeat` (health, metrics, `runtime_heartbeats` row optional window, downlink commands: pending cancels + credential refetch hints; degraded→stop dispatch), cross-runtime 403.
- Tests: `test_runtime_service.py` extension + daemon route unit tests.
- Commit: `feat(runtime): daemon 鉴权与激活/心跳(mesh_rt_ 令牌哈希/TLS 强制/下行指令,MES-62 §3.2/§3.5)`

### Task 4: Claim engine + enqueue consumer (TDD, concurrency)
- `claim.py` exact §2.5 transaction (SQLAlchemy `with_for_update` runtime row → `with_for_update(of=e, skip_locked=True)` pick → single atomic success branch; rollback on no match). Emits `execution.claimed` + `queue.depth_changed` realtime, working_branch `agent/<execution_id>/a<attempt>`, credentials one-shot envelope assembly with env-name validation (422).
- `enqueue.py` handler: intent=enqueue insert with idempotency_key UNIQUE guard (duplicate → no-op), required_capabilities/label_requirements from payload (already normalized by agent module), trigger enum incl. `integration`; intent=cancel_in_flight → cancel queued/claimed/running with failure_reason. Register in `build_relay`.
- Tests: `test_runtime_claim.py` + `test_runtime_claim_concurrency.py` (T2/T3/T20 via asyncio.gather on session_factory) + `test_runtime_enqueue.py`.
- Commit: `feat(runtime): 原子 claim(SKIP LOCKED §2.5/容量无泄漏 T20/并发无重复 T2)+ execution.enqueue 消费端(MES-60 契约)`

### Task 5: Attempt state machine + lease + cancel/freeze (TDD)
- `attempts.py`: PATCH attempt transitions (claimed→running→completed/failed/timeout; cancelling two-phase), lease_seq fencing (T10: mismatch → 409), renew-lease (lease_seq+1, expiry extend), idempotent terminal release (`GREATEST(load-1,0)` guarded by transition), execution cancel (queued→cancelling→cancelled; claimed/running→cancelling via heartbeat downlink; idempotent no-op on terminal), freeze (revoke all envelopes immediately).
- Realtime: `execution.started/completed/failed/timeout/cancelled` on `execution:{id}` + `workspace:{ws}:executions`.
- Terminal notification dispatch per §6.13 matrix: failed/timeout → critical outbox `notification.fanout`; completed → none by default; cancelled → none to initiator. (Fanout payload only; comment-inbox module consumes later.)
- Tests: `test_runtime_attempts.py`.
- Commit: `feat(runtime): 双层状态机与租约 fencing(lease_seq 防诈尸 T10/幂等容量释放/两段式取消,MES-62 §4.7/§4.8)`

### Task 6: Reaper worker + offline detection (TDD)
- `reaper.py::run_reaper_pass`: sweep `lease_expires_at < now()` in-flight attempts (SKIP LOCKED rows) → attempt `reclaimed` + `lease_seq++`, execution: attempts < max_attempts → `queued` + `execution.requeued`, else `failed(max_retries)` + `execution.failed`; idempotent release same txn. Heartbeat-lost runtimes (`last_heartbeat_at` older than interval×multiplier) → `unavailable` + `runtime.offline` (their in-flight attempts caught by lease sweep). Approval expiry: pending approvals past `expires_at` → `expired`, execution → `cancelled(approval_expired)`.
- Register `runtime-reaper` TaskSpec in workers/main.py (interval setting).
- Tests: `test_runtime_reaper.py` (T4 audit preservation: old attempt row intact, new attempt #N+1).
- Commit: `feat(runtime): reaper 失联自愈(租约回收/审计保留 T4/max_retries/审批过期,MES-62 §4.8)`

### Task 7: Credentials fencing + checkout whitelist + logs (TDD)
- `credentials.py`: runtime_credentials CRUD (plaintext in → encrypted_value, never out), envelope issue (TTL ≤2h, attempt+lease binding), refetch (only claimed/running + lease match, revoke old, cap 3 → freeze), terminal revocation.
- Redaction scanner reused for log append; expose `redact_secret_hits(text, values)` returning (clean_text, hit_count).
- `checkout.py`: `POST /daemon/attempts/{id}/checkouts` — whitelist check vs `workspaces.settings["allowed_repos"]` (403 `repo_not_allowed`), SSRF guard for platform_managed (ipaddress module: RFC1918/loopback/link-local/metadata → 403 `private_address_forbidden`), repo_checkouts row lifecycle (cloning→ready→diff_ready→recycled).
- `logs.py`: `POST /daemon/attempts/{id}/logs` (offset continuity per attempt, redaction, segment seal→`ObjectStorage.put_bytes`, `task_log_segments` row, `execution.log` realtime frames on `execution:{id}:logs`), `GET /executions/{id}/logs?offset=&stream=` (storage backfill + live), SSE `GET /executions/{id}/logs/stream` (fallback; same offset protocol).
- Tests: `test_runtime_credentials.py`, `test_runtime_checkout.py` (T16), `test_runtime_logs.py`.
- Commit: `feat(runtime): 凭证 fencing + checkout 白名单(T16/SSRF)+ 日志流式与续传(MES-62 §3.3/§6.16)`

### Task 8: Approvals + console routes + channel checkers (TDD)
- `approvals.py` + `POST /daemon/executions/{id}/approvals`: create approval (only from running; current attempt → `cancelled(awaiting_approval)`, lease ended, load released, execution → `awaiting_approval`, `execution.awaiting_approval` realtime, `approval.created` + critical notification fanout); console `POST /approvals/{id}/approve` (→ execution `queued` + `approval.decided`, resume_context frozen in action_summary) / `:reject` (→ `cancelled(approval_rejected)`); GET list (role=mine) + detail; single pending per subject via partial unique index (duplicate → returns existing).
- `routes.py` console API complete (§3.1 table) incl. credentials CRUD, executions list per runtime, cancel/freeze.
- `channels.py` execution checker (workspace membership + execution belongs to workspace), register in both app factories.
- Tests: `test_runtime_approvals.py` (T21 full protocol end-to-end at service level).
- Commit: `feat(runtime): 审批唯一续跑协议(T21)+ 控制台 API 全量 + 频道授权(MES-62 §6.10/§3.1)`

### Task 9: Real e2e suite (red-line items over HTTP)
- `tests/e2e/test_runtime_e2e.py`: real api_server + worker subprocesses; full activation over HTTP; daemon token flow; T2 (3 daemons claim concurrently → 1 winner each, no dup), T3 (max_concurrent=2, 5 parallel claims → exactly 2; report terminal → load 0), T4 (lease expiry → requeue with audit), T10 (stale lease_seq report → 409), T16 (checkout whitelist 403 + private address reject), T20 (no-match → 204 + load unchanged), T21 (approval protocol over HTTP), TLS middleware, env-name 422, cross-runtime 403, log resume over REST.
- Commit: `test(runtime): 红线 e2e 全量(T2/T3/T4/T10/T16/T20/T21 真实起服并发实测,MES-62 §5.2)`

### Task 10: Frontend (parallel subagent, after Task 8 API freeze)
- Feature package per File Structure; unit tests (vitest ≥90% per-file); routes + nav + i18n catalogs; real UI walkthrough spec.
- Commit: `feat(runtime): 前端 runtime 列表/详情/注册引导/执行详情实时日志(MES-62 §4)`

### Task 11: Docs + validation SQL + coverage gate + code review + PR
- `schema_r2_validation.sql` runtime assertions (string-array CHECK behavior, claim rollback invariants at DDL level).
- README.md status row, backend/README.md module section + endpoint tables, CHANGELOG 0.14.0, runtime.md sync if needed.
- Full `pytest --cov=mesh --cov-fail-under=90` green; ruff clean; frontend lint/typecheck/tests green.
- Real UI operation session (compose stack, screenshots as evidence).
- Request code review (requesting-code-review skill), address findings, rebase main, PR.
- Commit: `docs(runtime): 文档同步 + 验收基线补齐(MES-62)`

## Self-Review

- **Spec coverage:** §2 data model → T1; §2.5 claim → T4; §3.1 console → T8; §3.2 daemon → T3/T4/T5/T7; §3.3 logs → T7; §3.6 events → emitted across T2/T4/T5/T6/T8 (all names pre-registered); §4.1–4.6 UI → T10; §4.7 state machine → T5; §4.8 lease/reaper → T5/T6; §4.9 log streaming → T7; §4.10 notifications → T5 (matrix); §6.10 approvals → T8; §6.11 snapshot → T4 (frozen at enqueue by agent module, carried through claim response); §6.16 redaction/SSRF/TLS → T3/T7. Red-line tests T2/T3/T4/T10/T16/T20/T21 → T9 (+ service-level in T4/T5/T6/T8). Sandbox container internals (R10/R12 daemon-side) are daemon/deploy concerns — server-side contracts (task_spec fields, env validation, credential envelopes, deploy-doc constraints) are in scope; the daemon binary itself is not (spec §1.3: platform provides the protocol; daemon implementation detail is out of this issue's server+UI scope — noted in completion comment).
- **Placeholder scan:** no TBDs; interfaces named per file structure.
- **Type consistency:** ClaimResult, RuntimeService method names, transition maps consistent across tasks.

## Execution

Inline execution in this session (I am the assigned implementer), TDD per task, commit after each task green. Frontend (Task 10) delegated to a parallel subagent once backend routes exist.
