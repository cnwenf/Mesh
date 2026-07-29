"""Typed client for the Mesh server machine API (``/api/v1/daemon/*``).

One responsibility: speak the HTTP contract and translate failures into the
:mod:`mesh_runtime.errors` taxonomy. It performs NO retries and NO backoff —
orchestration loops own cadence (runtime-executor.md §3.1 keeps heartbeat /
renew / claim backoff policies distinct).

Envelope: success bodies are ``{"data": ...}``; error bodies are
``{"error": {"code": ..., "message": ...}}``. The bearer ``mesh_rt_`` token
authenticates every call except ``activate`` (which carries the one-time code).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from mesh_runtime import __version__
from mesh_runtime.errors import FatalAuthError, ProtocolError, ServerError, classify_response

_TIMEOUT_SECONDS = 30.0


def _parse_retry_after(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except (ValueError, AttributeError):
        return None
    return value if value >= 0 else None


@dataclass(frozen=True)
class ActivateResponse:
    runtime_id: str
    runtime_token: str
    heartbeat_interval_seconds: float


@dataclass(frozen=True)
class CancelCommand:
    attempt_id: str
    execution_id: str | None
    grace_seconds: float


@dataclass(frozen=True)
class HeartbeatResponse:
    server_time: str | None
    commands: list[dict] = field(default_factory=list)

    def cancel_commands(self) -> list[CancelCommand]:
        out: list[CancelCommand] = []
        for cmd in self.commands:
            if not isinstance(cmd, dict) or cmd.get("type") != "cancel_execution":
                continue
            attempt_id = cmd.get("attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id:
                continue
            execution_id = cmd.get("execution_id")
            out.append(
                CancelCommand(
                    attempt_id=attempt_id,
                    execution_id=execution_id if isinstance(execution_id, str) else None,
                    grace_seconds=float(cmd.get("grace_seconds", 15) or 15),
                )
            )
        return out


@dataclass(frozen=True)
class ClaimResponse:
    execution: dict
    attempt: dict

    @property
    def attempt_id(self) -> str:
        return str(self.attempt["id"])

    @property
    def execution_id(self) -> str:
        return str(self.execution["id"])

    @property
    def lease_seq(self) -> int:
        return int(self.attempt["lease_seq"])

    @property
    def lease_expires_at(self) -> str:
        return str(self.attempt["lease_expires_at"])

    @property
    def config_snapshot(self) -> dict:
        snapshot = self.execution.get("config_snapshot")
        return snapshot if isinstance(snapshot, dict) else {}

    @property
    def credentials(self) -> list[dict]:
        creds = self.attempt.get("credentials")
        return [c for c in creds if isinstance(c, dict)] if isinstance(creds, list) else []

    @property
    def task_token(self) -> str | None:
        """One-time ``mesh_task_`` token delivered at claim (MES-98 P0). Held
        by the daemon-side ToolBroker only — never enters the sandbox."""
        token = self.attempt.get("task_token")
        return token if isinstance(token, str) and token else None

    @property
    def task_token_expires_at(self) -> str | None:
        expires = self.attempt.get("task_token_expires_at")
        return expires if isinstance(expires, str) and expires else None

    @property
    def resume_context(self) -> dict | None:
        """Structured resume checkpoint present ONLY when this claim continues
        an execution whose tool_call approval was granted (runtime.md §3.2)."""
        ctx = self.execution.get("resume_context")
        return ctx if isinstance(ctx, dict) else None


@dataclass(frozen=True)
class LeaseInfo:
    lease_seq: int
    lease_expires_at: str
    task_token: str | None = None  # rotated on every renew; plaintext once
    task_token_expires_at: str | None = None


@dataclass(frozen=True)
class LogAck:
    accepted_end_offset: int
    redacted_hits: int


class RuntimeApiClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": f"mesh-runtime/{__version__}"},
        )

    def __repr__(self) -> str:  # never echo the token
        return f"RuntimeApiClient(base_url={self._base_url!r}, authenticated={self._token is not None})"

    def set_token(self, token: str) -> None:
        self._token = token

    async def close(self) -> None:
        await self._http.aclose()

    # -- core ---------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        auth: bool = True,
    ) -> dict | None:
        headers: dict[str, str] = {}
        if auth:
            if not self._token:
                raise FatalAuthError("no runtime token loaded — activate first")
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = await self._http.request(method, path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ServerError(f"transport failure on {method} {path}: {type(exc).__name__}") from exc

        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        parsed = self._parse_body(response)
        classify_response(response.status_code, parsed, retry_after)
        if response.status_code == 204 or parsed is None:
            return None
        if isinstance(parsed, dict) and "data" in parsed:
            data = parsed["data"]
            return data if isinstance(data, dict) else None
        return parsed

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict | None:
        if not response.content:
            return None
        try:
            parsed = response.json()
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    # -- endpoints ----------------------------------------------------------

    async def activate(
        self,
        activation_code: str,
        metadata: dict,
        *,
        protocol_version: int = 1,
        provider_manifest: dict | None = None,
        daemon_features: dict | None = None,
    ) -> ActivateResponse:
        data = await self._request(
            "POST",
            "/api/v1/daemon/runtimes:activate",
            body={
                "activation_code": activation_code,
                "metadata": metadata,
                "protocol_version": protocol_version,
                "provider_manifest": provider_manifest or {},
                "daemon_features": daemon_features or {},
            },
            auth=False,
        )
        if not data or "runtime_id" not in data or "runtime_token" not in data:
            raise ProtocolError("activate returned no data")
        return ActivateResponse(
            runtime_id=str(data["runtime_id"]),
            runtime_token=str(data["runtime_token"]),
            heartbeat_interval_seconds=float(data.get("heartbeat_interval_seconds", 15) or 15),
        )

    async def heartbeat(
        self,
        runtime_id: str,
        *,
        current_load: int,
        health: str,
        metrics: dict,
        inflight: list[str],
        protocol_version: int | None = None,
    ) -> HeartbeatResponse:
        body: dict = {
            "current_load": current_load,
            "health": health,
            "metrics": metrics,
            "inflight": inflight,
        }
        if protocol_version is not None:
            body["protocol_version"] = protocol_version
        data = await self._request(
            "POST",
            f"/api/v1/daemon/runtimes/{runtime_id}:heartbeat",
            body=body,
        )
        data = data or {}
        commands = data.get("commands")
        server_time = data.get("server_time")
        return HeartbeatResponse(
            server_time=server_time if isinstance(server_time, str) else None,
            commands=[c for c in commands if isinstance(c, dict)] if isinstance(commands, list) else [],
        )

    async def claim(self, runtime_id: str, diagnostics: dict | None = None) -> ClaimResponse | None:
        data = await self._request(
            "POST",
            f"/api/v1/daemon/runtimes/{runtime_id}/executions:claim",
            body={"diagnostics": diagnostics or {}},
        )
        if data is None:
            return None  # 204 — queue empty / no match / capacity full
        execution = data.get("execution")
        attempt = data.get("attempt")
        if not isinstance(execution, dict) or not isinstance(attempt, dict):
            raise ProtocolError("claim response missing execution/attempt")
        return ClaimResponse(execution=execution, attempt=attempt)

    async def renew_lease(self, attempt_id: str, *, lease_seq: int) -> LeaseInfo:
        data = await self._request(
            "POST",
            f"/api/v1/daemon/attempts/{attempt_id}:renew-lease",
            body={"lease_seq": lease_seq},
        )
        if not data:
            raise ProtocolError("renew returned no data")
        token = data.get("task_token")
        expires = data.get("task_token_expires_at")
        return LeaseInfo(
            lease_seq=int(data["lease_seq"]),
            lease_expires_at=str(data["lease_expires_at"]),
            task_token=token if isinstance(token, str) and token else None,
            task_token_expires_at=expires if isinstance(expires, str) and expires else None,
        )

    async def transition(
        self,
        attempt_id: str,
        *,
        lease_seq: int,
        status: str,
        result: dict | None = None,
        failure_reason: str | None = None,
    ) -> dict:
        data = await self._request(
            "PATCH",
            f"/api/v1/daemon/attempts/{attempt_id}",
            body={
                "lease_seq": lease_seq,
                "status": status,
                "result": result,
                "failure_reason": failure_reason,
            },
        )
        return data or {}

    async def append_logs(
        self,
        attempt_id: str,
        *,
        lease_seq: int,
        stream: str,
        start_offset: int,
        lines: list[str],
        sealed: bool = False,
    ) -> LogAck:
        data = await self._request(
            "POST",
            f"/api/v1/daemon/attempts/{attempt_id}/logs",
            body={
                "lease_seq": lease_seq,
                "stream": stream,
                "start_offset": start_offset,
                "lines": lines,
                "sealed": sealed,
            },
        )
        data = data or {}
        return LogAck(
            accepted_end_offset=int(data.get("accepted_end_offset", start_offset)),
            redacted_hits=int(data.get("redacted_hits", 0)),
        )

    async def report_checkout(
        self,
        attempt_id: str,
        *,
        lease_seq: int,
        status: str,
        repo_url: str | None = None,
        base_ref: str | None = None,
        commit_sha: str | None = None,
        local_path: str | None = None,
        diff: str | None = None,
    ) -> dict:
        data = await self._request(
            "POST",
            f"/api/v1/daemon/attempts/{attempt_id}/checkouts",
            body={
                "lease_seq": lease_seq,
                "status": status,
                "repo_url": repo_url,
                "base_ref": base_ref,
                "commit_sha": commit_sha,
                "local_path": local_path,
                "diff": diff,
            },
        )
        return data or {}

    async def refetch_credentials(self, attempt_id: str, *, lease_seq: int) -> list[dict]:
        data = await self._request(
            "POST",
            f"/api/v1/daemon/attempts/{attempt_id}/credentials:refetch",
            body={"lease_seq": lease_seq},
        )
        data = data or {}
        creds = data.get("credentials")
        return [c for c in creds if isinstance(c, dict)] if isinstance(creds, list) else []

    async def request_approval(
        self,
        execution_id: str,
        *,
        lease_seq: int,
        attempt_id: str,
        action_summary: dict,
        resume_context: dict | None = None,
    ) -> dict:
        data = await self._request(
            "POST",
            f"/api/v1/daemon/executions/{execution_id}/approvals",
            body={
                "lease_seq": lease_seq,
                "attempt_id": attempt_id,
                "action_summary": action_summary,
                "resume_context": resume_context or {},
            },
        )
        return data or {}
