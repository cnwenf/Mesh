# Kanban Views Definition Layer Implementation Plan (MES-43)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the issue-decoupled slice of kanban.md — the `views` table (JSONB projection config), views CRUD + config PATCH + WIP config + reorder endpoints, the `view.updated` realtime event via the outbox single write path, and the frontend kanban page shell (column skeleton from `group_by`, view switcher, filter/sort/group/WIP config panels, §6.12 empty states, i18n) — with NO real issue data, NO moves/projection/replay.

**Architecture:** New backend module `mesh/views/` (service owns transactions, sets the `mesh.workspace_id` GUC, routes resolve workspace-less paths via a narrow SECURITY DEFINER lookup — same pattern as `mesh/project/`). Config JSONB is validated by a whitelist Pydantic schema layer before storage; `view.updated` rides the outbox → realtime projector path (`emit_realtime`). Frontend adds `src/features/board/` wired to the views API; columns render from the saved `group_by`/`board_settings` config with empty-state bodies (projection query is the excluded MES-33 remainder).

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x (async), Alembic, PostgreSQL 16 (RLS, partial expression unique indexes), pytest/pytest-cov, httpx ASGITransport; React 18 + TypeScript, react-router-dom, react-intl, zustand, vitest, Playwright/chrome-devtools for real UI ops.

## Global Constraints

- **Spec authority:** `docs/specs/features/kanban.md` + `docs/specs/README.md` §6 (§6.2/§6.3/§6.7/§6.12/§6.14). On conflict, README §6 wins; the issue text's "422" for config validation yields to kanban §3.3/§6.14 → `400 validation_error` with named codes (`invalid_filters`, `invalid_group_by`, `invalid_sort`, `invalid_board_settings`), `400 filter_too_complex` for depth >3 / conditions >20.
- **Excluded (MES-33 remainder, gated on MES-31):** `view_issue_positions`, atomic move + WIP enforcement, `GET /views/{id}/issues` grouped projection, realtime incremental merge / `resume_from` / `resync_required`, real-data drag, optimistic updates + 409 convergence on cards.
- **Multi-tenancy (README §6.2):** `views` carries `workspace_id`; `UNIQUE (workspace_id, id)`; composite FKs `(workspace_id, project_id)→projects(workspace_id, id)` and `(workspace_id, owner_member_id)→members(workspace_id, id)` ON DELETE CASCADE; RLS policy `USING (workspace_id = current_setting('mesh.workspace_id')::uuid)` + app-role (`mesh_app`) privileges; workspace-less paths use a SECURITY DEFINER resolver (`mesh_view_workspace_id`) with EXECUTE revoked from PUBLIC.
- **Name/default uniqueness (README §6.3):** partial EXPRESSION unique indexes — `uq_views_name ON views (workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'), name)` and `uq_views_default ON views (workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000')) WHERE is_default`.
- **Events (README §6.7):** only registered names — `view.updated` (and `view.presence`, not in this slice). Business tx writes ONLY `outbox_events(event_type='realtime.publish')` via `mesh.outbox.service.emit_realtime`; never `realtime_events` directly. Delete has no registered `view.deleted` → emit `view.updated` with `deleted: true` in payload.
- **API (README §6.14):** `/api/v1` prefix; `{"data": ...}` / `{"data": [...], "next_cursor": ...}` envelopes; keyset cursor pagination; `If-Match: <updated_at>` optimistic concurrency → `409 conflict`; error envelope `{"error": {"code","message","details"}}`; rate limiting on writes (`429 rate_limited` + `Retry-After`).
- **Auth (kanban §3.4):** private views visible/writable by `owner_member_id` only (foreign read → 404, foreign write → 403); shared views readable by workspace members (project-scoped: project visibility gate), writable by owner / workspace admin (`project:manage`) / project lead (project-scoped). Guests cannot create.
- **No-op PATCH (§6.9):** empty diff emits nothing.
- **WIP storage (kanban §2.5):** simple implementation — `views.board_settings.wip` JSONB only (no `board_wip_limits` table in this slice; enforcement is remainder).
- **Testing:** UT ≥ 90% (pytest-cov measured, overall AND new code); real e2e (real uvicorn subprocess + real PostgreSQL + real API calls + DB durability + RLS negative tests); real UI operations; no mock theatre.
- **Git:** author/committer `cnwenf <cnwenf@outlook.com>`; `core.hooksPath /dev/null`; NO `Co-Authored-By` lines ever; rebase on latest main before PR.
- **Anonymity:** zero references to any reference product / source in code, comments, docs, commits, branch names.
- **i18n (README §6.18/§6.12):** all visible UI strings externalized to `en.json` + `zh-CN.json`; semantic design tokens only, WCAG AA contrast, keyboard reachable.

