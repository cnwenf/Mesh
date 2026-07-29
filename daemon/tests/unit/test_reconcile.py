import pytest

from mesh_runtime.errors import LeaseConflictError
from mesh_runtime.journal import Journal
from mesh_runtime.reconcile import reconcile_on_startup


class StubApi:
    def __init__(self, fail_attempts=None):
        self.transitions = []
        self.fail_attempts = set(fail_attempts or [])

    async def transition(self, attempt_id, *, lease_seq, status, result=None, failure_reason=None):
        self.transitions.append(
            dict(attempt_id=attempt_id, lease_seq=lease_seq, status=status, failure_reason=failure_reason)
        )
        if attempt_id in self.fail_attempts:
            raise LeaseConflictError("409", code="attempt_terminal")
        return {}


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    yield j
    await j.close()


async def seed(journal, attempt_id, status, lease_seq=1):
    await journal.put(
        attempt_id, execution_id="e1", runtime_id="rt-1",
        lease_seq=lease_seq, status=status, work_dir=f"/w/{attempt_id}",
    )


class TestReconcile:
    async def test_reports_daemon_restart_and_deletes_active(self, journal):
        await seed(journal, "a1", "running", lease_seq=7)
        await seed(journal, "a2", "claimed", lease_seq=3)
        api = StubApi()
        cleaned = await reconcile_on_startup(journal, api, "rt-1")
        assert cleaned == 2
        by_id = {t["attempt_id"]: t for t in api.transitions}
        assert by_id["a1"]["status"] == "failed"
        assert by_id["a1"]["failure_reason"] == "daemon_restart"
        assert by_id["a1"]["lease_seq"] == 7
        assert by_id["a2"]["lease_seq"] == 3
        assert await journal.get("a1") is None
        assert await journal.get("a2") is None

    async def test_drops_rows_already_terminal_on_server(self, journal):
        await seed(journal, "a1", "running")
        api = StubApi(fail_attempts={"a1"})  # server says already settled
        cleaned = await reconcile_on_startup(journal, api, "rt-1")
        assert cleaned == 1
        assert await journal.get("a1") is None  # dropped despite 409

    async def test_ignores_non_active_rows(self, journal):
        await seed(journal, "done", "terminal_reported")
        await seed(journal, "lost", "lease_lost")
        await seed(journal, "live", "running")
        api = StubApi()
        cleaned = await reconcile_on_startup(journal, api, "rt-1")
        assert cleaned == 1
        assert [t["attempt_id"] for t in api.transitions] == ["live"]
        # non-active rows remain untouched
        assert await journal.get("done") is not None
        assert await journal.get("lost") is not None

    async def test_empty_journal(self, journal):
        api = StubApi()
        assert await reconcile_on_startup(journal, api, "rt-1") == 0
        assert api.transitions == []


class ResidualStubApi(StubApi):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.logs = []

    async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
        from mesh_runtime.api import LogAck

        self.logs.append(
            dict(attempt_id=attempt_id, stream=stream, start_offset=start_offset,
                 lines=list(lines), sealed=sealed)
        )
        end = start_offset + sum(len(line.encode()) for line in lines)
        return LogAck(accepted_end_offset=end, redacted_hits=0)


def make_paths(tmp_path):
    from mesh_runtime.residual import ResidualPaths

    work_root = tmp_path / "work"
    spool_root = tmp_path / "spool"
    cgroup_base = tmp_path / "cgroups"
    work_root.mkdir()
    spool_root.mkdir()
    cgroup_base.mkdir()
    return ResidualPaths(work_root=work_root, spool_root=spool_root, cgroup_base=cgroup_base)


