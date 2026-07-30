"""Dispatcher edge-branch coverage: error containment, stale-candidate skips,
repair-interval loop path, timezone helper."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from mesh.integrations import dispatcher as disp
from mesh.integrations.dispatcher import (
    _seconds_since,
    run_dispatcher_pass,
    run_lease_repair_pass,
)
from tests.unit.test_integration_dispatcher import (
    _item,
    _seed_execution,
    _seed_item,
    _seed_world,
    _settings,
)

pytestmark = pytest.mark.unit


class TestPassErrorContainment:
    async def test_integrity_contention_backs_off(self, session_factory, monkeypatch):
        world = await _seed_world(session_factory)
        await _seed_item(session_factory, world, seq=1)

        async def _boom(*a, **k):
            raise IntegrityError("stmt", {}, Exception("unique"))

        monkeypatch.setattr(disp, "_dispatch_conversation_head", _boom)
        # must not raise; contention is a backoff, not a failure
        assert await run_dispatcher_pass(session_factory, settings=_settings()) == 0

    async def test_unexpected_error_does_not_block_other_conversations(
        self, session_factory, monkeypatch
    ):
        world = await _seed_world(session_factory)
        await _seed_item(session_factory, world, seq=1)

        async def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(disp, "_dispatch_conversation_head", _boom)
        assert await run_dispatcher_pass(session_factory, settings=_settings()) == 0


class TestRepairStaleCandidates:
    async def test_stale_candidates_skipped(self, session_factory, monkeypatch):
        """Re-validation under the row lock: terminal / lease-refreshed /
        deleted candidates from the scan are all skipped without error."""
        world = await _seed_world(session_factory)
        # each in its OWN conversation — the serial exclusive index allows
        # only one in-flight item per conversation (by design)
        terminal_id, _ = await _seed_item(
            session_factory, world, seq=1, state="processing", lease_expired=True,
            conv_key="dingtalk:dingsample:cidSTALE1==",
        )
        refreshed_id, _ = await _seed_item(
            session_factory, world, seq=1, state="processing", lease_expired=True,
            conv_key="dingtalk:dingsample:cidSTALE2==",
        )
        deleted_id, _ = await _seed_item(
            session_factory, world, seq=1, state="processing", lease_expired=True,
            conv_key="dingtalk:dingsample:cidSTALE3==",
        )
        ws = world["ws"]

        from sqlalchemy import text

        async with session_factory() as session, session.begin():
            # flip states AFTER the scan would have listed them
            await session.execute(
                text("UPDATE integration_message_queue SET state = 'done' WHERE id = :id"),
                {"id": terminal_id},
            )
            await session.execute(
                text(
                    "UPDATE integration_message_queue SET lease_expires_at = now() + interval '1 hour' "
                    "WHERE id = :id"
                ),
                {"id": refreshed_id},
            )
            await session.execute(
                text("DELETE FROM integration_message_queue WHERE id = :id"),
                {"id": deleted_id},
            )

        async def _stale_ids(sf):
            return [(ws, terminal_id), (ws, refreshed_id), (ws, deleted_id)]

        monkeypatch.setattr(disp, "_expired_item_ids", _stale_ids)
        assert await run_lease_repair_pass(session_factory, settings=_settings()) == 0


class TestRepairLoopInDispatcherLoop:
    async def test_loop_runs_repair_when_interval_elapsed(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="completed")
        item_id, _ = await _seed_item(
            session_factory, world, seq=1, state="processing",
            execution_id=exec_id, lease_expired=True,
        )
        settings = _settings(im_dispatch_tick_seconds=0.05, im_lease_repair_interval_seconds=0.01)
        wake = asyncio.Event()
        stop = asyncio.Event()

        async def _stop_after_repair():
            for _ in range(100):
                from tests.unit.test_integration_dispatcher import _item as _load

                item = await _load(session_factory, item_id)
                if item.state == "done":
                    stop.set()
                    wake.set()
                    return
                await asyncio.sleep(0.05)
            stop.set()
            wake.set()

        await asyncio.wait_for(
            asyncio.gather(
                disp.dispatcher_loop(session_factory, settings=settings, wake=wake, stop=stop),
                _stop_after_repair(),
            ),
            timeout=20,
        )
        item = await _item(session_factory, item_id)
        assert item.state == "done"  # repair ran inside the loop

    async def test_loop_survives_dispatch_crash(self, session_factory, monkeypatch):
        settings = _settings(im_dispatch_tick_seconds=0.05, im_lease_repair_interval_seconds=999)
        wake = asyncio.Event()
        stop = asyncio.Event()
        calls = {"n": 0}
        real = disp.run_dispatcher_pass

        async def _flaky(sf, *, settings):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient crash")
            return await real(sf, settings=settings)

        monkeypatch.setattr(disp, "run_dispatcher_pass", _flaky)

        async def _stop_soon():
            await asyncio.sleep(0.3)
            stop.set()
            wake.set()

        await asyncio.wait_for(
            asyncio.gather(
                disp.dispatcher_loop(session_factory, settings=settings, wake=wake, stop=stop),
                _stop_soon(),
            ),
            timeout=15,
        )
        assert calls["n"] >= 2  # survived the crash and kept looping


class TestHelpers:
    def test_seconds_since_naive_datetime(self):
        naive = datetime.utcnow() - timedelta(seconds=10)
        assert 9 <= _seconds_since(naive) <= 12

    def test_seconds_since_none(self):
        assert _seconds_since(None) == 0.0


class TestMoreBranches:
    async def test_head_taken_by_replica_returns_false(self, session_factory, monkeypatch):
        """Candidate listed, but the FIFO head was taken under the lock by
        another replica → _dispatch_conversation_head returns False."""
        world = await _seed_world(session_factory)
        ws = world["ws"]

        async def _fake_candidates(sf):
            return [(ws, "dingtalk:dingsample:cidGHOST==")]  # nothing pending there

        monkeypatch.setattr(disp, "_candidate_conversations", _fake_candidates)
        assert await run_dispatcher_pass(session_factory, settings=_settings()) == 0

    async def test_repair_item_error_swallowed(self, session_factory, monkeypatch):
        world = await _seed_world(session_factory)
        exec_id = await _seed_execution(session_factory, world, status="completed")
        item_id, _ = await _seed_item(
            session_factory, world, seq=1, state="processing",
            execution_id=exec_id, lease_expired=True,
        )

        async def _boom(session, settings, item):
            raise RuntimeError("repair crash")

        monkeypatch.setattr(disp, "_repair_one", _boom)
        # poison item must not stall or fail the pass
        assert await run_lease_repair_pass(session_factory, settings=_settings()) == 0

    async def test_loop_survives_repair_crash(self, session_factory, monkeypatch):
        settings = _settings(im_dispatch_tick_seconds=0.05, im_lease_repair_interval_seconds=0.01)
        wake = asyncio.Event()
        stop = asyncio.Event()
        calls = {"n": 0}

        async def _flaky_repair(sf, *, settings):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("repair transient crash")
            return 0

        monkeypatch.setattr(disp, "run_lease_repair_pass", _flaky_repair)

        async def _stop_soon():
            await asyncio.sleep(0.4)
            stop.set()
            wake.set()

        await asyncio.wait_for(
            asyncio.gather(
                disp.dispatcher_loop(session_factory, settings=settings, wake=wake, stop=stop),
                _stop_soon(),
            ),
            timeout=15,
        )
        assert calls["n"] >= 2  # crash contained, loop continued
