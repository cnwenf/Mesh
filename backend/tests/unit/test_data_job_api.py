"""In-process data-jobs API tests (import-export.md §3.1–§3.6 route layer).

Real create_app() via ASGITransport against real PostgreSQL + Redis:
workspace-less path resolution (SECURITY DEFINER 0020), the two-phase
import contract over HTTP, export creation guards, owner/admin gates,
idempotency and rate-limit headers (§3.0 / §6.14).
"""

from __future__ import annotations

import hashlib
import uuid

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text

from mesh.api.app import create_app
from mesh.config import load_settings
from mesh.db.models.attachment import Attachment, AttachmentBlob

PASSWORD = "a-strong-passw0rd"

pytestmark = pytest.mark.unit


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-data-jobs-test-secret-000000",
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await app.state.redis.aclose()
    await app.state.engine.dispose()


@pytest.fixture(autouse=True)
async def _flush_redis(redis_url):
    c = aioredis.from_url(redis_url, decode_responses=True)
    await c.flushdb()
    yield
    await c.flushdb()
    await c.aclose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post("/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _promote_admin(session_factory, workspace_id, user_id) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE members SET role='admin' WHERE workspace_id=:ws AND user_id=:u"),
            {"ws": workspace_id, "u": user_id},
        )


async def _member_id(session_factory, workspace_id, user_id) -> uuid.UUID:
    async with session_factory() as session:
        return (
            await session.execute(
                text("SELECT id FROM members WHERE workspace_id=:ws AND user_id=:u"),
                {"ws": workspace_id, "u": user_id},
            )
        ).scalar_one()


async def _seed_source(
    storage, session_factory, workspace_id, member_id, content: bytes = b"Title,Key\na,K1\n"
) -> uuid.UUID:
    """Seed a completed attachment AND its real object in MinIO."""
    blob_key = f"ws/{workspace_id}/00/{uuid.uuid4().hex}"
    await storage.ensure_bucket()
    await storage.put_bytes(blob_key, content, content_type="text/csv")
    async with session_factory() as session, session.begin():
        blob = AttachmentBlob(
            workspace_id=workspace_id,
            content_hash=hashlib.sha256(content).hexdigest(),
            storage_provider="s3",
            storage_bucket="mesh-attachments",
            storage_key=blob_key,
            file_size=len(content),
            scan_status="skipped",
            ref_count=1,
        )
        session.add(blob)
        await session.flush()
        attachment = Attachment(
            workspace_id=workspace_id,
            uploader_id=member_id,
            blob_id=blob.id,
            file_name="issues.csv",
            file_size=len(content),
            upload_status="completed",
        )
        session.add(attachment)
        await session.flush()
        return attachment.id


_MAPPING = {
    "columns": [
        {"source": "Title", "target": "title", "transform": {"type": "direct"}},
        {"source": "Key", "target": "external_ref", "transform": {"type": "direct"}},
    ]
}


async def _setup_admin(client, session_factory, slug="dj-ws"):
    token = await _register_and_login(client, f"admin-{uuid.uuid4().hex[:8]}@mesh.example")
    me = await client.get("/api/v1/me", headers=_auth(token))
    user_id = uuid.UUID(me.json()["data"]["id"])
    workspace = await _create_workspace(client, token, f"{slug}-{uuid.uuid4().hex[:6]}")
    ws_id = uuid.UUID(workspace["id"])
    # creator is owner already; fetch member id
    member_id = await _member_id(session_factory, ws_id, user_id)
    return token, ws_id, member_id


