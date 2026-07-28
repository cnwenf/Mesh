# Mesh Backend

Python backend for Mesh (see [docs/specs/README.md](../docs/specs/README.md) for the
canonical spec — it is the single source of truth for every contract here).

## Architecture (docs/specs/README.md §2–§3)

Three independently deployable units, all stateless except PostgreSQL:

| Unit | Entrypoint | Responsibility |
| --- | --- | --- |
| API | `uvicorn mesh.api.app:create_app --factory` | REST `/api/v1`, §6.14 envelopes, health checks |
| Worker | `python -m mesh.workers` | Outbox relay (incl. `execution.enqueue` → `task_executions`), realtime projector, realtime + outbox retention purges, invitation sweep, attachment quarantine pipeline, **runtime reaper** (lease-expiry reclaim + heartbeat-loss offline + approval expiry) — each an isolated supervised asyncio task (§2.2) |
| Realtime gateway | `uvicorn mesh.realtime.app:create_app --factory` | WebSocket `/ws`: first-frame auth, per-channel authorization, `resume_from` replay, `resync_required` (§6.7/§6.16) |

Layering inside `src/mesh/`:

- `api/` — FastAPI factory, error/pagination contracts (§6.14), health, deps.
- `auth/` — authentication & authorization core (`docs/specs/features/auth.md`): argon2id password hashing, register/login/logout, short-lived access JWT (fixed `alg`, rejects `none`/HS-RS confusion) + revocable refresh (SHA-256 hash only, rotation with replay detection), single-use reset/verification tokens, TOTP MFA (encrypted secret + one-time backup codes), `(IP, email)` login lockout, Redis sliding-window rate limiting, `users.settings`/`timezone` preferences (`PATCH /api/v1/users/me`), PAT / agent access tokens (`api_tokens`: hash-only, `role_override` double-validated against the holder role, scope∩role least privilege, agent `agent:trigger` default-deny), vendor-neutral OAuth login/bind (`oauth.py`: authorization-code + PKCE, mock provider in dev), session/token-revocation realtime broadcast (`realtime.py`: `session.revoked` via outbox), transactional email (`mailer.py`: Redis dev-mailbox / SMTP), and the append-only audit query with `before`/`after` time-range.
- `db/` — SQLAlchemy 2.x models, Alembic migrations, multi-tenant infrastructure (§6.2: `UNIQUE(workspace_id, id)` + composite-FK templates, RLS GUC `mesh.workspace_id`, global-table exemption list).
- `events/vocab.py` — canonical realtime event vocabulary (§6.7, kept in sync with the README registry by tests + CI).
- `integrations/` — integrations platform (`docs/specs/features/integrations.md`, README §6.17): unified third-party abstraction (IM/VCS/developer webhook) with four connector adapters (feishu/slack/github/gitlab: signature verification + payload normalization), the shared ingestion pipeline reusing the autopilot `webhook_events` paradigm (constant-time signature check + ±300s replay window → `rejected:<hash>` audit namespace → `UNIQUE(integration_id, external_event_id)` dedup → binding match → same-transaction `execution.enqueue` with `trigger='integration'` and the `sha256(agent|binding|external_event_id)` idempotency key; payloads enter the agent context only under the §6.15 untrusted root), `external_identities` global identity table (maps onto `users.id`; no `workspace_id` / no workspace RLS; owner-only unlink with no admin bypass — `external_identity_unlink_allowed` executable reference), external-identity link flow (one-time code delivered to the external account's DM; dev Redis dev-outbox), card-callback authorization chain (clicker identity → `users.id` → workspace roster JOIN → §6.10 permission re-check → unified approvals forwarding), `vcs_links` truth source (identifier `WEB-123` auto-link + `auto_status_map` status flow with system-comment trail), outbound developer webhooks (`webhook.dispatch` outbox derivation from `realtime.publish` → delivery worker with `Mesh-Signature` HMAC headers, exponential backoff, subscription-level circuit breaker; https-only + SSRF guards), OAuth authorization-code + PKCE (single-use state, ciphertext-only tokens), credential ciphertext contract (`secret_ref`, same as `runtime_credentials`; plaintext shown exactly once), and `integration.updated` / `integration.event_ingested` realtime events over the single outbox write path.
- `project/` — project module (`docs/specs/features/project.md`): project CRUD + archive/restore + soft delete, health/status trail with writeback, milestones (derived overdue), cycles (auto-roll), project members + private visibility, project templates + instantiation (§3.2b); same-transaction prefix-registry occupation with permanent reservation (README §6.3), workspace-less paths resolved through narrow SECURITY DEFINER lookups, `project:{id}` channel resource-level subscription checker.
- `outbox/` — transactional outbox (§6.6): `emit_event`/`emit_realtime` write in the business transaction; the relay claims `FOR UPDATE SKIP LOCKED`; the projector is the ONLY writer of `realtime_events` and allocates per-channel `seq` in the same transaction.
- `realtime/` — gateway protocol, channel auth hooks, Redis fan-out (Redis is fan-out only; replay truth is in `realtime_events`).
- `runtime/` — runtime module (`docs/specs/features/runtime.md`): three-stage registration (shadow row + one-time activation-code hash + daemon activation issuing a hash-only `mesh_rt_` token), heartbeat/health with downlink cancel commands, the §2.5 atomic claim (runtime-row lock without pre-deduction → `FOR UPDATE SKIP LOCKED` task pick with tenant equality + server-side label/capability `<@` matching + default-runtime affinity → capacity + `claimed` + attempt in one commit; zero writes when nothing matches), the dual-layer state machine (terminal-transition-guarded idempotent capacity release, `lease_seq` zombie fencing), lease renewal, two-phase cancel/freeze, credential fencing (Fernet ciphertext, one-shot per-attempt envelopes, refetch cap 3 → freeze, full-channel redaction), checkout allowlist + SSRF guard, offset-continuous log segments in object storage with REST/SSE resume, the unified approvals single-resume protocol (README §6.10), and the `execution.enqueue` outbox consumer closing the MES-60 contract. Machine API (`/api/v1/daemon/`) is TLS-only with token-hash auth (workspace always server-derived); `execution:{id}[:logs]` channels carry resource-level subscription checks registered on both the API and the gateway. **P0 server contracts (MES-98, runtime-executor.md §2.1–2.6):** §2.1 frozen AttemptSpec snapshot (provider/model/effort/system_instructions/budget/network/data policy + SHA-256 digest) via unified `build_config_snapshot` (assign/mention/autopilot share one entry); §2.2 S-05 task-level `mesh_task_` tokens (`attempt_task_tokens` table, claim-issued / renew-rotated / five-path terminal revocation, `validate_task_token` full校验 with lease_seq/runtime/scope/rate-limit, `/api/v1/task/*` endpoints via `resolve_task_principal` dependency); §2.4 S-11 `runtimes.runtime_token_hash` single source of truth (no `api_tokens` dual-write, migration 0029 rebuilds `mesh_runtime_by_token_hash` SECURITY DEFINER function); §2.5 S-06 server-side fallback redaction (`redact_result` before result persist, `redact_diff_text` before diff persist, ISO-13 aligned); §2.6 structured result columns on `execution_attempts` (provider/model/tokens/cost/turns) + protocol negotiation (activate/heartbeat accept protocol_version/provider_manifest/daemon_features); §3.7 S-09 `result_sink` issue completion closure (non-squad assign/mention triggers → `CommentService` real comment, suppress_triggers + idempotency key, stub-result and independent-closure triggers skipped).
- `validation.py` — shared validators (IANA timezone, supported locale/theme, https-only user-controlled URLs; auth-canonical 422 codes, §6.16/§6.18).
- `workspace/` — workspace module (`docs/specs/features/workspace.md`): workspace CRUD + slug redirects, invitation lifecycle with redemption separation (hash-only tokens, atomic accept, workspace-configurable caps), settings single-source locale, prefix registry (§6.3); plus the RBAC adjudicator (`auth/rbac.py`), the unified `members` roster table (member.md owns), append-only `audit_logs` (auth.md §2.6) and the guest project-visibility hook.
- `workers/` — supervisor (isolated cancel domains + restart backoff), retention purge, invitation expiry sweep, process entrypoint.

## Global contracts implemented here

- **Error envelope** `{"error": {"code", "message", "details"}}` — 500s are sanitized, never leaking internals (§6.14).
- **Success envelopes** — single `{"data": {...}}`; list `{"data": [...], "next_cursor"}`; keyset cursors are opaque base64 `(sort_value, id)`.
- **Event vocabulary** — 97 registered realtime event names; unregistered names are rejected at write time and projection time.
- **Outbox → realtime single write path** — `UNIQUE(outbox_event_id)` gives exactly-once registration under at-least-once delivery; seqs are gapless per channel.
- **Multi-tenancy** — composite-FK migration templates; `realtime_channels/realtime_events` and every workspace tenant table (`members`, `workspace_invitations`, `workspace_invitation_redemptions`, `workspace_slug_history`, `identifier_prefix_registry`, `member_project_access`, `audit_logs`) carry the tenant key with fail-closed RLS policies; `users`/`external_identities` are exempt global tables (§6.1). Workspace-unknown reads (token accept, my-workspaces list, old-slug redirect) go through narrow `SECURITY DEFINER` functions so the policies stay strict everywhere else.

## Auth endpoints (`docs/specs/features/auth.md`)

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/v1/auth/register` · `/login` · `/refresh` · `/logout` · `/logout-all` | password auth + sessions |
| POST | `/api/v1/auth/forgot-password` · `/reset-password` · `/verify-email` | single-use hashed tokens |
| POST | `/api/v1/auth/mfa/setup` · `/enable` · `/disable` · `/verify` | TOTP + backup codes |
| GET / PATCH | `/api/v1/me` · `/api/v1/users/me` | current user + display preferences |
| GET / DELETE | `/api/v1/sessions[/{id}]` | active-session list / revoke |
| GET / POST / DELETE | `/api/v1/workspaces/{ws}/api-tokens[/{id}]` | PAT / agent credentials — hash-only, plaintext returned once on create; `role_override` ≤ holder role (422, validated at create **and** use); agent tokens drop `agent:trigger` (anti-loop); create/list/revoke gated by `token:manage`, cross-holder create needs admin |
| GET | `/api/v1/api-tokens/whoami` | authenticate a Bearer PAT/agent token, resolve its effective principal (workspace/role/scopes) |
| GET | `/api/v1/workspaces/{ws}/audit-logs` | append-only audit query (filter by action/actor + `before`/`after` time-range, cursor pagination; admin+) |
| GET | `/api/v1/auth/oauth/{provider}/start` · `/bind` | OAuth authorization-code + PKCE start (302 to provider); `/bind` is authenticated |
| GET / POST | `/api/v1/auth/oauth/{provider}/callback` | exchange code (validates `state`), login-or-register-and-bind (A5) or bind to caller (A6) |
| GET / DELETE | `/api/v1/auth/oauth/identities` · `/api/v1/auth/oauth/{provider}` | list bound providers / unbind (keeps ≥1 login method) |

The auth backend is feature-complete for `docs/specs/features/auth.md` except the
§4 frontend pages and the `POST /agents/{agent_id}/tokens` convenience endpoint
(awaiting the agents table). In place now: the global identity tables (`users`,
`sessions`, one-time tokens, `oauth_identities`, `login_attempts`), `api_tokens`,
the append-only `audit_logs` table + query endpoint, the RBAC adjudicator, the
vendor-neutral OAuth round-trip (mock provider in dev for a real code+PKCE
round-trip; production providers operator-configured), session/token-revocation
realtime broadcast (`session.revoked` via outbox → projector, §3.7/§5.6), and
transactional email (Redis dev-mailbox in dev; SMTP via `MESH_SMTP_*` in
production — see `auth/mailer.py`).

## Workspace endpoints (`docs/specs/features/workspace.md`)

| Method | Path | Notes |
| --- | --- | --- |
| POST / GET | `/api/v1/workspaces` | create (creator becomes owner) / list mine (keyset cursor) |
| GET | `/api/v1/workspaces/{id}` · `/api/v1/workspaces/by-slug/{slug}` | member-only; old slugs resolve via `workspace_slug_history` |
| PATCH | `/api/v1/workspaces/{id}` | admin; name/slug/logo/timezone/settings (shallow merge) |
| DELETE | `/api/v1/workspaces/{id}` | owner + typed-slug confirmation (soft delete) |
| POST | `/api/v1/workspaces/{id}/restore` | owner, within retention |
| POST / GET / DELETE | `/api/v1/workspaces/{id}/invitations[/{inv_id}]` | admin; link or email batch, revoke |
| POST | `/api/v1/invitations/accept` | logged-in; atomic + idempotent |
| GET | `/api/v1/invitations/preview` | public, limited fields |
| PATCH | `/api/v1/workspaces/{ws}/members/{id}` | admin; role change (last-owner / agent-owner guards, audited) |

## Project endpoints (`docs/specs/features/project.md`)

| Method | Path | Notes |
| --- | --- | --- |
| POST / GET | `/api/v1/workspaces/{ws}/projects` | create (key registered in prefix registry same-txn; 409 `project_key_taken` / `project_name_taken`) / list (status/visibility/archived/mine/lead filters, keyset cursor) |
| GET / PATCH / DELETE | `/api/v1/projects/{id}` | detail (progress + milestones) / update (`If-Match` optimistic concurrency → 409 `conflict`; tri-state fields) / soft delete (prefix stays reserved) |
| POST | `/api/v1/projects/{id}/archive` · `/unarchive` | lead/admin; archived projects are read-only (writes → 422 `project_archived`) |
| POST / GET | `/api/v1/projects/{id}/updates` | health/status trail (writes back to project; `project_update.added` event) / history |
| GET / POST | `/api/v1/projects/{id}/milestones` | list (overdue derived: `open` + past target) / create |
| PATCH / DELETE | `/api/v1/milestones/{id}` | update (title/description/target_date/state) / delete |
| GET / POST | `/api/v1/workspaces/{ws}/cycles` | list (state/project filters) / create (`ends_at >= starts_at`) |
| PATCH | `/api/v1/cycles/{id}` | update incl. state transitions; completing an `auto_roll` cycle creates the next one (returned as `next_cycle`) |
| POST / GET | `/api/v1/projects/{id}/members` · PATCH / DELETE `/members/{member_id}` | project membership (lead/member/viewer), lead/admin managed |
| GET / POST | `/api/v1/workspaces/{ws}/project-templates` · PATCH / DELETE `/api/v1/project-templates/{id}` · POST `/instantiate` | templates (§3.2b): CRUD + instantiate (key registry-checked; prefill for not-yet-built modules degrades into `skipped`) |

Writes are rate limited per principal+IP (120/min). Private-project realtime events only hit the `project:{id}` channel; public ones additionally hit `workspace:{ws}:projects` (§6.7).

## HTML entry middleware (`docs/specs/features/theme.md` §2.3)

`mesh.web.entry` serves the built SPA shell for HTML document navigations and
implements the first-frame precise-injection tier:

- Reads the HttpOnly `mesh_session` cookie (auth.md §5.5 web session form),
  locates the session **read-only** via SHA-256 `token_hash` (revoked/expired
  → anonymous), resolves the requester's theme negotiation chain
  (`users.settings.theme` → route-derived workspace default: `/w/{slug}/…`
  slug segment, `/invite/{token}` via invitation-preview same-source data →
  system), and inlines the non-sensitive binary
  `window.__MESH_APPEARANCE__ = {"mode":"light|dark"}` before `</head>`.
  The payload carries only the converged mode — never workspace identifiers.
- Cache boundary: personalized responses are `Cache-Control: private,
  no-store` with a per-request nonce CSP; the anonymous shell is byte-stable
  (`public, max-age=300`) with a sha256-hashed FOUC script. `script-src`
  never allows `unsafe-inline`.
- `mesh.web.appearance` holds the server-side chain resolution truth table
  (binary convergence; any lookup failure degrades to no injection — the
  entry never breaks the HTML response).
- Session cookie (`auth.md §5.5`): `login`/`register`/`mfa/verify`/`refresh`
  issue the HttpOnly `mesh_session` cookie (`Secure` derived from `auth_mode`,
  overridable via `MESH_COOKIE_SECURE`; `SameSite=Strict`; `Path=/`;
  `Max-Age` tracks the refresh token TTL, extended when remember-me is used) carrying the
  refresh token — the additive channel this middleware reads; `logout`/`logout-all`
  clear it. The in-body refresh token is retained for the Bearer API flow.

- Deployment: nginx routes HTML document misses (`@app`) to this middleware;
  the built frontend is shared via the `frontend_dist` compose volume
  (`MESH_FRONTEND_DIST_DIR`, default `/srv/mesh/frontend`; absent → 404,
  API unaffected).

## Security notes

- **Secrets are env-only** (`MESH_*`); startup validates required values and fails fast.
- **Auth mode defaults to `production`** (fail-safe). `MESH_AUTH_MODE=dev` enables the development token authenticator (`mesh-dev:<workspace-uuid>` grants access to that workspace) — docker compose sets it for local development only.
- **JWT signing key** — `MESH_JWT_SECRET` signs access tokens; both application factories (the API's and the realtime gateway's `create_app`) share one startup guard (`validate_auth_settings` in `mesh.config`) that refuses to serve in `production` on the well-known dev key (fail-safe). The gateway is an independently deployable unit (§2.2), so it validates its own configuration at startup — forgetting `MESH_JWT_SECRET` on the gateway alone fails closed instead of verifying tokens against the public default. The verification algorithm is pinned from config — a token's own `alg` header is never trusted, so `alg=none` and HS/RS confusion are rejected.
- **Tokens at rest are hashes** — refresh / reset / verification tokens store only SHA-256; the MFA TOTP secret is Fernet-encrypted; plaintext exists only at creation. Login failures are counted on the `(IP, email)` tuple (not email alone) to avoid a lockout DoS, and login/register/reset are rate limited (Redis sliding window, `429` + `Retry-After`).
- **RLS is defense-in-depth** (§6.2 rule 5): policies are installed on `realtime_channels`/`realtime_events` and on every workspace tenant table (migration `0004`) using the `mesh.workspace_id` GUC (set via `mesh.db.tenant.set_tenant_context`). Without the GUC the policies cannot even be evaluated — reads fail closed. PostgreSQL bypasses RLS for table owners (and superusers), so the **API and realtime gateway connect as the restricted, non-owner role `mesh_app`** (created by migration `0002`), which makes RLS enforce on the app path; the app sets the tenant GUC at the start of every tenant-scoped request (membership gate, channel authorization, replay, reconciliation). The **worker keeps the owner role** for the inherently cross-tenant relay / projector / retention / invitation-sweep loops. `MESH_APP_DATABASE_URL` carries the restricted-role URL (falls back to `MESH_DATABASE_URL` when unset); the `mesh_app` password is `MESH_APP_DB_PASSWORD`. Composite FKs remain the primary tenant guard — cross-workspace references are rejected at INSERT time.
- **Invitation tokens are hashes** — only the SHA-256 of an invitation token is stored; the plaintext exists only in the create response. `max_uses`/`expires_at` are never NULL (no unlimited/never-expiring links), and explicit values are bounded by workspace-configurable caps.
- **App-path statement timeout** — API/gateway sessions set PostgreSQL `statement_timeout` (default 30s, `MESH_APP_STATEMENT_TIMEOUT`; `0` disables) so a runaway query is cancelled instead of holding a connection and client request indefinitely. The worker path (relay / projector / retention) is exempt — its loops run long-lived cross-tenant maintenance.

## Local development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.lock   # reproducible: the lockfile is the authoritative install source
pip install -e . --no-deps

# PostgreSQL 16 + Redis (or use the repo-root docker-compose stack)
export MESH_DATABASE_URL=postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh
export MESH_REDIS_URL=redis://127.0.0.1:6379/0

alembic upgrade head
uvicorn mesh.api.app:create_app --factory --reload
```

Tests (real PostgreSQL 16 + Redis required; `MESH_TEST_DATABASE_URL` /
`MESH_TEST_REDIS_URL` override defaults):

```bash
pytest --cov=mesh --cov-report=term-missing   # unit + real e2e, coverage ≥90%
```

## Dependency management (lockfile)

`pyproject.toml` carries semantic version ranges; the committed lockfiles are
the **authoritative reproducible install source** for CI, Docker and local
venvs — same pins everywhere:

| File | Contents | Consumed by |
| --- | --- | --- |
| `requirements.lock` | runtime deps, hash-pinned | `backend/Dockerfile`, production images |
| `requirements-dev.lock` | runtime + dev deps, hash-pinned (constrained to `requirements.lock`) | CI `test` job, local development |

Both are universal (cross-platform) resolutions generated with Python 3.12.
After any dependency change in `pyproject.toml`, regenerate both and commit:

```bash
pip install uv
uv pip compile pyproject.toml --universal --generate-hashes --no-emit-package mesh-backend -o requirements.lock
uv pip compile pyproject.toml --all-extras -c requirements.lock --universal --generate-hashes --no-emit-package mesh-backend -o requirements-dev.lock
pip-audit --strict -r requirements.lock && pip-audit --strict -r requirements-dev.lock
```

The CI `supply-chain` job runs `pip-audit --strict` on both lockfiles on every
push/PR (and weekly), failing the build on any known CVE. Ignore policy: none
today — a future `--ignore-vuln` requires an inline comment with the reason
and a review-by date; silent ignores are forbidden.
