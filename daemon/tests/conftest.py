"""Shared fixtures: FakeClock and a scripted fake Mesh server.

The fake server is an ``httpx.MockTransport`` handler with real HTTP
semantics (status codes, headers, JSON envelopes) — the daemon client and
every orchestration loop run against it with zero network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest

from mesh_runtime.timeutil import FakeClock


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@dataclass
class RecordedCall:
    method: str
    path: str
    body: dict | None
    headers: dict


@dataclass
class FakeServer:
    """Scripted responses keyed by ``METHOD /path`` (path without query).

    ``enqueue(key, status, json_body, headers)`` scripts a FIFO of replies per
    route; when the FIFO is empty the ``default_status`` (204) applies.
    """

    default_status: int = 204
    calls: list[RecordedCall] = field(default_factory=list)
    _queues: dict[str, list[tuple[int, dict | None, dict]]] = field(default_factory=dict)

    def enqueue(
        self,
        key: str,
        status: int = 200,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> None:
        self._queues.setdefault(key, []).append((status, body, headers or {}))

    def calls_for(self, key: str) -> list[RecordedCall]:
        return [c for c in self.calls if f"{c.method} {c.path}" == key]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        key = f"{request.method} {path}"
        raw = request.content
        body = json.loads(raw) if raw else None
        self.calls.append(
            RecordedCall(
                method=request.method,
                path=path,
                body=body,
                headers={k.lower(): v for k, v in request.headers.items()},
            )
        )
        queue = self._queues.get(key) or []
        if queue:
            status, resp_body, headers = queue.pop(0)
        else:
            status, resp_body, headers = self.default_status, None, {}
        if resp_body is None:
            return httpx.Response(status_code=status, headers=headers)
        return httpx.Response(status_code=status, json=resp_body, headers=headers)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@pytest.fixture
def fake_server() -> FakeServer:
    return FakeServer()


def make_rand(values: list[float]) -> Callable[[], float]:
    """Deterministic rand() source feeding full_jitter in tests."""
    iterator = iter(values)
    return lambda: next(iterator)