class TestImportEndpoints:
    async def test_create_validate_run_flow(self, client, app, session_factory):
        token, ws_id, member_id = await _setup_admin(client, session_factory)
        source_id = await _seed_source(app.state.storage, session_factory, ws_id, member_id)

        created = await client.post(
            "/api/v1/data-jobs/import",
            json={
                "workspace_id": str(ws_id),
                "entity_type": "issues",
                "format": "csv",
                "source_attachment_id": str(source_id),
                "mapping": _MAPPING,
            },
            headers=_auth(token),
        )
        assert created.status_code == 201, created.text
        job = created.json()["data"]
        assert job["status"] == "pending" and job["kind"] == "import"
        # rate limit headers present (§3.0)
        assert "x-ratelimit-limit" in created.headers

        # run before validate → 422 validation_required
        too_soon = await client.post(f"/api/v1/data-jobs/import/{job['id']}/run", headers=_auth(token))
        assert too_soon.status_code == 422
        assert too_soon.json()["error"]["code"] == "validation_required"

        # validate → validating (worker would process; we simulate completion)
        validated = await client.post(f"/api/v1/data-jobs/import/{job['id']}/validate", headers=_auth(token))
        assert validated.status_code == 200
        assert validated.json()["data"]["status"] == "validating"

        # simulate worker dry-run completion
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE data_jobs SET status='pending', total_rows=1, "
                    "source_content_hash=:h, "
                    'params = params || \'{"validated_at": "2026-01-01T00:00:00Z"}\' '
                    "WHERE id=:id"
                ),
                {"h": hashlib.sha256(b"Title,Key\na,K1\n").hexdigest(), "id": job["id"]},
            )
        ran = await client.post(f"/api/v1/data-jobs/import/{job['id']}/run", headers=_auth(token))
        assert ran.status_code == 202
        assert ran.json()["data"]["status"] == "running"
        # repeated run → 409 conflict
        again = await client.post(f"/api/v1/data-jobs/import/{job['id']}/run", headers=_auth(token))
        assert again.status_code == 409

    async def test_create_rejects_bad_mapping_400(self, client, app, session_factory):
        token, ws_id, member_id = await _setup_admin(client, session_factory)
        source_id = await _seed_source(app.state.storage, session_factory, ws_id, member_id)
        resp = await client.post(
            "/api/v1/data-jobs/import",
            json={
                "workspace_id": str(ws_id),
                "format": "csv",
                "source_attachment_id": str(source_id),
                "mapping": {"columns": [{"source": "T", "target": "nope", "transform": {"type": "direct"}}]},
            },
            headers=_auth(token),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "mapping_invalid"

    async def test_idempotency_key_dedup(self, client, app, session_factory):
        token, ws_id, member_id = await _setup_admin(client, session_factory)
        source_id = await _seed_source(app.state.storage, session_factory, ws_id, member_id)
        body = {
            "workspace_id": str(ws_id),
            "format": "csv",
            "source_attachment_id": str(source_id),
            "mapping": _MAPPING,
        }
        headers = {**_auth(token), "Idempotency-Key": "dj-idem-1"}
        first = await client.post("/api/v1/data-jobs/import", json=body, headers=headers)
        second = await client.post("/api/v1/data-jobs/import", json=body, headers=headers)
        assert first.json()["data"]["id"] == second.json()["data"]["id"]

    async def test_member_cannot_workspace_import(self, client, app, session_factory):
        # a plain member (non-admin, no target project) is refused
        token = await _register_and_login(client, f"mem-{uuid.uuid4().hex[:8]}@mesh.example")
        me = await client.get("/api/v1/me", headers=_auth(token))
        user_id = uuid.UUID(me.json()["data"]["id"])
        workspace = await _create_workspace(client, token, f"djw-{uuid.uuid4().hex[:6]}")
        ws_id = uuid.UUID(workspace["id"])
        # demote the creator to member
        async with session_factory() as session, session.begin():
            await session.execute(
                text("UPDATE members SET role='member' WHERE workspace_id=:ws AND user_id=:u"),
                {"ws": ws_id, "u": user_id},
            )
        member_id = await _member_id(session_factory, ws_id, user_id)
        source_id = await _seed_source(app.state.storage, session_factory, ws_id, member_id)
        resp = await client.post(
            "/api/v1/data-jobs/import",
            json={
                "workspace_id": str(ws_id),
                "format": "csv",
                "source_attachment_id": str(source_id),
                "mapping": _MAPPING,
            },
            headers=_auth(token),
        )
        assert resp.status_code == 403


class TestExportEndpoints:
    async def test_export_create_and_list_and_get(self, client, session_factory):
        token, ws_id, _member_id = await _setup_admin(client, session_factory)
        created = await client.post(
            "/api/v1/data-jobs/export",
            json={"workspace_id": str(ws_id), "scope": "workspace", "format": "csv"},
            headers=_auth(token),
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["data"]["id"]

        listing = await client.get(
            "/api/v1/data-jobs",
            params={"workspace_id": str(ws_id), "kind": "export"},
            headers=_auth(token),
        )
        assert listing.status_code == 200
        ids = [j["id"] for j in listing.json()["data"]]
        assert job_id in ids

        got = await client.get(f"/api/v1/data-jobs/{job_id}", headers=_auth(token))
        assert got.status_code == 200
        assert got.json()["data"]["status"] == "pending"

    async def test_export_workspace_scope_forbidden_for_member(self, client, session_factory):
        token = await _register_and_login(client, f"m2-{uuid.uuid4().hex[:8]}@mesh.example")
        me = await client.get("/api/v1/me", headers=_auth(token))
        user_id = uuid.UUID(me.json()["data"]["id"])
        workspace = await _create_workspace(client, token, f"djx-{uuid.uuid4().hex[:6]}")
        ws_id = uuid.UUID(workspace["id"])
        async with session_factory() as session, session.begin():
            await session.execute(
                text("UPDATE members SET role='member' WHERE workspace_id=:ws AND user_id=:u"),
                {"ws": ws_id, "u": user_id},
            )
        resp = await client.post(
            "/api/v1/data-jobs/export",
            json={"workspace_id": str(ws_id), "scope": "workspace"},
            headers=_auth(token),
        )
        assert resp.status_code == 403

    async def test_export_filter_too_complex_400(self, client, session_factory):
        token, ws_id, _m = await _setup_admin(client, session_factory)
        resp = await client.post(
            "/api/v1/data-jobs/export",
            json={
                "workspace_id": str(ws_id),
                "scope": "workspace",
                "filters": {f"k{i}": "v" for i in range(21)},
            },
            headers=_auth(token),
        )
        assert resp.status_code == 400


class TestOwnershipAndDownload:
    async def test_stranger_gets_403(self, client, app, session_factory):
        token, ws_id, member_id = await _setup_admin(client, session_factory)
        source_id = await _seed_source(app.state.storage, session_factory, ws_id, member_id)
        created = await client.post(
            "/api/v1/data-jobs/import",
            json={
                "workspace_id": str(ws_id),
                "format": "csv",
                "source_attachment_id": str(source_id),
                "mapping": _MAPPING,
            },
            headers=_auth(token),
        )
        job_id = created.json()["data"]["id"]
        # another member of the SAME workspace (non-admin) → 403 forbidden
        # (§3.12 / §5.4: a same-tenant non-owner/admin is a permission failure;
        # only a missing / cross-tenant job is 404).
        other_token = await _register_and_login(client, f"other-{uuid.uuid4().hex[:8]}@mesh.example")
        # join the workspace via invitation is complex; create a member row directly
        other_me = await client.get("/api/v1/me", headers=_auth(other_token))
        other_user = uuid.UUID(other_me.json()["data"]["id"])
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO members (id, workspace_id, member_type, user_id, role, status) "
                    "VALUES (gen_random_uuid(), :ws, 'human', :u, 'member', 'active')"
                ),
                {"ws": ws_id, "u": other_user},
            )
        got = await client.get(f"/api/v1/data-jobs/{job_id}", headers=_auth(other_token))
        assert got.status_code == 403

    async def test_download_without_product_404(self, client, session_factory):
        token, ws_id, _m = await _setup_admin(client, session_factory)
        created = await client.post(
            "/api/v1/data-jobs/export",
            json={"workspace_id": str(ws_id), "scope": "workspace"},
            headers=_auth(token),
        )
        job_id = created.json()["data"]["id"]
        resp = await client.get(f"/api/v1/data-jobs/{job_id}/download", headers=_auth(token))
        assert resp.status_code == 404

    async def test_bad_uuid_is_404_not_400(self, client):
        resp = await client.get("/api/v1/data-jobs/not-a-uuid")
        assert resp.status_code == 401 or resp.status_code == 404

    async def test_list_validates_filters(self, client, session_factory):
        token, ws_id, _m = await _setup_admin(client, session_factory)
        resp = await client.get(
            "/api/v1/data-jobs",
            params={"workspace_id": str(ws_id), "kind": "bogus"},
            headers=_auth(token),
        )
        assert resp.status_code == 400
        resp = await client.get(
            "/api/v1/data-jobs",
            params={"workspace_id": str(ws_id), "status": "bogus"},
            headers=_auth(token),
        )
        assert resp.status_code == 400
