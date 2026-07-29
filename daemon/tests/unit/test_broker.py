"""S-02 ToolBroker — real unix sockets + SO_PEERCRED.

The broker holds the task token OUTSIDE the sandbox and executes Mesh API
calls on the task's behalf. Every call is gated: peer uid (SO_PEERCRED) +
cgroup membership + attempt nonce, then the §3.3 action→gate table, then the
task-token scope. ``confirm_required`` actions never suspend the sandbox —
they answer CONFIRM_REQUIRED and the supervisor runs the approvals protocol.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest

from mesh_runtime.broker import (
    GATE_TABLE,
    ActionBroker,
    ToolBrokerServer,
    gate_for,
)

ATTEMPT_ID = str(uuid.uuid4())
ISSUE_ID = str(uuid.uuid4())
NONCE = uuid.uuid4().hex


class StubMeshTransport(httpx.AsyncBaseTransport):
    """Task-token-authenticated Mesh API stand-in: records calls, enforces a
    Bearer mesh_task_ header, answers issue reads/comments."""

    def __init__(self):
        self.calls = []
        self.fail_next = None

    async def handle_async_request(self, request):
        self.calls.append(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer mesh_task_"):
            return httpx.Response(401, json={"error": {"code": "unauthorized"}})
        if self.fail_next:
            code = self.fail_next
            self.fail_next = None
            return httpx.Response(403, json={"error": {"code": code}})
        if request.url.path.endswith(f"/issues/{ISSUE_ID}"):
            return httpx.Response(200, json={"data": {"id": ISSUE_ID, "title": "demo"}})
        if request.url.path.endswith(f"/issues/{ISSUE_ID}/comments") and request.method == "POST":
            return httpx.Response(201, json={"data": {"id": "c1"}})
        return httpx.Response(404, json={"error": {"code": "not_found"}})


@pytest.fixture
async def broker(tmp_path):
    transport = StubMeshTransport()
    server = ToolBrokerServer(
        attempt_id=ATTEMPT_ID,
        socket_dir=tmp_path / "run",
        sandbox_uid=os.getuid(),  # tests run the client as ourselves
        cgroup_marker="",  # empty marker disables the cgroup check (unit scope)
        nonce=NONCE,
        task_token="mesh_task_initial",
        server_base_url="https://mesh.example.com",
        issue_id=ISSUE_ID,
        grants={"issue.read": "read_only", "issue.comment": "write"},
        transport=transport,
        rate_burst=5,
    )
    await server.start()
    yield server, transport
    await server.stop()


async def connect(server: ToolBrokerServer, *, nonce: str | None = NONCE):
    reader, writer = await asyncio.open_unix_connection(server.socket_path)
    if nonce is not None:
        writer.write((json.dumps({"nonce": nonce}) + "\n").encode())
        await writer.drain()
        hello = json.loads(await reader.readline())
        assert hello.get("ok") is True, hello
    return reader, writer


async def call(reader, writer, method, params=None, call_id=1):
    writer.write((json.dumps({"id": call_id, "method": method, "params": params or {}}) + "\n").encode())
    await writer.drain()
    return json.loads(await reader.readline())


class TestGateTable:
    def test_action_gate_mapping_is_unique_and_complete(self):
        # §3.3: every action maps to exactly one gate; unknown actions refuse.
        assert gate_for("issue.read").via == "broker"
        assert gate_for("issue.comment").via == "broker"
        assert gate_for("git.push").permission == "confirm_required"
        assert gate_for("cross_issue.write").permission == "confirm_required"
        assert gate_for("mount").permission == "forbidden"
        assert gate_for("daemon_control").permission == "forbidden"
        assert gate_for("totally_unknown_action").permission == "forbidden"  # fail-closed
        # mount-scope actions never traverse the socket.
        assert gate_for("worktree.read").via == "mount"
        assert gate_for("worktree.write").via == "mount"

    def test_forbidden_actions_are_never_grantable(self):
        for _action, gate in GATE_TABLE.items():
            if gate.permission == "forbidden":
                assert gate.via == "kernel"  # kernel policy, not approval


class TestPeerAndNonce:
    async def test_wrong_nonce_refused(self, broker):
        server, _ = broker
        with pytest.raises(AssertionError):
            await connect(server, nonce="wrong-nonce")

    async def test_missing_handshake_refused(self, broker):
        server, _ = broker
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        resp = await call(reader, writer, "issue.read")
        assert resp["ok"] is False
        assert resp["error"]["code"] == "handshake_required"
        writer.close()

    async def test_socket_permissions(self, broker):
        server, _ = broker
        import stat

        mode = stat.S_IMODE(os.stat(server.socket_path).st_mode)
        assert mode == 0o600
        parent = Path(server.socket_path).parent
        parent_mode = stat.S_IMODE(parent.stat().st_mode)
        assert parent_mode & 0o077 == 0  # parent 0700-ish: no group/other


class TestGatedMethods:
    async def test_issue_read_allowed_with_scope(self, broker):
        server, transport = broker
        reader, writer = await connect(server)
        resp = await call(reader, writer, "issue.read", {"issue_id": ISSUE_ID})
        assert resp["ok"] is True
        assert resp["result"]["data"]["title"] == "demo"
        # Task token rode the wire, scoped to the attempt (never the sandbox).
        assert transport.calls[0].headers["authorization"] == "Bearer mesh_task_initial"
        writer.close()

    async def test_cross_issue_read_refused_by_resource_scope(self, broker):
        server, _ = broker
        reader, writer = await connect(server)
        other = str(uuid.uuid4())
        resp = await call(reader, writer, "issue.read", {"issue_id": other})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "resource_scope_mismatch"
        writer.close()

    async def test_ungranted_action_refused(self, broker):
        server, _ = broker
        reader, writer = await connect(server)
        resp = await call(reader, writer, "issue.status", {"issue_id": ISSUE_ID, "status": "done"})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "capability_not_granted"
        writer.close()

    async def test_unknown_method_fail_closed(self, broker):
        server, _ = broker
        reader, writer = await connect(server)
        resp = await call(reader, writer, "rm_rf_everything", {})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "unknown_action"
        writer.close()

    async def test_forbidden_action_never_executes(self, broker):
        server, transport = broker
        reader, writer = await connect(server)
        resp = await call(reader, writer, "daemon_control", {})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "permanently_forbidden"
        assert transport.calls == []  # nothing reached the Mesh API
        writer.close()

    async def test_confirm_required_returns_signal_not_suspension(self, broker):
        # §3.3: confirm_required answers immediately; the sandbox NEVER parks
        # waiting for a human. The supervisor owns the approvals protocol.
        server, transport = broker
        reader, writer = await connect(server)
        resp = await call(reader, writer, "git.push", {"repo": "x"})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "confirm_required"
        assert resp["error"]["details"]["action"] == "git.push"
        assert transport.calls == []  # no privileged call was made
        # The socket stays live: the sandbox is not suspended.
        resp2 = await call(reader, writer, "issue.read", {"issue_id": ISSUE_ID}, call_id=2)
        assert resp2["ok"] is True
        writer.close()

    async def test_issue_comment_write_allowed(self, broker):
        server, transport = broker
        reader, writer = await connect(server)
        resp = await call(reader, writer, "issue.comment", {"issue_id": ISSUE_ID, "body": "update"})
        assert resp["ok"] is True
        sent = transport.calls[-1]
        assert sent.method == "POST"
        writer.close()


class TestTokenLifecycle:
    async def test_rotate_task_token_swaps_bearer(self, broker):
        server, transport = broker
        reader, writer = await connect(server)
        await call(reader, writer, "issue.read", {"issue_id": ISSUE_ID})
        await server.rotate_task_token("mesh_task_rotated")
        await call(reader, writer, "issue.read", {"issue_id": ISSUE_ID}, call_id=2)
        assert transport.calls[0].headers["authorization"] == "Bearer mesh_task_initial"
        assert transport.calls[1].headers["authorization"] == "Bearer mesh_task_rotated"
        writer.close()

    async def test_freeze_refuses_everything(self, broker):
        server, _ = broker
        reader, writer = await connect(server)
        await server.freeze()
        resp = await call(reader, writer, "issue.read", {"issue_id": ISSUE_ID})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "broker_frozen"
        # New connections after freeze are refused outright.
        reader2, writer2 = await asyncio.open_unix_connection(server.socket_path)
        writer2.write((json.dumps({"nonce": NONCE}) + "\n").encode())
        await writer2.drain()
        hello = json.loads(await reader2.readline())
        assert hello["ok"] is False
        writer.close()
        writer2.close()


class TestRateLimit:
    async def test_burst_limit_then_allowed(self, broker):
        server, _ = broker
        reader, writer = await connect(server)
        limited = 0
        for i in range(8):  # burst=5 → some get rate_limited
            resp = await call(reader, writer, "issue.read", {"issue_id": ISSUE_ID}, call_id=i)
            if not resp["ok"] and resp["error"]["code"] == "rate_limited":
                limited += 1
        assert limited >= 1
        writer.close()


class TestActionBroker:
    async def test_one_shot_grant_used_exactly_once(self):
        executed = []

        async def executor(grant):
            executed.append(grant)
            return {"pushed": True}

        ab = ActionBroker()
        grant = ab.issue_grant(
            action="git.push",
            fields={
                "repo": "https://git.example.com/team/app.git",
                "base_ref": "main",
                "target_ref": "agent/x",
                "commit_sha": "c0ffee",
                "diff_digest": "sha256:aa",
            },
        )
        result = await ab.execute(grant["grant_id"], executor)
        assert result == {"pushed": True}
        with pytest.raises(ValueError, match="expired"):
            await ab.execute(grant["grant_id"], executor)  # one-shot
        assert len(executed) == 1

    async def test_grant_fields_are_exact_match(self):
        ab = ActionBroker()
        grant = ab.issue_grant(
            action="git.push",
            fields={"repo": "r", "base_ref": "main", "target_ref": "t",
                    "commit_sha": "c", "diff_digest": "d"},
        )
        async def executor(g):
            return {}
        with pytest.raises(ValueError, match="mismatch"):
            await ab.execute(grant["grant_id"], executor, override={"repo": "other"})