---

## File Structure

### Backend

| File | Responsibility |
|---|---|
| `backend/src/mesh/db/models/view.py` (create) | `View` ORM model — kanban §2.2 columns, CHECKs, composite FKs, `uq_views_ws_id`, partial expression indexes (name/default), §2.8 indexes. |
| `backend/src/mesh/db/models/__init__.py` (modify) | Register `View` in `__all__` (e2e TRUNCATE + metadata rely on it). |
| `backend/migrations/versions/0008_views.py` (create) | DDL mirroring the model: table, constraints, indexes, RLS policy, `mesh_view_workspace_id` SECURITY DEFINER resolver, `mesh_app` grants. Rev 0008 ← 0007 (renumber if main moved). |
| `backend/src/mesh/views/__init__.py` (create) | Package marker. |
| `backend/src/mesh/views/config.py` (create) | Pure whitelist validators for `filters` / `sort` / `board_settings` / `group_by` / `layout` / `visibility` + named-code `ValidationError`s; filter depth/condition counters → `filter_too_complex`. Field/op matrices from kanban §2.3. |
| `backend/src/mesh/views/schemas.py` (create) | Pydantic v2 request models: `CreateViewRequest`, `UpdateViewRequest` (all-optional tri-state), `WipRequest`, `ReorderViewsRequest`. |
| `backend/src/mesh/views/service.py` (create) | `ViewService` — transactions, tenant GUC, authz gates, CRUD + duplicate + wip + reorder, `If-Match`, no-op diff, `view.updated` emission, serialization (`render_view`). |
| `backend/src/mesh/views/routes.py` (create) | FastAPI router — the 8 endpoints, membership gate (`require_workspace` / `resolve_workspace_context`), write rate limit, envelope dicts. |
| `backend/src/mesh/api/app.py` (modify) | Instantiate `ViewService` on `app.state.view_service`; include router. |
| `backend/tests/unit/test_view_config.py` (create) | Validator unit tests (pure functions — every rule + boundary). |
| `backend/tests/unit/test_view_service.py` (create) | Service against real DB: CRUD, authz matrix, default-uniqueness 409, no-op, wip merge, reorder, outbox rows. |
| `backend/tests/unit/test_view_api.py` (create) | In-process ASGI app: envelopes, codes (400/403/404/409/429), If-Match, pagination cursor round-trip. |
| `backend/tests/e2e/test_view_e2e.py` (create) | Real server: full flow + durability + T1 cross-tenant (API 404 + composite FK INSERT rejection) + RLS live + outbox `view.updated`. |

### Frontend

