"""S-02: the unique ToolBroker gate (runtime-executor.md §3.2~§3.3).

The broker is the ONLY path from the sandbox to Mesh-side effects. It lives
outside the sandbox, holds the short-lived ``mesh_task_`` token (which never
enters sandbox env/files/stdin), and answers newline-delimited JSON over an
attempt-private unix socket.

Every call clears four gates in order (any miss refuses, nothing executes):

1. peer identity — SO_PEERCRED uid equals the sandbox uid, and the peer's
   cgroup contains the attempt marker (empty marker disables the check for
   hermetic unit tests; production always sets it);
2. attempt nonce — first message must carry the daemon-injected nonce;
3. action → gate — the §3.3 table is the UNIQUE mapping; unknown actions are
   forbidden (fail-closed); ``mount``/``privilege``/``daemon_control``/
   ``cloud_metadata`` are permanently forbidden — approval cannot release
   them either;
4. grant + scope — the action's permission must be in the frozen
   capability_grants, and task-token scope pins the resource (issue id).

``confirm_required`` actions answer ``confirm_required`` IMMEDIATELY — the
sandbox is never suspended waiting for a human (§3.3): the supervisor cancels
this attempt (awaiting_approval) and an approved new attempt resumes.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

#: Write actions that MUST carry a caller-supplied idempotency key (§3.3):
#: retries replay the cached result instead of re-executing the side effect.
_IDEMPOTENT_ACTIONS = frozenset({"issue.comment", "issue.status"})
_IDEMPOTENCY_KEY_MAX = 200
_IDEMPOTENCY_CACHE_MAX = 256

_GATE_BROKER = "broker"
_GATE_MOUNT = "mount"
_GATE_ACTION = "action"
_GATE_KERNEL = "kernel"


@dataclass(frozen=True)
class GateSpec:
    permission: str  # read_only | write | confirm_required | forbidden
    via: str  # broker | mount | action | kernel
    scope: str | None = None  # task-token scope required on the broker path


#: §3.3 — action → gate. This table is the UNIQUE mapping; anything absent is
#: treated as forbidden (fail-closed default below).
GATE_TABLE: dict[str, GateSpec] = {
    "worktree.read": GateSpec("read_only", _GATE_MOUNT),
    "worktree.write": GateSpec("write", _GATE_MOUNT),
    "issue.read": GateSpec("read_only", _GATE_BROKER, scope="issue:read"),
    "issue.comment": GateSpec("write", _GATE_BROKER, scope="issue:comment:write"),
    "issue.status": GateSpec("write", _GATE_BROKER, scope="issue:status:write"),
    "project.read": GateSpec("read_only", _GATE_BROKER, scope="project:read"),
    "cross_issue.write": GateSpec("confirm_required", _GATE_ACTION),
    "git.push": GateSpec("confirm_required", _GATE_ACTION),
    "egress.grant": GateSpec("confirm_required", _GATE_ACTION),
    "secret.use": GateSpec("confirm_required", _GATE_ACTION),
    # Permanently forbidden — kernel policy + socket/network isolation. No
    # approval can release these.
    "mount": GateSpec("forbidden", _GATE_KERNEL),
    "privilege": GateSpec("forbidden", _GATE_KERNEL),
    "daemon_control": GateSpec("forbidden", _GATE_KERNEL),
    "cloud_metadata": GateSpec("forbidden", _GATE_KERNEL),
}

_FORBIDDEN = GateSpec("forbidden", _GATE_KERNEL)


def gate_for(action: str) -> GateSpec:
    return GATE_TABLE.get(action, _FORBIDDEN)


class ToolBrokerServer:
    def __init__(
        self,
        *,
        attempt_id: str,
        socket_dir,
        sandbox_uid: int,
        cgroup_marker: str,
        nonce: str,
        task_token: str,
        server_base_url: str,
        issue_id: str | None,
        grants: dict[str, str],
        transport: httpx.AsyncBaseTransport | None = None,
        rate_burst: int = 120,
        rate_refill_per_s: float = 2.0,
    ) -> None:
        self.attempt_id = attempt_id
        self.socket_path = os.path.join(str(socket_dir), "mesh-broker.sock")
        self._socket_dir = str(socket_dir)
        self._sandbox_uid = sandbox_uid
        self._cgroup_marker = cgroup_marker
        self._nonce = nonce
        self._task_token = task_token
        self._issue_id = issue_id
        self._grants = dict(grants)
        self._http = httpx.AsyncClient(
            base_url=server_base_url.rstrip("/"), transport=transport, timeout=30.0
        )
        self._rate_burst = rate_burst
        self._rate_refill = rate_refill_per_s
        self._frozen = False
        self._server: asyncio.Server | None = None
        self._tokens = 0.0
        self._tokens_at = 0.0
        self._lock = asyncio.Lock()
        self._idempotency: OrderedDict[str, dict] = OrderedDict()
        self.audit: list[dict] = []

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        os.makedirs(self._socket_dir, exist_ok=True)
        os.chmod(self._socket_dir, 0o700)
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._on_connect, path=self.socket_path)
        os.chmod(self.socket_path, 0o600)
        os.chown(self.socket_path, self._sandbox_uid, os.getgid())

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self._http.aclose()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

    async def rotate_task_token(self, token: str) -> None:
        """renew returned a fresh token; the old one is already revoked
        server-side in the same transaction (§2.2)."""
        async with self._lock:
            self._task_token = token

    async def freeze(self) -> None:
        """Lease lost / terminal: refuse everything. The supervisor closes
        the broker BEFORE tearing the sandbox down (§2.2)."""
        self._frozen = True

    # -- connection handling -----------------------------------------------------

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if not self._peer_allowed(writer):
                await self._send(writer, {"ok": False, "error": {"code": "peer_refused"}})
                return
            if self._frozen:
                await self._send(writer, {"ok": False, "error": {"code": "broker_frozen"}})
                return
            hello = await asyncio.wait_for(reader.readline(), timeout=5.0)
            try:
                nonce_msg = json.loads(hello)
            except ValueError:
                nonce_msg = {}
            if not isinstance(nonce_msg, dict) or "nonce" not in nonce_msg:
                await self._send(writer, {"ok": False, "error": {"code": "handshake_required"}})
                return
            if nonce_msg["nonce"] != self._nonce:
                await self._send(writer, {"ok": False, "error": {"code": "bad_nonce"}})
                return
            await self._send(writer, {"ok": True})
            while True:
                line = await reader.readline()
                if not line:
                    return
                await self._handle_request(line, writer)
        except (OSError, TimeoutError, UnicodeDecodeError):
            pass  # sandbox peer went away; refusal state needs no echo
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    def _peer_allowed(self, writer: asyncio.StreamWriter) -> bool:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return False
        try:
            cred = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 16)  # struct ucred
        except OSError:
            return False
        peer_pid, peer_uid, _peer_gid = _parse_ucred(cred)
        if peer_uid != self._sandbox_uid:
            self._audit("peer_uid_mismatch", {})
            return False
        if self._cgroup_marker:
            try:
                with open(f"/proc/{peer_pid}/cgroup", encoding="utf-8") as fh:
                    cgroups = fh.read()
            except OSError:
                return False
            if self._cgroup_marker not in cgroups:
                self._audit("peer_cgroup_mismatch", {})
                return False
        return True

    async def _handle_request(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            await self._send(writer, {"ok": False, "error": {"code": "malformed_request"}})
            return
        if not isinstance(msg, dict):
            await self._send(writer, {"ok": False, "error": {"code": "malformed_request"}})
            return
        call_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if self._frozen:
            await self._send(writer, self._reply(call_id, None, "broker_frozen"))
            return
        if not isinstance(method, str) or not method:
            await self._send(writer, self._reply(call_id, None, "malformed_request"))
            return
        gate = gate_for(method)
        if gate.permission == "forbidden":
            self._audit("forbidden_action", {"action": method})
            await self._send(
                writer, self._reply(call_id, None, "permanently_forbidden" if method in GATE_TABLE
                                   else "unknown_action")
            )
            return
        if not self._rate_allow():
            await self._send(writer, self._reply(call_id, None, "rate_limited"))
            return
        if gate.permission == "confirm_required":
            # §3.3: answer immediately; the supervisor runs cancel(awaiting_
            # approval) + new-attempt resume. The privileged sandbox NEVER parks.
            self._audit("confirm_required", {"action": method})
            await self._send(
                writer,
                self._reply(call_id, None, "confirm_required", {"action": method, "params": params}),
            )
            return
        granted = self._grants.get(method)
        if granted is None or granted not in ("read_only", "write"):
            await self._send(writer, self._reply(call_id, None, "capability_not_granted"))
            return
        if granted == "read_only" and gate.permission == "write":
            await self._send(writer, self._reply(call_id, None, "capability_not_granted"))
            return
        await self._execute(method, gate, params, call_id, writer)

    # -- task-token-scoped execution --------------------------------------------

    async def _execute(
        self, method: str, gate: GateSpec, params: dict, call_id, writer: asyncio.StreamWriter
    ) -> None:
        issue_id = params.get("issue_id")
        # Resource scope pinning: the frozen task token covers ONE issue.
        if gate.scope and gate.scope.startswith("issue:") and self._issue_id is not None:
            if issue_id != self._issue_id:
                await self._send(writer, self._reply(call_id, None, "resource_scope_mismatch"))
                return
        # §3.3 idempotency gate: write actions must carry a caller-supplied
        # key; a repeated key replays the cached result instead of executing
        # the side effect twice. Missing/malformed key => fail-closed.
        idempotency_key: str | None = None
        if method in _IDEMPOTENT_ACTIONS:
            raw_key = params.get("idempotency_key")
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > _IDEMPOTENCY_KEY_MAX:
                await self._send(writer, self._reply(call_id, None, "invalid_params"))
                return
            idempotency_key = f"{method}:{raw_key}"
            cached = self._idempotency.get(idempotency_key)
            if cached is not None:
                self._idempotency.move_to_end(idempotency_key)
                self._audit("idempotent_replay", {"action": method})
                await self._send(writer, {"id": call_id, "ok": True, "result": cached})
                return
        try:
            if method == "issue.read":
                response = await self._mesh_get(f"/api/v1/issues/{issue_id}")
            elif method == "issue.comment":
                body = params.get("body")
                if not isinstance(body, str) or not body:
                    await self._send(writer, self._reply(call_id, None, "invalid_params"))
                    return
                response = await self._mesh_post(
                    f"/api/v1/issues/{issue_id}/comments", {"body": body[:8000]}
                )
            elif method == "issue.status":
                status = params.get("status")
                if not isinstance(status, str):
                    await self._send(writer, self._reply(call_id, None, "invalid_params"))
                    return
                response = await self._mesh_patch(
                    f"/api/v1/issues/{issue_id}", {"status": status}
                )
            elif method == "project.read":
                project_id = params.get("project_id")
                response = await self._mesh_get(f"/api/v1/projects/{project_id}")
            else:  # pragma: no cover — gate table drives reachability
                await self._send(writer, self._reply(call_id, None, "unknown_action"))
                return
        except httpx.HTTPError:
            await self._send(writer, self._reply(call_id, None, "upstream_unavailable"))
            return
        if response.status_code >= 400:
            code = "upstream_error"
            try:
                payload = response.json()
                raw = payload.get("error", {}).get("code")
                if isinstance(raw, str) and raw:
                    code = raw
            except ValueError:
                pass
            await self._send(writer, self._reply(call_id, None, code))
            return
        try:
            data = response.json()
        except ValueError:
            data = {}
        if idempotency_key is not None:
            # Cache ONLY successes — a failed upstream stays retryable under
            # the same key.
            self._idempotency[idempotency_key] = data
            if len(self._idempotency) > _IDEMPOTENCY_CACHE_MAX:
                self._idempotency.popitem(last=False)
        self._audit("executed", {"action": method})
        await self._send(writer, {"id": call_id, "ok": True, "result": data})

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._task_token}"}

    async def _mesh_get(self, path: str) -> httpx.Response:
        return await self._http.get(path, headers=self._auth_headers())

    async def _mesh_post(self, path: str, body: dict) -> httpx.Response:
        return await self._http.post(path, json=body, headers=self._auth_headers())

    async def _mesh_patch(self, path: str, body: dict) -> httpx.Response:
        return await self._http.patch(path, json=body, headers=self._auth_headers())

    # -- helpers -------------------------------------------------------------------

    def _rate_allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self._tokens_at if self._tokens_at else 0.0
        self._tokens = min(self._rate_burst, self._tokens + elapsed * self._rate_refill)
        if self._tokens_at == 0.0:
            self._tokens = float(self._rate_burst)
        self._tokens_at = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def _audit(self, event: str, fields: dict) -> None:
        self.audit.append({"event": event, **fields})

    @staticmethod
    def _reply(call_id, result: dict | None, code: str, details: dict | None = None) -> dict:
        error = {"code": code}
        if details is not None:
            error["details"] = details
        return {"id": call_id, "ok": False, "error": error}

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: dict) -> None:
        writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()


def _parse_ucred(cred: bytes) -> tuple[int, int, int]:
    import struct

    pid, uid, gid = struct.unpack("iII", cred[: struct.calcsize("iII")])
    return pid, uid, gid


class ActionBroker:
    """One-shot action grants for approved high-risk actions (§3.3): exact
    field match, single use, executor returns results only — never secrets."""

    def __init__(self) -> None:
        self._grants: dict[str, dict] = {}

    def issue_grant(self, *, action: str, fields: dict) -> dict:
        grant_id = uuid.uuid4().hex
        self._grants[grant_id] = {"action": action, "fields": dict(fields), "used": False}
        return {"grant_id": grant_id, "action": action, "fields": dict(fields)}

    async def execute(
        self,
        grant_id: str,
        executor: Callable[[dict], Awaitable[dict]],
        override: dict | None = None,
    ) -> dict:
        grant = self._grants.get(grant_id)
        if grant is None or grant["used"]:
            raise ValueError("grant expired or already used")
        if override:
            for key, value in override.items():
                if grant["fields"].get(key) != value:
                    raise ValueError(f"grant field mismatch: {key}")
        grant["used"] = True
        return await executor(dict(grant["fields"]))


def new_nonce() -> str:
    return secrets.token_hex(16)
