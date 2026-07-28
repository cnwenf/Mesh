"""Data import/export jobs — the platform data-mover (import-export.md).

CSV/JSON imports (field mapping → dry-run validate → partial-success run)
and async CSV/JSON exports, unified under the ``data_jobs`` entity. All
execution flows through the transactional outbox to the data-jobs worker
(README §6.6); products travel the unified attachment channel
(import-export.md §3.9). Crash recovery is fenced (``lease_seq``, §3.8 R4)
and row-idempotent (``data_job_rows`` ledger, §2.5).
"""