| File | Responsibility |
|---|---|
| `frontend/src/features/board/types.ts` (create) | `View`, `FilterGroup`, `FilterCondition`, `SortRule`, `BoardSettings`, `WipLimit` types mirroring backend schemas. |
| `frontend/src/features/board/api.ts` (create) | Views API client over `getApiClient()`: list/create/get/update/delete/duplicate/wip/reorder. |
| `frontend/src/features/board/columns.ts` (create) | Pure: `columnsForView(view)` → column descriptors from `group_by` + `board_settings.columns`/`collapsed_columns`/`wip` (7 `state_category` keys, `priority` 5 tiers, `status/assignee/project/label` config-skeleton placeholders). |
| `frontend/src/features/board/ViewSwitcher.tsx` (create) | Sidebar view list + create + rename/duplicate/default/delete menu; selection syncs URL. |
| `frontend/src/features/board/BoardColumns.tsx` (create) | Column container: header (color token + label + count 0 + WIP badge), collapsed state, §6.12 empty body, quick-create stub (disabled, tooltip: remainder slice). |
| `frontend/src/features/board/FilterConfigPanel.tsx` (create) | AND/OR condition builder (field/op/value rows, nested group add) writing draft `filters`; preview count shows "—" (no real data). |
| `frontend/src/features/board/SortConfigPanel.tsx` (create) | Sort rule list (field + asc/desc, reorder, add/remove). |
| `frontend/src/features/board/WipConfigPanel.tsx` (create) | Per-column limit + enforcement (warn/block) editor → `PATCH /views/{id}/wip`. |
| `frontend/src/features/board/ViewSaveBar.tsx` (create) | Dirty-config bar: save / save-as / discard (§4.2). |
| `frontend/src/features/board/BoardPage.tsx` (create) | Shell: toolbar (view name, group_by/sub_group_by selectors, filter/sort/display/WIP popover triggers, layout switcher board/list), left switcher, main columns; §6.12 loading/empty/error/permission states; URL `/w/{slug}/board[/{viewId}]`. |
| `frontend/src/features/board/board.css` (create) | Styles via semantic tokens only. |
| `frontend/src/features/board/__tests__/*.test.ts(x)` (create) | vitest: columns.ts logic, api client (fetch mock), panels render + interactions, BoardPage states. |
| `frontend/src/i18n/catalogs/en.json` + `zh-CN.json` (modify) | `board.*` message keys. |
| `frontend/src/App.tsx` (modify) | Routes `/w/:workspaceSlug/board` and `/w/:workspaceSlug/board/:viewId`. |
| `frontend/src/shell/Sidebar.tsx` (modify) | Kanban nav entry (i18n label, deep link). |

---

## Task 1: `views` model + migration + RLS

**Files:**
- Create: `backend/src/mesh/db/models/view.py`, `backend/migrations/versions/0008_views.py`
- Modify: `backend/src/mesh/db/models/__init__.py`
- Test: `backend/tests/unit/test_view_model.py`, existing `backend/tests/unit/test_model_migration_drift.py` must stay green

**Interfaces:**
- Produces: `mesh.db.models.view.View` (tablename `views`), constants `VIEW_LAYOUT_VALUES = ("board","list","timeline","table")`, `VIEW_VISIBILITY_VALUES = ("private","shared")`; migration head `0008` (down `0007`); SQL function `mesh_view_workspace_id(uuid)`.

**DDL (exact):**

```sql
CREATE TABLE views (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id      UUID NULL,
  owner_member_id UUID NOT NULL,
  name            TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
  layout          TEXT NOT NULL DEFAULT 'board' CHECK (layout IN ('board','list','timeline','table')),
  visibility      TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','shared')),
  filters         JSONB NOT NULL DEFAULT '{}'::jsonb,
  group_by        TEXT NULL,
  sub_group_by    TEXT NULL,
  sort            JSONB NOT NULL DEFAULT '[]'::jsonb,
  display_fields  JSONB NOT NULL DEFAULT '[]'::jsonb,
  board_settings  JSONB NOT NULL DEFAULT '{}'::jsonb,
  position        REAL NOT NULL DEFAULT 0,
  is_default      BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, owner_member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_views_ws_id ON views(workspace_id, id);
CREATE UNIQUE INDEX uq_views_name ON views(workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'), name);
CREATE UNIQUE INDEX uq_views_default ON views(workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000')) WHERE is_default;
CREATE INDEX idx_views_workspace ON views(workspace_id, position);
CREATE INDEX idx_views_project ON views(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_views_owner ON views(owner_member_id);
CREATE INDEX idx_views_visibility ON views(workspace_id, visibility);
ALTER TABLE views ENABLE ROW LEVEL SECURITY;
CREATE POLICY mesh_views_tenant ON views USING (workspace_id = current_setting('mesh.workspace_id')::uuid);
CREATE OR REPLACE FUNCTION mesh_view_workspace_id(p_id uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT v.workspace_id FROM views v WHERE v.id = p_id
$$;
REVOKE EXECUTE ON FUNCTION mesh_view_workspace_id(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mesh_view_workspace_id(uuid) TO mesh_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON views TO mesh_app;
```

