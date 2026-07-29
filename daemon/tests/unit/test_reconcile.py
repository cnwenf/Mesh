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
