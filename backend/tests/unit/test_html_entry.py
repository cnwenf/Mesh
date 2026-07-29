"""SPA entry probe (search-command-palette.md §3.4 execution layer).

The probe is public (no auth, returns no data) and exists so the reverse
proxy can answer expired-slug deep links with a real HTTP 301:

* current slug → 200 empty body + ``X-Mesh-Entry: ok`` (proxy serves the SPA);
* slug renamed away → 301 with ``Location`` rebuilt on the current slug,
  query string preserved (fragments never reach the server);
* unknown slug → 200 ok (SPA renders not-found).

In-process ASGI via ``create_app`` + ``httpx.ASGITransport`` (same fixture
pattern as test_favorites_api.py); slug rename goes through the real
workspace PATCH endpoint so ``workspace_slug_history`` is written by the
service layer, not by test-side SQL.
"""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio


def _settings(db_url: str, redis_url: str) -> dict:
    return {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "html-entry-signing-secret-00000000000",
        "storage_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        "storage_public_endpoint": os.environ.get(
            "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"
        ),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh"),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret"),
        "storage_bucket": "mesh-html-entry-test",
    }


@pytest_asyncio.fixture
async def client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings(db_url, redis_url)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: httpx.AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Html-Entry-123",
            "display_name": email.split("@")[0],
        },
    )
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Html-Entry-123"}
    )
    return r.json()["data"]["access_token"]


async def _create_workspace(client: httpx.AsyncClient, token: str, slug: str) -> str:
    r = await client.post(
        "/api/v1/workspaces", json={"name": slug, "slug": slug}, headers=_h(token)
    )
    return r.json()["data"]["id"]


@pytest.mark.asyncio
async def test_entry_probe_current_slug_ok(client: httpx.AsyncClient):
    token = await _login(client, "entry-current@example.com")
    await _create_workspace(client, token, "acme")

    # No auth header at all — the probe is a public entry point.
    r = await client.get("/__mesh_entry/w/acme/board", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers.get("x-mesh-entry") == "ok"
    assert r.content == b""

    # Bare workspace root variant.
    r = await client.get("/__mesh_entry/w/acme", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers.get("x-mesh-entry") == "ok"


@pytest.mark.asyncio
async def test_entry_probe_expired_slug_301_preserves_query(client: httpx.AsyncClient):
    token = await _login(client, "entry-expired@example.com")
    ws_id = await _create_workspace(client, token, "oldsquad")

    # Rename the slug through the real API: the service layer releases the
    # old slug into workspace_slug_history (workspace.md §2.5 / W6).
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"slug": "newsquad"}, headers=_h(token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["slug"] == "newsquad"

    r = await client.get(
        "/__mesh_entry/w/oldsquad/board?view=x&keep=1", follow_redirects=False
    )
    assert r.status_code == 301
    assert r.headers["location"] == "/w/newsquad/board?view=x&keep=1"

    # The new slug itself probes ok.
    r = await client.get("/__mesh_entry/w/newsquad/board?view=x", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers.get("x-mesh-entry") == "ok"

    # Deep subpaths rebuild correctly too (squad task deep link).
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"slug": "thirdname"}, headers=_h(token)
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        "/__mesh_entry/w/newsquad/squads/123/tasks/456", follow_redirects=False
    )
    assert r.status_code == 301
    assert r.headers["location"] == "/w/thirdname/squads/123/tasks/456"


@pytest.mark.asyncio
async def test_entry_probe_unknown_slug_ok(client: httpx.AsyncClient):
    # Unknown slug: 200 ok so the SPA renders its not-found state (the proxy
    # serves index.html); existence of foreign workspaces never leaks.
    r = await client.get("/__mesh_entry/w/never-existed/board", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers.get("x-mesh-entry") == "ok"
    assert r.content == b""


@pytest.mark.asyncio
async def test_entry_probe_cjk_subpath_location_is_valid(client: httpx.AsyncClient):
    """LOW-1 — a CJK subpath must produce a valid (percent-encoded) Location
    instead of a latin-1 encoding 500."""
    token = await _login(client, "entry-cjk@example.com")
    ws_id = await _create_workspace(client, token, "cjkteam")
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"slug": "cjkteam2"}, headers=_h(token)
    )
    assert r.status_code == 200, r.text

    r = await client.get(
        "/__mesh_entry/w/cjkteam/board/看板", follow_redirects=False
    )
    assert r.status_code == 301
    location = r.headers["location"]
    # ASCII-safe header value, decodes back to the original path.
    location.encode("latin-1")
    from urllib.parse import unquote

    assert unquote(location) == "/w/cjkteam2/board/看板"


@pytest.mark.asyncio
async def test_entry_probe_control_chars_rejected_400(client: httpx.AsyncClient):
    """LOW-1 — control characters can never build a well-formed same-origin
    Location; the probe rejects 400 (documented same-origin guarantee).

    Drives ``_probe`` directly: the HTTP transport (h11) refuses CRLF in the
    request line, so the in-handler guard is exercised at the unit level.
    """
    from unittest.mock import Mock

    from mesh.api.html_entry import _probe

    token = await _login(client, "entry-ctrl@example.com")
    await _create_workspace(client, token, "ctrlteam")
    r = await client.patch(
        "/api/v1/workspaces/ctrlteam", json={"slug": "ctrlteam2"}, headers=_h(token)
    )
    assert r.status_code == 200, r.text

    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.session_factory() as session:
        request = Mock()
        request.url = Mock(query="")
        # CRLF injection attempt in the subpath.
        response = await _probe(request, session, "ctrlteam", "board\r\nInjected: x")
        assert response.status_code == 400
        # Control char in the slug too.
        response = await _probe(request, session, "ctrl\rteam", "")
        assert response.status_code == 400