**ORM notes:** mirror `db/models/project.py` style — `Index("uq_views_default", "workspace_id", text("COALESCE(project_id, '00000000-0000-0000-0000-000000000000')"), unique=True, postgresql_where=text("is_default"))`; `uq_views_name` uses the same `text()` expression column; composite FKs via `ForeignKeyConstraint(..., ondelete="CASCADE")`; `updated_at` has `onupdate=text("now()")`? — project model does NOT set onupdate (service bumps via SELECT ... now()); follow project: service explicitly sets `updated_at = now()` on writes so If-Match semantics are server-controlled. Check how project service bumps updated_at — replicate exactly.

- [ ] **Step 1:** write `tests/unit/test_view_model.py` asserting table metadata: columns/defaults/CHECKs present on `Base.metadata`, composite FKs exist, both partial expression unique indexes render with `postgresql_where`, `uq_views_ws_id` present. Run → FAIL (import error).
- [ ] **Step 2:** implement `view.py` model + register in `models/__init__.py`. Run model test → PASS.
- [ ] **Step 3:** write migration `0008_views.py` with the exact DDL + full `downgrade()`. Run `pytest tests/unit/test_model_migration_drift.py tests/unit/test_migration_matches_spec.py -v` → PASS (drift test compares model vs migrated DB).
- [ ] **Step 4:** migrate the local test DB (`provision_database` fixture does it) and verify RLS + resolver manually: `psql` as `mesh_app` — SELECT without GUC returns 0 rows; `mesh_view_workspace_id` works.
- [ ] **Step 5:** commit `feat(kanban): views 数据模型 + 迁移 + RLS(kanban.md §2.2/§2.8,README §6.2/§6.3,MES-43)`.

## Task 2: config validators (pure)

**Files:** Create `backend/src/mesh/views/config.py`, `backend/src/mesh/views/__init__.py`; Test `backend/tests/unit/test_view_config.py`

**Interfaces:**
- `validate_filters(value: Any) -> dict` — returns normalized `{}` or `{"operator": "AND"|"OR", "conditions": [...]}`; raises `ValidationError(code="invalid_filters", details={"path": ...})` on shape violations; `ValidationError(code="filter_too_complex", details={"depth": n, "conditions": m})` when nesting depth > `FILTER_MAX_DEPTH = 3` or total conditions > `FILTER_MAX_CONDITIONS = 20`.
- `validate_sort(value: Any) -> list[dict]` — each `{"field": <whitelist>, "order": "asc"|"desc"}` or `{"field_kind": "custom_field", "field_def_id": str, "order": ...}`; code `invalid_sort`.
- `validate_board_settings(value: Any) -> dict` — keys ⊆ `{columns, collapsed_columns, card_fields, wip}`; string arrays; `wip: {<group_key>: {"limit": int ≥ 1, "enforcement": "warn"|"block"}}`; code `invalid_board_settings`.
- `validate_group_by(value: str | None) -> str | None` — `None` ok; else ∈ `GROUP_BY_FIELDS = ("state_category","status","assignee","priority","project","label")`; code `invalid_group_by`.
- `validate_layout(value) -> str` / `validate_visibility(value) -> str` — codes `invalid_layout` / `invalid_visibility`.
- Constants: `FILTER_FIELDS` (15 built-ins from kanban §2.3), `FILTER_OPS` (11), `FIELD_OP_RESTRICTIONS = {"label": {"in","not_in"}, "q": {"contains"}}`, `SORT_FIELDS = ("position","priority","due_date","start_date","created_at","updated_at","status_id")`, `STATE_CATEGORY_KEYS` (7), `PRIORITY_KEYS` (5 — confirm issue.md ordering: urgent/high/medium/low/none), `WIP_ENFORCEMENTS = ("warn","block")`.

