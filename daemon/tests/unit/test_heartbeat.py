import asyncio

import pytest

from mesh_runtime.api import HeartbeatResponse, RuntimeApiClient
from mesh_runtime.backoff import KEEPALIVE
from mesh_runtime.errors import ServerError
from mesh_runtime.heartbeat import HeartbeatLoop
from mesh_runtime.inventory import Inventory
from mesh_runtime.providers.base import ProbeResult
from mesh_runtime.providers.fake import FakeProvider
from mesh_runtime.timeutil import FakeClock
from tests.conftest import make_rand

TOKEN = "mesh_rt_test"
RUNTIME_ID = "11111111-1111-1111-1111-111111111111"
HB_KEY = f"POST /api/v1/daemon/runtimes/{RUNTIME_ID}:heartbeat"


def make_heartbeat(
    server, *, on_cancel=None, inventory=None, operational_guard=None,
    on_operational_incident=None, interval=15.0, rand=None, inflight=None,
):
    api = RuntimeApiClient("https://x.example", TOKEN, transport=server.transport())
    return HeartbeatLoop(
        api, RUNTIME_ID, interval_seconds=interval, clock=FakeClock(),
        inventory=inventory, on_cancel=on_cancel,
        operational_guard=operational_guard,
        on_operational_incident=on_operational_incident,
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

    async def test_cancel_callback_failure_keeps_beat_ok(self, fake_server):
        """TD-D MEDIUM: the beat was accepted server-side, so a raising cancel
        handler must not fail the beat — and MUST NOT escape ``beat_once``
        (an escape kills the whole daemon via the loop's task group)."""
        fake_server.enqueue(
            HB_KEY, 200,
            {"data": {"commands": [
                {"type": "cancel_execution", "attempt_id": "a1", "grace_seconds": 5},
            ]}},
        )

        async def on_cancel(attempt_id, grace):
            raise RuntimeError("cancel handler exploded")

        hb = make_heartbeat(fake_server, on_cancel=on_cancel)
        outcome, delay = await hb.beat_once()  # must not raise
        assert outcome == "ok"
        assert delay > 0
        assert hb.beats == 1
        assert hb.consecutive_failures == 0

    async def test_malformed_cancel_payload_keeps_beat_ok(self, fake_server):
        """A corrupt ``grace_seconds`` raises inside ``cancel_commands()`` —
        the same never-raise contract applies (commands redeliver next beat)."""
        fake_server.enqueue(
            HB_KEY, 200,
            {"data": {"commands": [
                {"type": "cancel_execution", "attempt_id": "a1", "grace_seconds": "not-a-number"},
            ]}},
        )
        hb = make_heartbeat(fake_server)
        outcome, delay = await hb.beat_once()  # must not raise
        assert outcome == "ok"
        assert delay > 0
        assert hb.consecutive_failures == 0

    async def test_fatal_on_401(self, fake_server):
        fake_server.enqueue(HB_KEY, 401, {"error": {"code": "invalid_token"}})
        incidents = []
        hb = make_heartbeat(fake_server, on_operational_incident=incidents.append)
        outcome, delay = await hb.beat_once()
        assert outcome == "fatal"
        assert hb.fatal is not None
        assert hb.beats == 0
        assert incidents == ["runtime_auth_failed"]

    async def test_rate_limited_uses_retry_after(self, fake_server):
        fake_server.enqueue(HB_KEY, 429, None, headers={"Retry-After": "30"})
        hb = make_heartbeat(fake_server)
        outcome, delay = await hb.beat_once()
        assert outcome == "rate_limited"
        assert delay == 30.0

    async def test_rate_limited_retry_after_capped_at_minute(self, fake_server):
        fake_server.enqueue(HB_KEY, 429, None, headers={"Retry-After": "7200"})
        hb = make_heartbeat(fake_server)
        outcome, delay = await hb.beat_once()
        assert outcome == "rate_limited"
        assert delay == 60.0

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
                available=False, name="claude-code", version=None, binary_sha256=None,
                capabilities=("python", "version_control"),
                reason="binary missing at /srv/private/provider"))
        ])
        hb = make_heartbeat(fake_server, inventory=inv)
        await hb.beat_once()
        call = fake_server.calls_for(HB_KEY)[0]
        assert call.body["health"] == "degraded"
        assert call.body["operational_state"] == "degraded"
        assert call.body["diagnostics"] == [
            {
                "reason_code": "provider_unavailable",
                "missing_capabilities": ["python", "version_control"],
                "affected_task_types": ["provider:claude-code"],
            }
        ]
        assert "/srv/private/provider" not in str(call.body)

    async def test_inflight_reported_in_body(self, fake_server):
        fake_server.enqueue(HB_KEY, 200, {"data": {}})
        hb = make_heartbeat(fake_server, inflight=lambda: ["a1", "a2"])
        await hb.beat_once()
        call = fake_server.calls_for(HB_KEY)[0]
        assert call.body["inflight"] == ["a1", "a2"]
        assert call.body["current_load"] == 2

    async def test_isolated_guard_overrides_inventory_report(self, fake_server, tmp_path):
        from mesh_runtime.operational import OperationalGuard

        fake_server.enqueue(HB_KEY, 200, {"data": {}})
        inventory = await Inventory.probe([FakeProvider(events=[])])
        guard = OperationalGuard(tmp_path / "operational-state.json", inventory)
        guard.isolate("cleanup_failed")
        hb = make_heartbeat(
            fake_server,
            inventory=inventory,
            operational_guard=guard,
        )

        await hb.beat_once()

        call = fake_server.calls_for(HB_KEY)[0]
        assert call.body["health"] == "degraded"
        assert call.body["operational_state"] == "isolated"
        assert call.body["diagnostics"][0]["reason_code"] == "cleanup_failed"

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


