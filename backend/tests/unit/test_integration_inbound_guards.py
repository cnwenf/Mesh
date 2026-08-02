"""Inbound DoS-guard + rejected-audit truncation tests (MEDIUM-3 / §5.4).

The six inbound endpoints are UNAUTHENTICATED, so every route enforces a
per-IP rate limit and a body cap BEFORE any signature work, and rejected
audits persist only a size-capped head of attacker-controlled content.
Guards are exercised through a fabricated Starlette ``Request`` (the app
object + limiter are the only collaborators); the logic under test is real.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from mesh.integrations import inbound_routes as ir
from mesh.integrations.inbound import (
    REJECTED_PAYLOAD_HEAD_BYTES,
    REJECTED_PAYLOAD_MAX_BYTES,
    _audit_payload,
)

pytestmark = pytest.mark.unit


class FakeLimiter:
    def __init__(self, remaining: int) -> None:
        self.remaining = remaining
        self.calls: list[str] = []

    async def check(self, key, *, limit, window_seconds):
        self.calls.append(key)
        return self.remaining, 0


class FakeState:
    def __init__(self, limiter: FakeLimiter) -> None:
        self.rate_limiter = limiter


class FakeApp:
    def __init__(self, limiter: FakeLimiter) -> None:
        self.state = FakeState(limiter)


class ScriptedReceive:
    """ASGI receive channel that exposes how many chunks were consumed."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.calls = 0

    async def __call__(self) -> dict:
        if self.calls >= len(self.chunks):
            raise AssertionError("request body was read past the scripted chunks")
        body = self.chunks[self.calls]
        self.calls += 1
        return {
            "type": "http.request",
            "body": body,
            "more_body": self.calls < len(self.chunks),
        }


class FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


def make_request(
    *,
    body: bytes = b"{}",
    limiter: FakeLimiter | None = None,
    content_length: str | None = "...",
    client: tuple[str, int] | None = ("1.2.3.4", 999),
) -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
    if content_length is None:
        headers.append((b"content-length", str(len(body)).encode()))
    elif content_length != "...":
        headers.append((b"content-length", content_length.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "client": client,
        "app": FakeApp(limiter or FakeLimiter(100)),
    }
    request = Request(scope)
    request._body = body  # short-circuit body() — no receive channel needed
    return request


def make_streaming_request(
    chunks: list[bytes], *, content_length: str | None = None
) -> tuple[Request, ScriptedReceive]:
    headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", content_length.encode()))
    receive = ScriptedReceive(chunks)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/integrations/dingtalk/events",
        "raw_path": b"/api/v1/integrations/dingtalk/events",
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "headers": headers,
        "client": ("1.2.3.4", 999),
        "app": FakeApp(FakeLimiter(100)),
    }
    return Request(scope, receive), receive


# ---------------------------------------------------------------------------
# Rate limit (429) — limiter short-circuits before any body work
# ---------------------------------------------------------------------------


async def test_guard_returns_429_when_limiter_exhausted():
    request = make_request(limiter=FakeLimiter(-1))
    response = await ir._guard(request)
    assert isinstance(response, JSONResponse)
    assert response.status_code == 429
    assert json.loads(response.body) == {"error": "rate_limited"}


async def test_guard_keys_the_limiter_by_client_ip():
    limiter = FakeLimiter(10)
    await ir._guard(make_request(limiter=limiter))
    assert limiter.calls == ["integration-inbound:1.2.3.4"]


async def test_guard_passes_when_under_limit_and_small_body():
    assert await ir._guard(make_request(body=b"{}", limiter=FakeLimiter(10))) is None


# ---------------------------------------------------------------------------
# Body cap (413) — declared Content-Length AND actual length
# ---------------------------------------------------------------------------


async def test_guard_rejects_oversized_declared_content_length_without_reading():
    # Declared size exceeds the cap; the real body is tiny and never read.
    request = make_request(
        body=b"x",
        content_length=str(ir.INBOUND_BODY_MAX_BYTES + 1),
        limiter=FakeLimiter(10),
    )
    response = await ir._guard(request)
    assert response.status_code == 413
    assert json.loads(response.body) == {"error": "payload_too_large"}


async def test_guard_rejects_malformed_content_length():
    request = make_request(content_length="not-a-number", limiter=FakeLimiter(10))
    response = await ir._guard(request)
    assert response.status_code == 413


async def test_read_body_rejects_oversized_actual_body():
    # No usable Content-Length (chunked-style) — the read length is the guard.
    oversized = b"x" * (ir.INBOUND_BODY_MAX_BYTES + 1)
    request = make_request(body=oversized, content_length=None, limiter=FakeLimiter(10))
    result = await ir._read_body(request)
    assert isinstance(result, JSONResponse)
    assert result.status_code == 413


async def test_read_body_returns_bytes_when_within_cap():
    request = make_request(body=b'{"ok":1}', content_length=None, limiter=FakeLimiter(10))
    assert await ir._read_body(request) == b'{"ok":1}'