**Rules (kanban §2.3/§3.4):** a condition is one of `{field, op, value}` / `{field_kind:"custom_field", field_def_id, op, value}` / nested `{operator, conditions}`; unknown extra keys rejected; `is_null`/`is_not_null` take no value (value must be absent or null); `in`/`not_in` require non-empty list; `contains` text-only fields; `q` only `contains`; `label` only `in`/`not_in`; value type sanity per field family (uuid-ish fields: non-empty str; date fields: str; booleans not accepted as values).

- [ ] **Step 1:** write `test_view_config.py` — ≥ 40 cases: valid shapes, every rejection code, depth boundary (3 ok / 4 → filter_too_complex), condition count boundary (20 ok / 21 → filter_too_complex), op restrictions, wip enforcement enum, custom-field condition/sort shapes. Run → FAIL.
- [ ] **Step 2:** implement `config.py`. Run → PASS.
- [ ] **Step 3:** commit `feat(kanban): 视图配置白名单校验器(filters/sort/board_settings/group_by,kanban.md §2.3/§3.4,MES-43)`.

## Task 3: schemas + service (CRUD, authz, events)

**Files:** Create `backend/src/mesh/views/schemas.py`, `backend/src/mesh/views/service.py`; Test `backend/tests/unit/test_view_service.py`

**Interfaces:**
- `CreateViewRequest`: `name: str(1..100)`, `layout: str = "board"`, `visibility: str = "private"`, `project_id: str | None`, `filters: dict = {}`, `group_by: str | None`, `sub_group_by: str | None`, `sort: list = []`, `display_fields: list = []`, `board_settings: dict = {}`, `is_default: bool = False`.
- `UpdateViewRequest`: all fields optional (tri-state via `model_fields_set`).
- `WipRequest`: `group_key: str`, `limit: int | None` (None = remove), `enforcement: str = "warn"`.
- `ReorderViewsRequest`: `view_ids: list[str]` (ordered).
- `ViewService(factory)`:
  - `resolve_view_workspace(view_id) -> UUID | None` (SECURITY DEFINER lookup, no GUC)
  - `create_view(*, actor: Member, workspace_id, body) -> dict` — validate all config; project existence/visibility when `project_id` set; 409 `view_name_taken` on unique-name conflict; `is_default=True` → clear previous default in same scope in-tx; `position = max(position)+1` in scope; emit `view.updated` (channels `view:{id}` + `workspace:{ws}:views`); audit row.
  - `list_views(*, viewer: Member, workspace_id, project_id: UUID | None = None, limit, cursor) -> (list[dict], next_cursor)` — visible = own private ∪ shared (workspace-level: all members; project-scoped: project-visible); keyset `(position, id)` ascending via `mesh.api.pagination.paginate`.
  - `get_view(*, viewer, workspace_id, view_id) -> dict` — foreign private → 404.
  - `update_view(*, actor, workspace_id, view_id, patch: dict(fields_set resolved), if_match: str | None) -> dict` — row lock (`with_for_update`) when writing; If-Match vs `updated_at.isoformat()` (strip quotes) → 409 `conflict`; write gate (owner / admin / project lead); validate changed config; JSONB shallow merge: `filters/sort/display_fields` replaced, `board_settings = {**old, **new}`; name conflict → 409; `is_default` set → clear others same scope in-tx (unique index backstop → 409 `default_view_conflict`); no-op → return current, emit nothing; else bump `updated_at = now()`, emit `view.updated` with full render on `view:{id}` + `workspace:{ws}:views`.
  - `delete_view(*, actor, workspace_id, view_id)` — write gate; delete; emit `view.updated` `{id, deleted: True}` on `workspace:{ws}:views`.
  - `duplicate_view(*, actor, workspace_id, view_id) -> dict` — read gate; copy config, new owner = actor, name = `name + " (copy)"` re-checked vs uniqueness (auto-suffix loop `(2)`…); `is_default=False`, position max+1; emit `view.updated`.
  - `patch_wip(*, actor, workspace_id, view_id, body: WipRequest) -> dict` — write gate; shallow merge `board_settings.wip[group_key] = {limit, enforcement}` or delete key when `limit is None`; emit `view.updated`.
  - `reorder_views(*, actor, workspace_id, view_ids)` — writer must own/ manage each; assign positions 1.0, 2.0, …; no event (sidebar order is private presentation) — actually emit nothing (YAGNI; not in registry semantics).
  - `render_view(view: View, *, can_write: bool) -> dict` — id/workspace_id/project_id/owner_member_id/name/layout/visibility/filters/group_by/sub_group_by/sort/display_fields/board_settings/position/is_default/created_at/updated_at/`can_write`.

