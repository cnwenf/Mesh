"""Real Linux namespace/cgroup sandbox tests (S-03 foundation).

These run REAL kernels features — user/mount/pid/net/ipc/uts namespaces,
cgroup2 limits, veth pairs — with no mocks. They require root on a controlled
Linux runner; on any other environment they FAIL (never skip): a sandbox the
tests cannot exercise is a sandbox the daemon must not claim to have.
"""

import os
import uuid
from pathlib import Path

import pytest

from mesh_runtime.sandbox import SandboxManager, SandboxSpec, SandboxUnavailableError

CGROUP_BASE = Path("/sys/fs/cgroup") / f"mesh-test-{os.getpid()}"


def _require_root() -> None:
    if os.getuid() != 0:
        pytest.fail(
            "sandbox tests require root + Linux namespaces on a controlled runner "
            "(fail-closed: the daemon may not claim a sandbox it cannot prove)"
        )


pytestmark = pytest.mark.sandbox


@pytest.fixture
async def manager(tmp_path):
    _require_root()
    mgr = SandboxManager(
        state_root=tmp_path / "sandbox-state",
        sandbox_uid=65534,
        sandbox_gid=65534,
        cgroup_base=CGROUP_BASE,
    )
    await mgr.start()
    yield mgr
    await mgr.shutdown()


def make_spec(root: Path, *, argv: tuple[str, ...] = ("/bin/echo", "hi"), **kw) -> SandboxSpec:
    defaults = dict(
        attempt_id=str(uuid.uuid4()),
        root=root,
        uid=65534,
        gid=65534,
        argv=argv,
        env={"PROVIDER_PROBE": "1"},
        ro_binds=(),
        memory_bytes=256 * 1024 * 1024,
        cpu_quota_us=100_000,
        cpu_period_us=100_000,
        pids_max=64,
        tmp_bytes=64 * 1024 * 1024,
    )
    defaults.update(kw)
    return SandboxSpec(**defaults)


async def _read_all(handle, *, timeout: float = 15.0) -> tuple[bytes, int]:
    import asyncio

    stdout, _ = await asyncio.wait_for(handle.proc.communicate(), timeout=timeout)
    return stdout, handle.proc.returncode


