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
- `db/` — SQLAlchemy 2.x models, Alembic migrations, multi-tenant infrastructure (§6.2: `UNIQUE(workspace_id, id)` + composite-FK templates, RLS GUC `mesh.workspace_id`, global-table exemption list).
- `events/vocab.py` — canonical realtime event vocabulary (§6.7, kept in sync with the README registry by tests + CI).
- `outbox/` — transactional outbox (§6.6): `emit_event`/`emit_realtime` write in the business transaction; the relay claims `FOR UPDATE SKIP LOCKED`; the projector is the ONLY writer of `realtime_events` and allocates per-channel `seq` in the same transaction.
- `realtime/` — gateway protocol, channel auth hooks, Redis fan-out (Redis is fan-out only; replay truth is in `realtime_events`).
- `workers/` — supervisor (isolated cancel domains + restart backoff), retention purge, process entrypoint.

## Global contracts implemented here

- **Error envelope** `{"error": {"code", "message", "details"}}` — 500s are sanitized, never leaking internals (§6.14).
- **Success envelopes** — single `{"data": {...}}`; list `{"data": [...], "next_cursor"}`; keyset cursors are opaque base64 `(sort_value, id)`.
- **Event vocabulary** — 96 registered realtime event names; unregistered names are rejected at write time and projection time.
- **Outbox → realtime single write path** — `UNIQUE(outbox_event_id)` gives exactly-once registration under at-least-once delivery; seqs are gapless per channel.
- **Multi-tenancy** — composite-FK migration templates; `realtime_channels/realtime_events` carry the tenant key with RLS policies; `users`/`external_identities` are exempt global tables (§6.1).

## Security notes

- **Secrets are env-only** (`MESH_*`); startup validates required values and fails fast.
- **Auth mode defaults to `production`** (fail-safe). `MESH_AUTH_MODE=dev` enables the development token authenticator (`mesh-dev:<workspace-uuid>` grants access to that workspace) — docker compose sets it for local development only.
- **RLS is defense-in-depth** (§6.2 rule 5): policies are installed on `realtime_channels`/`realtime_events` using the `mesh.workspace_id` GUC (set via `mesh.db.tenant.set_tenant_context`). PostgreSQL bypasses RLS for table owners, so production deployments must connect with a non-owner database role; the auth/API module wires the per-request GUC as tenant-scoped endpoints land. Composite FKs remain the primary tenant guard.

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