**Authz gates (service helpers):** `_assert_can_read(viewer, view, session)` (private: owner else 404; shared project-scoped: reuse project visibility — `ProjectMember`/`MemberProjectAccess` lookup or public project; workspace managers bypass), `_assert_can_write(viewer, view, session)` (owner / `role_satisfies(viewer.role, "project:manage")` / project lead for project-scoped else 403; foreign private write → 403). Guests cannot create (403).

- [ ] **Step 1:** write failing service tests (real DB, same helper style as `test_project_service.py`): create happy path + persisted row; invalid config codes bubble; name conflict 409 (workspace-level AND project-level scopes; same name in different project allowed); default uniqueness 409 + in-tx default handoff; get/list visibility matrix (own private / foreign private 404 / shared readable / project-scoped shared vs outsider); update If-Match 409; no-op patch emits nothing (outbox count unchanged); board_settings shallow merge keeps other keys; delete removes row + emits deleted marker; duplicate naming + ownership; wip set/remove; reorder positions; outbox rows carry `event=view.updated` with right channels.
- [ ] **Step 2:** implement `schemas.py` + `service.py`. Iterate to green.
- [ ] **Step 3:** commit `feat(kanban): views 服务层 —— CRUD/复制/WIP/排序 + 鉴权 + view.updated outbox 事件(kanban.md §3,README §6.6/§6.7,MES-43)`.

## Task 4: routes + app wiring + API-level tests

**Files:** Create `backend/src/mesh/views/routes.py`; Modify `backend/src/mesh/api/app.py`; Test `backend/tests/unit/test_view_api.py`

**Endpoints (kanban §3.1 independent subset):**

| Method | Path | Notes |
|---|---|---|
| GET | `/workspaces/{workspace_id}/views` | `require_workspace()` gate; `project_id`/`limit`/`cursor` query params |
| POST | `/workspaces/{workspace_id}/views` | 201; write rate limit |
| GET | `/views/{view_id}` | SECURITY DEFINER resolve → membership gate |
| PATCH | `/views/{view_id}` | `If-Match` header → service |
| DELETE | `/views/{view_id}` | 204 |
| POST | `/views/{view_id}/duplicate` | 201 |
| PATCH | `/views/{view_id}/wip` | 200, returns `{"data": {"board_settings": ...}}`? — returns full view render |
| PATCH | `/workspaces/{workspace_id}/views/reorder` | 200 list render |

- Route helpers copied from project routes: `_path_uuid` (404), `_rate_limit_write` (`view-write:{user}:{ip}` 120/60s), `_context_for`.
- `app.py`: `from mesh.views.routes import router as view_router`, `app.state.view_service = ViewService(session_factory)`, `app.include_router(view_router)`.

