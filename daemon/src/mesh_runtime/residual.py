"""Crash-residual reaping (runtime-executor.md §3.1 / §3.6, S-08).

The live-attempt paths (supervisor ``_cleanup`` / security ``finish``) own
teardown while the daemon runs. If the daemon CRASHES, none of that runs and
resources leak: work directories, spooled log batches, sandbox cgroups and
host-side veth links. Startup reconciliation (before the first claim, so no
attempt can be in flight) reaps them here.

Every removal is whitelist-and-containment based: journal-provided work dirs
must resolve inside the daemon's own work root, per-attempt spool dirs are
derived from the attempt id (never from external input), and the daemon-wide
sweep only touches resources under the daemon's own cgroup base / with the
daemon's own link prefix. Deletion never follows symlinks. Failures are
logged and reported, never fatal — reconciliation must complete.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mesh_runtime.sandbox import _safe_rmtree

logger = logging.getLogger("mesh_runtime.residual")

#: Host-side veth links created by the sandbox are named ``mvh<digest>``; the
#: peer (``mvs…``) lives inside the sandbox netns and dies with it, so only
#: host-side stragglers need reaping.
_VETH_HOST_PREFIX = "mvh"
_CGROUP_LEAF_PREFIX = "mesh-"


@dataclass(frozen=True)
class ResidualPaths:
    """Daemon-owned locations where attempt resources live."""

    work_root: Path
    spool_root: Path
    cgroup_base: Path = Path("/sys/fs/cgroup/mesh-attempts")


@dataclass(frozen=True)
class ResidualReport:
    attempts_purged: int = 0
    cgroups_removed: int = 0
    links_removed: int = 0
    errors: tuple[str, ...] = ()


def purge_attempt_residuals(
    attempt_id: str, paths: ResidualPaths, *, work_dir: str
) -> list[str]:
    """Remove the on-disk resources of ONE settled attempt. Returns a list of
    human-readable errors (empty on full success)."""
    errors: list[str] = []
    if work_dir:
        errors.extend(_remove_contained_dir(Path(work_dir), paths.work_root, "work_dir"))
    errors.extend(
        _remove_contained_dir(
            paths.spool_root / _segment(attempt_id), paths.spool_root, "spool_dir"
        )
    )
    return errors


def purge_sandbox_wide(paths: ResidualPaths) -> tuple[int, int, list[str]]:
    """Daemon-wide sweep at startup: remove EVERY leftover sandbox cgroup
    under the daemon's cgroup base and EVERY host-side veth link. Safe because
    reconciliation runs before the first claim — nothing is in flight."""
    errors: list[str] = []
    cgroups = _sweep_cgroups(paths.cgroup_base, errors)
    links = _sweep_veth_links(errors)
    return cgroups, links, errors


def _remove_contained_dir(target: Path, root: Path, label: str) -> list[str]:
    """rmtree ``target`` only if it resolves strictly inside ``root``."""
    try:
        real_root = os.path.realpath(root)
        real_target = os.path.realpath(target)
        if real_target == real_root or not real_target.startswith(real_root + os.sep):
            return [f"{label} escapes its root — refused"]
        _safe_rmtree(target)
        return []
    except OSError as exc:
        return [f"{label}: {type(exc).__name__}"]


def _sweep_cgroups(cgroup_base: Path, errors: list[str]) -> int:
    if not cgroup_base.is_dir():
        return 0
    removed = 0
    for child in sorted(cgroup_base.iterdir()):
        if not (child.is_dir() and child.name.startswith(_CGROUP_LEAF_PREFIX)):
            continue
        try:
            _kill_cgroup(child)
            child.rmdir()
            removed += 1
        except OSError as exc:
            # A non-empty cgroup (processes the kernel has not reaped yet) or
            # a vanished dir — report and move on; the next startup retries.
            errors.append(f"cgroup {child.name}: {type(exc).__name__}")
    return removed


def _kill_cgroup(cgroup: Path) -> None:
    """Best-effort kill of any process still in the leaf cgroup before rmdir."""
    for killer in ("cgroup.kill", "cgroup.procs"):
        path = cgroup / killer
        if not path.exists():
            continue
        try:
            if killer == "cgroup.kill":
                path.write_text("1")
            else:
                procs = path.read_text().split()
                for pid in procs:
                    os.kill(int(pid), 9)
        except (OSError, ValueError):
            pass


def _sweep_veth_links(errors: list[str]) -> int:
    try:
        probe = subprocess.run(
            ["ip", "-o", "link", "show"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"veth sweep: {type(exc).__name__}")
        return 0
    if probe.returncode != 0:
        errors.append("veth sweep: ip link show failed")
        return 0
    removed = 0
    for line in probe.stdout.splitlines():
        name = _parse_link_name(line)
        if name is None or not name.startswith(_VETH_HOST_PREFIX):
            continue
        delete = subprocess.run(
            ["ip", "link", "del", name], capture_output=True, timeout=15
        )
        if delete.returncode == 0:
            removed += 1
        else:
            errors.append(f"veth {name}: delete failed")
    return removed


def _parse_link_name(ip_line: str) -> str | None:
    """``2: mvhXXXX: <BROADCAST...> mtu ...`` -> ``mvhXXXX``."""
    parts = ip_line.split()
    if len(parts) < 2:
        return None
    return parts[1].rstrip(":").split("@")[0] or None


def _segment(attempt_id: str) -> str:
    """Attempt ids are UUIDs; strip anything that could traverse paths."""
    return attempt_id.replace("/", "_").replace("\\", "_").replace("..", "_")
