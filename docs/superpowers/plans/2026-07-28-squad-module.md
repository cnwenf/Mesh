# Squad Module (阶段 7 · 智能体编排层) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full Squad feature (docs/specs/features/squad.md, 5 chapters) — the multi-agent orchestration unit where a leader decomposes an issue into a dependency-DAG of subtasks, dispatches them to agent/human members, and aggregates results, with the unique-active-assignment identity (§6.9/T23), unified plan approval (§6.10/T8), and real-time progress.

**Architecture:** A new `mesh.squad` backend module (SQLAlchemy 2.0 models + raw-SQL Alembic migration `0020` + service orchestrators + FastAPI routes + WS channel checkers + SSE stream) layered on the existing transactional-outbox pipeline. Squad owns 7 tables; it reuses the already-landed `approvals` entity (runtime migration `0019` created it with a bare `subject_task_id` column + `uq_approvals_pending_task` — this increment adds the deferred composite FK) and `task_executions` (runtime §6.4). Derived actions (leader wake-up, execution enqueue, plan-decision effects, execution-terminal observation) flow through the outbox relay, never in-process. A React 19 `features/squads` module consumes the REST + WS/SSE surface.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / asyncpg / PostgreSQL 16 / Alembic / Redis / pytest(+cov) · React 19 / TypeScript / Vite / react-intl / Vitest / Playwright.

## Global Constraints

- **Anonymity (red line):** NO reference to any reference product (neither by name nor by copied naming) anywhere in code, comments, docs, commits, branch names — not even inside negations like this one. Mesh is an independent original project; the anonymization grep for known product names must come back empty.
- **Git identity:** author AND committer `cnwenf <cnwenf@outlook.com>`; NEVER any `Co-Authored-By` line. `git config core.hooksPath /dev/null` in-repo; self-check before push (`git log @{u}..HEAD --format=%B | grep -i co-authored-by` must be empty).
- **Coverage ≥90%:** overall AND new-code, measured with pytest-cov (`fail_under = 90`, branch). Frontend ≥90% lines/funcs/branches/statements + per-file gate.
- **Real e2e:** real server process + real PostgreSQL; no mock-only walkthroughs on contract paths.
- **Tenant safety (README §6.2):** every cross-module ref is a composite FK `(workspace_id, ref_id) → target(workspace_id, id)`; referenced tables carry `UNIQUE(workspace_id, id)`; RLS policy `workspace_id = current_setting('mesh.workspace_id')::uuid` on all new tenant tables.
- **No `*_type`/`*_kind` discriminator columns in storage (README §6.1):** human/agent resolved by JOIN `members.member_type`; API responses carry a computed `member_type` snapshot only. System actors use the `('member','system')` null-FK pattern.
- **Vocabulary (README §6.7):** realtime event names come ONLY from `mesh.events.vocab` (SQUAD_EVENTS already registered). New internal outbox types must be added to `OUTBOX_INTERNAL_EVENT_TYPES` and `tests/docs/check_event_vocab.py` whitelist.
- **Outbox discipline (README §6.6):** business txn writes `outbox_events` only; relay/projector materialize executions/notifications/realtime. Never INSERT `realtime_events` or assign `seq` from business code — use `emit_realtime`.
- Conventional commits (`feat:`/`fix:`/`test:`/`docs:`), small frequent commits.

---

## File Structure

### Backend — new `backend/src/mesh/squad/`

| File | Responsibility |
|------|----------------|
| `db/models/squad.py` | 7 ORM models: `Squad`, `SquadMember`, `SquadTask`, `IssueSquadAssignment`, `SquadTaskDependency`, `SquadMessage`, `SquadActivity`. CHECK enums, composite FKs, `UNIQUE(workspace_id,id)`, partial unique indexes. |
| `migrations/versions/0020_squad.py` | Raw-SQL DDL for the 7 tables + all §2.9 indexes + RLS + `GRANT mesh_app` + the deferred `approvals.subject_task_id → squad_tasks(workspace_id,id)` composite FK. `revision="0020"`, `down_revision="0019"`. |
| `squad/__init__.py` | package marker |
| `squad/schemas.py` | Pydantic v2 request models (`CreateSquadRequest`, `AssignTaskRequest`, `CreateSubtasksRequest`, `SendMessageRequest`, …). |
| `squad/service.py` | `SquadService` — CRUD, archive/restore, member add/role/remove, activity writes, rendering. |
| `squad/tasks.py` | `SquadTaskService` — assignment orchestration (§2.5/T23), decomposition + DAG cycle detection, subtask dispatch, status state machine, cancel cascade, aggregation, execution-terminal observation. |
| `squad/plan.py` | plan-approval integration: create `approvals(squad_plan)`, thin approve/reject wrappers, plan-decided relay handler. |
| `squad/relay.py` | outbox relay handlers: `squad_plan_decided_handler`, `squad_execution_finished_handler`, `squad_assignee_changed_handler`. |
| `squad/channels.py` | `register_squad_checkers` — `squad:{id}` subscription authorization. |
| `squad/routes.py` | FastAPI `router` (all §3.1 endpoints) + SSE `/stream`. |
| `squad/sse.py` | SSE stream (GET, EventSource-compatible, `Last-Event-ID` replay from a per-task event buffer). |

