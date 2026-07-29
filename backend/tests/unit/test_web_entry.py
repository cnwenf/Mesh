"""HTML entry route (theme.md §2.3 ① precise injection) — ASGI integration.

Real create_app() over PostgreSQL + Redis. Covers: binary-converged
``__MESH_APPEARANCE__`` injection before ``</head>``, per-request nonce CSP
(never unsafe-inline for scripts), cache partitioning (personalized →
private,no-store; static shell → public), route guards (/api, non-HTML
Accept), dist-absent degradation, and the real login credential flowing
through the ``mesh_session`` cookie read path.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from mesh.api.app import create_app
from mesh.auth.security import hash_token
from mesh.config import load_settings

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <title>Mesh</title>
    <script>
      (function () {
        document.documentElement.setAttribute('data-theme', 'light');
      })();
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/assets/main.js"></script>
  </body>
</html>
"""


@pytest.fixture
def dist_dir(tmp_path):
    (tmp_path / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(db_url, redis_url, dist_dir):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-entry-test-signing-secret-000000",
        frontend_dist_dir=str(dist_dir),
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await app.state.redis.aclose()
    await app.state.engine.dispose()


async def _seed_session_user(session_factory, *, email: str, theme: object = "__absent__"):
    settings = {} if theme == "__absent__" else {"theme": theme}
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name, settings) "
                    "VALUES (:e, 'U', CAST(:s AS jsonb)) RETURNING id"
                ),
                {"e": email, "s": json.dumps(settings)},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO sessions (user_id, token_hash, type, expires_at) "
                "VALUES (:u, :h, 'web', :exp)"
            ),
            {
                "u": user_id,
                "h": hash_token("mesh_rft_entry_test"),
                "exp": datetime.now(UTC) + timedelta(days=1),
            },
        )
    return user_id


def _nonce_values(body: str) -> list[str]:
    return re.findall(r'nonce="([^"]+)"', body)


def _csp_nonce(csp: str) -> str | None:
    match = re.search(r"'nonce-([^']+)'", csp)
    return match.group(1) if match else None


async def test_anonymous_shell_has_no_injection_and_is_public(client):
    resp = await client.get("/", headers={"accept": "text/html"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "__MESH_APPEARANCE__" not in resp.text
    assert resp.headers["cache-control"] == "public, max-age=300"
    assert "Cookie" in resp.headers.get("vary", "")
    csp = resp.headers["content-security-policy"]
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    # The static shell FOUC script stays byte-identical (no nonce attribute)
    # so shared caches serve one canonical copy.
    assert "<script>\n      (function" in resp.text


async def test_session_cookie_injects_dark_with_nonce_csp(app, client):
    await _seed_session_user(app.state.session_factory, email="e2@corp.com", theme="dark")
    resp = await client.get(
        "/settings",
        headers={"accept": "text/html", "cookie": "mesh_session=mesh_rft_entry_test"},
    )
    assert resp.status_code == 200
    assert 'window.__MESH_APPEARANCE__ = {"mode": "dark"};' in resp.text
    assert resp.headers["cache-control"] == "private, no-store"
    csp = resp.headers["content-security-policy"]
    header_nonce = _csp_nonce(csp)
    assert header_nonce is not None
    body_nonces = _nonce_values(resp.text)
    # Both inline scripts (injected data + FOUC resolver) carry the CSP nonce;
    # the type=module src script does not need one (script-src 'self').
    assert body_nonces.count(header_nonce) == 2
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]


async def test_session_cookie_injects_light(app, client):
    await _seed_session_user(app.state.session_factory, email="e3@corp.com", theme="light")
    resp = await client.get(
        "/",
        headers={"accept": "text/html", "cookie": "mesh_session=mesh_rft_entry_test"},
    )
    assert 'window.__MESH_APPEARANCE__ = {"mode": "light"};' in resp.text
    assert resp.headers["cache-control"] == "private, no-store"


async def test_invalid_cookie_falls_back_to_static_shell(client):
    resp = await client.get(
        "/",
        headers={"accept": "text/html", "cookie": "mesh_session=mesh_rft_ghost"},
    )
    assert resp.status_code == 200
    assert "__MESH_APPEARANCE__" not in resp.text
    assert resp.headers["cache-control"] == "public, max-age=300"


async def test_invalid_persisted_theme_is_never_injected(app, client):
    # Binary convergence (theme.md §5.3): a corrupted persisted value must not
    # reach the DOM; the shell is still personalized (private, no-store).
    await _seed_session_user(app.state.session_factory, email="e4@corp.com", theme="evil")
    resp = await client.get(
        "/",
        headers={"accept": "text/html", "cookie": "mesh_session=mesh_rft_entry_test"},
    )
    assert "__MESH_APPEARANCE__" not in resp.text
    assert resp.headers["cache-control"] == "private, no-store"


async def test_nonce_is_fresh_per_request(app, client):
    await _seed_session_user(app.state.session_factory, email="e5@corp.com", theme="dark")
    headers = {"accept": "text/html", "cookie": "mesh_session=mesh_rft_entry_test"}
    first = await client.get("/", headers=headers)
    second = await client.get("/", headers=headers)
    assert _csp_nonce(first.headers["content-security-policy"]) != _csp_nonce(
        second.headers["content-security-policy"]
    )


async def test_workspace_slug_default_injected(app, client):
    # User without preference + /w/{slug}/ route → workspace default (level 2).
    await _seed_session_user(app.state.session_factory, email="e6@corp.com")
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO workspaces (name, slug, settings) "
                "VALUES ('Entry WS', 'entry-ws', CAST(:s AS jsonb))"
            ),
            {"s": json.dumps({"default_theme": "dark"})},
        )
    resp = await client.get(
        "/w/entry-ws/board",
        headers={"accept": "text/html", "cookie": "mesh_session=mesh_rft_entry_test"},
    )
    assert 'window.__MESH_APPEARANCE__ = {"mode": "dark"};' in resp.text