class StubHealApi:
    """Scriptable heartbeat endpoint for TD-D self-heal tests."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.resets = 0
        self.bodies = []

    async def reset_transport(self):
        self.resets += 1

    async def heartbeat(self, runtime_id, **kwargs):
        self.bodies.append(kwargs)
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
        return HeartbeatResponse(server_time=None, commands=[])


def make_heal_loop(api, *, reset=5, exit_=10, on_self_heal=None, rand=None):
    return HeartbeatLoop(
        api,
        RUNTIME_ID,
        interval_seconds=15.0,
        clock=FakeClock(),
        rand=rand or (lambda: 0.0),
        self_heal_reset_threshold=reset,
        self_heal_exit_threshold=exit_,
        on_self_heal=on_self_heal,
    )


class TestTdSelfHeal:
    """TD-D: the heartbeat loop must never die silently, must count failures,
    and must escalate (transport reset → whole-process exit) deterministically."""

    async def test_unexpected_exception_never_kills_loop(self):
        api = StubHealApi([RuntimeError("boom")])
        hb = make_heal_loop(api)
        outcome, delay = await hb.beat_once()
        assert outcome == "client_error"
        assert hb.consecutive_failures == 1
        assert hb.fatal is None

    async def test_protocol_error_rejection_survives(self, fake_server):
        # A 400 the daemon did not enumerate is fail-closed ProtocolError in
        # the API layer — the loop must absorb it and keep beating.
        fake_server.enqueue(HB_KEY, 400, {"error": {"code": "bad_request"}})
        hb = make_heartbeat(fake_server)
        outcome, delay = await hb.beat_once()
        assert outcome == "client_error"
        assert hb.consecutive_failures == 1
        assert hb.fatal is None

    async def test_transport_reset_escalation_in_run(self):
        api = StubHealApi([ServerError("x")] * 5)
        events = []
        hb = make_heal_loop(api, reset=5, exit_=10, on_self_heal=events.append)

        def stop_after_reset(reason):
            events.append(reason)
            if reason == "heartbeat_transport_reset":
                hb.request_stop()

        hb._on_self_heal = stop_after_reset
        await hb.run(asyncio.Event())

        assert events == ["heartbeat_transport_reset"]
        assert api.resets == 1  # run() rebuilt the connection pool
        assert hb.consecutive_failures == 5

    async def test_process_exit_escalation_fires_once(self):
        api = StubHealApi([ServerError("x")] * 12)
        events = []
        hb = make_heal_loop(api, reset=2, exit_=4, on_self_heal=events.append)

        def stop_on_exit(reason):
            events.append(reason)
            if reason == "heartbeat_process_exit":
                hb.request_stop()

        hb._on_self_heal = stop_on_exit
        await hb.run(asyncio.Event())
        # Further failures keep counting but never re-signal the exit.
        for _ in range(3):
            await hb.beat_once()

        assert events == ["heartbeat_transport_reset", "heartbeat_process_exit"]
        assert hb.consecutive_failures == 7

    async def test_success_resets_failure_count(self):
        api = StubHealApi([ServerError("x"), ServerError("x")])
        hb = make_heal_loop(api)
        await hb.beat_once()
        await hb.beat_once()
        assert hb.consecutive_failures == 2
        outcome, _ = await hb.beat_once()  # stub exhausted → success
        assert outcome == "ok"
        assert hb.consecutive_failures == 0

    async def test_failure_count_degrades_reported_health(self):
        api = StubHealApi([ServerError("x")])
        hb = make_heal_loop(api)
        await hb.beat_once()  # failure
        await hb.beat_once()  # next beat reports while failures > 0
        assert api.bodies[-1]["health"] == "degraded"
        await hb.beat_once()  # success resets
        await hb.beat_once()
        assert api.bodies[-1]["health"] == "healthy"

    async def test_rejects_misconfigured_thresholds(self):
        api = StubHealApi([])
        with pytest.raises(ValueError):
            make_heal_loop(api, reset=0, exit_=1)
        with pytest.raises(ValueError):
            make_heal_loop(api, reset=5, exit_=4)