class TestProvision:
    async def test_executes_argv_as_sandbox_uid(self, manager, tmp_path):
        spec = make_spec(tmp_path / "a1", argv=("/usr/bin/id", "-u"))
        handle = await manager.provision(spec)
        out, code = await _read_all(handle)
        assert out.strip() == b"65534"  # dropped to nobody
        assert code == 0
        assert handle.verified_uid == 65534
        await manager.destroy(handle)

    async def test_scrubbed_env_only(self, manager, tmp_path):
        # env must be exactly what the spec says (+ sandbox PATH), nothing
        # inherited from the daemon.
        spec = make_spec(
            tmp_path / "a2",
            argv=("/usr/bin/env",),
            env={"ONLY_THIS": "1"},
        )
        handle = await manager.provision(spec)
        out, _ = await _read_all(handle)
        lines = set(out.decode().strip().splitlines())
        assert "ONLY_THIS=1" in lines
        assert any(line.startswith("PATH=") for line in lines)
        assert all(not line.startswith("MESH_RT_") for line in lines)
        assert not any(line.startswith("LD_") for line in lines)
        assert not any(line.startswith("PYTHONPATH") for line in lines)
        await manager.destroy(handle)

    async def test_private_pid_namespace(self, manager, tmp_path):
        # /proc shows only sandbox processes — the daemon's pid is invisible.
        script = (
            "import os; pids = [p for p in os.listdir('/proc') if p.isdigit()]; "
            "print(len(pids)); print(os.getppid())"
        )
        spec = make_spec(tmp_path / "a3", argv=("/usr/bin/python3", "-c", script))
        handle = await manager.provision(spec)
        out, _ = await _read_all(handle)
        lines = out.decode().split()
        assert int(lines[0]) <= 3  # init(child), maybe kthreadd absent, python itself
        daemon_pid = str(os.getpid())
        assert daemon_pid not in lines
        await manager.destroy(handle)

    async def test_mount_namespace_readonly_system_writable_worktree(self, manager, tmp_path):
        script = (
            "touch /usr/evil 2>/dev/null && echo USR-WRITABLE || echo USR-RO; "
            "touch /etc/evil 2>/dev/null && echo ETC-WRITABLE || echo ETC-RO; "
            "touch /worktree/marker && echo WORKTREE-OK; "
            "test -w /tmp && echo TMP-OK"
        )
        spec = make_spec(tmp_path / "a4", argv=("/bin/sh", "-c", script))
        handle = await manager.provision(spec)
        out, _ = await _read_all(handle)
        text = out.decode()
        assert "USR-RO" in text
        assert "ETC-RO" in text
        assert "WORKTREE-OK" in text
        assert "TMP-OK" in text
        # Host side: the worktree file exists at the daemon-managed path.
        assert (spec.root / "worktree" / "marker").exists()
        await manager.destroy(handle)

    async def test_host_paths_invisible_inside_sandbox(self, manager, tmp_path):
        # The new root contains only the bound system dirs + attempt dirs —
        # no host home, daemon state, or other workspace dirs. "/" is not
        # listable (0711), so probe existence of hostile and required paths.
        daemon_cwd = os.getcwd()
        script = (
            "import os\n"
            f"hostile = ['/root', '/etc/shadow', {daemon_cwd!r}, '/var']\n"
            "for path in hostile:\n"
            "    print(f'{path}={os.path.exists(path)}')\n"
            "required = ['/worktree', '/tmp', '/run', '/proc', '/usr/bin/python3', '/etc/hosts']\n"
            "for path in required:\n"
            "    print(f'{path}={os.path.exists(path)}')\n"
        )
        spec = make_spec(tmp_path / "a5", argv=("/usr/bin/python3", "-c", script))
        handle = await manager.provision(spec)
        out, _ = await _read_all(handle)
        lines = dict(line.split("=") for line in out.decode().splitlines() if "=" in line)
        for hostile in ("/root", "/etc/shadow", daemon_cwd, "/var"):
            assert lines[hostile] == "False", f"host path {hostile} leaked into the sandbox"
        for required in ("/worktree", "/tmp", "/run", "/proc", "/usr/bin/python3", "/etc/hosts"):
            assert lines[required] == "True", f"required sandbox path {required} missing"
        await manager.destroy(handle)

    async def test_network_no_default_route_only_gateway_reachable(self, manager, tmp_path):
        # The manager injects MESH_GATEWAY_HOST_IP — the ONLY routed address
        # inside the netns (a /30 with the host veth, no default route).
        script = (
            "import os, socket\n"
            "routes = open('/proc/net/route').read()\n"
            "lines = [ln.split() for ln in routes.strip().splitlines()[1:]]\n"
            "has_default = any(fields[1] == '00000000' for fields in lines)\n"
            "print('DEFAULT' if has_default else 'NO-DEFAULT')\n"
            "pub = socket.socket(); pub.settimeout(2)\n"
            "print('PUB=', pub.connect_ex(('93.184.216.34', 80)))\n"
            "host_ip = os.environ['MESH_GATEWAY_HOST_IP']\n"
            "gw = socket.socket(); gw.settimeout(2)\n"
            "print('GW=', gw.connect_ex((host_ip, 1)))\n"
        )
        spec = make_spec(tmp_path / "a6", argv=("/usr/bin/python3", "-c", script))
        handle = await manager.provision(spec)
        out, _ = await _read_all(handle)
        text = out.decode()
        assert "NO-DEFAULT" in text  # sandbox has NO default route
        assert "PUB= 101" in text  # ENETUNREACH to a public IP
        assert "GW= 111" in text  # gateway IP routed (ECONNREFUSED: port 1 closed)
        await manager.destroy(handle)

    async def test_cgroup_pids_limit_kills_fork_bomb_only_inside(self, manager, tmp_path):
        script = (
            "import os, sys\n"
            "children = []\n"
            "for i in range(200):\n"
            "    try:\n"
            "        pid = os.fork()\n"
            "        if pid == 0:\n"
            "            os._exit(0)\n"
            "        children.append(pid)\n"
            "    except OSError:\n"
            "        print(f'LIMIT-AT={len(children)}')\n"
            "        sys.exit(0)\n"
            "print('NO-LIMIT')\n"
        )
        spec = make_spec(
            tmp_path / "a7",
            argv=("/usr/bin/python3", "-c", script),
            pids_max=16,
        )
        handle = await manager.provision(spec)
        out, _ = await _read_all(handle)
        text = out.decode()
        assert "NO-LIMIT" not in text
        assert "LIMIT-AT=" in text
        count = int(text.split("LIMIT-AT=")[1].strip())
        assert count <= 20  # pids.max enforced (forker + a couple slack)
        # Daemon (outside the cgroup) is unaffected — we're still running.
        assert os.getpid() > 0
        await manager.destroy(handle)

    async def test_two_concurrent_sandboxes_isolated(self, manager, tmp_path):
        spec_a = make_spec(tmp_path / "A", argv=("/bin/sleep", "2"))
        spec_b_script = (
            "import os, sys\n"
            "entries = os.listdir('/worktree')\n"
            "print('SEES-A' if 'secret-A.txt' in entries else 'ISOLATED')\n"
            "sys.exit(0)\n"
        )
        spec_b = make_spec(tmp_path / "B", argv=("/usr/bin/python3", "-c", spec_b_script))
        handle_a = await manager.provision(spec_a)
        (spec_a.root / "worktree" / "secret-A.txt").write_text("A-private")
        handle_b = await manager.provision(spec_b)
        out, _ = await _read_all(handle_b)
        assert "ISOLATED" in out.decode()
        assert "SEES-A" not in out.decode()
        # B's cgroup / netns differ from A's.
        assert handle_a.cgroup_path != handle_b.cgroup_path
        await manager.destroy(handle_b)
        await manager.destroy(handle_a)


