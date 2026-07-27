# Kanban Issue-Projection Layer (MES-33) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the issue-coupled projection layer on top of the merged views definition layer (MES-43): grouped projection query with the overall-cursor contract, atomic `move` + WIP enforcement, per-view card ordering (`view_issue_positions`), realtime incremental merge + `view.presence`, and the real-data board frontend (drag / WIP / resync).

**Architecture:** New work lives almost entirely in `backend/src/mesh/views/` (a `projection.py` query compiler + a `moves.py` board-move service) plus one new table/model/migration (`view_issue_positions`) and three new routes on the existing views router. It REUSES the merged issue module (`IssueService.render_issue/_base_visibility_clause/apply_changes_in_tx`, `MoveService.move`, `resolve_default_status`, `filters` limits, `api/pagination` cursors) rather than re-implementing issue logic. The frontend fills real issue data into the existing board shell (`features/board/`).

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.x (async) · Alembic · PostgreSQL 16 (RLS, advisory locks) · Redis · pytest (real DB, ≥90% branch coverage) · React 18 + TS + Vite · vitest · Playwright.

## Global Constraints

- **Spec authority:** `docs/specs/features/kanban.md` + `docs/specs/README.md` §6 (§6.2 multi-tenant, §6.3 numbering, §6.6 outbox, §6.7 realtime, §6.12 UX, §6.14 API/error/pagination). README §6 wins on conflict.
- **Multi-tenancy (README §6.2):** new table stores `workspace_id`, builds composite FKs `(workspace_id, view_id)→views(workspace_id,id)` and `(workspace_id, issue_id)→issues(workspace_id,id)`, `UNIQUE(view_id, issue_id)`, fail-closed RLS policy `mesh_view_issue_positions_tenant`, `mesh_app` grants (mirror migration `0011_views.py`).
- **Overall-cursor contract (README §6.14):** grouped response `{"groups":[{key,label,count,wip?,data}], "next_cursor"}`; `count` = full per-group total, `data` = current page slice; **NO per-group cursor**.
- **Filter limits (README §6.14):** depth ≤3, conditions ≤20 → `400 filter_too_complex`; `statement_timeout` (3s) overrun → `422 query_cost_exceeded`.
- **Events (README §6.7):** only registered names via `emit_realtime`; reuse `issue.updated`/`issue.moved`/`issue.project_changed`/`view.updated`/`view.presence`; NEW `view.wip_exceeded` must be added to BOTH `events/vocab.py` AND README §6.7 table (CI `tests/unit/test_vocab.py` + `tests/docs/check_event_vocab.py` enforce parity).
- **Move atomicity (kanban §3.2):** a drag = ONE transaction: optimistic-lock (`version`) → `pg_advisory_xact_lock(hashtext('wip:'||view_id||':'||group_key))` → WIP count → field change → `view_issue_positions` upsert. `group_by=project` delegates to the issue module two-step move contract (`MoveService.move`, `move_confirmation_required`).
- **Label / custom-field grouping & filtering:** the association tables (`issue_labels`/`issue_custom_field_values`) are owned by the parallel MES-32 line and are NOT in main. Gate them EXACTLY like the issue module does (`group_by=label` → 400 "…awaits the label-property increment"). Do not fake with mocks.
- **Testing doctrine:** NO mock theatre — unit + e2e both run against real PostgreSQL 16 + Redis. Backend gate `pytest --cov=mesh --cov-fail-under=90`; scope module runs with `--cov=mesh.views`. Real e2e spawns real uvicorn. UI via Playwright against a real backend stack.
- **Git:** identity `cnwenf <cnwenf@outlook.com>`; NEVER any `Co-Authored-By` line; `git config core.hooksPath /dev/null`; verify before push. No reference to any competitor product anywhere (code/comments/docs/commits/branches).
- **Anonymization:** no internal/product-reference strings; i18n fully externalized (zh-CN + en) with djb2 catalog `version` recomputed.

## File Structure

**Backend — Create:**
- `backend/src/mesh/db/models/view_position.py` — `ViewIssuePosition` model (table `view_issue_positions`).
- `backend/migrations/versions/0012_view_issue_positions.py` — DDL + RLS + grants (`down_revision="0011"`).
- `backend/src/mesh/views/projection.py` — view-filter compiler + grouped projection query (`ProjectionService.execute_view`).
- `backend/src/mesh/views/moves.py` — `BoardMoveService` (atomic move + reorder + WIP + cross-project delegation).
- `backend/src/mesh/views/presence.py` — `note_presence(...)` (Redis set + `view.presence` emit).
- Tests: `backend/tests/unit/test_view_position_model.py`, `test_view_projection.py`, `test_view_moves.py`, `test_view_presence.py`; `backend/tests/e2e/test_board_projection_e2e.py`.

