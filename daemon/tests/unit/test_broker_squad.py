"""S-02 ToolBroker — squad actions (§2.2 S-05 current-squad-task ops).

Real unix sockets, stubbed Mesh transport. Verifies the two §3.3 squad
actions: ``squad.members`` (read) and ``squad.subtasks`` (write, idempotent)
— gating, grant enforcement, payload validation, idempotent replay.
"""

import asyncio
import json
import os
import uuid

import httpx
import pytest

from mesh_runtime.broker import GATE_TABLE, ToolBrokerServer, gate_for

ATTEMPT_ID = str(uuid.uuid4())
ISSUE_ID = str(uuid.uuid4())
NONCE = uuid.uuid4().hex


class StubMeshTransport(httpx.AsyncBaseTransport):
    """Task-token-authenticated stand-in for the task-principal routes."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.next_status = 200
        self.next_body: dict = {"data": {}}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer mesh_task_"):
            return httpx.Response(401, json={"error": {"code": "unauthorized"}})
        if request.url.path == "/api/v1/task/squad/members" and request.method == "GET":
            return httpx.Response(
                200, json={"data": {"squad_id": "s1", "members": [{"member_id": "m1"}]}}
            )
        if request.url.path == "/api/v1/task/squad/subtasks" and request.method == "POST":
            return httpx.Response(self.next_status, json=self.next_body)
        return httpx.Response(404, json={"error": {"code": "not_found"}})


async def _make_broker(tmp_path, grants: dict) -> tuple[ToolBrokerServer, StubMeshTransport]:
    transport = StubMeshTransport()
    server = ToolBrokerServer(
        attempt_id=ATTEMPT_ID,
        socket_dir=tmp_path / "run",
        sandbox_uid=os.getuid(),
        cgroup_marker="",
        nonce=NONCE,
        task_token="mesh_task_initial",
        server_base_url="https://mesh.example.com",
        issue_id=ISSUE_ID,
        grants=grants,
        transport=transport,
        rate_burst=20,
    )
    await server.start()
    return server, transport


async def _connect(server: ToolBrokerServer):
    reader, writer = await asyncio.open_unix_connection(server.socket_path)
    writer.write((json.dumps({"nonce": NONCE}) + "\n").encode())
    await writer.drain()
    hello = json.loads(await reader.readline())
    assert hello.get("ok") is True, hello
    return reader, writer


async def _call(reader, writer, method, params=None, call_id=1) -> dict:
    writer.write(
        (json.dumps({"id": call_id, "method": method, "params": params or {}}) + "\n").encode()
    )
    await writer.drain()
    return json.loads(await reader.readline())


class TestSquadGateTable:
    def test_squad_actions_map_to_broker_gates_with_scopes(self):
        assert GATE_TABLE["squad.members"].permission == "read_only"
        assert GATE_TABLE["squad.members"].scope == "squad:task:read"
        assert GATE_TABLE["squad.subtasks"].permission == "write"
        assert GATE_TABLE["squad.subtasks"].scope == "squad:task:decompose"
        assert gate_for("squad.members").via == "broker"
        assert gate_for("squad.subtasks").via == "broker"


class TestSquadMembers:
    async def test_members_read_with_grant(self, tmp_path):
        server, transport = await _make_broker(tmp_path, {"squad.members": "read_only"})
        try:
            reader, writer = await _connect(server)
            resp = await _call(reader, writer, "squad.members")
            assert resp["ok"] is True, resp
            assert resp["result"]["data"]["members"] == [{"member_id": "m1"}]
            assert transport.calls[0].url.path == "/api/v1/task/squad/members"
            writer.close()
        finally:
            await server.stop()

    async def test_members_refused_without_grant(self, tmp_path):
        server, transport = await _make_broker(tmp_path, {})
        try:
            reader, writer = await _connect(server)
            resp = await _call(reader, writer, "squad.members")
            assert resp["ok"] is False
            assert resp["error"]["code"] == "capability_not_granted"
            assert transport.calls == []  # never reached Mesh
            writer.close()
        finally:
            await server.stop()


class TestSquadSubtasks:
    async def test_subtasks_write_with_grant(self, tmp_path):
        server, transport = await _make_broker(tmp_path, {"squad.subtasks": "write"})
        transport.next_status = 201
        transport.next_body = {"data": {"created_subtasks": [{"id": "t1"}]}}
        try:
            reader, writer = await _connect(server)
            resp = await _call(
                reader,
                writer,
                "squad.subtasks",
                {
                    "subtasks": [{"title": "part A", "assignee_member_id": "m1"}],
                    "plan_markdown": "split",
                    "idempotency_key": "key-1",
                },
            )
            assert resp["ok"] is True, resp
            assert resp["result"]["data"]["created_subtasks"] == [{"id": "t1"}]
            sent = json.loads(transport.calls[0].content.decode())
            assert sent["subtasks"][0]["title"] == "part A"
            assert sent["plan_markdown"] == "split"
            writer.close()
        finally:
            await server.stop()

    async def test_subtasks_refused_with_read_only_grant(self, tmp_path):
        server, transport = await _make_broker(tmp_path, {"squad.subtasks": "read_only"})
        try:
            reader, writer = await _connect(server)
            resp = await _call(
                reader,
                writer,
                "squad.subtasks",
                {"subtasks": [{"title": "x"}], "idempotency_key": "k"},
            )
            assert resp["ok"] is False
            assert resp["error"]["code"] == "capability_not_granted"
            assert transport.calls == []
            writer.close()
        finally:
            await server.stop()

    async def test_subtasks_require_idempotency_key(self, tmp_path):
        server, transport = await _make_broker(tmp_path, {"squad.subtasks": "write"})
        try:
            reader, writer = await _connect(server)
            resp = await _call(reader, writer, "squad.subtasks", {"subtasks": [{"title": "x"}]})
            assert resp["ok"] is False
            assert resp["error"]["code"] == "invalid_params"
            assert transport.calls == []
            writer.close()
        finally:
            await server.stop()

    @pytest.mark.parametrize(
        "params",
        [
            {"subtasks": [], "idempotency_key": "k"},
            {"subtasks": "nope", "idempotency_key": "k"},
            {"subtasks": [{"title": ""}], "idempotency_key": "k"},
            {"subtasks": [{"no_title": 1}], "idempotency_key": "k"},
            {"idempotency_key": "k"},
        ],
    )
    async def test_subtasks_invalid_payload_refused(self, tmp_path, params):
        server, transport = await _make_broker(tmp_path, {"squad.subtasks": "write"})
        try:
            reader, writer = await _connect(server)
            resp = await _call(reader, writer, "squad.subtasks", params)
            assert resp["ok"] is False
            assert resp["error"]["code"] == "invalid_params"
            assert transport.calls == []
            writer.close()
        finally:
            await server.stop()

    async def test_subtasks_idempotent_replay(self, tmp_path):
        server, transport = await _make_broker(tmp_path, {"squad.subtasks": "write"})
        transport.next_status = 201
        transport.next_body = {"data": {"created_subtasks": [{"id": "t9"}]}}
        try:
            reader, writer = await _connect(server)
            payload = {"subtasks": [{"title": "once"}], "idempotency_key": "dup"}
            first = await _call(reader, writer, "squad.subtasks", payload, call_id=1)
            second = await _call(reader, writer, "squad.subtasks", payload, call_id=2)
            assert first["ok"] is True and second["ok"] is True
            assert first["result"] == second["result"]
            assert len(transport.calls) == 1  # replayed from cache
            writer.close()
        finally:
            await server.stop()

    async def test_subtasks_upstream_error_surfaced(self, tmp_path):
        server, transport = await _make_broker(tmp_path, {"squad.subtasks": "write"})
        transport.next_status = 422
        transport.next_body = {"error": {"code": "assignee_not_member", "message": "x"}}
        try:
            reader, writer = await _connect(server)
            resp = await _call(
                reader,
                writer,
                "squad.subtasks",
                {"subtasks": [{"title": "x"}], "idempotency_key": "k"},
            )
            assert resp["ok"] is False
            assert resp["error"]["code"] == "assignee_not_member"
            writer.close()
        finally:
            await server.stop()

    async def test_subtasks_plan_markdown_capped_and_optional(self, tmp_path):
        server, transport = await _make_broker(tmp_path, {"squad.subtasks": "write"})
        transport.next_status = 201
        try:
            reader, writer = await _connect(server)
            resp = await _call(
                reader,
                writer,
                "squad.subtasks",
                {
                    "subtasks": [{"title": "t"}],
                    "plan_markdown": "p" * 20000,
                    "idempotency_key": "k",
                },
            )
            assert resp["ok"] is True
            sent = json.loads(transport.calls[0].content.decode())
            assert len(sent["plan_markdown"]) == 8000
            writer.close()
        finally:
            await server.stop()