- [ ] **Step 1:** write `test_view_api.py` (in-process ASGI, same fixture style as `test_project_api.py`): unauthenticated 401; create 201 envelope; list envelope + cursor round-trip; get 404 unknown UUID shape and unknown id; PATCH If-Match stale → 409; cross-workspace access → 404; duplicate 201; wip patch; reorder; invalid config 400 codes over the wire; rate-limit headers present.
- [ ] **Step 2:** implement `routes.py`, wire `app.py`. Green.
- [ ] **Step 3:** commit `feat(kanban): views REST 端点 + 应用接线(§6.14 包络/游标/If-Match/限流,MES-43)`.

## Task 5: real e2e (durability + T1 + RLS + events)

**Files:** Create `backend/tests/e2e/test_view_e2e.py`

- [ ] **Step 1:** write e2e tests (real uvicorn subprocess via `api_client` fixture, `session_factory` for DB asserts):
  - full flow durable: register/login → workspace → project → create view → row in `views` (real SQL assert) → get/patch/duplicate/wip/reorder → delete → gone;
  - outbox `view.updated` rows exist after create/patch with channels `view:{id}` and `workspace:{ws}:views` (query `outbox_events`);
  - T1 cross-tenant: workspace B token GET/ PATCH workspace A's view → 404; raw composite FK INSERT referencing another workspace's member/project rejected by constraint (owner engine);
  - RLS live: app-role connection without GUC sees 0 `views` rows, with `set_tenant_context` sees the tenant's rows (pattern from `test_workspace_rls_e2e.py`/`test_tenant_rls_e2e.py`);
  - optimistic concurrency over the wire: two PATCHes same If-Match → second 409;
  - duplicate default view → 409.
- [ ] **Step 2:** run `pytest tests/e2e/test_view_e2e.py -v` → green. Commit `test(kanban): views 真实 e2e —— CRUD 落库/事件/T1 跨租户/RLS/乐观并发(MES-43)`.

## Task 6: coverage gate

- [ ] **Step 1:** `pytest --cov=src/mesh --cov-report=term-missing` — overall ≥ 90%; `--cov=src/mesh/views` ≥ 90% on the new module; patch holes with targeted tests. Commit `test(kanban): 补足覆盖率(views 模块 ≥90%,MES-43)` if new tests added.

## Task 7: frontend — types, API client, column logic

**Files:** Create `frontend/src/features/board/types.ts`, `api.ts`, `columns.ts`, `board.css`, `__tests__/columns.test.ts`, `__tests__/api.test.ts`; Modify i18n catalogs

- [ ] **Step 1:** write `columns.test.ts` (state_category 7 columns in fixed order; priority 5; `board_settings.columns` reorders/filters state_category columns; collapsed flag; wip badge data; unknown group_by → fallback state_category). Write `api.test.ts` (fetch mocked via existing api client test pattern — check `src/api/__tests__`): every endpoint path/method/body.
- [ ] **Step 2:** implement `types.ts` / `columns.ts` / `api.ts`. Green. Commit `feat(board): 视图类型 + API 客户端 + 列配置派生(kanban.md §2.4,MES-43)`.

## Task 8: frontend — components + page + routing + i18n

**Files:** Create `ViewSwitcher.tsx`, `BoardColumns.tsx`, `FilterConfigPanel.tsx`, `SortConfigPanel.tsx`, `WipConfigPanel.tsx`, `ViewSaveBar.tsx`, `BoardPage.tsx`, tests under `__tests__/`; Modify `App.tsx`, `Sidebar.tsx`, catalogs

**Behavior:**
- `BoardPage` loads `listViews` (loading skeleton per §6.12; error → retry toast; empty → empty illustration + "新建视图" primary action); first/default view auto-selected; URL sync `/w/{slug}/board/{viewId}`.
- Toolbar: group_by select (6 built-ins), sub_group_by select (none + 6), layout switcher (board enabled; list → same shell note; timeline/table → disabled with "即将推出" i18n, §2.2 501 fallback spirit), filter/sort/display/WIP buttons opening popover panels.
- Config edits mutate a local draft; dirty → `ViewSaveBar` (save = PATCH; save-as = POST duplicate-then-rename flow? — simpler: save-as = create new with draft config; discard = reset draft).
- Columns: header color from semantic tokens per category, count "0", WIP "0/5" badge (warn amber / block red tokens when count would exceed — count always 0 here), collapse toggle, body = §6.12 empty state ("暂无卡片") — projection is the remainder slice; quick-create button visible but disabled with tooltip.
- All strings via `useT()` `board.*` keys; both catalogs updated in lockstep (missing-key CI: check `frontend/src/i18n/__tests__` for parity test — keep parity).

