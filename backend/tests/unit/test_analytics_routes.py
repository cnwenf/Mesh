"""Analytics endpoint contract (analytics.md §3): envelopes, error codes,
rate-limit headers, auth gates — real app via ASGITransport."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.unit


def _settings_kwargs(db_url: str, redis_url: str) -> dict:
    return {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "analytics-routes-signing-secret-0000000",
        "daemon_tls_required": False,
        "storage_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        "storage_public_endpoint": os.environ.get(
            "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"
        ),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh"),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret"),
        "storage_bucket": "mesh-analytics-routes-test",
    }


@pytest_asyncio.fixture
async def app_and_client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings_kwargs(db_url, redis_url)))
    try:
        await app.state.storage.ensure_bucket()
    except Exception:  # noqa: BLE001 — storage optional in unit context
        pass
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield app, client


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _world(app, client: httpx.AsyncClient, suffix: str) -> tuple[str, str, str]:
    """Register → login → workspace → agent. Returns (token, ws_id, agent_id)."""
    email = f"an-routes-{suffix}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Routes-Test-12345", "display_name": "AN Routes"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Routes-Test-12345"}
    )
    token = login.json()["data"]["access_token"]
    ws = (
        await client.post(
            "/api/v1/workspaces",
            json={"name": f"AN {suffix}", "slug": f"an-routes-{suffix}"},
            headers=_auth(token),
        )
    ).json()["data"]
    agent = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/agents",
            json={"name": f"an-agent-{suffix}"},
            headers=_auth(token),
        )
    ).json()["data"]
    return token, ws["id"], agent["id"]


async def _seed_done_issue(app, ws_id: str) -> None:
    """One done issue with an in_progress trail → cycle-time sample."""
    import uuid as _uuid

    from sqlalchemy import select

    from mesh.db.models.issue import Issue, IssueActivity, IssueStatus
    from mesh.db.tenant import set_tenant_context

    session_factory = app.state.session_factory
    async with session_factory() as session, session.begin():
        # app 引擎以 mesh_app 角色连接,RLS 生效——写入前必须设租户上下文
        await set_tenant_context(session, _uuid.UUID(ws_id))
        # 复用工作区初始化播种的 done 状态(状态名工作区唯一)
        status = (
            await session.execute(
                select(IssueStatus).where(
                    IssueStatus.workspace_id == _uuid.UUID(ws_id),
                    IssueStatus.category == "done",
                )
            )
        ).scalars().first()
        assert status is not None, "workspace bootstrap should seed a done status"
        session.add(status)
        await session.flush()
        issue = Issue(
            workspace_id=_uuid.UUID(ws_id), title="routes done",
            identifier_namespace_key="inbox", number=1, identifier="inbox-1",
            status_id=status.id, state_category="done",
            completed_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )
        session.add(issue)
        await session.flush()
        session.add(IssueActivity(
            workspace_id=_uuid.UUID(ws_id), issue_id=issue.id,
            field="state_category", old_value="todo", new_value="in_progress",
            created_at=datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
        ))


class TestAuthAndEnvelope:
    async def test_unauthenticated_401(self, app_and_client):
        _app, client = app_and_client
        _, ws_id, _ = await _world(_app, client, "authn")
        resp = await client.get(f"/api/v1/workspaces/{ws_id}/analytics/cycle-time")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"

    async def test_non_member_blocked(self, app_and_client):
        app, client = app_and_client
        _, ws_id, _ = await _world(app, client, "owner")
        # 第二个用户(非成员)
        await client.post(
            "/api/v1/auth/register",
            json={"email": "outsider@example.com", "password": "Routes-Test-12345",
                  "display_name": "Out"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "outsider@example.com", "password": "Routes-Test-12345"},
        )
        out_token = login.json()["data"]["access_token"]
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/cycle-time", headers=_auth(out_token)
        )
        assert resp.status_code in (403, 404)

    async def test_envelope_and_rate_limit_headers(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "env")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/cycle-time", headers=_auth(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["meta"]["display_timezone"]
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers


class TestValidationErrors:
    async def test_invalid_time_range(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "timerange")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/cycle-time"
            "?from=2026-07-08T00:00:00Z&to=2026-07-01T00:00:00Z",
            headers=_auth(token),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_time_range"

    async def test_invalid_granularity(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "gran")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/throughput?granularity=year",
            headers=_auth(token),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "validation_error"

    async def test_invalid_timezone(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "tz")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/throughput?tz=Mars/Olympus",
            headers=_auth(token),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_timezone"

    async def test_burndown_scope_codes(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "bd")
        missing = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/burndown", headers=_auth(token)
        )
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "burndown_scope_required"
        import uuid as _uuid

        conflict = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/burndown"
            f"?cycle_id={_uuid.uuid4()}&milestone_id={_uuid.uuid4()}",
            headers=_auth(token),
        )
        assert conflict.status_code == 400
        assert conflict.json()["error"]["code"] == "burndown_scope_conflict"

    async def test_cycle_ids_limit(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "cyclelimit")
        import uuid as _uuid

        ids = ",".join(str(_uuid.uuid4()) for _ in range(21))
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/velocity?cycle_ids={ids}",
            headers=_auth(token),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "filter_too_complex"

    async def test_invalid_agent_id_query_uuid(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "badagent")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/agents/stats?agent_id=not-a-uuid",
            headers=_auth(token),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "validation_error"

    async def test_project_dashboard_invalid_path_uuid(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "badproj")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/dashboards/project/not-a-uuid",
            headers=_auth(token),
        )
        assert resp.status_code == 404


class TestDataFlows:
    async def test_cycle_time_sample_via_http(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "ct")
        await _seed_done_issue(app, ws_id)
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/cycle-time"
            "?from=2026-07-01T00:00:00Z&to=2026-07-29T00:00:00Z",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sample_size"] == 1
        assert data["p50_seconds"] == 172800  # 2 天

    async def test_throughput_shape(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "tp")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/throughput"
            "?from=2026-07-01T00:00:00Z&to=2026-07-08T00:00:00Z&granularity=day"
            "&calendar_timezone=Asia/Shanghai",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["granularity"] == "day"
        assert data["meta"]["calendar_timezone"] == "Asia/Shanghai"
        assert isinstance(data["series"], list)

    async def test_workload_list_envelope(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "wl")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/workload", headers=_auth(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["data"], list)
        assert body["next_cursor"] is None

    async def test_agent_stats_multi_shape(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "ag")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/analytics/agents/stats"
            "?from=2026-07-01T00:00:00Z&to=2026-07-29T00:00:00Z",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "agents" in data

    async def test_workspace_dashboard_shape(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "wd")
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/dashboards/workspace"
            "?from=2026-07-01T00:00:00Z&to=2026-07-29T00:00:00Z",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "throughput" in data
        assert "workload" in data
        assert "agent_stats" in data
        assert data["meta"]["visibility_filtered"] is False  # creator=owner

    async def test_project_dashboard_shape(self, app_and_client):
        app, client = app_and_client
        token, ws_id, _ = await _world(app, client, "pd")
        import uuid as _uuid

        key = "AN" + _uuid.uuid4().hex[:4].upper()
        resp_create = await client.post(
            f"/api/v1/workspaces/{ws_id}/projects",
            json={"name": "Dash Project", "key": key},
            headers=_auth(token),
        )
        assert resp_create.status_code == 201, resp_create.text
        project = resp_create.json()["data"]
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/dashboards/project/{project['id']}"
            "?from=2026-07-01T00:00:00Z&to=2026-07-29T00:00:00Z",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["project_id"] == project["id"]
        assert "velocity" in data and "cycle_time" in data
