"""S-08 cleanup — ordered, idempotent, whitelist-only, symlink-safe."""

from pathlib import Path

import pytest

from mesh_runtime.cleanup import (
    CLEANUP_STEPS,
    AttemptCleaner,
    CleanupError,
    CleanupHandles,
    ResourceManifest,
)
from mesh_runtime.journal import Journal


@pytest.fixture
async def journal(tmp_path):
    j = Journal(tmp_path / "ledger.sqlite3")
    await j.open()
    await j.put("att-1", execution_id="e1", runtime_id="r1", lease_seq=1, status="running")
    yield j
    await j.close()


class CallRecorder:
    def __init__(self, *, fail_on: str | None = None):
        self.calls: list[str] = []
        self._fail_on = fail_on

    def _make(self, name):
        async def _fn():
            self.calls.append(name)
            if self._fail_on == name:
                raise RuntimeError(f"{name} exploded")

        return _fn

    def handles(self) -> CleanupHandles:
        return CleanupHandles(
            close_broker_and_egress=self._make("broker"),
            revoke_credentials=self._make("revoke"),
            kill_sandbox=self._make("sandbox"),
        )


def plant_attempt(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "attempts" / "att-1"
    (root / "worktree").mkdir(parents=True)
    (root / "worktree" / "file.txt").write_text("data")
    sock = root / "run" / "broker.sock"
    sock.parent.mkdir(parents=True)
    sock.write_text("socket-stub")
    spool = tmp_path / "spool" / "att-1"
    spool.mkdir(parents=True)
    (spool / "batch.json").write_text("[]")
    return root, sock


class TestCleanup:
    async def test_full_cleanup_removes_everything_in_order(self, journal, tmp_path):
        root, sock = plant_attempt(tmp_path)
        recorder = CallRecorder()
        manifest = ResourceManifest(
            attempt_root=root, socket_paths=(str(sock),), spool_dir=tmp_path / "spool" / "att-1"
        )
        cleaner = AttemptCleaner(journal)
        report = await cleaner.cleanup("att-1", manifest, recorder.handles(), spool_flushed=True)
        assert report.ok
        # §3.6 order: broker → revoke → sandbox kill → ... → done.
        assert recorder.calls == ["broker", "revoke", "sandbox"]
        order = [s for s in report.steps_done if s in CLEANUP_STEPS]
        assert order == list(CLEANUP_STEPS)
        assert not root.exists()
        assert not (tmp_path / "spool" / "att-1").exists()
        entry = await journal.get("att-1")
        assert entry.cleanup_state.endswith("done")

    async def test_cleanup_is_idempotent(self, journal, tmp_path):
        root, sock = plant_attempt(tmp_path)
        manifest = ResourceManifest(attempt_root=root, socket_paths=(str(sock),))
        cleaner = AttemptCleaner(journal)
        first = await cleaner.cleanup("att-1", manifest, CallRecorder().handles(), spool_flushed=True)
        second = await cleaner.cleanup("att-1", manifest, CallRecorder().handles(), spool_flushed=True)
        assert first.ok and second.ok  # nothing raises on the second pass

    async def test_symlink_is_never_followed(self, journal, tmp_path):
        root, _sock = plant_attempt(tmp_path)
        outside = tmp_path / "precious.txt"
        outside.write_text("must survive")
        link = root / "run" / "evil.sock"
        link.symlink_to(outside)
        manifest = ResourceManifest(attempt_root=root, socket_paths=(str(link),))
        cleaner = AttemptCleaner(journal)
        report = await cleaner.cleanup("att-1", manifest, CallRecorder().handles(), spool_flushed=True)
        assert report.ok
        assert outside.read_text() == "must survive"  # target untouched
        assert not link.exists()  # the link itself is gone

    async def test_path_outside_root_is_not_removed(self, journal, tmp_path):
        root, _sock = plant_attempt(tmp_path)
        outside = tmp_path / "elsewhere.txt"
        outside.write_text("x")
        manifest = ResourceManifest(attempt_root=root, socket_paths=(str(outside),))
        cleaner = AttemptCleaner(journal)
        await cleaner.cleanup("att-1", manifest, CallRecorder().handles(), spool_flushed=True)
        assert outside.exists()  # containment check refused it

    async def test_spool_retained_until_flush_confirmed(self, journal, tmp_path):
        root, sock = plant_attempt(tmp_path)
        spool = tmp_path / "spool" / "att-1"
        manifest = ResourceManifest(
            attempt_root=root, socket_paths=(str(sock),), spool_dir=spool
        )
        cleaner = AttemptCleaner(journal)
        report = await cleaner.cleanup("att-1", manifest, CallRecorder().handles(), spool_flushed=False)
        assert not report.ok
        assert "spool_flushed" in report.failures
        assert spool.exists()  # retained: redacted batches not yet uploaded
        entry = await journal.get("att-1")
        assert not entry.cleanup_state.endswith("done")

    async def test_step_failure_reported_and_sequence_continues(self, journal, tmp_path):
        root, sock = plant_attempt(tmp_path)
        recorder = CallRecorder(fail_on="revoke")
        manifest = ResourceManifest(attempt_root=root, socket_paths=(str(sock),))
        cleaner = AttemptCleaner(journal)
        report = await cleaner.cleanup("att-1", manifest, recorder.handles(), spool_flushed=True)
        assert not report.ok
        assert "tokens_revoked" in report.failures
        # Later steps still ran — a stuck revoke must not leave the worktree.
        assert "sandbox" in recorder.calls
        assert not root.exists()

    async def test_manifest_validation(self, journal, tmp_path):
        cleaner = AttemptCleaner(journal)
        with pytest.raises(CleanupError):
            await cleaner.cleanup(
                "att-1",
                ResourceManifest(attempt_root=Path("relative/path")),
                CallRecorder().handles(),
                spool_flushed=True,
            )

    async def test_none_handles_are_noop_steps(self, journal, tmp_path):
        root, sock = plant_attempt(tmp_path)
        manifest = ResourceManifest(attempt_root=root, socket_paths=(str(sock),))
        cleaner = AttemptCleaner(journal)
        report = await cleaner.cleanup("att-1", manifest, CleanupHandles(), spool_flushed=True)
        assert report.ok