- [ ] **Step 1:** component tests first (render + key interactions: switch selection fires callback, save bar appears on draft change, wip panel calls api.setWip, filter panel builds nested condition, empty/loading/error states). FAIL.
- [ ] **Step 2:** implement components + page + routing + sidebar entry + catalogs. Green (`npm run test`, `npm run typecheck`, `npm run lint`).
- [ ] **Step 3:** commit `feat(board): 看板页面 shell —— 视图切换器/列骨架/筛选/排序/WIP 配置面板 + i18n(kanban.md §4,README §6.12,MES-43)`.

## Task 9: real UI verification (docker compose + browser ops)

- [ ] **Step 1:** `docker compose up -d --build` (or the repo Quick Start path — read `README.md`/`docker-compose.yml` first); migrate; seed a user/workspace via API.
- [ ] **Step 2:** drive the real UI via chrome-devtools MCP: open board page → empty state renders → create view via dialog → switcher shows it → change group_by to priority → 5 columns render → open WIP panel, set limit → persisted (reload, still there) → filter panel add condition → save bar → save → PATCH 200 in network → rename/duplicate/default/delete flows → console clean.
- [ ] **Step 3:** screenshots attached to the completion comment only if they travel as attachments (never local paths in text).

## Task 10: docs, hygiene, PR

- [ ] **Step 1:** update `CHANGELOG.md` (new version, feature list), root `README.md` (module status line for kanban views layer), `frontend/README.md` if it tracks pages. kanban.md untouched (no spec change needed; note the one interpretation decision — 400 vs 422 — in the completion comment, not the spec).
- [ ] **Step 2:** `git log -1 --format='%an <%ae> | %cn <%ce>'` = `cnwenf <cnwenf@outlook.com>` on every commit; `git log @{u}..HEAD --format=%B | grep -i co-authored-by` empty; rebase on `origin/main`; anonymity scan: `git diff origin/main...HEAD | grep -inE 'platform|同类|参考|外部出处'` empty (allow the word inside spec-quoted strings only if pre-existing).
- [ ] **Step 3:** push branch, open PR to main (gh CLI), title `feat(kanban): views 定义层 —— views 表 + CRUD + 配置 UI 静态层(MES-43)`.
- [ ] **Step 4:** post the single completion comment with acceptance mention (platform-mentioning skill), PR URL, verification summary (UT% both bars, e2e list, UI ops done). Pin `pr_url` metadata.

## Self-Review Notes

- Spec coverage: §2.2 model ✔ T1; §2.3 config structures ✔ T2; §2.8 indexes ✔ T1; §3.1 independent endpoints ✔ T4 (8 of 11 — the 3 excluded are remainder, confirmed against issue scope); §3.3 error codes ✔ T3/T4/T5; §3.4 pagination/auth/security ✔ T3/T4; §6.2/§6.3/§6.7/§6.12/§6.14 ✔ global constraints; §4 UI shell ✔ T7/T8; §5.1 in-scope bullets ✔ (CRUD, JSONB persistence, group_by columns, scope auth, default uniqueness, save/discard bar, filter builder without live preview count); §5.1 projection/drag/WIP-enforcement/realtime-merge bullets are the excluded remainder; §5.2 is remainder except `view.updated` emission ✔ T3/T5.
- Risk: migration head collision with MES-32-C1 — T10 rebase step renumbers to the next free rev + fixes `down_revision` before PR.
- Type consistency: `render_view` keys consumed verbatim by frontend `types.ts` `View` (T7 mirrors T3).