class TestFailClosed:
    async def test_provision_failure_raises_and_cleans_up(self, manager, tmp_path):
        spec = make_spec(tmp_path / "f1", argv=("/nonexistent/binary",))
        with pytest.raises(SandboxUnavailableError):
            await manager.provision(spec)
        # No leftover cgroup, veth or root dir.
        assert not (manager.cgroup_base / "f1").exists()
        assert not (tmp_path / "f1").exists()

    async def test_uid_must_differ_from_daemon(self, manager, tmp_path):
        spec = make_spec(tmp_path / "f2", uid=os.getuid())
        with pytest.raises(SandboxUnavailableError):
            await manager.provision(spec)

    async def test_destroy_is_idempotent(self, manager, tmp_path):
        spec = make_spec(tmp_path / "f3", argv=("/bin/sleep", "30"))
        handle = await manager.provision(spec)
        await manager.destroy(handle)
        await manager.destroy(handle)  # second call must not raise
        assert not Path(handle.cgroup_path).exists()

    async def test_kill_all_stops_sleeping_provider(self, manager, tmp_path):
        spec = make_spec(tmp_path / "f4", argv=("/bin/sleep", "300"))
        handle = await manager.provision(spec)
        inner = handle.inner_pid
        await manager.destroy(handle)
        assert not Path(f"/proc/{inner}").exists()