**Backend — Modify:**
- `backend/src/mesh/db/models/__init__.py` — register `ViewIssuePosition`.
- `backend/src/mesh/views/schemas.py` — add `MoveRequest`, `ReorderRequest`.
- `backend/src/mesh/views/routes.py` — add `GET /views/{id}/issues`, `POST /views/{id}/moves`, `POST /views/{id}/reorder`.
- `backend/src/mesh/api/app.py` — wire `app.state.projection_service`, `app.state.board_move_service`.
- `backend/src/mesh/events/vocab.py` + `docs/specs/README.md` §6.7 — register `view.wip_exceeded`.
- `backend/src/mesh/realtime/session.py` — call `note_presence` on subscribe/unsubscribe of `view:` channels (isolated, last).

**Frontend — Create/Modify (under `frontend/src/features/board/`):**
- `projection.ts` (fetch view issues + move/reorder API + types), `boardRealtime.ts` (frame merge into columns), extend `BoardPage.tsx` (cards, drag, WIP, optimistic+409, resync banner), `BoardColumns.tsx` (render cards + drop targets), `types.ts` (card/group types). i18n keys in `src/i18n/catalogs/{en,zh-CN}.json` (+ recompute djb2). Tests in `__tests__/`. Playwright `e2e/real-board-projection.spec.ts` + `playwright.mes33.config.ts`.

**Docs:** `docs/specs/features/kanban.md` status note, `README.md` status table, `CHANGELOG.md` `[0.12.0]`.

---

## Task 1: `view_issue_positions` model + migration 0012 + RLS

**Files:**
- Create: `backend/src/mesh/db/models/view_position.py`
- Create: `backend/migrations/versions/0012_view_issue_positions.py`
- Modify: `backend/src/mesh/db/models/__init__.py`
- Test: `backend/tests/unit/test_view_position_model.py`

**Interfaces:**
- Produces: `class ViewIssuePosition(Base)` with columns `id, workspace_id, view_id, issue_id, group_key(TEXT default ''), position(REAL default 0), created_at, updated_at`; `__tablename__="view_issue_positions"`; `UniqueConstraint("view_id","issue_id",name="uq_vip_view_issue")`; `Index("idx_vip_view_group_pos","view_id","group_key","position")`; composite FKs `("workspace_id","view_id")→views(workspace_id,id)` CASCADE and `("workspace_id","issue_id")→issues(workspace_id,id)` CASCADE.

