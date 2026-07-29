import asyncio

import pytest

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.backoff import KEEPALIVE
from mesh_runtime.heartbeat import HeartbeatLoop
from mesh_runtime.inventory import Inventory
from mesh_runtime.providers.base import ProbeResult
from mesh_runtime.providers.fake import FakeProvider
from mesh_runtime.timeutil import FakeClock
from tests.conftest import make_rand

TOKEN = "mesh_rt_test"
RUNTIME_ID = "11111111-1111-1111-1111-111111111111"
HB_KEY = f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat"


def make_heartbeat(server, *, on_cancel=None, inventory=None, interval=15.0, rand=None, inflight=None):
    api = RuntimeApiClient("https://x.example", TOKEN, transport=server.transport())
    return HeartbeatLoop(
        api, RUNTIME_ID, interval_seconds=interval, clock=FakeClock(),
        inventory=inventory, on_cancel=on_cancel,
        inflight_source=inflight, rand=rand,
    )


class TestBeatOnce:
    async def test_ok_increments_and_returns_jittered_interval(self, fake_server):
        fake_server.enqueue(HB_KEY, 200, {"data": {"server_time": "t", "commands": []}})
        hb = make_heartbeat(fake_server, interval=15.0, rand=make_rand([1.0]))
        outcome, delay = await hb.beat_once()
        assert outcome == "ok"
        assert delay == pytest.approx(15.0 * 1.10)  # rand=1 -> +10%
        assert hb.beats == 1

    async def test_jitter_bounds_within_10pct(self, fake_server):
        hb = make_heartbeat(fake_server, interval=20.0, rand=make_rand([0.0]))
        assert hb.jittered_interval() == pytest.approx(20.0 * 0.90)  # rand=0 -> -10%

    async def test_dispatches_cancel_commands(self, fake_server):
        fake_server.enqueue(
            HB_KEY, 200,
            {"data": {"commands": [
                {"type": "cancel_execution", "attempt_id": "a1", "grace_seconds": 15},
                {"type": "cancel_execution", "attempt_id": "a2", "grace_seconds": 5},
            ]}},
        )
        calls = []

        async def on_cancel(attempt_id, grace):
            calls.append((attempt_id, grace))

        hb = make_heartbeat(fake_server, on_cancel=on_cancel)
        await hb.beat_once()
        assert calls == [("a1", 15.0), ("a2", 5.0)]

    async def test_ignores_non_cancel_commands(self, fake_server):
        fake_server.enqueue(
            HB_KEY, 200,
            {"data": {"commands": [{"type": "freeze"}, {"type": "cancel_execution", "attempt_id": "a1"}]}},
        )
        calls = []

        async def on_cancel(attempt_id, grace):
            calls.append(attempt_id)

        hb = make_heartbeat(fake_server, on_cancel=on_cancel)
        await hb.beat_once()
        assert calls == ["a1"]

    async def test_fatal_on_401(self, fake_server):
        fake_server.enqueue(HB_KEY, 401, {"error": {"code": "invalid_token"}})
        hb = make_heartbeat(fake_server)
        outcome, delay = await hb.beat_once()
        assert outcome == "fatal"
        assert hb.fatal is not None
        assert hb.beats == 0

    async def test_rate_limited_uses_retry_after(self, fake_server):
        fake_server.enqueue(HB_KEY, 429, None, headers={"Retry-After": "30"})
        hb = make_heartbeat(fake_server)
        outcome, delay = await hb.beat_once()
        assert outcome == "rate_limited"
        assert delay == 30.0

    async def test_server_error_keepalive_backoff_no_increment(self, fake_server):
        fake_server.enqueue(HB_KEY, 500, None)
        hb = make_heartbeat(fake_server, rand=make_rand([1.0]))
        outcome, delay = await hb.beat_once()
        assert outcome == "server_error"
        assert delay == pytest.approx(KEEPALIVE.delay(0, make_rand([1.0])))
        assert hb.beats == 0  # failures do not count as beats

    async def test_degraded_health_reported_when_inventory_unhealthy(self, fake_server):
        fake_server.enqueue(HB_KEY, 200, {"data": {}})
        inv = await Inventory.probe([
            FakeProvider(events=[], probe_result=ProbeResult(
                available=False, name="x", version=None, binary_sha256=None,
                capabilities=(), reason="missing"))
        ])
        hb = make_heartbeat(fake_server, inventory=inv)
        await hb.beat_once()
        call = fake_server.calls_for(HB_KEY)[0]
        assert call.body["health"] == "degraded"

    async def test_inflight_reported_in_body(self, fake_server):
        fake_server.enqueue(HB_KEY, 200, {"data": {}})
        hb = make_heartbeat(fake_server, inflight=lambda: ["a1", "a2"])
        await hb.beat_once()
        call = fake_server.calls_for(HB_KEY)[0]
        assert call.body["inflight"] == ["a1", "a2"]
        assert call.body["current_load"] == 2

    async def test_rejects_nonpositive_interval(self, fake_server):
        with pytest.raises(ValueError):
            make_heartbeat(fake_server, interval=0)


class TestRunLoop:
    async def test_run_stops_on_shutdown_without_beating(self, fake_server):
        hb = make_heartbeat(fake_server)
        shutdown = asyncio.Event()
        shutdown.set()
        await hb.run(shutdown)
        assert hb.beats == 0

    async def test_run_stops_on_fatal(self, fake_server):
        fake_server.enqueue(HB_KEY, 401, {"error": {"code": "invalid_token"}})
        hb = make_heartbeat(fake_server)
        await hb.run(asyncio.Event())
        assert hb.fatal is not None