class TestConcurrentRollback:
    async def test_failed_attempt_rollback_kills_only_its_own_sandbox(
        self, manager, tmp_path
    ):
        """MES-96 P1-1: two attempts provision concurrently; a handshake
        failure injected into A must roll back ONLY A's process. The shared
        ``SandboxManager`` may not let A's rollback reach across and kill B's
        already-spawned sandbox (the cascading double sandbox_violation).

        The property measured is rollback isolation: B completing with a live
        process while A's rollback ran concurrently. On a saturated CI host
        B's REAL kernel handshake can independently exceed its per-stage
        deadline (``sandbox handshake timeout``) — an environmental class the
        pre-fix bug did NOT produce (it killed B's process, surfacing as
        ``nsenter: cannot open /proc/...`` / dead-proc asserts). Such timeout
        attempts are retried so the assertions measure exactly the race under
        test; any OTHER failure mode fails immediately."""
        import asyncio

        last_env_failure: object = None
        for _ in range(5):
            spec_a = make_spec(
                tmp_path / "a", attempt_id="attempt-a-concurrent", argv=("/bin/sleep", "2")
            )
            spec_b = make_spec(
                tmp_path / "b", attempt_id="attempt-b-concurrent", argv=("/bin/sleep", "2")
            )
            real_handshake = manager._handshake
            # Deterministic overlap: A's injected failure fires only AFTER B's
            # handshake has begun (B's proc is spawned and parked in B's own
            # created[] slot by then) — a fixed sleep would make the pre-fix
            # regression flaky under load.
            b_handshake_started = asyncio.Event()

            async def flaky_handshake(spec, **kw):
                if spec.attempt_id == "attempt-a-concurrent":
                    await asyncio.wait_for(b_handshake_started.wait(), timeout=10.0)
                    raise SandboxUnavailableError("injected handshake failure")
                b_handshake_started.set()
                return await real_handshake(spec, **kw)

            manager._handshake = flaky_handshake
            results = await asyncio.gather(
                manager.provision(spec_a),
                manager.provision(spec_b),
                return_exceptions=True,
            )
            handle_b = results[1]
            if (
                isinstance(handle_b, SandboxUnavailableError)
                and "handshake timeout" in str(handle_b)
            ):
                last_env_failure = handle_b
                manager._handshake = real_handshake
                continue  # host load starvation — not the race under test
            assert isinstance(results[0], SandboxUnavailableError)
            assert not isinstance(handle_b, BaseException)
            # B's sandbox process was NOT killed by A's rollback.
            assert handle_b.proc.returncode is None
            # A is gone from the live handles (its own process reaped, no orphan).
            assert all(
                h.attempt_id != "attempt-a-concurrent" for h in manager._handles.values()
            )
            await manager.destroy(handle_b)
            return
        raise AssertionError(
            f"B's handshake timed out on 5/5 attempts — host too loaded to "
            f"measure the rollback race: {last_env_failure!r}"
        )


class TestCapabilities:
    async def test_probe_reports_linux_ns_when_root(self, tmp_path):
        _require_root()
        caps = await SandboxManager.probe_capabilities(
            cgroup_base=CGROUP_BASE / "probe", state_root=tmp_path / "probe"
        )
        assert caps["sandbox"] == "linux_ns"
        assert caps["egress_enforced"] is True


async def test_reserve_link_is_consumed_by_provision(manager, tmp_path):
    """The egress gateway binds the veth host IP BEFORE the sandbox exists
    (§3.4): provisioning must consume that exact reservation, not allocate a
    fresh /30 — otherwise the bound listener and the sandbox exit diverge."""
    attempt_id = str(uuid.uuid4())
    link = await manager.reserve_link(attempt_id)
    assert link.host_ip != link.sandbox_ip
    assert await manager.reserve_link(attempt_id) is link  # idempotent
    spec = make_spec(tmp_path / "root", attempt_id=attempt_id)
    handle = await manager.provision(spec)
    try:
        assert handle.host_ip == link.host_ip
        assert handle.sandbox_ip == link.sandbox_ip
        assert handle.veth_host == link.veth_host
        assert attempt_id not in manager._pending_links  # consumed
    finally:
        await manager.destroy(handle)
    manager.release_link(attempt_id)  # idempotent after consumption


async def test_release_link_drops_unconsumed_reservation(manager):
    attempt_id = str(uuid.uuid4())
    await manager.reserve_link(attempt_id)
    assert attempt_id in manager._pending_links
    manager.release_link(attempt_id)
    assert attempt_id not in manager._pending_links
    manager.release_link(attempt_id)  # no-op second time
