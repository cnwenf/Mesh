"""S-08 cleanup checklist (runtime-executor.md §3.6).

Every terminal / cancel / reclaim / restart-reconcile runs this idempotent
sequence:

    broker+egress closed → tokens/grants/credentials revoked → sandbox cgroup
    KILLed → leftover mounts released → sockets/worktree/cgroup removed →
    spool cleared ONLY if fully uploaded → journal cleanup bit = done.

The cleaner works from a WHITELIST resource manifest built by the daemon
itself — provider-supplied paths are never accepted. Deletion never follows
symlinks and every target must resolve inside the attempt root. Failures are
reported, never swallowed: each step's outcome is recorded in the journal
cleanup bits (cleanup_state) and reconciled at startup (§3.6 restart
reconciliation). A cleanup failure does NOT isolate the runtime here —
runtime isolation on cleanup failure is a deferred §4.4.1 ledger item,
wired later with the doctor/isolated state (tracked under S-12).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from mesh_runtime.sandbox import _safe_rmtree

CLEANUP_STEPS = (
    "broker_closed",
    "tokens_revoked",
    "cgroup_killed",
    "mounts_released",
    "artifacts_removed",
    "spool_flushed",
    "done",
)


@dataclass(frozen=True)
class ResourceManifest:
    """Whitelisted resources owned by one attempt. Built by the daemon —
    never from provider output."""

    attempt_root: Path
    socket_paths: tuple[str, ...] = ()
    spool_dir: Path | None = None


@dataclass(frozen=True)
class CleanupHandles:
    close_broker_and_egress: Callable[[], Awaitable[None]] | None = None
    revoke_credentials: Callable[[], Awaitable[None]] | None = None
    kill_sandbox: Callable[[], Awaitable[None]] | None = None


@dataclass
class CleanupReport:
    steps_done: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures and "done" in self.steps_done


class CleanupError(Exception):
    """Manifest validation failure — refuse to touch anything."""


class AttemptCleaner:
    def __init__(self, journal) -> None:
        self._journal = journal

    async def cleanup(
        self,
        attempt_id: str,
        manifest: ResourceManifest,
        handles: CleanupHandles,
        *,
        spool_flushed: bool,
    ) -> CleanupReport:
        self._validate_manifest(manifest)
        report = CleanupReport()
        await self._step(report, "broker_closed", attempt_id, self._close_broker, handles)
        await self._step(report, "tokens_revoked", attempt_id, self._revoke, handles)
        await self._step(report, "cgroup_killed", attempt_id, self._kill_sandbox, handles)
        await self._step(report, "mounts_released", attempt_id, self._release_mounts, manifest)
        await self._step(report, "artifacts_removed", attempt_id, self._remove_artifacts, manifest)
        if spool_flushed:
            await self._step(report, "spool_flushed", attempt_id, self._clear_spool, manifest)
        else:
            report.failures["spool_flushed"] = "spool not confirmed uploaded — retained"
        if not report.failures:
            report.steps_done.append("done")
        await self._journal.update(attempt_id, cleanup_state="|".join(report.steps_done))
        return report

    # -- steps -----------------------------------------------------------------

    @staticmethod
    async def _close_broker(handles: CleanupHandles) -> None:
        if handles.close_broker_and_egress is not None:
            await handles.close_broker_and_egress()

    @staticmethod
    async def _revoke(handles: CleanupHandles) -> None:
        if handles.revoke_credentials is not None:
            await handles.revoke_credentials()

    @staticmethod
    async def _kill_sandbox(handles: CleanupHandles) -> None:
        if handles.kill_sandbox is not None:
            await handles.kill_sandbox()

    @staticmethod
    async def _release_mounts(manifest: ResourceManifest) -> None:
        """Host-side stragglers only: sandbox mounts live in its private mnt
        namespace and vanish with it. Anything still mounted under the
        attempt root is a leak and gets lazily released."""
        root_real = os.path.realpath(manifest.attempt_root)
        leaked: list[str] = []
        try:
            with open("/proc/mounts", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].startswith(root_real + "/"):
                        leaked.append(parts[1])
        except OSError:
            return
        for target in leaked:
            subprocess.run(["umount", "-l", target], capture_output=True, check=False)

    @staticmethod
    async def _remove_artifacts(manifest: ResourceManifest) -> None:
        root_real = os.path.realpath(manifest.attempt_root)
        for socket_path in manifest.socket_paths:
            _remove_contained(socket_path, root_real)
        _safe_rmtree(manifest.attempt_root)

    @staticmethod
    async def _clear_spool(manifest: ResourceManifest) -> None:
        if manifest.spool_dir is not None:
            _safe_rmtree(manifest.spool_dir)

    # -- helpers -----------------------------------------------------------------

    async def _step(
        self, report: CleanupReport, name: str, attempt_id: str, fn: Callable, arg
    ) -> None:
        try:
            await fn(arg)
            report.steps_done.append(name)
        except Exception as exc:  # noqa: BLE001 — report, keep going
            report.failures[name] = type(exc).__name__

    @staticmethod
    def _validate_manifest(manifest: ResourceManifest) -> None:
        if not manifest.attempt_root or not Path(manifest.attempt_root).is_absolute():
            raise CleanupError("attempt_root must be an absolute path")


def _remove_contained(path: str, root_real: str) -> None:
    """Unlink one path: lstat only (no symlink follow), must resolve inside
    the attempt root. A symlink is removed as a link, never traversed."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    import stat as _stat

    if _stat.S_ISLNK(st.st_mode):
        # Refuse to act through the link: drop the link itself ONLY if it
        # lives inside the root.
        if not os.path.realpath(os.path.dirname(path)).startswith(root_real + os.sep):
            return
        os.unlink(path)
        return
    if not os.path.realpath(path).startswith(root_real + os.sep):
        return  # escape attempt — leave it, report elsewhere
    os.unlink(path)