### Backend — modified

| File | Change |
|------|--------|
| `db/models/__init__.py` | re-export the 7 squad models in `__all__`. |
| `events/vocab.py` | add `squad.plan_decided`, `squad.assignee_changed`, `execution.finished` to `OUTBOX_INTERNAL_EVENT_TYPES`. |
| `comment_inbox/notifications.py` | `policy_for` handles `squad_task_assigned` / `squad_task_finished` / `squad_plan_review`. |
| `db/models/notification.py` | add the 3 squad types to `NOTIFICATION_TYPE_VALUES`. |
| `runtime/approvals.py` | `decide_approval` emits `squad.plan_decided` for `squad_plan`; `_assert_may_decide` allows any human for `squad_plan`; response carries `subject_task_id`. |
| `runtime/reaper.py` | `_expire_approvals` emits `squad.plan_decided(decision=expired)` for `squad_plan`. |
| `runtime/attempts.py` | `_sync_execution_status` emits internal `execution.finished` on terminal. |
| `issue/service.py` | `IssueService.__init__(*, squad_assignee_watcher=None)`; call it on `assignee_id` change (issue_reassigned cascade). |
| `workers/main.py` | register the 3 squad relay handlers in `build_relay`. |
| `api/app.py` | construct `SquadService`/`SquadTaskService`, wire issue watcher, register router + channel checkers. |
| `realtime/app.py` | register squad channel checkers. |
| `tests/docs/check_event_vocab.py` | whitelist the new internal outbox types. |

### Frontend — new `frontend/src/features/squads/`

`api.ts`, `types.ts`, `realtime.ts`, `SquadsPage.tsx`, `SquadDetailPage.tsx`, `SquadTaskDetailPage.tsx`, `squads.css`, `__tests__/*`. Plus: `Sidebar.tsx` nav entry, `App.tsx` routes, both i18n catalogs.

### Docs

`README.md` (feature list + Quick Start unchanged), `CHANGELOG.md` (new version), `docs/specs/features/squad.md` (sync only if implementation reveals a gap — report to Leader first per rule 4).

---

## Task 1: ORM models

**Files:** Create `backend/src/mesh/db/models/squad.py`; modify `backend/src/mesh/db/models/__init__.py`.
**Interfaces:** Produces model classes consumed by every later task. Column sets verbatim from spec §2.2–§2.8.

- [ ] Write `squad.py` with the 7 models. Enums as tuple constants + `CheckConstraint`. Every model: `id` UUID PK `gen_random_uuid()`, `workspace_id` FK→workspaces CASCADE, `created_at`/`updated_at` `now()`. Composite FKs in `__table_args__` (`ForeignKeyConstraint(("workspace_id","x_id"),("t.workspace_id","t.id"), name=...)`). `Index("uq_<t>_ws_id","workspace_id","id",unique=True)` on squads/squad_tasks/issue_squad_assignments. Partial uniques: `uq_issue_squad_active ON (issue_id) WHERE status='active'`, `uq_squad_member_active ON (squad_id,member_id) WHERE left_at IS NULL`, `uq_task_dep (task_id,depends_on_task_id)`. CHECKs: `squad_messages (kind='system' OR sender_id IS NOT NULL)`, `squad_activity (actor_kind='system' OR actor_id IS NOT NULL)`. All §2.9 indexes.
- [ ] Register in `db/models/__init__.py` `__all__`.
- [ ] Sanity: `python -c "import mesh.db.models.squad"` imports clean.
- [ ] Commit `feat(squad): ORM models for 7 squad tables (spec §2)`.

## Task 2: Alembic migration 0020

