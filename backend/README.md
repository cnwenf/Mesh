# Mesh Backend

Python backend for Mesh (see [docs/specs/README.md](../docs/specs/README.md) for the
canonical spec — it is the single source of truth for every contract here).

## Architecture (docs/specs/README.md §2–§3)

Three independently deployable units, all stateless except PostgreSQL:

| Unit | Entrypoint | Responsibility |
| --- | --- | --- |
| API | `uvicorn mesh.api.app:create_app --factory` | REST `/api/v1`, §6.14 envelopes, health checks |
| Worker | `python -m mesh.workers` | Outbox relay, realtime projector, retention purge — each an isolated supervised asyncio task (§2.2) |
| Realtime gateway | `uvicorn mesh.realtime.app:create_app --factory` | WebSocket `/ws`: first-frame auth, per-channel authorization, `resume_from` replay, `resync_required` (§6.7/§6.16) |

Layering inside `src/mesh/`:

- `api/` — FastAPI factory, error/pagination contracts (§6.14), health, deps.
- `auth/` — authentication & authorization core (`docs/specs/features/auth.md`): argon2id password hashing, register/login/logout, short-lived access JWT (fixed `alg`, rejects `none`/HS-RS confusion) + revocable refresh (SHA-256 hash only, rotation with replay detection), single-use reset/verification tokens, TOTP MFA (encrypted secret + one-time backup codes), `(IP, email)` login lockout, Redis sliding-window rate limiting, `users.settings`/`timezone` preferences (`PATCH /api/v1/users/me`).
- `db/` — SQLAlchemy 2.x models, Alembic migrations, multi-tenant infrastructure (§6.2: `UNIQUE(workspace_id, id)` + composite-FK templates, RLS GUC `mesh.workspace_id`, global-table exemption list).
- `events/vocab.py` — canonical realtime event vocabulary (§6.7, kept in sync with the README registry by tests + CI).
- `outbox/` — transactional outbox (§6.6): `emit_event`/`emit_realtime` write in the business transaction; the relay claims `FOR UPDATE SKIP LOCKED`; the projector is the ONLY writer of `realtime_events` and allocates per-channel `seq` in the same transaction.
- `realtime/` — gateway protocol, channel auth hooks, Redis fan-out (Redis is fan-out only; replay truth is in `realtime_events`).
- `validation.py` — shared validators (IANA timezone, supported locale/theme, https-only user-controlled URLs; auth-canonical 422 codes, §6.16/§6.18).
- `workspace/` — workspace module (`docs/specs/features/workspace.md`): workspace CRUD + slug redirects, invitation lifecycle with redemption separation (hash-only tokens, atomic accept, workspace-configurable caps), settings single-source locale, prefix registry (§6.3); plus the RBAC adjudicator (`auth/rbac.py`), the unified `members` roster table (member.md owns), append-only `audit_logs` (auth.md §2.6) and the guest project-visibility hook.
- `workers/` — supervisor (isolated cancel domains + restart backoff), retention purge, invitation expiry sweep, process entrypoint.

## Global contracts implemented here

- **Error envelope** `{"error": {"code", "message", "details"}}` — 500s are sanitized, never leaking internals (§6.14).
- **Success envelopes** — single `{"data": {...}}`; list `{"data": [...], "next_cursor"}`; keyset cursors are opaque base64 `(sort_value, id)`.
- **Event vocabulary** — 96 registered realtime event names; unregistered names are rejected at write time and projection time.
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

`PAT / api_tokens`, the OAuth provider round-trip, session-revocation realtime
broadcast and production SMTP delivery build on the `members` table and land
with later increments; the global identity tables (`users`, `sessions`,
one-time tokens, `oauth_identities`, `login_attempts`), the append-only
`audit_logs` table and the RBAC adjudicator are in place now.

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

## Security notes

- **Secrets are env-only** (`MESH_*`); startup validates required values and fails fast.
- **Auth mode defaults to `production`** (fail-safe). `MESH_AUTH_MODE=dev` enables the development token authenticator (`mesh-dev:<workspace-uuid>` grants access to that workspace) — docker compose sets it for local development only.
- **JWT signing key** — `MESH_JWT_SECRET` signs access tokens; `create_app` refuses to serve in `production` on the well-known dev key (fail-safe). The verification algorithm is pinned from config — a token's own `alg` header is never trusted, so `alg=none` and HS/RS confusion are rejected.
- **Tokens at rest are hashes** — refresh / reset / verification tokens store only SHA-256; the MFA TOTP secret is Fernet-encrypted; plaintext exists only at creation. Login failures are counted on the `(IP, email)` tuple (not email alone) to avoid a lockout DoS, and login/register/reset are rate limited (Redis sliding window, `429` + `Retry-After`).
- **RLS is defense-in-depth** (§6.2 rule 5): policies are installed on `realtime_channels`/`realtime_events` and on every workspace tenant table (migration `0004`) using the `mesh.workspace_id` GUC (set via `mesh.db.tenant.set_tenant_context`). Without the GUC the policies cannot even be evaluated — reads fail closed. PostgreSQL bypasses RLS for table owners (and superusers), so the **API and realtime gateway connect as the restricted, non-owner role `mesh_app`** (created by migration `0002`), which makes RLS enforce on the app path; the app sets the tenant GUC at the start of every tenant-scoped request (membership gate, channel authorization, replay, reconciliation). The **worker keeps the owner role** for the inherently cross-tenant relay / projector / retention / invitation-sweep loops. `MESH_APP_DATABASE_URL` carries the restricted-role URL (falls back to `MESH_DATABASE_URL` when unset); the `mesh_app` password is `MESH_APP_DB_PASSWORD`. Composite FKs remain the primary tenant guard — cross-workspace references are rejected at INSERT time.
- **Invitation tokens are hashes** — only the SHA-256 of an invitation token is stored; the plaintext exists only in the create response. `max_uses`/`expires_at` are never NULL (no unlimited/never-expiring links), and explicit values are bounded by workspace-configurable caps.

## Local development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

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
