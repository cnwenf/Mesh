"""MCP stdio bridge — hermetic subprocess tests (no provider, no network).

Runs the platform bridge script (the exact bytes written into the attempt
run dir) as a subprocess against a fake ToolBroker unix socket. Verifies the
MCP JSON-RPC surface: initialize / tools/list / tools/call forwarding, the
nonce handshake, idempotency-key defaulting, and error surfacing.

Synchronous tests on purpose: the bridge client blocks on subprocess stdio,
so the fake broker runs its own asyncio loop in a background thread — a
blocking readline inside a test-loop coroutine would deadlock the server.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

from mesh_runtime.provider_env import MCP_BRIDGE_SOURCE

NONCE = uuid.uuid4().hex


class FakeBroker:
    """Minimal ToolBroker wire stand-in on its own loop thread: nonce
    handshake + newline JSON request/reply."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self.received_nonce: str | None = None
        self.requests: list[dict] = []
        self.reply_override: dict | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: asyncio.AbstractServer | None = None
        self._ready = threading.Event()

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=10), "fake broker failed to start"

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path)
        self._ready.set()
        async with self._server:
            await self._server.serve_forever()

    def stop(self):
        if self._loop is not None and self._server is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        if self._thread is not None:
            self._thread.join(timeout=10)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            first = json.loads(await reader.readline())
            self.received_nonce = first.get("nonce")
            if self.received_nonce != NONCE:
                writer.write(
                    (json.dumps({"ok": False, "error": {"code": "bad_nonce"}}) + "\n").encode()
                )
                await writer.drain()
                return
            writer.write((json.dumps({"ok": True}) + "\n").encode())
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    break
                req = json.loads(line)
                self.requests.append(req)
                if self.reply_override is not None:
                    reply = {**self.reply_override, "id": req.get("id")}
                else:
                    reply = {"id": req.get("id"), "ok": True, "result": {"echo": req["method"]}}
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()