**Files:** Create `backend/migrations/versions/0020_squad.py`.
- [ ] Raw `op.execute` DDL mirroring the models exactly (CREATE TABLE w/ inline CHECKs + composite FKs), all §2.9 indexes, the deferred `ALTER TABLE approvals ADD CONSTRAINT approvals_subject_task_id_squad_tasks FOREIGN KEY (workspace_id, subject_task_id) REFERENCES squad_tasks(workspace_id, id)`, `ENABLE ROW LEVEL SECURITY` + `mesh_<t>_tenant` policy per table, `GRANT SELECT,INSERT,UPDATE,DELETE ON <tables> TO mesh_app`. Reversible `downgrade()` (drop FK, tables, indexes).
- [ ] Verify against real PG: `alembic upgrade head` then `downgrade -1` then `upgrade head` (via the test provisioning fixture).
- [ ] Commit `feat(squad): migration 0020 — squad tables, indexes, RLS, deferred approvals FK`.

## Task 3: Schemas + service skeleton + CRUD/members

**Files:** `squad/schemas.py`, `squad/service.py`, `squad/__init__.py`.
- [ ] Pydantic request models (IDs as `str`, enums via `Field(pattern=...)`).
- [ ] `SquadService(session_factory, *, clock=None)`: `create_squad`, `list_squads`, `get_squad`, `update_squad`, `archive_squad`, `restore_squad`, `list_members`, `add_members`, `change_role`, `remove_member`. Each public method owns a txn (`async with factory() as s, s.begin(): set_tenant_context; …`). Guards: ≥1 leader invariant (`no_leader`), `squad_name_taken`, `member_has_active_task` on remove, agent-cannot-self-edit (403). `member_type` resolved via JOIN for response snapshots. Activity rows for every mutation; `emit_realtime` `squad.updated`/`squad.archived`/`squad_member.changed`.
- [ ] Unit tests (AAA) for each guard + happy path. Commit `feat(squad): squad CRUD + membership service`.

## Task 4: Task service — assignment orchestration (CRITICAL §2.5/T23)

**Files:** `squad/tasks.py`.
**Interfaces:** `SquadTaskService(session_factory, *, clock=None)`; `assign_issue_to_squad(...) -> dict` returns `{assignment_id, id(root_task), …, status_url, stream_url}`.
- [ ] `assign_issue_to_squad`: same-txn — squad must have `primary_leader_id` else `422 squad_no_leader`; look up existing active assignment for the issue; if same squad → **no-op** return existing; else cancel old assignment (`cancel_reason='reassigned'`) + cascade-cancel its root tree, INSERT new active `issue_squad_assignments` (partial-unique guards concurrency → `409 conflict` on race), set `issues.assignee_id = primary_leader_id`, create root `squad_tasks` (depth 0, root self-backfill, backfill `root_task_id` on assignment), emit `squad_assignment.changed` + `execution.enqueue`-via-internal-event to wake the leader, activity `task_received`. Same-leader-cross-squad is NEVER a no-op (decided by assignment row, not assignee value).
- [ ] `change_primary_leader`: same-txn update all active assignments' `leader_member_id` + their `issues.assignee_id`, audit + `squad_assignment.changed`; root NOT cancelled.
- [ ] `handle_leader_loss`: no replacement → root `blocked(failure_reason='leader_lost')` + notify; replacement restores.
- [ ] `on_issue_assignee_changed` (watcher): if issue's active assignment leader ≠ new assignee → cancel assignment (`issue_reassigned`) + cascade root.
- [ ] Unit tests: S1→S2 reassign cascade, duplicate no-op, same-leader-cross-squad not no-op, leader change same-txn, leader loss blocked, partial-unique single-active. Commit `feat(squad): unique-active assignment orchestration (§2.5/T23)`.

## Task 5: Decomposition, DAG, dispatch, state machine

**Files:** `squad/tasks.py`.
- [ ] `create_subtasks(plan_markdown, subtasks[])`: depth check (`decompose_depth_exceeded`), `assignee_not_member`, resolve `depends_on` (batch title / temp_ref / task_id), **recursive-CTE cycle detection** (`dependency_cycle`), create rows + `squad_task_dependencies`, write `plan_submitted`/`task_decomposed`. If `require_plan_approval` → root `awaiting_plan_approval` + create `approvals(squad_plan)` (delegate Task 6) else `dispatching`.
- [ ] `dispatch_ready`: subtasks whose deps all `done` and stage allows → `pending→dispatching`; agent assignee → internal `execution.enqueue` (snapshot §6.11, idempotency §6.5) + record `execution_id`; human → notify.
- [ ] `transition_status`: server-side state machine (§4.4); illegal → `409 conflict`; emit `squad_task.status_changed`.
- [ ] `on_execution_finished`: execution terminal → subtask `done`/`failed(failure_reason)`; when all siblings terminal → parent `aggregating`; `aggregate` → root `done`/`failed` + write back to issue + assignment `completed` + notify initiator.
- [ ] `cancel_task`: cascade-cancel unfinished descendants, cancel their executions (queued→cancelled, claimed/running→cancelling), keep finished results.
- [ ] Unit tests: cycle rejection, depth exceeded, auto-unlock, aggregation, cancel cascade. Commit `feat(squad): decomposition DAG, dispatch, state machine, aggregation`.

