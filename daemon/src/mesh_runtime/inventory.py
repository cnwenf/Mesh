"""Provider inventory + fail-closed binary probing (spec §1.4, design §4.2).

Probing scans ONLY administrator-configured absolute paths — never PATH/HOME/
repo coincidences. The binary is verified (regular file, no symlink, sane
owner/mode, SHA-256) BEFORE a no-network ``--version`` read; any failure marks
the provider unavailable and the runtime degraded (no claim).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from mesh_runtime.providers.base import ExecutorAdapter, ProbeResult

_VERSION_TIMEOUT_SECONDS = 5.0
_VERSION_OUTPUT_MAX = 4096
_SAFE_DIAGNOSTIC_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class BinaryProbe:
    ok: bool
    path: str
    sha256: str | None
    version: str | None
    reason: str | None


def _fail(path: str, reason: str) -> BinaryProbe:
    return BinaryProbe(ok=False, path=path, sha256=None, version=None, reason=reason)


def verify_binary_static(path: str) -> BinaryProbe:
    """Verify a provider binary's path/owner/mode and compute its SHA-256 —
    WITHOUT executing it (§1.4 step 2: verify BEFORE running anything, so an
    attacker-planted binary is never exec'd). Fail-closed on any doubt. The
    returned probe carries the digest but no version."""
    p = Path(path)
    if not p.is_absolute():
        return _fail(path, "provider path must be absolute")

    try:
        st = p.lstat()
    except FileNotFoundError:
        return _fail(path, "binary not found")
    if stat.S_ISLNK(st.st_mode):
        return _fail(path, "binary is a symlink — refusing")
    if not stat.S_ISREG(st.st_mode):
        return _fail(path, "binary is not a regular file")
    if st.st_mode & 0o002:
        return _fail(path, "binary is world-writable — refusing")
    if st.st_uid not in (0, os.getuid()):
        return _fail(path, "binary owner is neither root nor the daemon uid — refusing")
    if not os.access(p, os.X_OK):
        return _fail(path, "binary is not executable")

    sha = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            sha.update(chunk)
    return BinaryProbe(ok=True, path=path, sha256=sha.hexdigest(), version=None, reason=None)


async def read_binary_version(
    path: str, *, timeout: float = _VERSION_TIMEOUT_SECONDS
) -> BinaryProbe:
    """Read ``--version`` in a bare env. CALL ONLY on a statically verified
    (digest-matched) binary — never on an unverified path."""
    p = Path(path)
    try:
        proc = await asyncio.create_subprocess_exec(
            str(p),
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},  # bare env, no daemon leakage
        )
    except OSError as exc:
        return _fail(path, f"cannot exec binary for version check: {type(exc).__name__}")
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return _fail(path, "version check timeout")
    if proc.returncode != 0:
        return _fail(path, f"version check exited {proc.returncode}")
    lines = stdout[:_VERSION_OUTPUT_MAX].decode("utf-8", errors="replace").strip().splitlines()
    return BinaryProbe(ok=True, path=path, sha256=None, version=lines[0] if lines else "", reason=None)


async def probe_binary(path: str, *, timeout: float = _VERSION_TIMEOUT_SECONDS) -> BinaryProbe:
    """Verify and fingerprint a provider binary. Fail-closed on any doubt:
    static (path/owner/mode/SHA-256) first, then the ``--version`` read."""
    static = verify_binary_static(path)
    if not static.ok:
        return static
    ver = await read_binary_version(path, timeout=timeout)
    if not ver.ok:
        return ver
    return BinaryProbe(
        ok=True, path=path, sha256=static.sha256, version=ver.version, reason=None
    )


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    available: bool
    version: str | None
    binary_sha256: str | None
    capabilities: tuple[str, ...]
    reason: str | None


class Inventory:
    """Snapshot of probed providers; heartbeats report only what passed."""

    def __init__(self, statuses: list[ProviderStatus]) -> None:
        self.statuses = tuple(statuses)

    @classmethod
    async def probe(cls, adapters: list[ExecutorAdapter]) -> Inventory:
        statuses: list[ProviderStatus] = []
        for adapter in adapters:
            result: ProbeResult = await adapter.probe()
            statuses.append(
                ProviderStatus(
                    name=result.name,
                    available=result.available,
                    version=result.version,
                    binary_sha256=result.binary_sha256,
                    capabilities=tuple(result.capabilities),
                    reason=result.reason,
                )
            )
        return cls(statuses)

    def healthy(self) -> bool:
        return all(s.available for s in self.statuses)

    def security_isolation_failed(self) -> bool:
        """Whether the real provider fixture proved an isolation breach.

        Other provider failures are capability degradation.  A digest-verified
        binary that launches but honours a hostile repo fixture is a security
        boundary failure and must latch the runtime in ``isolated``.
        """
        return any(
            not status.available
            and isinstance(status.reason, str)
            and status.reason.startswith("isolation fixture probe failed:")
            for status in self.statuses
        )

    def capability_keys(self) -> list[str]:
        return sorted({cap for s in self.statuses for cap in s.capabilities})

    def degraded_reasons(self) -> list[str]:
        return [s.reason or f"{s.name} unavailable" for s in self.statuses if not s.available]

    def operational_diagnostics(self) -> list[dict]:
        """Return the safe, structured heartbeat projection for failed probes.

        Probe ``reason`` strings intentionally stay local: they can contain a
        binary path, host detail, or provider output. The server receives only
        a fixed reason code and already-declared slug-like capabilities.
        """
        diagnostics: list[dict] = []
        for status in self.statuses:
            if status.available:
                continue
            provider = status.name.lower()
            if not _SAFE_DIAGNOSTIC_NAME.fullmatch(provider):
                provider = "unknown"
            capabilities = sorted(
                {
                    capability
                    for capability in status.capabilities
                    if _SAFE_DIAGNOSTIC_NAME.fullmatch(capability)
                }
            )
            diagnostics.append(
                {
                    "reason_code": "provider_unavailable",
                    "missing_capabilities": capabilities,
                    "affected_task_types": [f"provider:{provider}"],
                }
            )
        return diagnostics

    def inventory_hash(self) -> str:
        canonical = json.dumps(
            [
                {
                    "name": s.name,
                    "available": s.available,
                    "version": s.version,
                    "binary_sha256": s.binary_sha256,
                    "capabilities": list(s.capabilities),
                }
                for s in self.statuses
            ],
            sort_keys=True,
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def heartbeat_payload(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "version": s.version,
                "healthy": s.available,
                "binary_sha256": s.binary_sha256,
                "capabilities": list(s.capabilities),
            }
            for s in self.statuses
            if s.available
        ]
