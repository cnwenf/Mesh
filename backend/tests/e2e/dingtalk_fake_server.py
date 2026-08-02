"""In-process fake DingTalk OpenAPI platform for e2e tests.

A real HTTP server on 127.0.0.1 (ThreadingHTTPServer) that records every
request and answers with scriptable payloads — the worker / API processes
talk to it over real sockets via ``MESH_DINGTALK_API_BASE`` and
``MESH_DINGTALK_OAPI_BASE``. Nothing on the contract path is mocked: the
adapter's credential proof, token flow, classification and redaction run for
real against this peer.
"""

from __future__ import annotations

import hmac
import json
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

LEGACY_GETTOKEN_PATH = "/gettoken"
TOKEN_PATH = "/v1.0/oauth2/accessToken"
GROUP_SEND_PATH = "/v1.0/robot/groupMessages/send"
DIRECT_SEND_PATH = "/v1.0/robot/oToMessages/batchSend"
CARD_CREATE_PATH = "/v1.0/card/instances/createAndDeliver"
CARD_UPDATE_PATH = "/v1.0/card/instances"


@dataclass
class FakeDingTalkState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    token_calls: int = 0
    token_delay: float = 0.0
    token_status: int = 200
    token_body: dict[str, Any] | None = None
    send_status: int = 200
    send_body: dict[str, Any] | None = None
    card_status: int = 200
    expected_app_secret: str | None = None
    # FIFO one-shot overrides: (status, body); falls back to the defaults.
    send_queue: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def record(self, method: str, path: str, body: dict[str, Any] | None, headers: dict) -> None:
        with self.lock:
            self.requests.append(
                {"method": method, "path": path, "body": body, "headers": dict(headers)}
            )

    def calls(self, path: str) -> list[dict[str, Any]]:
        with self.lock:
            return [r for r in self.requests if r["path"] == path]

    def group_sends(self) -> list[dict[str, Any]]:
        return self.calls(GROUP_SEND_PATH)

    def direct_sends(self) -> list[dict[str, Any]]:
        return self.calls(DIRECT_SEND_PATH)

    def card_creates(self) -> list[dict[str, Any]]:
        return self.calls(CARD_CREATE_PATH)

    def next_send_override(self) -> tuple[int, dict[str, Any]] | None:
        with self.lock:
            if self.send_queue:
                return self.send_queue.pop(0)
        return None


def _make_handler(state: FakeDingTalkState):
    import time as _time

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # silence request logging
            pass

        def _read_body(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return None
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
            return parsed if isinstance(parsed, dict) else None

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _handle(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            body = self._read_body()
            state.record(method, path, body, self.headers)
            if path == TOKEN_PATH:
                if state.token_delay:
                    _time.sleep(state.token_delay)
                if state.token_body is not None:
                    self._reply(state.token_status, state.token_body)
                    return
                with state.lock:
                    state.token_calls += 1
                    number = state.token_calls
                self._reply(200, {"accessToken": f"tok-{number}", "expireIn": 7200})
                return
            if path in (GROUP_SEND_PATH, DIRECT_SEND_PATH):
                override = state.next_send_override()
                if override is not None:
                    self._reply(*override)
                    return
                if state.send_body is not None:
                    self._reply(state.send_status, state.send_body)
                    return
                self._reply(state.send_status,
                            {"processQueryKey": "pqk", "flowControlledStaffIdList": []})
                return
            if path in (CARD_CREATE_PATH, CARD_UPDATE_PATH):
                self._reply(state.card_status,
                            {"result": {"outTrackId": (body or {}).get("outTrackId")}})
                return
            self._reply(404, {"code": "notFound"})

        def do_POST(self):  # noqa: N802
            self._handle("POST")

        def do_PUT(self):  # noqa: N802
            self._handle("PUT")

        def do_GET(self):  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path != LEGACY_GETTOKEN_PATH:
                self._reply(404, {"errcode": 404, "errmsg": "not found"})
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            app_key = (query.get("appkey") or [""])[0]
            presented = (query.get("appsecret") or [""])[0]
            expected = state.expected_app_secret or ""
            state.record("GET", parsed.path, {"appkey": app_key}, self.headers)
            if app_key and expected and hmac.compare_digest(presented, expected):
                self._reply(200, {"errcode": 0, "access_token": "ownership-proof"})
                return
            self._reply(200, {"errcode": 40089, "errmsg": "invalid app credentials"})

    return Handler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_fake_dingtalk(
    *, expected_app_secret: str | None = None
) -> tuple[ThreadingHTTPServer, str, FakeDingTalkState]:
    """Start the fake platform on a free loopback port. Returns
    (server, base_url, state)."""
    state = FakeDingTalkState(expected_app_secret=expected_app_secret)
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return server, base_url, state
