import asyncio

import pytest

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.backoff import EMPTY_QUEUE, NETWORK, RATE_LIMITED_FALLBACK_SECONDS
from mesh_runtime.scheduler import ClaimScheduler
from tests.conftest import make_rand

TOKEN = "mesh_rt_test"
RUNTIME_ID = "11111111-1111-1111-1111-111111111111"
CLAIM_KEY = f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}/executions:claim"


def claim_body(attempt_id="a1", lease_seq=1):
    return {
        "data": {
            "execution": {"id": "e1", "config_snapshot": {}},
            "attempt": {"id": attempt_id, "lease_seq": lease_seq,
                        "lease_expires_at": "2026-07-29T00:00:00Z"},
        }
    }


def make_scheduler(server, *, on_claimed, max_concurrent=1, rand=None, clock=None):
    api = RuntimeApiClient("https://x.example", TOKEN, transport=server.transport())
    from mesh_runtime.timeutil import FakeClock

    return ClaimScheduler(
        api, RUNTIME_ID, max_concurrent=max_concurrent,
        clock=clock or FakeClock(), on_claimed=on_claimed, rand=rand,
    )


async def drain():
    for _ in range(10):
        await asyncio.sleep(0)


class TestStep:
    async def test_204_returns_empty_with_full_jitter_backoff(self, fake_server):
        fake_server.default_status = 204
        sched = make_scheduler(fake_server, on_claimed=lambda c: None, rand=make_rand([0.5, 0.5]))
        outcome, delay = await sched.step()
        assert outcome == "empty"
        assert delay == pytest.approx(EMPTY_QUEUE.delay(0, make_rand([0.5])))
        outcome2, delay2 = await sched.step()
        assert outcome2 == "empty"
        assert delay2 == pytest.approx(EMPTY_QUEUE.delay(1, make_rand([0.5])))

    async def test_claim_spawns_and_counts_inflight(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 200, claim_body("a1"))
        seen = []

        async def on_claimed(claim):
            seen.append(claim.attempt_id)

        sched = make_scheduler(fake_server, on_claimed=on_claimed)
        outcome, delay = await sched.step()
        assert outcome == "claimed"
        assert delay == 0.0
        assert sched.inflight == 1
        await drain()
        assert seen == ["a1"]
        assert sched.inflight == 0

    async def test_at_capacity_when_full(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 200, claim_body("a1"))
        release = asyncio.Event()

        async def on_claimed(claim):
            await release.wait()

        sched = make_scheduler(fake_server, on_claimed=on_claimed, max_concurrent=1)
        await sched.step()  # claims, inflight=1 (blocked on release)
        await drain()
        outcome, delay = await sched.step()
        assert outcome == "at_capacity"
        assert delay > 0
        release.set()
        await drain()
        assert sched.inflight == 0

    async def test_fatal_on_401_stops_claiming(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 401, {"error": {"code": "invalid_token"}})
        sched = make_scheduler(fake_server, on_claimed=lambda c: None)
        outcome, _ = await sched.step()
        assert outcome == "fatal"
        assert sched.fatal is not None

    async def test_rate_limited_uses_retry_after(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 429, None, headers={"Retry-After": "30"})
        sched = make_scheduler(fake_server, on_claimed=lambda c: None)
        outcome, delay = await sched.step()
        assert outcome == "rate_limited"
        assert delay == 30.0

    async def test_rate_limited_fallback_without_header(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 429, None)
        sched = make_scheduler(fake_server, on_claimed=lambda c: None)
        outcome, delay = await sched.step()
        assert outcome == "rate_limited"
        assert delay == RATE_LIMITED_FALLBACK_SECONDS

    async def test_server_error_backoff_increments_counter(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 500, None)
        fake_server.enqueue(CLAIM_KEY, 500, None)
        sched = make_scheduler(fake_server, on_claimed=lambda c: None, rand=make_rand([1.0, 1.0]))
        outcome, delay = await sched.step()
        assert outcome == "server_error"
        assert delay == pytest.approx(NETWORK.delay(0, make_rand([1.0])))
        outcome2, delay2 = await sched.step()
        assert delay2 == pytest.approx(NETWORK.delay(1, make_rand([1.0])))

    async def test_success_resets_backoff_counters(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 204)
        fake_server.enqueue(CLAIM_KEY, 204)
        fake_server.enqueue(CLAIM_KEY, 200, claim_body("a1"))
        fake_server.enqueue(CLAIM_KEY, 204)  # after success -> counter reset to 0

        async def on_claimed(claim):
            pass

        sched = make_scheduler(fake_server, on_claimed=on_claimed, rand=make_rand([0.5] * 5))
        await sched.step()  # empty (attempt 0)
        await sched.step()  # empty (attempt 1)
        outcome, _ = await sched.step()
        assert outcome == "claimed"
        await drain()
        outcome, delay = await sched.step()  # empty again, but reset to attempt 0
        assert outcome == "empty"
        assert delay == pytest.approx(EMPTY_QUEUE.delay(0, make_rand([0.5])))

    async def test_rejects_bad_max_concurrent(self, fake_server):
        with pytest.raises(ValueError):
            make_scheduler(fake_server, on_claimed=lambda c: None, max_concurrent=0)


class TestRunLoop:
    async def test_run_returns_immediately_on_fatal(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 401, {"error": {"code": "invalid_token"}})
        sched = make_scheduler(fake_server, on_claimed=lambda c: None)
        await sched.run(asyncio.Event())  # stops on fatal, no hang
        assert sched.fatal is not None

    async def test_run_stops_on_shutdown(self, fake_server):
        fake_server.default_status = 204
        sched = make_scheduler(fake_server, on_claimed=lambda c: None, rand=make_rand([0.0] * 100))
        shutdown = asyncio.Event()
        shutdown.set()
        await sched.run(shutdown)  # returns without claiming