## Task 6: Plan approval integration (§6.10/T8)

**Files:** `squad/plan.py`; modify `runtime/approvals.py`, `runtime/reaper.py`, `events/vocab.py`.
- [ ] `create_plan_approval(...)`: INSERT `approvals(subject_type='squad_plan', subject_task_id=root, requested_by_member_id=leader, action_summary={plan_digest, impact_scope, subtask_count, expires_at}, expires_at=now+ttl)`. Partial-unique → return existing pending.
- [ ] `plan_approve`/`plan_reject` (routes) = locate pending approval by `subject_task_id` → call runtime `decide_approval`.
- [ ] Extend `decide_approval`: for `squad_plan`, emit internal `squad.plan_decided{approval_id, subject_task_id, decision}`; `_assert_may_decide` returns early for `squad_plan` (any human). Response includes `subject_task_id`.
- [ ] Extend `_expire_approvals`: `squad_plan` → emit `squad.plan_decided(decision='expired')`.
- [ ] `squad_plan_decided_handler` (relay): approve → root `dispatching` + `dispatch_ready`; reject → root `decomposing`; expired → root `failed(approval_expired)` + notify leader/initiator.
- [ ] Add `squad.plan_decided` to `OUTBOX_INTERNAL_EVENT_TYPES` + check_event_vocab whitelist.
- [ ] Unit tests: approve→dispatching, reject→decomposing, expired→failed, idempotent re-decide, one-pending-per-task. Commit `feat(squad): unified plan approval (§6.10) + reaper expiry (T8)`.

## Task 7: Execution-terminal observation + issue watcher wiring

**Files:** modify `runtime/attempts.py`, `issue/service.py`, `workers/main.py`, `events/vocab.py`, `squad/relay.py`.
- [ ] `_sync_execution_status`: on terminal, `emit_event(event_type='execution.finished', payload={execution_id, workspace_id, status, failure_reason})`.
- [ ] `squad_execution_finished_handler`: find `squad_tasks` by `execution_id` → `on_execution_finished`.
- [ ] `IssueService.__init__(*, squad_assignee_watcher=None)`; in `update_issue` after `apply_assign_triggers`, if `assignee_id` changed call watcher (session-level, same txn).
- [ ] `squad_assignee_changed_handler` not needed (watcher is synchronous in-txn) — watcher calls `SquadTaskService.on_issue_assignee_changed_tx`.
- [ ] Register handlers in `build_relay`. Add `execution.finished` to internal vocab + whitelist.
- [ ] Unit tests for terminal mapping + watcher cascade. Commit `feat(squad): execution-terminal observation + issue-reassigned cascade`.

## Task 8: Messages, activity, channels

**Files:** `squad/service.py` (messages), `squad/channels.py`.
- [ ] `list_messages`/`send_message`: `kind` tabs, recipient optional (broadcast), system messages (null sender + `kind='system'`), sanitize `body_html`/`body_text`, `squad_message.created` realtime, optional activity `message_sent`. Instruction→agent-member triggers a run (internal enqueue) with loop suppression (skip if sender is agent and recipient is the same/leader loop).
- [ ] `list_activity` with `task_id`/`action` filters (append-only).
- [ ] `register_squad_checkers`: `squad:{id}` → principal is an active member/observer of the squad or admin.
- [ ] Unit tests. Commit `feat(squad): messages, activity timeline, WS channel authz`.

## Task 9: REST routes + SSE stream