async def test_read_body_stops_at_first_chunk_crossing_cap(monkeypatch):
    """A lying Content-Length cannot make us drain the rest of the stream."""
    monkeypatch.setattr(ir, "INBOUND_BODY_MAX_BYTES", 8)
    request, receive = make_streaming_request(
        [b"1234", b"56789", b"must-not-be-read"], content_length="4"
    )

    result = await ir._read_body(request)

    assert isinstance(result, JSONResponse)
    assert result.status_code == 413
    assert receive.calls == 2


async def test_real_asgi_chunked_body_stops_consumption_at_cap(monkeypatch):
    """Exercise the real FastAPI/Starlette streaming path, not Request._body."""
    monkeypatch.setattr(ir, "INBOUND_BODY_MAX_BYTES", 8)
    app = FastAPI()
    app.state.rate_limiter = FakeLimiter(100)
    app.include_router(ir.router)
    third_chunk_requested = False

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal third_chunk_requested
        yield b"1234"
        yield b"56789"
        third_chunk_requested = True
        yield b"must-not-be-read"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/integrations/slack/events",
            content=chunks(),
            headers={"content-length": "4"},
        )

    assert response.status_code == 413
    assert response.json() == {"error": "payload_too_large"}
    assert third_chunk_requested is False


async def test_real_asgi_dingtalk_ip_limit_precedes_body_consumption(monkeypatch):
    """An exhausted IP-only budget silently returns before reading chunk one."""
    calls: list[uuid.UUID | None] = []

    async def exhausted(_request: Request, *, integration_id: uuid.UUID | None) -> bool:
        calls.append(integration_id)
        return True

    async def unlocated(*_args, **_kwargs):
        return []

    monkeypatch.setattr(ir, "_dingtalk_pre_limit_exceeded", exhausted)
    monkeypatch.setattr(ir, "_lookup_by_config_value", unlocated)
    app = FastAPI()
    app.state.session_factory = lambda: FakeSessionContext()
    app.include_router(ir.router)
    body_started = False

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal body_started
        body_started = True
        yield b'{"chatbotCorpId":"dingcorp"}'

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/integrations/dingtalk/events", content=chunks()
        )

    assert response.status_code == 200
    assert response.json() == {
        "received": True,
        "event_id": "",
        "process_status": "received",
    }
    assert calls == [None]
    assert body_started is False


@pytest.mark.parametrize("located", [False, True])
async def test_dingtalk_limiter_accounting_does_not_double_count_unlocated(
    monkeypatch, located: bool
):
    integration_id = uuid.uuid4()
    robot_code = "dingrobot0001"
    calls: list[uuid.UUID | None] = []

    async def within_budget(_request: Request, *, integration_id: uuid.UUID | None) -> bool:
        calls.append(integration_id)
        return False

    async def lookup(*_args, **_kwargs):
        return (
            [
                (
                    integration_id,
                    uuid.uuid4(),
                    "active",
                    "im_dingtalk",
                    {"receive_mode": "http", "robot_code": robot_code},
                    "secret-ref",
                )
            ]
            if located
            else []
        )

    async def completed(*_args, **_kwargs) -> JSONResponse:
        return JSONResponse(status_code=200, content={"received": True})

    monkeypatch.setattr(ir, "_dingtalk_pre_limit_exceeded", within_budget)
    monkeypatch.setattr(ir, "_lookup_by_config_value", lookup)
    monkeypatch.setattr(ir, "_run_inbound", completed)
    request = make_request(
        body=b'{"chatbotCorpId":"dingcorp","robotCode":"dingrobot0001"}',
        content_length=None,
    )
    request.app.state.session_factory = lambda: FakeSessionContext()

    response = await ir.dingtalk_events(request)

    assert response.status_code == 200
    assert calls == ([None, integration_id] if located else [None])


def test_client_ip_falls_back_to_unknown():
    assert ir._client_ip(make_request(client=None)) == "unknown"


# ---------------------------------------------------------------------------
# Rejected-audit payload truncation (§5.4 — untrusted, attacker-inflatable)
# ---------------------------------------------------------------------------


def test_audit_payload_passthrough_for_valid_events():
    payload = {"a": 1, "nested": {"b": "x" * 50000}}
    assert _audit_payload(payload, "received") is payload
    assert _audit_payload(payload, "dispatched") is payload


def test_audit_payload_passthrough_for_small_rejected():
    payload = {"small": "value"}
    assert _audit_payload(payload, "rejected") == payload


def test_audit_payload_truncates_oversized_rejected():
    # A rejected payload larger than the 16 KiB audit cap is replaced by a
    # forensic stub: the truncated flag, the original byte size, and a
    # 4 KiB head — never the full attacker-controlled content.
    big = {"text": "A" * (REJECTED_PAYLOAD_MAX_BYTES + 10_000)}
    result = _audit_payload(big, "rejected")
    assert result["_truncated"] is True
    encoded_len = len(json.dumps(big, ensure_ascii=False, separators=(",", ":")).encode())
    assert result["original_bytes"] == encoded_len
    head = result["head"]
    assert len(head.encode()) <= REJECTED_PAYLOAD_HEAD_BYTES + 3  # utf-8 replacement slack
    assert "text" in head, "the forensic prefix is preserved"
    assert "A" * 100 in head
