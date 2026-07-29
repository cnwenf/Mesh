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


def make_scheduler(server, *, on_claimed, max_concurrent=1, rand=None, clock=None,
                   on_attempt_error=None):
    api = RuntimeApiClient("https://x.example", TOKEN, transport=server.transport())
    from mesh_runtime.timeutil import FakeClock

    return ClaimScheduler(
        api, RUNTIME_ID, max_concurrent=max_concurrent,
        clock=clock or FakeClock(), on_claimed=on_claimed, rand=rand,
        on_attempt_error=on_attempt_error,
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


class TestAttemptTaskLifecycle:
    """HIGH-1: spawned attempt tasks must be strongly referenced (so the event
    loop cannot GC them mid-await and wedge ``inflight``) and any exception from
    ``on_claimed`` must be surfaced to diagnostics — never silently swallowed as
    an unretrieved task exception that drops the claim."""

    async def test_spawned_task_is_strongly_referenced_until_done(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 200, claim_body("a1"))
        release = asyncio.Event()

        async def on_claimed(claim):
            await release.wait()

        sched = make_scheduler(fake_server, on_claimed=on_claimed)
        await sched.step()
        await drain()
        assert len(sched.tasks) == 1  # held by a strong reference, not GC-able
        release.set()
        await drain()
        assert sched.tasks == set()  # released on completion

    async def test_on_claimed_error_is_reported_and_inflight_released(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 200, claim_body("a1"))
        reported = []

        async def on_claimed(claim):
            raise RuntimeError("boom")

        sched = make_scheduler(
            fake_server, on_claimed=on_claimed,
            on_attempt_error=lambda claim, exc: reported.append((claim.attempt_id, exc)),
        )
        outcome, _ = await sched.step()
        assert outcome == "claimed"
        assert sched.inflight == 1
        await drain()
        # The failure is surfaced to the diagnostics hook, not lost as an
        # unretrieved task exception, and the slot is released so claiming
        # continues.
        assert sched.inflight == 0
        assert len(reported) == 1
        attempt_id, exc = reported[0]
        assert attempt_id == "a1"
        assert isinstance(exc, RuntimeError)
        assert sched.tasks == set()

    async def test_on_claimed_error_without_hook_is_logged_not_swallowed(self, fake_server, caplog):
        fake_server.enqueue(CLAIM_KEY, 200, claim_body("a1"))

        async def on_claimed(claim):
            raise KeyError("attempt")  # e.g. malformed claim payload

        sched = make_scheduler(fake_server, on_claimed=on_claimed)
        await sched.step()
        import logging

        with caplog.at_level(logging.ERROR, logger="mesh_runtime.scheduler"):
            await drain()
        assert sched.inflight == 0  # slot still released
        assert any("a1" in record.message for record in caplog.records)

    async def test_scheduler_keeps_claiming_after_attempt_error(self, fake_server):
        fake_server.enqueue(CLAIM_KEY, 200, claim_body("a1"))
        fake_server.enqueue(CLAIM_KEY, 200, claim_body("a2"))

        async def on_claimed(claim):
            if claim.attempt_id == "a1":
                raise RuntimeError("boom")

        sched = make_scheduler(fake_server, on_claimed=on_claimed, max_concurrent=1)
        await sched.step()  # a1 -> errors
        await drain()
        assert sched.inflight == 0
        outcome, _ = await sched.step()  # a2 still claimable — not wedged
        assert outcome == "claimed"
        await drain()
        assert sched.inflight == 0


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