**Files:** `squad/routes.py`, `squad/sse.py`; wire `api/app.py`, `realtime/app.py`.
- [ ] `APIRouter(prefix="/api/v1", tags=["squad"])` with all §3.1 endpoints, `require_workspace(...)` deps, rate-limit on writes + separate bucket for leader decompose/dispatch, error codes §3.3, cursor pagination §3.4.
- [ ] SSE `GET .../tasks/{task_id}/stream`: EventSource-compatible, `id:`/`event:`/`data:` frames, `Last-Event-ID` replay from a persisted per-task buffer (reuse `realtime_events` on `squad_task:{id}` channel OR a dedicated buffer). Events `task.status`/`subtask.created`/`plan.submitted`/`task.aggregated`.
- [ ] Wire services + router + checkers into `api/app.py` and checkers into `realtime/app.py`.
- [ ] In-process API tests (ASGITransport) for each endpoint + error code. Commit `feat(squad): REST API (§3.1-§3.4) + SSE orchestration stream (§3.5)`.

## Task 10: Backend e2e (real server + PG)

**Files:** `backend/tests/e2e/test_squad_e2e.py`, `backend/tests/unit/squad_support.py`.
- [ ] Real uvicorn API + worker subprocess. Cover: full assign→decompose→approve→dispatch→finish flow; **T23** (S1→S2 reassign cascade + duplicate no-op + leader change same-txn + leader loss blocked); **T8** approval expiry; **DAG cycle** rejection; cross-workspace 404; partial-unique single-active under concurrency.
- [ ] Run `pytest --cov=mesh --cov-fail-under=90`. Commit `test(squad): real e2e — T23/T8/DAG/cross-tenant`.

## Task 11: Frontend types + api + realtime

**Files:** `features/squads/{types.ts,api.ts,realtime.ts}`.
- [ ] snake_case readonly entity types mirroring wire; api fns `listSquads/getSquad/createSquad/assignTask/listTasks/getTaskTree/createSubtasks/approvePlan/rejectPlan/listMessages/sendMessage/listActivity/listMembers/addMember/changeRole/removeMember` + channel helpers `squadChannel(id)`; pure frame-merge fns. Commit `feat(squads): frontend api + types + realtime merge`.

## Task 12: Frontend pages + nav + routes + i18n

**Files:** `SquadsPage.tsx`, `SquadDetailPage.tsx`, `SquadTaskDetailPage.tsx`, `squads.css`; modify `Sidebar.tsx`, `App.tsx`, both catalogs.
- [ ] Squads list (search/kind/status filters, member avatar wall, active-task count). Detail (members pane w/ role edit, decomposition tree w/ deps, task board by status, activity timeline w/ filters, message area w/ kind tabs + pinned context + composer). Task detail (status + progress, approval banner w/ approve/reject, tree/board toggle, cancel). Create/edit dialog (member picker, ≥1 leader gate).
- [ ] `nav.squads` + `squads.*` keys in BOTH `en.json` and `zh-CN.json` (parity-tested). Render-state order empty→error→skeleton→content. All strings via `useT()`.
- [ ] Commit `feat(squads): list/detail/task pages, nav, routes, i18n`.

## Task 13: Frontend tests + real UI verification

- [ ] Vitest `__tests__` for api/realtime/pages (stubFetch + renderWithProviders + fake RealtimeClient), ≥90% + per-file gate.
- [ ] Real UI operation: launch compose stack, drive the squad flow in a real browser (create squad → assign issue → approve plan → finish), capture screenshots as evidence. Commit `test(squads): component tests + real UI walkthrough evidence`.

## Task 14: Docs, rebase, PR, handoff

- [ ] `CHANGELOG.md` new version entry; `README.md` feature list; confirm `docker compose up --build` Quick Start still green; anonymization grep (all known reference-product names) = empty.
- [ ] `git rebase origin/main`; migration renumber if a parallel line landed `0020` first. Push with clean identity; open PR.
- [ ] Post ONE result comment with validator mention requesting acceptance.

---

## Self-Review

- **Spec coverage:** §1 (positioning/exclusive-assignee) → T4; §2 (7 tables) → T1/T2; §3 (endpoints/errors/pagination/realtime) → T9; §4 (UI/state machine) → T5/T12; §5 acceptance (T23/T8/DAG/composite-FK/no-discriminator) → T4/T5/T6/T10; §6.10 approvals → T6; §6.9 trigger matrix → T4/T7. README anchors §6.1/§6.2/§6.4/§6.6/§6.7/§6.8/§6.11 → Global Constraints + T2/T5/T6/T9. All covered.
- **Placeholders:** none — each task names exact files, interfaces, and the concrete behaviors/tests.
- **Type consistency:** service names (`SquadService`, `SquadTaskService`), handler names (`squad_plan_decided_handler`, `squad_execution_finished_handler`), event names (`squad.plan_decided`, `execution.finished`, `squad_assignment.changed`) are used consistently across tasks.