class BridgeClient:
    """Drives the bridge subprocess over stdio (newline JSON-RPC)."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._id = 0

    def send(self, msg: dict):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self._id += 1
        self.send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        line = self._readline(timeout)
        assert line, "bridge closed stdout without a reply"
        return json.loads(line)

    def _readline(self, timeout: float) -> bytes:
        result: list[bytes] = []

        def _read():
            result.append(self.proc.stdout.readline())

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout=timeout)
        assert not reader.is_alive(), "bridge did not reply within timeout"
        return result[0] if result else b""

    def notify(self, method: str, params: dict | None = None):
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self):
        try:
            self.proc.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def bridge_script(tmp_path) -> Path:
    script = tmp_path / "mesh_task_broker_mcp.py"
    script.write_text(MCP_BRIDGE_SOURCE, encoding="utf-8")
    return script


@pytest.fixture
def socket_path(tmp_path) -> str:
    return str(tmp_path / "broker.sock")


@pytest.fixture
def broker(socket_path):
    fake = FakeBroker(socket_path)
    fake.start()
    yield fake
    fake.stop()


def spawn_bridge(script: Path, socket_path: str | None, nonce: str | None = NONCE):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONIOENCODING": "utf-8",
    }
    if socket_path is not None:
        env["MESH_BROKER_SOCKET"] = socket_path
    if nonce is not None:
        env["MESH_BROKER_NONCE"] = nonce
    return subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=False,
    )


def test_initialize_and_tools_list(bridge_script, socket_path, broker):
    client = BridgeClient(spawn_bridge(bridge_script, socket_path))
    try:
        init = client.request("initialize", {"protocolVersion": "2024-11-05"})
        assert init["result"]["serverInfo"]["name"] == "mesh-task-broker"
        assert "protocolVersion" in init["result"]
        client.notify("notifications/initialized")  # must not produce a reply
        listed = client.request("tools/list")
        names = {t["name"] for t in listed["result"]["tools"]}
        assert names == {
            "issue_read", "issue_comment", "issue_status",
            "project_read", "squad_members", "squad_subtasks",
        }
        for tool in listed["result"]["tools"]:
            assert "inputSchema" in tool and tool["description"]
    finally:
        client.close()


def test_tools_call_forwards_with_nonce(bridge_script, socket_path, broker):
    client = BridgeClient(spawn_bridge(bridge_script, socket_path))
    try:
        client.request("initialize")
        resp = client.request("tools/call", {"name": "squad_members", "arguments": {}})
        result = resp["result"]
        assert result["isError"] is False, result
        assert json.loads(result["content"][0]["text"]) == {"echo": "squad.members"}
        assert broker.received_nonce == NONCE
        assert broker.requests[0]["method"] == "squad.members"
    finally:
        client.close()


def test_idempotent_tool_gets_generated_key(bridge_script, socket_path, broker):
    client = BridgeClient(spawn_bridge(bridge_script, socket_path))
    try:
        client.request("initialize")
        args = {"issue_id": "i1", "body": "hello"}
        client.request("tools/call", {"name": "issue_comment", "arguments": args})
        client.request("tools/call", {"name": "issue_comment", "arguments": args})
        keys = [r["params"]["idempotency_key"] for r in broker.requests]
        assert len(keys) == 2
        assert all(isinstance(k, str) and k for k in keys)
        assert keys[0] == keys[1]  # same args → same default key (retry-safe)
    finally:
        client.close()


def test_caller_supplied_idempotency_key_preserved(bridge_script, socket_path, broker):
    client = BridgeClient(spawn_bridge(bridge_script, socket_path))
    try:
        client.request("initialize")
        client.request(
            "tools/call",
            {
                "name": "squad_subtasks",
                "arguments": {
                    "subtasks": [{"title": "a"}],
                    "idempotency_key": "leader-key-1",
                },
            },
        )
        assert broker.requests[0]["params"]["idempotency_key"] == "leader-key-1"
    finally:
        client.close()


def test_broker_error_surfaced_as_tool_error(bridge_script, socket_path, broker):
    broker.reply_override = {"ok": False, "error": {"code": "capability_not_granted"}}
    client = BridgeClient(spawn_bridge(bridge_script, socket_path))
    try:
        client.request("initialize")
        resp = client.request("tools/call", {"name": "squad_members", "arguments": {}})
        assert resp["result"]["isError"] is True
        assert "capability_not_granted" in resp["result"]["content"][0]["text"]
    finally:
        client.close()


def test_unknown_tool_is_error(bridge_script, socket_path, broker):
    client = BridgeClient(spawn_bridge(bridge_script, socket_path))
    try:
        client.request("initialize")
        resp = client.request("tools/call", {"name": "rm_rf", "arguments": {}})
        assert resp["result"]["isError"] is True
        assert "unknown tool" in resp["result"]["content"][0]["text"]
        assert broker.requests == []  # nothing reached the broker
    finally:
        client.close()


def test_missing_socket_env_is_tool_error(bridge_script):
    client = BridgeClient(spawn_bridge(bridge_script, socket_path=None))
    try:
        client.request("initialize")
        resp = client.request("tools/call", {"name": "squad_members", "arguments": {}})
        assert resp["result"]["isError"] is True
        assert "broker socket not configured" in resp["result"]["content"][0]["text"]
    finally:
        client.close()


def test_unknown_method_gets_jsonrpc_error(bridge_script, socket_path, broker):
    client = BridgeClient(spawn_bridge(bridge_script, socket_path))
    try:
        resp = client.request("resources/list")
        assert resp["error"]["code"] == -32601
    finally:
        client.close()


def test_ping_answered(bridge_script, socket_path, broker):
    client = BridgeClient(spawn_bridge(bridge_script, socket_path))
    try:
        resp = client.request("ping")
        assert resp["result"] == {}
    finally:
        client.close()
