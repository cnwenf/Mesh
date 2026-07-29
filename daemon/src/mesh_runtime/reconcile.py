"""Crash-recovery reconciliation (spec §3.1 / design §7.5).

On startup the daemon reads its journal and reconciles each still-active row
against the server. It NEVER resumes an old provider and never guesses a
continuation from local state: if the server still considers the attempt
in-flight, this fresh process reports it ``failed`` with ``daemon_restart`` so
the server's reaper / retry policy owns what happens next; if the server has
already moved on (terminal / 409), the local row is dropped.
"""

from __future__ import annotations

import logging

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.errors import LeaseConflictError
from mesh_runtime.journal import Journal

logger = logging.getLogger("mesh_runtime")


async def reconcile_on_startup(journal: Journal, api: RuntimeApiClient, runtime_id: str) -> int:
    """Return the number of journal rows cleaned up."""
    cleaned = 0
    for entry in await journal.list_active():
        try:
            await api.transition(
                entry.attempt_id,
                lease_seq=entry.lease_seq,
                status="failed",
                failure_reason="daemon_restart",
            )
            logger.info(
                "reconciled attempt %s (lease_seq=%s) as daemon_restart",
                entry.attempt_id,
                entry.lease_seq,
            )
        except LeaseConflictError:
            # Server already terminal / leased to someone else: drop local state.
            logger.info("attempt %s already settled on server; dropping journal row", entry.attempt_id)
        await journal.update(entry.attempt_id, status="reconciled")
        await journal.delete(entry.attempt_id)
        cleaned += 1
    return cleaned
