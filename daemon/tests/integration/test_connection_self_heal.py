"""TD-D connection self-healing integration test (runtime-executor.md §3.1).

Real end-to-end over a live TCP socket — NO mocks of the transport. A minimal
asyncio HTTP server plays the Mesh server heartbeat endpoint and can be
switched into a "down" mode where it actively disconnects every connection
(simulating the MES-190 CLOSE-WAIT incident). The real ``RuntimeApiClient``
(real httpx, real connection pooling) + ``HeartbeatLoop`` must:

  1. detect the disconnect / write failure (never hang on a dead socket),
  2. count the consecutive failures and reset the client transport,
  3. automatically reconnect and resume heartbeating once the server returns,

all within N seconds. Run as part of the normal suite (no root, loopback only).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.heartbeat import HeartbeatLoop
from mesh_runtime.timeutil import SystemClock

RUNTIME_ID = "22222222-2222-2222-2222-222222222222"
TOKEN = "mesh_rt_integration"
RECOVERY_DEADLINE_SECONDS = 10.0


class HeartbeatServer:
    """Minimal HTTP/1.1 heartbeat endpoint with a disconnect switch."""

    def __init__(self):
        self.mode = "up"  # "up" answers heartbeats; "down" disconnects
        self.heartbeats_served = 0
        self._writer = None
        self._open_writers: set = set()
        self._server = None
        self.port = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for writer in list(self._open_writers):
            writer.close()

    def set_down(self):
        self.mode = "down"
        # Kill every live keep-alive connection so the client's pooled socket
        # is genuinely dead — the recovery must come from a fresh connection.
        for writer in list(self._open_writers):
            try:
                writer.close()
            except Exception:
                pass
        self._open_writers.clear()

    def set_up(self):
        self.mode = "up"

    async def _handle(self, reader, writer):
        self._open_writers.add(writer)
        try:
            if self.mode == "down":
                # Active server-side disconnect: close without any response.
                return
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            if not request_line:
                return
            content_length = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())
            if content_length:
                await asyncio.wait_for(reader.readexactly(content_length), timeout=5)
            self.heartbeats_served += 1
            body = json.dumps({"data": {"server_time": "t", "commands": []}}).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"\r\n" + body
            )
            await writer.drain()
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._open_writers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass


async def _wait_for(predicate, timeout: float, message: str):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for: {message}")


@pytest.mark.asyncio
async def test_daemon_recovers_heartbeat_after_server_disconnect():
    server = HeartbeatServer()
    await server.start()
    try:
        api = RuntimeApiClient(f"http://127.0.0.1:{server.port}", TOKEN)
        hb = HeartbeatLoop(
            api,
            RUNTIME_ID,
            interval_seconds=0.05,
            clock=SystemClock(),
            rand=lambda: 0.0,  # zero backoff/jitter → fast, deterministic retry
            self_heal_reset_threshold=2,
            self_heal_exit_threshold=100,  # do NOT exit the process in this test
        )
        loop_task = asyncio.create_task(hb.run(asyncio.Event()))

        # Phase 1 — healthy: heartbeats land on the real server.
        await _wait_for(lambda: hb.beats >= 2, 5.0, "initial healthy heartbeats")
        assert server.heartbeats_served >= 2

        # Phase 2 — server actively disconnects: failures are detected and
        # counted, and the transport self-heal (reset) fires. Never hangs.
        beats_before_down = hb.beats
        server.set_down()
        await _wait_for(
            lambda: hb.consecutive_failures >= hb._self_heal_reset_threshold,
            5.0,
            "disconnect detected and counted",
        )

        # Phase 3 — server returns: the loop reconnects on its own and resumes
        # heartbeating within the deadline; the failure counter resets.
        server.set_up()
        await _wait_for(
            lambda: hb.beats > beats_before_down and hb.consecutive_failures == 0,
            RECOVERY_DEADLINE_SECONDS,
            "heartbeat recovered after reconnect",
        )
        assert hb.fatal is None
        assert server.heartbeats_served > beats_before_down

        hb.request_stop()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await api.close()
    finally:
        await server.stop()
