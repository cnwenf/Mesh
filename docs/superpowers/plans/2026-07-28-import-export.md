# MES-64 Import/Export Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full data import/export module (import-export.md five chapters): the unified `data_jobs` job entity + `data_job_rows` row ledger (migration 0021), the two-phase CSV/JSON import (validate dry-run → run with partial success), async export through the unified attachment channel, the T31 crash-recovery red lines (monotonic `lease_seq` fencing incl. `fail_job`, checkpoint resume, `row_key` claim-before-create, source-hash freeze, source-attachment `RESTRICT`), the §6.13 data-job notification rows, §6.7 `data_job.updated` realtime progress, and the frontend import wizard + data-management page — all backed by real e2e for the red-line items.

**Architecture:** Backend `src/mesh/data_jobs/` package (models in `src/mesh/db/models/data_job.py`, migration `0026_data_jobs.py`, chained after `0025_chat_security_indexes.py`). Import is two-phase: API creates the job + writes `data_job.enqueue` outbox in one transaction; `validate` claims and streams the source without persisting entities (freezes `source_content_hash`); `run` processes in batched transactions. Each batch transaction: `FOR UPDATE` lock job row → validate `lease_owner + lease_seq + unexpired` (R4 fencing, applied to `fail_job` too) → per row **claim the ledger row first** (`INSERT … ON CONFLICT (job_id,row_key) DO NOTHING` with a pre-allocated `target_id`) and only on a successful claim create the entity via the normal numbering path → update counters/checkpoint/renew lease/emit progress, one commit. The reaper sweeps expired leases (clear owner, **keep `lease_seq`**, re-emit `data_job.resume` with a **sub-lease-ttl bucketed idempotency key** so a wasted published row can never dedup the re-arm → no 7-day stall). Row-level exceptions are caught per row (never bubble to a job-level `failed`), and are pre-validated in `transform_row` so dry-run predicts them. Export streams via cursor pagination into a temp file and registers the product through `register_server_attachment` (content-addressed blob reuse). Realtime only via `emit_realtime` (event already in vocab). Notifications only via `emit_notification_fanout` with `data_job_finished` (the §6.13 three rows; module defines no tiers).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, PostgreSQL 16 (RLS, SKIP LOCKED, advisory locks, PG16 column-level `ON DELETE SET NULL (col)`), Redis pub/sub, MinIO (source + products + error reports), pytest + pytest-cov (≥90% branch), Playwright (frontend real e2e).

## Global Constraints

- Spec authority: `docs/specs/features/import-export.md` + README §6.2/§6.5/§6.6/§6.7/§6.13/§6.14. Never deviate; report spec gaps in the issue, don't self-amend. (Two clarifications were added to the spec itself: M3 fresh-validate re-freezes vs resume-checks; H2 bucketed re-arm key.)
- Coverage: pytest-cov global `fail_under = 90` (branch); `mesh.data_jobs` module ≥90%.
- **RLS red line (CRITICAL-1):** every fresh `self._factory()` session that touches an RLS table (`data_jobs`, `attachment_blobs`, …) MUST `await set_tenant_context(session, workspace_id)` first — including `_infer_mapping` (the wizard default `auto_infer` path) and the idempotency-race re-selects. The app role connects fail-closed; a missing GUC 500s. This MUST be e2e-tested through the real app-role server.
- Real e2e: real uvicorn subprocess + httpx against the `mesh_app` RLS role + real MinIO; red-line tests mandatory: two-phase partial success, run-before-validate 422, source-replace 422 + worker critical notification, T31 kill-worker resume + fencing reject-stale + replay-no-dup, source-attachment `RESTRICT`, export signed-download bytes, **auto_infer default flow under app-role RLS**, **double-crash-at-same-checkpoint reaper re-arm**.
- Git: author/committer `cnwenf <cnwenf@outlook.com>`, NEVER any Co-Authored-By line, `core.hooksPath /dev/null`, rebase on latest main before PR (resolve migration-number collisions with concurrent modules).
- Anonymization: no reference-source leakage anywhere (code/comments/docs/commits/branch names).
- Response envelope per README §6.14; errors via `src/mesh/errors.py` classes with the §3.12 codes (`mapping_invalid`, `validation_required`, `source_not_ready`, `source_changed`, `export_too_large`, row-level `project_key_taken`, …).

## File Structure

### Backend — create

| File | Responsibility |
|---|---|
| `backend/migrations/versions/0026_data_jobs.py` | DDL: `data_jobs` + `data_job_rows` per §2.5 (status/`row_key`/status-field CHECKs, `UNIQUE(workspace_id,id)`, composite FKs: source `RESTRICT`, product column-level `SET NULL (result_attachment_id)`, requester `RESTRICT`), §2.5 indexes (incl. `idx_data_jobs_lease_expired`), fail-closed RLS, `mesh_app` grants; `notifications_type` CHECK rebuilt with `data_job_finished` |
| `backend/src/mesh/db/models/data_job.py` | `DataJob` + `DataJobRow` ORM models + status tuple constants |
| `backend/src/mesh/data_jobs/mapping.py` | import/export mapping validation + `infer_mapping` (auto_infer) |
| `backend/src/mesh/data_jobs/transforms.py` | pure per-row transforms (7 transforms) + title-length / due<start pre-validation + row-error codes |
| `backend/src/mesh/data_jobs/parser.py` | streaming CSV/JSON row iteration + header read + `RowKeyAllocator` (`ref:<sha256(ref)>` fixed-length / content-addressed fallback) |
| `backend/src/mesh/data_jobs/report.py` | streaming error-report CSV writer |
| `backend/src/mesh/data_jobs/runner.py` | worker pipeline: claim (lease_seq++), fenced batched import, validate, export hand-off, `fail_job` (fenced), terminal notification |
| `backend/src/mesh/data_jobs/exporter.py` | streaming export (cursor pages → temp file → `register_server_attachment`) |
| `backend/src/mesh/data_jobs/reaper.py` | expired-lease reclaim + stuck-pending re-enqueue with **bucketed** resume keys + resume-cap guard |
| `backend/src/mesh/data_jobs/channels.py` | `data_job:{id}` per-resource subscription checker (requester/admin) |
| `backend/src/mesh/data_jobs/routes.py` | REST §3.1 (workspace-less paths via SECURITY DEFINER `mesh_data_job_workspace_id`) |
| `backend/src/mesh/data_jobs/service.py` | API orchestration; **set tenant ctx on every fresh session** (CRITICAL-1) |
| `backend/src/mesh/data_jobs/schemas.py` | Pydantic request bodies |