class TestCrashResidualReaping:
    """Pinned must-fix: reconciliation reaps crash residuals — spool files,
    work dirs, sandbox cgroups — not just journal rows (S-08)."""

    async def test_reaps_work_dir_and_spool_of_restarted_attempt(self, journal, tmp_path):
        paths = make_paths(tmp_path)
        work_dir = paths.work_root / "e1" / "a1"
        work_dir.mkdir(parents=True)
        (work_dir / "checkout.bin").write_text("data")
        spool_dir = paths.spool_root / "a1"
        spool_dir.mkdir()
        (spool_dir / "a1__stdout__00000000000000000000.json").write_text("{}")
        await journal.put(
            "a1", execution_id="e1", runtime_id="rt-1",
            lease_seq=1, status="running", work_dir=str(work_dir),
        )
        await reconcile_on_startup(journal, ResidualStubApi(), "rt-1", paths=paths)
        assert not work_dir.exists(), "crash work dir not reaped"
        assert not spool_dir.exists(), "crash spool dir not reaped"

    async def test_replays_terminal_seal_pending_spool_then_reaps(self, journal, tmp_path):
        """terminal_seal_pending: the sealed flush never finished, so the
        spooled batches get ONE best-effort replay+seal against the server
        before the attempt is reaped — logs are recovered, not just deleted."""
        from mesh_runtime.spool import LogSpool, SpooledBatch

        paths = make_paths(tmp_path)
        spool = LogSpool(paths.spool_root / "a9", max_bytes=4096)
        spool.write(SpooledBatch("a9", "stdout", 0, ("hello ",)))
        spool.write(SpooledBatch("a9", "stdout", 6, ("world",)))
        await journal.put(
            "a9", execution_id="e1", runtime_id="rt-1",
            lease_seq=4, status="terminal_seal_pending", work_dir="",
        )
        api = ResidualStubApi()
        cleaned = await reconcile_on_startup(journal, api, "rt-1", paths=paths)
        assert cleaned == 1
        assert api.transitions == []  # NOT re-reported — already terminal
        assert [c["start_offset"] for c in api.logs] == [0, 6]
        assert [c["sealed"] for c in api.logs] == [False, True]  # sealed on the last
        assert not (paths.spool_root / "a9").exists()  # reaped after replay
        assert await journal.get("a9") is None

    async def test_replay_fencing_ends_replay_and_still_reaps(self, journal, tmp_path):
        """If the server fences the replay (attempt fully settled), give up
        gracefully and still reap — reconciliation must always complete."""
        from mesh_runtime.spool import LogSpool, SpooledBatch

        paths = make_paths(tmp_path)
        spool = LogSpool(paths.spool_root / "a8", max_bytes=4096)
        spool.write(SpooledBatch("a8", "stdout", 0, ("x",)))
        await journal.put(
            "a8", execution_id="e1", runtime_id="rt-1",
            lease_seq=1, status="terminal_seal_pending", work_dir="",
        )

        class FencingApi(ResidualStubApi):
            async def append_logs(self, attempt_id, *, lease_seq, stream, start_offset, lines, sealed=False):
                raise LeaseConflictError("409", code="attempt_terminal")

        cleaned = await reconcile_on_startup(journal, FencingApi(), "rt-1", paths=paths)
        assert cleaned == 1
        assert not (paths.spool_root / "a8").exists()

    async def test_refuses_work_dir_outside_work_root(self, journal, tmp_path):
        """Containment red line: a journal work_dir resolving outside the
        daemon work root is NEVER deleted."""
        paths = make_paths(tmp_path)
        outside = tmp_path / "outside" / "precious"
        outside.mkdir(parents=True)
        (outside / "keep.txt").write_text("keep")
        await journal.put(
            "a1", execution_id="e1", runtime_id="rt-1",
            lease_seq=1, status="running", work_dir=str(outside),
        )
        await reconcile_on_startup(journal, ResidualStubApi(), "rt-1", paths=paths)
        assert outside.exists(), "escape attempt must be refused"
        assert (outside / "keep.txt").exists()

    async def test_startup_sweep_removes_leftover_sandbox_cgroups(self, journal, tmp_path, monkeypatch):
        """Daemon-wide sweep: leftover mesh-* cgroup leaves from crashed runs
        are removed; non-mesh directories are untouched."""
        import mesh_runtime.residual as residual_mod

        paths = make_paths(tmp_path)
        (paths.cgroup_base / "mesh-dead1").mkdir()
        (paths.cgroup_base / "mesh-dead2").mkdir()
        (paths.cgroup_base / "unrelated").mkdir()

        def fake_run(argv, **kw):
            class R:
                returncode = 1  # no `ip` sweep in unit tests
                stdout = ""
            return R()

        monkeypatch.setattr(residual_mod.subprocess, "run", fake_run)
        await reconcile_on_startup(journal, ResidualStubApi(), "rt-1", paths=paths)
        assert not (paths.cgroup_base / "mesh-dead1").exists()
        assert not (paths.cgroup_base / "mesh-dead2").exists()
        assert (paths.cgroup_base / "unrelated").exists()

    async def test_no_paths_keeps_legacy_behavior(self, journal):
        """Without residual paths (contract path), reconcile only settles
        journal rows — no filesystem reaping."""
        await seed(journal, "a1", "running")
        api = StubApi()
        assert await reconcile_on_startup(journal, api, "rt-1") == 1
        assert await journal.get("a1") is None
