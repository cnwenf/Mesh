"""Shared test doubles for the DingTalk OpenAPI adapter (unit level).

``ScriptedDingTalkTransport`` is an httpx AsyncBaseTransport that emulates
the DingTalk OpenAPI surface the adapter talks to (accessToken, robot
message send, card instances) with thread-/task-safe request recording and
scriptable failures. It is injected via ``httpx.AsyncClient(transport=...)``
so NOTHING on the contract path is mocked — the adapter runs its real
request-building / classification / redaction code against a programmable
peer.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

TOKEN_PATH = "/v1.0/oauth2/accessToken"
GROUP_SEND_PATH = "/v1.0/robot/groupMessages/send"
DIRECT_SEND_PATH = "/v1.0/robot/oToMessages/batchSend"
CARD_CREATE_PATH = "/v1.0/card/instances/createAndDeliver"
CARD_UPDATE_PATH = "/v1.0/card/instances"
CARD_STREAM_PATH = "/v1.0/card/streaming"


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: dict[str, Any] | None


@dataclass
class ScriptedDingTalkTransport(httpx.AsyncBaseTransport):
    """Programmable DingTalk OpenAPI peer.

    Knobs:
    - ``token_delay``: seconds the token endpoint awaits before answering
      (drives leader/follower timing tests).
    - ``token_status`` / ``token_body``: override the token answer
      (credential-failure injection).
    - ``token_exc``: raise this exception from the token endpoint
      (network-failure injection).
    - ``send_status`` / ``send_body``: override robot message answers
      (rate-limit code injection).
    """

    token_delay: float = 0.0
    token_status: int = 200
    token_body: dict[str, Any] | None = None
    token_exc: Exception | None = None
    token_expire_in: int = 7200
    send_status: int = 200
    send_body: dict[str, Any] | None = None
    card_status: int = 200
    card_body: dict[str, Any] | None = None
    # Per-request scripted answers for the send/card paths (popped FIFO;
    # falls back to send_status/send_body when empty) — drives
    # "first answer 40014, retry succeeds" scenarios.
    send_queue: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    card_queue: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    token_calls: int = 0
    requests: list[RecordedRequest] = field(default_factory=list)
    on_request: Callable[[RecordedRequest], None] | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body: dict[str, Any] | None = None
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = None
        recorded = RecordedRequest(
            method=request.method,
            path=path,
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
        )
        self.requests.append(recorded)
        if self.on_request is not None:
            self.on_request(recorded)

        if path == TOKEN_PATH:
            self.token_calls += 1
            if self.token_delay:
                await asyncio.sleep(self.token_delay)
            if self.token_exc is not None:
                raise self.token_exc
            if self.token_body is not None:
                return _json(self.token_status, self.token_body)
            return _json(
                self.token_status,
                {"accessToken": f"tok-{self.token_calls}", "expireIn": self.token_expire_in},
            )
        if path in (GROUP_SEND_PATH, DIRECT_SEND_PATH):
            if self.send_queue:
                status, payload = self.send_queue.pop(0)
                return _json(status, payload)
            if self.send_body is not None:
                return _json(self.send_status, self.send_body)
            return _json(self.send_status, {"processQueryKey": "pqk-1", "flowControlledStaffIdList": []})
        if path in (CARD_CREATE_PATH, CARD_UPDATE_PATH, CARD_STREAM_PATH):
            if self.card_queue:
                status, payload = self.card_queue.pop(0)
                return _json(status, payload)
            if self.card_body is not None:
                return _json(self.card_status, self.card_body)
            return _json(self.card_status, {"result": {"outTrackId": (body or {}).get("outTrackId")}})
        return _json(404, {"code": "notFound", "message": f"unknown path {path}"})

    # -- assertions ------------------------------------------------------

    def calls_for(self, path: str) -> list[RecordedRequest]:
        return [r for r in self.requests if r.path == path]

    def group_sends(self) -> list[RecordedRequest]:
        return self.calls_for(GROUP_SEND_PATH)

    def direct_sends(self) -> list[RecordedRequest]:
        return self.calls_for(DIRECT_SEND_PATH)


def _json(status: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, json=body)


def make_client(transport: ScriptedDingTalkTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, timeout=5.0)