### Backend — modify

| File | Change |
|---|---|
| `backend/src/mesh/db/models/__init__.py` | register `DataJob`, `DataJobRow` |
| `backend/src/mesh/db/models/notification.py` | add `data_job_finished` to `NOTIFICATION_TYPE_VALUES` |
| `backend/src/mesh/comment_inbox/notifications.py` | `policy_for(..., data_job_status=)` branch (the §6.13 three rows); `handle` passes it through |
| `backend/src/mesh/attachment/service.py` | `register_server_attachment` (content-addressed blob reuse + orphan cleanup) |
| `backend/src/mesh/attachment/storage.py` | streaming `put_fileobj` / `download_to_path` (memory red line) |
| `backend/src/mesh/config.py` | data-job settings (`data_job_batch_size`, `data_job_lease_ttl`, `data_job_max_resumes`, export caps, …) |
| `backend/src/mesh/api/app.py` | wire `DataJobService`, router, `register_data_job_checkers` |
| `backend/src/mesh/realtime/app.py` + `realtime/auth.py` | register data-job checker; add `data_job` to `RESOURCE_SCOPED_ENTITIES` |
| `backend/src/mesh/workers/main.py` | register `data_job.enqueue`/`data_job.resume` handlers + reaper `TaskSpec` |

### Frontend — create

`frontend/src/features/data-jobs/` — `api.ts`, `types.ts`, `realtime.ts`, `dataJobs.css`, `index.ts`, `ImportWizard.tsx` (5-step wizard; first step posts `auto_infer: mapping===null`), `ExportDialog.tsx`, `DataManagementPage.tsx`, `__tests__/{api,realtime,components}.test.tsx` + `testImports.ts`.

### Frontend — modify

`App.tsx` (route `w/:slug/settings/data`), `workspace/pages/WorkspaceSettingsPage.tsx` (data-management section), `features/projects/ProjectDetailPage.tsx` (export/import contextual entries), `features/attachments/useAttachmentUploader.ts` (`workspaceId` for unlinked source upload), i18n catalogs `en.json`/`zh-CN.json` (+ djb2 version bump) + `__tests__/catalogs.test.ts` placeholder allowlist.

## Tasks (red-line order)

- [ ] T1 models + migration 0026 (single head after 0025_chat_security_indexes) + model registration + drift-guard green.
- [ ] T2 mapping validation + transforms (incl. title-length / due<start pre-validation) + parser (fixed-length ref key) + report writer (unit).
- [ ] T3 service orchestration with **tenant ctx on every fresh session** (CRITICAL-1) + REST routes + channel checker wiring.
- [ ] T4 worker runner: fenced batched import + claim-before-create + checkpoint + **fenced `fail_job`** + per-row exception isolation + terminal notification.
- [ ] T5 exporter streaming + `register_server_attachment` content-addressed reuse.
- [ ] T6 reaper: expired-lease reclaim + **bucketed** resume key + resume-cap guard.
- [ ] T7 notifications `data_job_finished` policy + realtime `data_job.updated`.
- [ ] T8 frontend wizard + data-management page + project entries + i18n parity.
- [ ] T9 unit coverage ≥90% (module + global gate).
- [ ] T10 real e2e red lines incl. auto_infer-under-RLS + double-crash reaper re-arm + T31 set.
- [ ] T11 real-browser UI walkthrough against PR code (auto_infer default flow) → fresh screenshots.
- [ ] T12 rebase latest main (resolve migration collision) + push + CI green + re-mention acceptor.

## Verification checklist (acceptance)

- [ ] `pytest --cov` global ≥90% AND `mesh.data_jobs` ≥90%.
- [ ] auto_infer `POST /data-jobs/import` returns 201 (not 500) on the app-role server; inferred mapping validates.
- [ ] title>255 / due<start / key-collision rows → `completed_with_errors` (row-level), dry-run predicts them.
- [ ] double hard-crash at same checkpoint → reaper re-arms (bucketed key not deduped) → job completes, zero dup entities.
- [ ] fencing rejects stale-worker batch; source-replace → 422 + worker `failed(source_changed)` + critical notification; source-attachment physical delete `RESTRICT`ed; export product bytes fetched via signed URL.
- [ ] PR `mergeable` not CONFLICTING; backend-ci / frontend / spec-checks green on the PR.
- [ ] UI walkthrough screenshots produced from PR code (not a stale stack).
