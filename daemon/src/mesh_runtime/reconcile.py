"""Crash-recovery reconciliation (spec §3.1 / design §7.5).

On startup the daemon reads its journal and reconciles each still-active row
against the server. It NEVER resumes an old provider and never guesses a
continuation from local state: if the server still considers the attempt
in-flight, this fresh process reports it ``failed`` with ``daemon_restart`` so
the server's reaper / retry policy owns what happens next; if the server has
already moved on (terminal / 409), the local row is dropped.

Rows marked ``terminal_seal_pending`` are the special case where the terminal
state WAS reported but the sealed log flush never completed (§3.9.3): the
spooled redacted batches get one best-effort replay+seal against the server,
then everything is cleaned up. Either way, reconciliation also REAPS the
crash residuals the dead process left behind — work directories, spool
files, sandbox cgroups and host-side veth links (§3.6 S-08) — via
:mod:`mesh_runtime.residual`. Reconciliation runs before the first claim, so
nothing can be in flight; failures are logged, never fatal.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.errors import DaemonError, LeaseConflictError
from mesh_runtime.journal import Journal
from mesh_runtime.residual import ResidualPaths, purge_attempt_residuals, purge_sandbox_wide
from mesh_runtime.spool import LogSpool

logger = logging.getLogger("mesh_runtime")

#: Generous replay cap: reconciliation must finish; a huge backlog that never
#: drains is a server problem, not a reason to wedge startup.
_REPLAY_MAX_BATCHES = 1024


async def reconcile_on_startup(
    journal: Journal,
    api: RuntimeApiClient,
    runtime_id: str,
    *,
    paths: ResidualPaths | None = None,
) -> int:
    """Return the number of journal rows cleaned up."""
    cleaned = 0
    for entry in await journal.list_active():
        if entry.status == "terminal_seal_pending":
            replayed = 0
            if paths is not None:
                replayed = await _replay_spooled_logs(
                    api, entry.attempt_id, entry.lease_seq, paths.spool_root
                )
            logger.info(
                "reconciled terminal_seal_pending attempt %s: %s batch(es) replayed",
                entry.attempt_id, replayed,
            )
        else:
            try:
                await api.transition(
                    entry.attempt_id,
                    lease_seq=entry.lease_seq,
                    status="failed",
                    failure_reason="daemon_restart",
                )
                logger.info(
                    "reconciled attempt %s (lease_seq=%s) as daemon_restart",
                    entry.attempt_id, entry.lease_seq,
                )
            except LeaseConflictError:
                # Server already terminal / leased to someone else: drop local state.
                logger.info(
                    "attempt %s already settled on server; dropping journal row",
                    entry.attempt_id,
                )
        await journal.update(entry.attempt_id, status="reconciled")
        await journal.delete(entry.attempt_id)
        cleaned += 1
        if paths is not None:
            _reap_attempt(entry.attempt_id, entry.work_dir, paths)
    if paths is not None:
        _reap_sandbox_wide(paths)
    return cleaned


async def _replay_spooled_logs(
    api: RuntimeApiClient, attempt_id: str, lease_seq: int, spool_root: Path
) -> int:
    """Best-effort sealed replay of crash-retained batches (§3.9.3). Returns
    the number of batches the server accepted. Any fencing/transient failure
    ends the replay — the batches are reaped with the attempt regardless; the
    server's offset stays the authority."""
    spool = LogSpool(spool_root / attempt_id.replace("/", "_"), max_bytes=0)
    batches = sorted(
        (
            *spool.pending(attempt_id, "stdout"),
            *spool.pending(attempt_id, "stderr"),
        ),
        key=lambda b: b.start_offset,
    )[:_REPLAY_MAX_BATCHES]
    replayed = 0
    last = len(batches) - 1
    for index, batch in enumerate(batches):
        try:
            await api.append_logs(
                attempt_id, lease_seq=lease_seq, stream=batch.stream,
                start_offset=batch.start_offset, lines=list(batch.lines),
                sealed=index == last,
            )
            replayed += 1
        except (DaemonError, LeaseConflictError):
            break  # attempt is terminal server-side or relay still down
    return replayed


def _reap_attempt(attempt_id: str, work_dir: str, paths: ResidualPaths) -> None:
    errors = purge_attempt_residuals(attempt_id, paths, work_dir=work_dir)
    if errors:
        logger.warning("residual cleanup incomplete for %s: %s", attempt_id, errors)


def _reap_sandbox_wide(paths: ResidualPaths) -> None:
    cgroups, links, errors = purge_sandbox_wide(paths)
    if cgroups or links:
        logger.info("startup sweep removed %s cgroup(s), %s veth link(s)", cgroups, links)
    if errors:
        logger.warning("startup residual sweep errors: %s", errors)