async def test_invite_entry_injects_from_preview_without_session(app, client):
    # Unauthenticated invite-accept entry: level 2 via the invitation-preview
    # same-source data (theme.md §2.2). No session → still public-cacheable.
    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    user_id = await _seed_session_user(app.state.session_factory, email="e7@corp.com")
    user = User(id=user_id, email="e7@corp.com", display_name="U")
    created = await app.state.workspace_service.create_workspace(
        user=user, name="Invite WS", slug="invite-ws"
    )
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspaces SET settings = coalesce(settings, '{}'::jsonb) "
                "|| '{\"default_theme\": \"dark\"}'::jsonb WHERE id = :id"
            ),
            {"id": created["id"]},
        )
        member_id = (
            await session.execute(
                text("SELECT id FROM members WHERE workspace_id = :w"),
                {"w": created["id"]},
            )
        ).scalar_one()
    admin = Member(id=member_id, workspace_id=created["id"], user_id=user_id, role="owner")
    invitation = (
        await app.state.invitation_service.create_invitations(
            actor=admin, workspace_id=created["id"]
        )
    )[0]
    token = invitation["invite_link"].rsplit("/", 1)[-1]

    resp = await client.get(f"/invite/{token}", headers={"accept": "text/html"})
    assert resp.status_code == 200
    assert 'window.__MESH_APPEARANCE__ = {"mode": "dark"};' in resp.text
    assert resp.headers["cache-control"] == "public, max-age=300"


async def test_api_routes_are_not_shadowed(client):
    resp = await client.get("/api/v1/ping")
    assert resp.status_code == 200
    assert resp.json() == {"data": {"pong": True}}


async def test_unknown_api_path_is_404_not_html(client):
    resp = await client.get(
        "/api/v1/definitely-not-a-route", headers={"accept": "text/html"}
    )
    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")


async def test_non_html_accept_is_404(client):
    resp = await client.get("/", headers={"accept": "application/json"})
    assert resp.status_code == 404


async def test_missing_dist_dir_degrades_to_404(db_url, redis_url, tmp_path):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-entry-test-signing-secret-000000",
        frontend_dist_dir=str(tmp_path / "absent"),
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/", headers={"accept": "text/html"})
    assert resp.status_code == 404
    # The API itself is unaffected.
    transport2 = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport2, base_url="http://t") as c:
        assert (await c.get("/api/v1/ping")).status_code == 200
    await app.state.redis.aclose()
    await app.state.engine.dispose()


async def test_real_login_refresh_flows_through_cookie(app, client):
    # End-to-end with the real credential pipeline: register + login (current
    # Bearer model returns the refresh in the body); presenting it as the
    # HttpOnly mesh_session cookie the entry middleware reads.
    await client.post(
        "/api/v1/auth/register",
        json={"email": "entry-real@corp.com", "password": PASSWORD, "display_name": "E"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": "entry-real@corp.com", "password": PASSWORD}
    )
    refresh = login.json()["data"]["refresh_token"]

    # No preference yet → no injection, but personalized (private, no-store).
    resp = await client.get(
        "/", headers={"accept": "text/html", "cookie": f"mesh_session={refresh}"}
    )
    assert "__MESH_APPEARANCE__" not in resp.text
    assert resp.headers["cache-control"] == "private, no-store"

    # Write the preference through the real endpoint, then re-request.
    patch = await client.patch(
        "/api/v1/users/me",
        json={"settings": {"theme": "dark"}},
        headers={"authorization": f"Bearer {login.json()['data']['access_token']}"},
    )
    assert patch.status_code == 200
    resp = await client.get(
        "/", headers={"accept": "text/html", "cookie": f"mesh_session={refresh}"}
    )
    assert 'window.__MESH_APPEARANCE__ = {"mode": "dark"};' in resp.text
