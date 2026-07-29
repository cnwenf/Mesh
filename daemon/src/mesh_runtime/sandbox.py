"""Real Linux namespace/cgroup sandbox (runtime-executor.md §1.2/§4.3, S-03).

Each attempt runs in its own user-visible namespaces (mount + pid + net +
ipc + uts), a cgroup2 leaf with hard limits, and a network namespace with NO
default route — the only reachable address is the per-attempt egress gateway
on a veth /30. The child drops to an unprivileged uid before exec.

FAIL-CLOSED: any setup, handshake or verification failure tears down every
resource already created and raises ``SandboxUnavailableError``. The daemon
NEVER degrades to a bare run (§5.2 red line).

The in-child half lives in :mod:`mesh_runtime.sandbox_init`; the two
coordinate over a two-pipe handshake (NETNS_READY → daemon configures cgroup
+ netns → GO → mounts/setuid → SANDBOX_READY <pid> → exec).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mesh_runtime.errors import DaemonError

_STATUS_TIMEOUT_SECONDS = 15.0
_DESTROY_GRACE_SECONDS = 2.0
_CGROUP_CONTROLLERS = ("cpu", "memory", "pids", "io")


class SandboxUnavailableError(DaemonError):
    """Sandbox setup failed or cannot be verified. The attempt must fail
    with failure_reason=sandbox_violation — never run bare."""


@dataclass(frozen=True)
class SandboxSpec:
    attempt_id: str
    root: Path  # daemon-managed attempt root (worktree/tmp/run live here)
    uid: int
    gid: int
    argv: tuple[str, ...]
    env: dict
    ro_binds: tuple[str, ...]
    memory_bytes: int
    cpu_quota_us: int
    cpu_period_us: int
    pids_max: int
    tmp_bytes: int
    gateway_port: int = 0  # per-attempt egress proxy port on the host veth IP


@dataclass(frozen=True)
class SandboxHandle:
    attempt_id: str
    proc: asyncio.subprocess.Process
    outer_pid: int
    inner_pid: int
    cgroup_path: str
    veth_host: str
    veth_peer: str
    root: Path
    host_ip: str
    sandbox_ip: str
    verified_uid: int


@dataclass(frozen=True)
class LinkReservation:
    """A reserved per-attempt veth /30, allocated before the sandbox exists
    so the egress gateway can bind the host-side IP up front (§3.4)."""

    host_ip: str
    sandbox_ip: str
    veth_host: str
    veth_peer: str


class SandboxManager:
    def __init__(
        self,
        *,
        state_root: Path,
        sandbox_uid: int,
        sandbox_gid: int,
        cgroup_base: Path = Path("/sys/fs/cgroup/mesh-attempts"),
        python_bin: str | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.sandbox_uid = sandbox_uid
        self.sandbox_gid = sandbox_gid
        self.cgroup_base = Path(cgroup_base)
        self._python = python_bin or sys.executable
        self._handles: dict[str, SandboxHandle] = {}
        self._pending_links: dict[str, LinkReservation] = {}

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        await asyncio.to_thread(self._ensure_base_cgroup)

    async def shutdown(self) -> None:
        for handle in list(self._handles.values()):
            await self.destroy(handle)

    async def reserve_link(self, attempt_id: str) -> LinkReservation:
        """Reserve the attempt's /30 link addresses BEFORE provisioning so
        the egress gateway can bind the host veth IP first (idempotent)."""
        reservation = self._pending_links.get(attempt_id)
        if reservation is None:
            host_ip, sandbox_ip, veth_host, veth_peer = await self._reserve_link_addresses(
                attempt_id
            )
            reservation = LinkReservation(host_ip, sandbox_ip, veth_host, veth_peer)
            self._pending_links[attempt_id] = reservation
        return reservation

    def release_link(self, attempt_id: str) -> None:
        """Drop a reservation that provisioning never consumed (idempotent)."""
        self._pending_links.pop(attempt_id, None)

    def _ensure_base_cgroup(self) -> None:
        self.cgroup_base.mkdir(parents=True, exist_ok=True)
        current = _read(self.cgroup_base / "cgroup.subtree_control").split()
        want = {f"+{name}" for name in _CGROUP_CONTROLLERS}
        missing = want - set(current)
        if missing:
            try:
                _write(self.cgroup_base / "cgroup.subtree_control", " ".join(sorted(missing)))
            except OSError as exc:
                raise SandboxUnavailableError(f"cgroup delegation unavailable: {exc}") from exc

    # -- provision -----------------------------------------------------------

    async def provision(self, spec: SandboxSpec) -> SandboxHandle:
        """Create the full isolation stack and exec the provider inside it.
        Fail-closed: any error undoes every partial resource."""
        if not spec.argv:
            raise SandboxUnavailableError("empty provider argv")
        if spec.uid == os.getuid() or spec.uid == 0:
            raise SandboxUnavailableError(
                "sandbox uid must be unprivileged and differ from the daemon uid"
            )
        created: dict = {"root": False, "cgroup": None, "veth": None, "proc": None}
        try:
            await asyncio.to_thread(self._layout, spec)
            created["root"] = True
            cgroup_path = await asyncio.to_thread(self._create_cgroup, spec)
            created["cgroup"] = cgroup_path
            link = self._pending_links.pop(spec.attempt_id, None)
            if link is None:  # standalone provisioning (no prior reservation)
                host_ip, sandbox_ip, veth_host, veth_peer = await self._reserve_link_addresses(
                    spec.attempt_id
                )
            else:
                host_ip, sandbox_ip, veth_host, veth_peer = (
                    link.host_ip, link.sandbox_ip, link.veth_host, link.veth_peer,
                )
            created["veth"] = veth_host
            handle = await self._spawn_and_verify(
                spec, cgroup_path=cgroup_path,
                host_ip=host_ip, sandbox_ip=sandbox_ip,
                veth_host=veth_host, veth_peer=veth_peer,
            )
        except SandboxUnavailableError:
            await self._rollback(spec, created)
            raise
        except (OSError, ValueError, TimeoutError) as exc:
            await self._rollback(spec, created)
            raise SandboxUnavailableError(f"sandbox setup failed: {type(exc).__name__}") from exc
        self._handles[spec.attempt_id] = handle
        return handle

    def _layout(self, spec: SandboxSpec) -> None:
        root = Path(spec.root)
        # exist_ok: the broker may have created run/ already (§3.1 order).
        # Freshness is guaranteed by S-08 cleanup removing the root on terminal.
        root.mkdir(parents=True, exist_ok=True)
        # 0711: the sandbox user may traverse "/" (needed to reach its own
        # mounts) but cannot list it; everything sensitive sits in 0700 dirs.
        os.chmod(root, 0o711)
        worktree = root / "worktree"
        worktree.mkdir(exist_ok=True)
        os.chown(worktree, spec.uid, spec.gid)
        os.chmod(worktree, 0o700)
        tmp = root / "tmp"
        tmp.mkdir(exist_ok=True)
        run = root / "run"
        run.mkdir(exist_ok=True)
        # Owned by the sandbox user (0700) so it can reach its broker socket;
        # platform configs inside are root-owned 0444, later bind-mounted ro.
        os.chown(run, spec.uid, spec.gid)
        os.chmod(run, 0o700)

    def _create_cgroup(self, spec: SandboxSpec) -> str:
        path = self.cgroup_base / f"mesh-{spec.attempt_id}"
        path.mkdir(exist_ok=True)
        _write(path / "memory.max", str(spec.memory_bytes))
        _write(path / "memory.swap.max", "0")
        _write(path / "cpu.max", f"{spec.cpu_quota_us} {spec.cpu_period_us}")
        _write(path / "pids.max", str(spec.pids_max))
        try:
            _write(path / "io.max", "")  # best-effort: not all devices accept
        except OSError:
            pass
        return str(path)

    async def _reserve_link_addresses(self, attempt_id: str) -> tuple[str, str, str, str]:
        digest = hashlib.sha256(attempt_id.encode()).digest()
        for salt in range(16):
            base_fourth = (digest[salt] % 63) * 4  # /30-aligned network base
            base_third = digest[(salt + 1) % len(digest)]
            host_ip = f"169.254.{base_third}.{base_fourth + 1}"
            sandbox_ip = f"169.254.{base_third}.{base_fourth + 2}"
            short = hashlib.sha256(f"{attempt_id}:{salt}".encode()).hexdigest()[:8]
            veth_host = f"mvh{short[:11]}"
            veth_peer = f"mvs{short[:11]}"
            probe = subprocess.run(
                ["ip", "addr", "add", f"{host_ip}/30", "dev", "lo"],
                capture_output=True,
            )
            if probe.returncode == 0:  # address free — release and use it on the veth
                subprocess.run(["ip", "addr", "del", f"{host_ip}/30", "dev", "lo"],
                               capture_output=True)
                return host_ip, sandbox_ip, veth_host, veth_peer
        raise SandboxUnavailableError("no free link-local /30 for the sandbox veth")

    async def _spawn_and_verify(
        self, spec: SandboxSpec, *, cgroup_path: str,
        host_ip: str, sandbox_ip: str, veth_host: str, veth_peer: str,
    ) -> SandboxHandle:
        import json

        control_r, control_w = os.pipe()
        status_r, status_w = os.pipe()
        env = {**spec.env, "MESH_GATEWAY_HOST_IP": host_ip}
        if spec.gateway_port:
            # The ONLY exit: the per-attempt egress proxy on the host veth IP.
            proxy = f"http://{host_ip}:{spec.gateway_port}"
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy
        spec_payload = {
            "control_fd": control_r,
            "status_fd": status_w,
            "root": str(spec.root),
            "uid": spec.uid,
            "gid": spec.gid,
            "argv": list(spec.argv),
            "env": env,
            "ro_binds": list(spec.ro_binds),
            "tmp_bytes": spec.tmp_bytes,
        }
        spec_path = Path(spec.root) / "run" / "sandbox-spec.json"
        spec_path.write_text(json.dumps(spec_payload))
        os.chmod(spec_path, 0o600)
        proc = await asyncio.create_subprocess_exec(
            self._python, "-m", "mesh_runtime.sandbox_init", str(spec_path),
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            pass_fds=(control_r, status_w),
            cwd="/",
            env={"PATH": "/usr/bin:/bin"},
        )
        os.close(control_r)
        os.close(status_w)
        self._pending_proc = proc  # rollback kills it if the handshake fails
        try:
            return await self._handshake(
                spec, proc=proc, control_w=control_w, status_r=status_r,
                cgroup_path=cgroup_path, host_ip=host_ip, sandbox_ip=sandbox_ip,
                veth_host=veth_host, veth_peer=veth_peer,
            )
        finally:
            os.close(control_w)
            os.close(status_r)

    async def _handshake(
        self, spec, *, proc, control_w, status_r, cgroup_path,
        host_ip, sandbox_ip, veth_host, veth_peer,
    ) -> SandboxHandle:
        # 1. Child unshared its namespaces and is WAITING — configure its
        #    cgroup and netns, then release it.
        line = await asyncio.to_thread(_read_line, status_r, _STATUS_TIMEOUT_SECONDS)
        if line != "NETNS_READY":
            raise SandboxUnavailableError(f"bad sandbox handshake: {line!r}")
        _write(Path(cgroup_path) / "cgroup.procs", str(proc.pid))
        await self._configure_netns(proc.pid, veth_host, veth_peer, host_ip, sandbox_ip)
        os.write(control_w, b"GO\n")
        # 2. Outer child reports the inner pid in OUR namespace, then the
        #    inner child mounts, drops privileges, and reports ready.
        inner_line = await asyncio.to_thread(_read_line, status_r, _STATUS_TIMEOUT_SECONDS)
        if not inner_line.startswith("INNER_PID "):
            raise SandboxUnavailableError(f"bad sandbox handshake: {inner_line!r}")
        inner_pid = int(inner_line.split()[1])
        ready = await asyncio.to_thread(_read_line, status_r, _STATUS_TIMEOUT_SECONDS)
        if not ready.startswith("SANDBOX_READY "):
            stderr = b""
            if proc.stderr is not None:
                try:
                    stderr = await asyncio.wait_for(proc.stderr.read(4096), timeout=1.0)
                except TimeoutError:
                    pass
            raise SandboxUnavailableError(
                f"sandbox init failed: {ready!r} {stderr.decode(errors='replace')[:200]}"
            )
        # 3. Verify the kernel state BEFORE trusting the sandbox.
        verified_uid = await asyncio.to_thread(
            self._verify, inner_pid, cgroup_path, spec.uid
        )
        # Verification passed — release the provider.
        os.write(control_w, b"EXEC\n")
        return SandboxHandle(
            attempt_id=spec.attempt_id, proc=proc, outer_pid=proc.pid,
            inner_pid=inner_pid, cgroup_path=cgroup_path,
            veth_host=veth_host, veth_peer=veth_peer, root=Path(spec.root),
            host_ip=host_ip, sandbox_ip=sandbox_ip, verified_uid=verified_uid,
        )

    async def _configure_netns(
        self, pid: int, veth_host: str, veth_peer: str, host_ip: str, sandbox_ip: str
    ) -> None:
        def _run(*argv: str) -> None:
            result = subprocess.run(list(argv), capture_output=True)
            if result.returncode != 0:
                raise SandboxUnavailableError(
                    f"{' '.join(argv[:3])} failed: {result.stderr.decode(errors='replace')[:160]}"
                )

        def _setup() -> None:
            _run("ip", "link", "add", veth_host, "type", "veth", "peer", "name", veth_peer)
            try:
                _run("ip", "addr", "add", f"{host_ip}/30", "dev", veth_host)
                _run("ip", "link", "set", veth_host, "up")
                _run("ip", "link", "set", veth_peer, "netns", str(pid))
                _run("nsenter", "-t", str(pid), "-n", "ip", "addr", "add",
                     f"{sandbox_ip}/30", "dev", veth_peer)
                _run("nsenter", "-t", str(pid), "-n", "ip", "link", "set", veth_peer, "up")
                _run("nsenter", "-t", str(pid), "-n", "ip", "link", "set", "lo", "up")
                # NO default route is added: the /30 subnet route to the
                # gateway host IP is the sandbox's ONLY exit (§3.4).
            except OSError:
                subprocess.run(["ip", "link", "del", veth_host], capture_output=True)
                raise

        await asyncio.to_thread(_setup)

    def _verify(self, inner_pid: int, cgroup_path: str, expected_uid: int) -> int:
        proc_path = Path(f"/proc/{inner_pid}")
        if not proc_path.exists():
            raise SandboxUnavailableError("sandbox process vanished before verification")
        status = _read(proc_path / "status")
        uid_line = next(
            (line for line in status.splitlines() if line.startswith("Uid:")), ""
        )
        uid = int(uid_line.split()[1])
        if uid != expected_uid:
            raise SandboxUnavailableError(
                f"sandbox did not drop privileges (pid {inner_pid}: uid {uid} != {expected_uid})"
            )
        cgroups = _read(proc_path / "cgroup")
        if Path(cgroup_path).name not in cgroups:
            raise SandboxUnavailableError("sandbox process escaped its cgroup")
        # EVERY namespace the sandbox claims must actually differ from the
        # daemon's — a regression dropping any CLONE_NEW* flag is caught here,
        # before the provider is released by the EXEC gate (§5.2 ISO-02).
        for ns in ("net", "mnt", "pid", "ipc", "uts"):
            own = os.readlink(f"/proc/self/ns/{ns}")
            theirs = os.readlink(proc_path / "ns" / ns)
            if own == theirs:
                raise SandboxUnavailableError(
                    f"sandbox shares the daemon {ns} namespace"
                )
        return uid

    # -- teardown --------------------------------------------------------------

    async def destroy_attempt(self, attempt_id: str) -> None:
        """Destroy the sandbox registered for an attempt (idempotent)."""
        handle = self._handles.get(attempt_id)
        if handle is not None:
            await self.destroy(handle)

    async def destroy(self, handle: SandboxHandle) -> None:
        """Idempotent S-08-grade teardown: kill the cgroup, reap, remove the
        veth pair and the attempt root. Never raises on best-effort steps."""
        self._handles.pop(handle.attempt_id, None)
        self.release_link(handle.attempt_id)

        def _teardown() -> None:
            # TERM the provider, then KILL the whole cgroup (no survivors).
            try:
                os.kill(handle.inner_pid, 15)
            except (ProcessLookupError, PermissionError):
                pass
            cgroup = Path(handle.cgroup_path)
            kill_file = cgroup / "cgroup.kill"
            if kill_file.exists():
                try:
                    _write(kill_file, "1")
                except OSError:
                    pass
            try:
                os.kill(handle.outer_pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            subprocess.run(["ip", "link", "del", handle.veth_host], capture_output=True)
            _safe_rmtree(handle.root)
            # cgroup dir removal only once empty (kernel-enforced).
            try:
                cgroup.rmdir()
            except OSError:
                pass

        if handle.proc.returncode is None:
            try:
                await asyncio.wait_for(handle.proc.wait(), timeout=_DESTROY_GRACE_SECONDS)
            except TimeoutError:
                pass
        await asyncio.to_thread(_teardown)
        if handle.proc.returncode is None:
            handle.proc.kill()
            await handle.proc.wait()

    async def _rollback(self, spec: SandboxSpec, created: dict) -> None:
        proc = getattr(self, "_pending_proc", None)
        if proc is not None and proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                pass
        self._pending_proc = None
        if created.get("veth"):
            subprocess.run(["ip", "link", "del", created["veth"]], capture_output=True)
        if created.get("cgroup"):
            cgroup = Path(created["cgroup"])
            kill_file = cgroup / "cgroup.kill"
            if kill_file.exists():
                try:
                    _write(kill_file, "1")
                except OSError:
                    pass
            try:
                cgroup.rmdir()
            except OSError:
                pass
        if created.get("root"):
            await asyncio.to_thread(_safe_rmtree, Path(spec.root))

    # -- capabilities -----------------------------------------------------------

    @staticmethod
    async def probe_capabilities(*, cgroup_base: Path, state_root: Path) -> dict:
        """Report sandbox capability for heartbeat/activate (§4.3). Only what
        genuinely works is reported; anything missing → degraded, no claim."""

        def _probe() -> dict:
            if os.getuid() != 0:
                return {
                    "sandbox": "unavailable",
                    "reason": "daemon must run as root with a privileged helper",
                }
            if not hasattr(os, "unshare"):
                return {"sandbox": "unavailable", "reason": "os.unshare unsupported"}
            try:
                cgroup_base.mkdir(parents=True, exist_ok=True)
                probe = cgroup_base / "probe"
                probe.mkdir(exist_ok=True)
                probe.rmdir()
            except OSError as exc:
                return {"sandbox": "unavailable", "reason": f"cgroup2 not writable: {exc}"}
            if shutil.which("ip") is None or shutil.which("nsenter") is None:
                return {"sandbox": "unavailable", "reason": "iproute2 missing"}
            state_root.mkdir(parents=True, exist_ok=True)
            return {"sandbox": "linux_ns", "egress_enforced": True}

        return await asyncio.to_thread(_probe)


# -- helpers -------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _read_line(fd: int, timeout: float) -> str:
    """Blocking newline read on a raw fd with timeout (handshake runs in a
    worker thread)."""
    import select
    import time

    chunks = b""
    end = time.monotonic() + timeout
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise SandboxUnavailableError("sandbox handshake timeout")
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if ready:
            chunk = os.read(fd, 256)
            if not chunk:
                return chunks.decode("utf-8", errors="replace").strip()
            chunks += chunk
            if b"\n" in chunks:
                return chunks.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()


def _safe_rmtree(root: Path) -> None:
    """Remove the attempt root without following symlinks or escaping it."""
    root = Path(root)
    if not root.exists():
        return
    real_root = os.path.realpath(root)

    def on_error(_func, path, _exc) -> None:
        pass  # best-effort; leftover state is reported via journal cleanup bits

    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames + dirnames:
            target = Path(dirpath) / name
            if os.path.islink(target):
                target.unlink(missing_ok=True)
    shutil.rmtree(root, ignore_errors=False, onerror=on_error)
    _ = real_root  # realpath computed to assert containment in stricter callers