- [ ] **Step 1:** Write `test_view_position_model.py` (mirror `test_view_model.py`): assert tablename, columns/nullability, `uq_vip_view_issue`, `idx_vip_view_group_pos`, compiled-DDL contains both composite FK fragments, `group_key` default `''`, `position` REAL.
- [ ] **Step 2:** Run `cd backend && .venv/bin/pytest tests/unit/test_view_position_model.py -v` → FAIL (import error).
- [ ] **Step 3:** Write `view_position.py` model (mirror `db/models/view.py` style; `from mesh.db.base import Base`).
- [ ] **Step 4:** Register import in `db/models/__init__.py` (add `from mesh.db.models.view_position import ViewIssuePosition` + `__all__`).
- [ ] **Step 5:** Write migration `0012_view_issue_positions.py` (copy `0011_views.py` skeleton): `CREATE TABLE view_issue_positions (...)` with both composite FKs ON DELETE CASCADE, `CHECK (position IS NOT NULL)`; `CREATE UNIQUE INDEX uq_vip_view_issue ON view_issue_positions(view_id, issue_id)`; `CREATE INDEX idx_vip_view_group_pos ON view_issue_positions(view_id, group_key, position)`; RLS `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + `CREATE POLICY mesh_view_issue_positions_tenant ... USING (workspace_id = current_setting('mesh.workspace_id')::uuid)`; `GRANT SELECT, INSERT, UPDATE, DELETE ON view_issue_positions TO mesh_app`. `revision="0012"`, `down_revision="0011"`.
- [ ] **Step 6:** Run model test → PASS. Then `cd backend && .venv/bin/pytest tests/unit/test_model_migration_drift.py tests/unit/test_migration_matches_spec.py -v` → confirm the drift guards pass (fix if they enumerate tables).
- [ ] **Step 7:** Verify single-head + fresh-DB migration: `.venv/bin/alembic -c alembic.ini heads` (one head `0012`); against a scratch DB `MESH_DATABASE_URL=...mesh_scratch .venv/bin/alembic upgrade head` then `\d view_issue_positions`.
- [ ] **Step 8:** Commit `feat(kanban): view_issue_positions 每视图排序表 + 迁移 0012 + RLS (kanban.md §2.7/§2.8, README §6.2, MES-33)`.

---

## Task 2: View-filter compiler (view config shape → SQL)

**Files:** Create `backend/src/mesh/views/projection.py`; Test `backend/tests/unit/test_view_projection.py`.

**Interfaces:**
- Consumes: `mesh.views.config.FILTER_*`; `mesh.db.models.issue.Issue`; `mesh.issue.filters.FilterTooComplexError`, `MAX_FILTER_*`.
- Produces: `def compile_view_filters(filters: dict) -> Any` — accepts `{}` (→ `True`/no clause) or `{"operator":"AND"|"OR","conditions":[...]}`; each condition is a leaf `{field,op,value}`, a custom-field `{field_kind:"custom_field",field_def_id,...}`, or a nested group `{operator,conditions}`. Recursion maps `operator`→`and_`/`or_`. Built-in leaves compile against `Issue` columns; `label` and custom-field conditions raise `ValidationError("…awaits the label-property association increment", code="projection_field_pending")`; `q`/`contains` → `or_(title.ilike, identifier.ilike)`. Op mapping: `eq`→`==`, `neq`→`!=`, `in`→`in_`, `not_in`→`~in_`, `lt/lte/gt/gte`, `is_null`→`is_(None)`, `is_not_null`→`is_not(None)`. UUID fields (`status_id, assignee_id, reporter_id, project_id, cycle_id, milestone_id, parent_id`) coerce value(s) via `uuid.UUID`; date fields (`due_date,start_date`) via `coerce_date`; datetime (`created_at,updated_at`) parsed. Invalid value → `ValidationError(code="invalid_filters")`. Also produce `def count_conditions(filters) -> int` and enforce `FILTER_MAX_DEPTH`/`FILTER_MAX_CONDITIONS` → `FilterTooComplexError`.

- [ ] **Step 1:** Write `test_view_projection.py` (pure, no DB): `{}` → no clause; AND of two leaves compiles to a ClauseElement whose `str()` contains both columns; OR; nested depth-3 OK; depth-4 → `filter_too_complex`; >20 conditions → `filter_too_complex`; `label` leaf → `projection_field_pending`; custom-field leaf → `projection_field_pending`; `q contains` → ilike; UUID coercion bad value → `invalid_filters`; op mapping for each op.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/unit/test_view_projection.py -v` → FAIL.
- [ ] **Step 3:** Implement `compile_view_filters` + helpers in `projection.py` (recursive `_compile_node`, `_compile_leaf`, per-field column map + coercers). Parameterized only (never string-concat values).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(kanban): 视图 filters → SQL 编译器(白名单/参数化/label·自定义字段门控, kanban.md §2.3/§3.4, MES-33)`.

---

## Task 3: Grouped projection query `GET /views/{id}/issues` (overall cursor)

**Files:** Modify `projection.py` (add `ProjectionService`), `routes.py`, `schemas.py`(query params), `api/app.py`; Test `test_view_projection.py` (service-level, real DB) + extend `test_view_api.py` (HTTP).

**Interfaces:**
- Consumes: `IssueService.render_issue`, `IssueService._base_visibility_clause(viewer, session)`, `resolve_default_status`, `render_status`, `encode_cursor/decode_cursor`, `compile_view_filters`, `LIST_STATEMENT_TIMEOUT_MS`.
- Produces: `class ProjectionService(session_factory, issue_service, *, clock=None)` with `async def execute_view(*, viewer, workspace_id, view_id, limit=None, cursor=None) -> dict` returning `{"layout","group_by","column_target_status",{group_key:status_id|None},"groups":[{key,label,count,wip?,data}],"next_cursor"}`. Flow: open session + `set_tenant_context`; `SET LOCAL statement_timeout=3000`; load view (404 if missing) + `ViewService.assert_can_read`; `group_by = view.group_by or "state_category"`; gate `label`/custom-field → 400 `projection_field_pending`; base stmt `WHERE workspace_id, deleted_at IS NULL` + visibility clause + `compile_view_filters(view.filters)`; LEFT JOIN `view_issue_positions` on `(view_id, issue_id)`; order by `COALESCE(vip.position, issues.position) ASC, issues.id ASC` (manual-first, canonical fallback, §2.7); keyset-paginate the flat row list on `(coalesced_position, id)` → one `next_cursor`; per-group FULL counts via `with_only_columns(group_expr, count()).group_by`; bucket page rows; render each via `render_issue`; `wip` per group from `view.board_settings.wip`. `column_target_status`: for `state_category`/`status` map each key → default status id (`resolve_default_status(category=key)`); else `{}`. Group labels via member/project/status lookups (reuse `IssueService._group_label`-style). `DBAPIError` w/ "statement timeout" → `BusinessRuleError(code="query_cost_exceeded")`.
- Route: `GET /views/{view_id}/issues?limit&cursor` → `_resolve_context` → `projection_service.execute_view(...)` → return dict as-is (already `groups`+`next_cursor`; NOT wrapped in `data`). Rate-limit reads lightly (reuse pattern or skip).

- [ ] **Step 1:** Add service-level tests (real DB via `session_factory`, mirror `test_view_service.py._setup`): seed workspace+member+project+statuses+issues; create a view (`group_by=state_category`, a filter); assert `groups` keys/count/data, `next_cursor` single, `column_target_status` maps each category → default status id, WIP surfaced from board_settings, visibility trims a private-project issue, label group_by → `projection_field_pending`, pagination across `limit`.
- [ ] **Step 2:** Run → FAIL. Implement `ProjectionService.execute_view` + group helpers.
- [ ] **Step 3:** Run → PASS.
- [ ] **Step 4:** Add HTTP tests in `test_view_api.py` (ASGITransport): `GET /views/{id}/issues` returns `groups`+`next_cursor`+`column_target_status`; cross-workspace → 404; `filter_too_complex`/`projection_field_pending` over HTTP.
- [ ] **Step 5:** Wire `app.state.projection_service = ProjectionService(session_factory, app.state.issue_service)` in `api/app.py`; add route. Run `test_view_api.py` → PASS.
- [ ] **Step 6:** Commit `feat(kanban): 分组投影查询 GET /views/{id}/issues —— 整体游标/count·data/column_target_status/WIP/可见性裁剪/过滤限制 (kanban.md §3.2/§6.14, MES-33)`.

---

## Task 4: Atomic `move` + WIP enforcement `POST /views/{id}/moves`

**Files:** Create `moves.py`; modify `schemas.py` (`MoveRequest`), `routes.py`, `api/app.py`, `events/vocab.py`, README §6.7; Test `test_view_moves.py` (service, real DB) + `test_view_api.py` (HTTP).

**Interfaces:**
- Consumes: `IssueService._load_issue(for_update=True)`, `assert_can_write_issue`, `apply_changes_in_tx(session, actor, issue, patch)->(rendered,changes)`, `_emit_issue_event`, `_base_visibility_clause`, `IssuePatch`, `MoveService.move/preview`, `resolve_default_status`, `emit_realtime`, `compile_view_filters`, `ViewService.assert_can_read/resolve_view_workspace`.
- Produces: `class BoardMoveService(session_factory, issue_service, move_service, *, clock=None)`:
  - `async def move(*, actor, workspace_id, view_id, issue_id, to_group_key, position, version=None, confirm=False, dry_run=False) -> dict`
  - `async def reorder(*, actor, workspace_id, view_id, issue_id, to_group_key, position) -> dict`
  - WIP read from `view.board_settings.wip[group_key]` → `{limit,enforcement}`.

**move algorithm (non-project group_by), single transaction:**
1. read session: load view, resolve `group_by`. If `group_by=="project"` → delegate (below).
2. tx: `set_tenant_context`; `issue = issue_service._load_issue(for_update=True)`; `assert_can_write_issue`; if `version is not None and issue.version != version` → `ConflictError(code="conflict", details={id,current_version})`.
3. `SELECT pg_advisory_xact_lock(hashtext('wip:'||:view_id||':'||:group_key))`.
4. WIP: count target-group members = `SELECT count(*) FROM issues WHERE workspace, deleted_at IS NULL, <visibility>, <compile_view_filters(view.filters)>, <group_predicate(to_group_key)>, id != :issue_id`. If rule exists and `count >= limit`: enforcement `block` → `BusinessRuleError(code="wip_limit_exceeded", details={group_key,limit,count})`; `warn` → set `wip_exceeded_flag=True`.
5. Build `IssuePatch`: `state_category`→`status_id=resolve_default_status(category=to_group_key).id`; `status`→`status_id=UUID(to_group_key)`; `assignee`→`assignee_id=None if to_group_key in ("__none__","none","unassigned") else UUID(to_group_key)`; `priority`→`priority=to_group_key`.
6. `rendered, changes = await issue_service.apply_changes_in_tx(session, actor=actor, issue=issue, patch=patch)` (bumps version, trail, `issue.updated`, strict-mode).
7. If changes: `moved_event = issue_service._emit_issue_event(session, issue=issue, event="issue.moved", data={"id","from":{group},"to":{group_key},"position","view_id":str(view_id)}, project=issue_service._project_of(session,issue))`; if `"assignee_id" in changes` → `apply_assign_triggers(session, workspace_id, issue, previous_assignee_id, trigger_event_id=moved_event.id)`.
8. Upsert `view_issue_positions(view_id, issue_id, group_key=to_group_key, position)` (`INSERT ... ON CONFLICT (view_id,issue_id) DO UPDATE SET group_key, position, updated_at`); re-rank if precision exhausted (Task 5 helper).
9. If `wip_exceeded_flag` → `emit_realtime(channel=view:{id}, event="view.wip_exceeded", data={group_key,limit,count})`.
10. Return `rendered` (includes new `version`/`updated_at`) + `position`.

**move algorithm (group_by=="project"):** `target_project_id = None if to_group_key in ("__none__","none","no_project") else UUID(to_group_key)`. If `dry_run` → `return await move_service.preview(viewer=actor, workspace_id, issue_id, target_project_id)` (as `data`). If not `confirm` → `await move_service.move(..., confirm=False)` raises `move_confirmation_required` (422 w/ `details.preview`). Else `plan = await move_service.preview(...)`; `rendered = await move_service.move(..., confirm=True, expected_version=version)`; `rendered["move_result"] = {"mapped_fields": plan["mapped_fields"], "cleared_fields": plan["cleared_fields"]}`; then upsert `view_issue_positions` in a follow-up tx; return `rendered`.

- [ ] **Step 1:** Register `view.wip_exceeded` in `events/vocab.py` (add to the views frozenset) AND README §6.7 table; run `.venv/bin/pytest tests/unit/test_vocab.py -v` + `python3 tests/docs/check_event_vocab.py docs/specs` → parity passes.
- [ ] **Step 2:** Write `test_view_moves.py` (real DB): same-group reorder no status change; cross-category move sets default status of target category + version+1 + `issue.moved` outbox + `view_issue_positions` row; WIP `block` full → `wip_limit_exceeded` (details); WIP `warn` over → success + `view.wip_exceeded` outbox; stale `version` → 409; `group_by=project` unconfirmed → `move_confirmation_required` w/ preview; confirmed → `project_id` changed + `move_result`; `dry_run` → preview only; assignee/priority group moves.
- [ ] **Step 3:** Run → FAIL. Implement `moves.py`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Add `MoveRequest`/`ReorderRequest` schemas; HTTP tests in `test_view_api.py` (move 200/409/422 wip/422 move_confirmation_required; reorder 200); wire `app.state.board_move_service`; add routes `POST /views/{id}/moves`, `POST /views/{id}/reorder` (with `_rate_limit_write`).
- [ ] **Step 6:** Commit `feat(kanban): 原子 move + WIP 强制 POST /views/{id}/moves —— 乐观锁/advisory lock/事务内计数/block·warn/column_target_status 改状态/跨项目两步契约 (kanban.md §3.2/§4.3/§4.4, README §9 T9/T22, MES-33)`.

---

## Task 5: Per-view reorder + precision-exhaustion re-rank `POST /views/{id}/reorder`

**Files:** Modify `moves.py` (`reorder` + `_upsert_position` + `_rerank_if_needed`); Test `test_view_moves.py`.

**Interfaces:** Produces `reorder(...)` (no status change, no WIP): upsert `view_issue_positions(group_key, position)`; emit `issue.moved` (`view_id`, from/to group same, new position). `_rerank_if_needed(session, view_id, group_key, position)`: if the gap to the nearest neighbor position `< 1e-6`, fetch all `(issue_id)` for `(view_id, group_key)` ordered by current position, reassign `1.0,2.0,3.0…`, upsert all, emit `issue.moved` per card (full-column convergence, §4.3).

- [ ] **Step 1:** Tests: within-column reorder writes `view_issue_positions`, `issues.position` UNCHANGED (no pollution); view A reorder does NOT change view B order (isolation, §2.7); precision-exhaustion (seed two adjacent positions `1.0` and `1.0+1e-9`, reorder between) → whole column re-ranked to integer spacing + multiple `issue.moved`; fallback: a view with no position rows orders by `issues.position`.
- [ ] **Step 2:** Run → FAIL. Implement.
- [ ] **Step 3:** Run → PASS.
- [ ] **Step 4:** Commit `feat(kanban): 每视图列内排序 + 浮点中点精度耗尽整列重排 + 视图间排序隔离 (kanban.md §2.7/§4.3, README §6.14, MES-33)`.

---

## Task 6: Realtime incremental merge support + `view.presence`

**Files:** Create `presence.py`; modify `realtime/session.py` (isolated hook); Test `test_view_presence.py` + a realtime-gateway e2e addition.

**Interfaces:** `async def note_presence(session_factory, redis, *, workspace_id, channel, subject, joined) -> None`: Redis set key `mesh:presence:{channel}`; `SADD`/`SREM` `subject`; `count = SCARD`; under `set_tenant_context`, `emit_realtime(channel=channel, event="view.presence", data={"view_id":<key>,"online":count,"subject":subject,"joined":joined})`. Gateway: after a successful subscribe whose channel starts with `view:` → `note_presence(joined=True)`; on unsubscribe / connection close for each subscribed `view:` channel → `note_presence(joined=False)`. Guard all in try/except (presence must never break the WS session).

- [ ] **Step 1:** `test_view_presence.py` (real Redis + DB): `note_presence(joined=True)` → `view.presence` outbox frame w/ `online` count; join/leave updates count; non-view channel ignored by caller guard.
- [ ] **Step 2:** Run → FAIL. Implement `presence.py`.
- [ ] **Step 3:** Run → PASS.
- [ ] **Step 4:** Wire the gateway hook in `realtime/session.py` (subscribe/unsubscribe/close paths), importing lazily to avoid cycles; add an e2e assertion in `tests/e2e/test_realtime_gateway_e2e.py` (subscribe to `view:{id}` → a `view.presence` frame is delivered on the channel).
- [ ] **Step 5:** Run gateway e2e → PASS. Commit `feat(kanban): view.presence 在线协作事件 —— 订阅/退订广播在线数 (kanban.md §3.5/§6.7, MES-33)`.

---

## Task 7: Backend real e2e (T1/T6/T9/T22 + WIP concurrency + overall cursor)

**Files:** Create `backend/tests/e2e/test_board_projection_e2e.py` (`pytestmark = pytest.mark.e2e`).

- [ ] **Step 1:** Mirror `test_view_e2e.py`/`test_issue_e2e.py` harness (`api_client`, real uvicorn, `mesh_app` role). Tests:
  - **Projection durability + overall cursor:** seed issues via real HTTP, `GET /views/{id}/issues` → `groups[].count` = real totals, `data` = page slice, single `next_cursor`, paginate to end; raw-SQL assert `view_issue_positions` rows after a move.
  - **Atomic move + WIP reject:** `block` full column → `422 wip_limit_exceeded` + DB unchanged; `warn` → 200 + `view.wip_exceeded` outbox.
  - **T9 concurrent drag:** `asyncio.gather` two `POST /views/{id}/moves` on the same card with the same stale `version` → exactly one 200, one 409; final DB state = server's latest write, no lost update.
  - **WIP concurrency (T9-class):** `block limit=N`; fire `>N` concurrent moves into the column → final member count `≤ N`, extras `422`.
  - **T22 cross-project:** `group_by=project` move unconfirmed → 422 w/ preview; confirmed → `project_id` changed + status mapped + private milestone cleared + `issue.project_changed` outbox; identifier immutable.
  - **T1 cross-tenant:** A-creds `GET /views/{B-view}/issues` → 404; raw INSERT of a `view_issue_positions` row referencing another workspace's view/issue → `DBAPIError`.
  - **T6 replay reconciliation:** seed via `emit_realtime`→relay→projector; `DELETE FROM realtime_events WHERE seq<=k`; reconnect stale `resume_from` → `resync_required` + `rest`; reconcile over `GET /api/v1/realtime/events` then `GET /views/{id}/issues` → board converges.
- [ ] **Step 2:** Run `cd backend && .venv/bin/pytest tests/e2e/test_board_projection_e2e.py -v` → all PASS (fix until green).
- [ ] **Step 3:** Commit `test(kanban): 投影层真实 e2e —— 分组投影/整体游标/原子 move/WIP 拒绝/T9 并发拖拽/WIP 并发不穿透/T22 跨项目/T1 跨租户/T6 重放对账 (README §9, MES-33)`.

---

## Task 8: Backend coverage gate

- [ ] **Step 1:** `.venv/bin/ruff check src/mesh/views src/mesh/db/models/view_position.py tests/unit/test_view_*.py tests/e2e/test_board_projection_e2e.py` → clean.
- [ ] **Step 2:** Module scope: `.venv/bin/pytest tests/unit/test_view_projection.py tests/unit/test_view_moves.py tests/unit/test_view_position_model.py tests/unit/test_view_presence.py tests/unit/test_view_service.py tests/unit/test_view_config.py tests/unit/test_view_model.py tests/unit/test_view_api.py --cov=mesh.views --cov=mesh.db.models.view_position --cov-report=term-missing` → ≥90% on new code.
- [ ] **Step 3:** Full gate: `.venv/bin/pytest --cov=mesh --cov-report=term-missing --cov-fail-under=90` → PASS.
- [ ] **Step 4:** Commit (coverage fixes only if any) `test(kanban): 投影层单测补齐至 ≥90% 分支覆盖 (MES-33)`.

---

## Task 9: Frontend — projection API + types + realtime merge

**Files:** Create `frontend/src/features/board/projection.ts`, `boardRealtime.ts`; extend `types.ts`; Test `__tests__/projection.test.ts`, `boardRealtime.test.ts`.

**Interfaces:**
- `projection.ts`: `fetchViewIssues(client, viewId, {limit,cursor}): Promise<ViewProjection>` (`client.grouped`-shaped but returns `{layout,group_by,column_target_status,groups,next_cursor}` via `client.request('GET', ...)`); `moveCard(client, viewId, body: MoveBody): Promise<IssueSummary>` (`POST /views/{id}/moves`); `reorderCard(client, viewId, body)`; types `ViewProjection`, `BoardGroup`, `BoardCard`, `MoveBody {issue_id,to_group_key,position,version,confirm?,dry_run?}`.
- `boardRealtime.ts`: pure `applyBoardFrame(groups, frame, ctx)` — on `issue.updated`/`issue.moved`/`issue.created` re-bucket a single card by current `group_by` (remove from old group, insert into target per `belongs`/filters, anti-rollback via `updated_at`); on `issue.deleted` remove; on `view.updated` signal full-refetch; returns new structure (immutable). Reuse `applyIssueListFrame` semantics from `features/issues/realtime.ts`.
- [ ] **Step 1:** Write `projection.test.ts` (stubFetch): envelope unwrap, move body serialization, error code surfacing. `boardRealtime.test.ts`: card moves between groups on `issue.moved`; removed on no-longer-belongs; anti-rollback; `view.updated` → refetch flag.
- [ ] **Step 2:** `npm test` → FAIL. Implement. → PASS.
- [ ] **Step 3:** Commit `feat(board): 投影层契约 API + 实时增量合并(单卡重分桶/防回退) (kanban.md §3.5, MES-33)`.

---

## Task 10: Frontend — real-data board (cards, drag, WIP, optimistic+409, resync)

**Files:** Modify `BoardPage.tsx`, `BoardColumns.tsx`, `types.ts`, `board.css`; Test `__tests__/BoardPage.projection.test.tsx`.

**Interfaces:** On view select → `fetchViewIssues` → store `groups`; render cards (identifier/title/status dot/assignee/priority) from `group.data`; column header `count` + WIP `n/limit` (warn→yellow, block→red, from existing `WipBadge`). Drag (HTML5 dnd or pointer) → optimistic move → `moveCard`; on `422 wip_limit_exceeded` → snap back + toast; on `409` → refetch converge (reuse `useOptimisticMutation` pattern from `IssueDetailPage`); `group_by=project` → preview modal from `move_confirmation_required.details.preview` → confirm. Subscribe `workspaceIssuesChannel(ws)` + `viewChannel(viewId)` via `useRealtimeContext`; merge via `applyBoardFrame`; `resync_required`/reconnecting → "正在重新同步" banner (§6.12). Quick-create at column bottom (inherits group value → `POST /issues`). All strings via `t(...)`.
- [ ] **Step 1:** Write `BoardPage.projection.test.tsx` (renderWithProviders + stubFetch): renders real cards from groups; drag fires `moveCard`; WIP block snap-back toast; 409 refetch; resync banner on state. → FAIL.
- [ ] **Step 2:** Implement BoardPage/BoardColumns changes. → PASS.
- [ ] **Step 3:** `npm run lint && npm run typecheck` → clean. Commit `feat(board): 真实数据看板 —— 拖拽(乐观+409收敛)/WIP 超限提示/列实时增量/重连重同步态/列底快速创建 (kanban.md §4/§6.12, MES-33)`.

---

## Task 11: i18n keys + djb2 version recompute

**Files:** Modify `frontend/src/i18n/catalogs/en.json` + `zh-CN.json`.

- [ ] **Step 1:** Add new `board.*` (card, drag, wip-exceeded toast, resync banner, move-preview modal, quick-create) + `error.wip_limit_exceeded`/`error.move_confirmation_required`/`error.projection_field_pending`/`error.query_cost_exceeded` keys to BOTH catalogs (identical key set).
- [ ] **Step 2:** Recompute each file's djb2 `version` (run `computeCatalogVersion` from `catalogLoader.ts` via a throwaway vitest/node snippet) and overwrite the top-level `version`.
- [ ] **Step 3:** `npm test src/i18n` → parity + hash tests PASS. Commit `feat(board): 看板投影层 i18n 外部化(zh-CN+en)+ djb2 版本哈希重算 (MES-33)`.

---

## Task 12: Frontend coverage + Playwright real-UI walkthrough

**Files:** `frontend/e2e/real-board-projection.spec.ts`, `frontend/playwright.mes33.config.ts`.

- [ ] **Step 1:** `npm run test:coverage` → ≥90% global; `node scripts/verify-coverage.mjs --base origin/main` → changed-code ≥90%.
- [ ] **Step 2:** Stand up isolated backend stack (recipe in `playwright.mes44.config.ts` header): scratch PG + `alembic upgrade head` + uvicorn API `--port 8100` + gateway `--port 8181`, `MESH_AUTH_MODE=dev`, `MESH_APP_DATABASE_URL` set. Create `playwright.mes33.config.ts` (testMatch `real-board-projection.spec.ts`, dev port 5274 → API 8100 / WS 8181).
- [ ] **Step 3:** Write `real-board-projection.spec.ts` (serial): register/login → workspace → seed real issues (REST) → open board → cards render in correct columns → drag a card cross-column (optimistic + server persists; raw-SQL assert status changed) → WIP block column rejects (snap-back) → second client move triggers 409 convergence → force resync (delete realtime_events) → "正在重新同步" → recovers → screenshots to `e2e/evidence/board-projection/`. Zero console errors.
- [ ] **Step 4:** `npx playwright test --config playwright.mes33.config.ts` → PASS. Commit `test(board): 看板投影层真人实操走查(playwright + 真实后端:拖拽/WIP/409收敛/重同步, kanban.md §4/§6.12, MES-33)`.

---

## Task 13: Docs sync + full gates + PR + handoff

**Files:** `docs/specs/features/kanban.md` (mark projection layer done + label/custom-field gating note), `README.md` (status table row → kanban 投影层), `CHANGELOG.md` (`[0.12.0]`).

- [ ] **Step 1:** Update docs (no competitor references; keep spec↔impl consistent). Run doc gates: `python3 tests/docs/check_event_vocab.py docs/specs` + `python3 tests/docs/check_roster_entry.py docs/specs`.
- [ ] **Step 2:** Full backend gate `.venv/bin/pytest --cov=mesh --cov-fail-under=90`; full frontend `npm run lint && npm run typecheck && npm run test:coverage && npm run build`.
- [ ] **Step 3:** Pre-push self-check: `git log @{u}..HEAD --format=%B | grep -i 'co-authored-by'` → EMPTY; `git log -1 --format='%an <%ae> | %cn <%ce>'` → `cnwenf <cnwenf@outlook.com>` both; rebase onto latest `origin/main`.
- [ ] **Step 4:** Commit docs `docs(kanban): 投影层 Spec/README/CHANGELOG 同步 (MES-33)`; push `-u`; open PR via `gh`.
- [ ] **Step 5:** Post final result comment on MES-33 with verifier mention (per the platform mention convention), PR URL, coverage numbers, e2e/UI evidence, and the label/custom-field gating note (MES-32 dependency).

## Self-Review

- **Spec coverage:** §2.7 positions (T1), §6.14 grouped/overall-cursor (T3), filters limits (T2/T3), §3.2 atomic move + WIP + cross-project (T4), §4.3 reorder + precision + isolation (T5), §3.5 realtime merge + view.presence (T6/T9/T10), §4.4 WIP warn/block (T4/T10), §5.x acceptance via e2e (T7) + UI (T12), i18n (T11), docs (T13). Label/custom-field grouping explicitly gated (Global Constraints) — documented, not faked.
- **Type consistency:** `execute_view`/`move`/`reorder`/`note_presence`/`compile_view_filters`/`applyBoardFrame`/`fetchViewIssues`/`moveCard` names used consistently across tasks.
- **Placeholders:** none — each task has concrete interfaces, code paths, commands, expected outcomes.
