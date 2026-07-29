"""Shared fixtures for the ISO-01~14 red-line matrix.

Everything here is REAL: real Linux namespaces, cgroups, veth pairs and unix
sockets. No mocks, no skips — on an environment without root + namespace
support the whole matrix FAILS (fail-closed: the daemon may not claim a
sandbox it cannot prove).
"""

import os
import shutil
import uuid
from pathlib import Path

import pytest

from mesh_runtime.sandbox import SandboxManager, SandboxSpec

ISO_ROOT = Path(f"/mesh-iso-{os.getpid()}")
CGROUP_BASE = Path("/sys/fs/cgroup") / f"mesh-iso-{os.getpid()}"


def require_root() -> None:
    if os.getuid() != 0:
        pytest.fail(
            "ISO matrix requires root + Linux namespaces on a controlled runner — "
            "fail-closed, never skipped (§5.2)"
        )


@pytest.fixture
def iso_root():
    require_root()
    ISO_ROOT.mkdir(exist_ok=True)
    yield ISO_ROOT
    shutil.rmtree(ISO_ROOT, ignore_errors=True)


@pytest.fixture
async def manager():
    require_root()
    mgr = SandboxManager(
        state_root=ISO_ROOT / "state",
        sandbox_uid=65534,
        sandbox_gid=65534,
        cgroup_base=CGROUP_BASE,
    )
    await mgr.start()
    yield mgr
    await mgr.shutdown()
    shutil.rmtree(CGROUP_BASE, ignore_errors=True)


def make_spec(
    root: Path,
    argv: tuple[str, ...],
    *,
    attempt_id: str | None = None,
    pids_max: int = 64,
    ro_binds: tuple[str, ...] = (),
    env: dict | None = None,
) -> SandboxSpec:
    return SandboxSpec(
        attempt_id=attempt_id or str(uuid.uuid4()),
        root=root,
        uid=65534,
        gid=65534,
        argv=argv,
        env=env or {},
        ro_binds=ro_binds,
        memory_bytes=256 * 1024 * 1024,
        cpu_quota_us=100_000,
        cpu_period_us=100_000,
        pids_max=pids_max,
        tmp_bytes=64 * 1024 * 1024,
    )


async def run_sandbox(manager, spec, *, timeout: float = 20.0) -> tuple[bytes, int]:
    """Provision, drain stdout, destroy. Returns (stdout, exit_code)."""
    import asyncio

    handle = await manager.provision(spec)
    try:
        stdout, _ = await asyncio.wait_for(handle.proc.communicate(), timeout=timeout)
        code = handle.proc.returncode if handle.proc.returncode is not None else -1
    finally:
        await manager.destroy(handle)
    return stdout, code
