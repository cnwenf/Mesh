"""Attachment HTTP API tests — in-process app, real PG + real MinIO (§3/§6.14)."""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.config import load_settings

pytestmark = pytest.mark.unit

PASSWORD = "S3cure-passw0rd!"
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _make_png() -> bytes:
    from tests.unit.attachment_support import make_png

    return make_png()


PNG = _make_png()


@pytest.fixture
def app(db_url, redis_url, attachment_settings_kwargs):
    from mesh.api.app import create_app

    return create_app(load_settings(**attachment_settings_kwargs))


@pytest_asyncio.fixture
async def client(app, object_storage):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield http
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Attach Tester"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token: str, slug: str) -> str:
    response = await client.post(
        "/api/v1/workspaces", json={"name": "Attachment WS", "slug": slug}, headers=_auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def _seed_issue(session_factory, workspace_id: uuid.UUID) -> uuid.UUID:
    from tests.unit.attachment_support import seed_issue

    class _WS:
        id = workspace_id

    issue = await seed_issue(session_factory, _WS())
    return issue.id


async def _request_upload(client, token, workspace_id, *, name="pic.png", mime="image/png",
                          data: bytes = PNG, link_to=None, headers=None):
    body = {
        "workspace_id": workspace_id,
        "file_name": name,
        "file_size": len(data),
        "mime_type": mime,
    }
    if link_to is not None:
        body["link_to"] = link_to
    response = await client.post(
        "/api/v1/attachments/upload-requests",
        json=body,
        headers=_auth(token) | (headers or {}),
    )
    return response


# ----------------------------------------------------------------------
# three-stage flow over HTTP (§3.1–§3.3)
# ----------------------------------------------------------------------


async def test_full_http_flow_envelope_and_scan_gate(client, session_factory):
    token = await _register_and_login(client, f"flow-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")

    response = await _request_upload(client, token, workspace_id)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["upload_status"] == "pending"
    assert response.headers.get("X-RateLimit-Limit") == "60"  # §3.0 rate limited
    attachment_id = data["id"]

    # Direct PUT to object storage (bytes never transit the API).
    async with httpx.AsyncClient() as external:
        put = await external.put(
            data["upload"]["url"], content=PNG, headers={"Content-Type": "image/png"}
        )
        assert put.status_code == 200

    done = await client.post(
        f"/api/v1/attachments/{attachment_id}/complete", json={}, headers=_auth(token)
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["scan_status"] == "pending"

    # T14: download refused while quarantined — exact §3.5 envelope code.
    refused = await client.get(
        f"/api/v1/attachments/{attachment_id}/download", headers=_auth(token)
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "scan_pending"

    # Metadata reads fine and carries the JOINed snapshots (§2.3/§6.1).
    meta = await client.get(f"/api/v1/attachments/{attachment_id}", headers=_auth(token))
    assert meta.status_code == 200
    assert meta.json()["data"]["uploader"]["member_type"] == "human"
    assert meta.json()["data"]["scan_status"] == "pending"


async def test_idempotency_key_replays_first_record(client):
    token = await _register_and_login(client, f"idem-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    key = str(uuid.uuid4())
    first = await _request_upload(client, token, workspace_id, headers={"Idempotency-Key": key})
    second = await _request_upload(client, token, workspace_id, headers={"Idempotency-Key": key})
    assert first.status_code == 201
    assert first.json()["data"]["id"] == second.json()["data"]["id"]


async def test_workspace_derived_from_link_target(client, session_factory):
    token = await _register_and_login(client, f"link-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    issue_id = await _seed_issue(session_factory, uuid.UUID(workspace_id))

    response = await client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "file_name": "pic.png",
            "file_size": len(PNG),
            "mime_type": "image/png",
            "link_to": {"type": "issue", "id": str(issue_id), "display": "inline"},
        },
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    attachment_id = data["id"]

    # Pending uploads are NOT listed on the host (upload gate, §4.2).
    before = await client.get(f"/api/v1/issues/{issue_id}/attachments", headers=_auth(token))
    assert before.json()["data"] == []

    # Finish the three-stage flow, then the attachment appears.
    async with httpx.AsyncClient() as external:
        put = await external.put(
            data["upload"]["url"], content=PNG, headers={"Content-Type": "image/png"}
        )
        assert put.status_code == 200
    done = await client.post(
        f"/api/v1/attachments/{attachment_id}/complete", json={}, headers=_auth(token)
    )
    assert done.status_code == 200

    listing = await client.get(
        f"/api/v1/issues/{issue_id}/attachments", headers=_auth(token)
    )
    assert listing.status_code == 200
    items = listing.json()["data"]
    assert [i["id"] for i in items] == [attachment_id]
    assert items[0]["links"][0]["display"] == "inline"


async def test_upload_request_requires_workspace_without_link(client):
    token = await _register_and_login(client, f"nows-{uuid.uuid4().hex[:8]}@x.io")
    response = await client.post(
        "/api/v1/attachments/upload-requests",
        json={"file_name": "pic.png", "file_size": len(PNG), "mime_type": "image/png"},
        headers=_auth(token),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_workspace_less_gate_helper_contract(monkeypatch):
    """Direct helper-level contract for the workspace-less attachment paths
    (workspace.md §5.3): the membership gate runs AFTER the SECURITY
    DEFINER resolver, its 404 is rewritten to the resource message, and a
    passing gate returns (caller, member, workspace_id) untouched."""
    import mesh.attachment.routes as routes
    from mesh.errors import NotFoundError

    workspace_id = uuid.uuid4()
    sentinel_member = object()

    class _FakeService:
        def __init__(self, resolved):
            self._resolved = resolved

        async def resolve_attachment_workspace(self, attachment_id):
            return self._resolved

    class _FakeRequest:
        def __init__(self, resolved):
            self.app = type("_App", (), {"state": type("_S", (), {
                "attachment_service": _FakeService(resolved),
            })()})()

    async def _fake_authenticate(request, session_factory):
        return routes.Caller(user=object(), token=None)

    def _fake_gate_factory(exc=None):
        async def _fake_gate(session, caller, wid, permission=None):
            assert wid == workspace_id
            if exc is not None:
                raise exc
            return sentinel_member

        return _fake_gate

    monkeypatch.setattr(routes, "authenticate", _fake_authenticate)
    monkeypatch.setattr(routes, "get_session_factory", lambda request: None)

    # Passing gate → the resolved context is returned untouched.
    monkeypatch.setattr(routes, "gate_workspace", _fake_gate_factory())
    caller, member, resolved = await routes._resolve_attachment_context(
        _FakeRequest(workspace_id), None, uuid.uuid4()
    )
    assert member is sentinel_member
    assert resolved == workspace_id

    # Gate 404 (exists somewhere, caller not a member) → resource message,
    # indistinguishable from an unknown id.
    monkeypatch.setattr(
        routes, "gate_workspace", _fake_gate_factory(NotFoundError("workspace not found"))
    )
    with pytest.raises(NotFoundError) as excinfo:
        await routes._resolve_attachment_context(_FakeRequest(workspace_id), None, uuid.uuid4())
    assert excinfo.value.message == "attachment not found"

    # Unknown attachment → resolver None → same resource message.
    with pytest.raises(NotFoundError) as excinfo:
        await routes._resolve_attachment_context(_FakeRequest(None), None, uuid.uuid4())
    assert excinfo.value.message == "attachment not found"


async def test_unknown_attachment_is_404_envelope(client):
    token = await _register_and_login(client, f"nf-{uuid.uuid4().hex[:8]}@x.io")
    response = await client.get(f"/api/v1/attachments/{uuid.uuid4()}", headers=_auth(token))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    bad_shape = await client.get("/api/v1/attachments/not-a-uuid", headers=_auth(token))
    assert bad_shape.status_code == 404


async def test_cross_workspace_read_is_uniform_404(client):
    owner_token = await _register_and_login(client, f"own-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, owner_token, f"att-{uuid.uuid4().hex[:10]}")
    response = await _request_upload(client, owner_token, workspace_id)
    attachment_id = response.json()["data"]["id"]

    # A user with no membership (another workspace's owner) sees a uniform
    # 404 — invisible and missing are indistinguishable (§5.3), down to the
    # message text: the membership gate 404 is rewritten to the resource
    # message, not "workspace not found".
    outsider_token = await _register_and_login(client, f"out-{uuid.uuid4().hex[:8]}@x.io")
    await _create_workspace(client, outsider_token, f"att-{uuid.uuid4().hex[:10]}")
    denied = await client.get(
        f"/api/v1/attachments/{attachment_id}", headers=_auth(outsider_token)
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "not_found"
    assert denied.json()["error"]["message"] == "attachment not found"
    unknown = await client.get(f"/api/v1/attachments/{uuid.uuid4()}", headers=_auth(outsider_token))
    assert unknown.status_code == 404
    assert unknown.json()["error"]["message"] == "attachment not found"
    assert workspace_id


async def test_agent_api_token_uses_same_endpoints(client, session_factory):
    """§5.3 core difference: agent runtime PAT over the same upload path."""
    token = await _register_and_login(client, f"agt-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")

    pat_response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/api-tokens",
        json={"name": "runtime-token", "scopes": ["issue:read", "issue:write"]},
        headers=_auth(token),
    )
    assert pat_response.status_code == 201, pat_response.text
    secret = pat_response.json()["data"]["token"]

    response = await _request_upload(client, secret, workspace_id, name="artifact.png")
    assert response.status_code == 201, response.text
    attachment_id = response.json()["data"]["id"]
    meta = await client.get(f"/api/v1/attachments/{attachment_id}", headers=_auth(secret))
    assert meta.status_code == 200

    # A PAT scoped to workspace A cannot touch workspace B (uniform 404).
    other_token = await _register_and_login(client, f"oth-{uuid.uuid4().hex[:8]}@x.io")
    other_workspace = await _create_workspace(client, other_token, f"att-{uuid.uuid4().hex[:10]}")
    foreign = await client.get(
        f"/api/v1/attachments/{attachment_id}", headers=_auth(
            (await client.post(
                f"/api/v1/workspaces/{other_workspace}/api-tokens",
                json={"name": "other-token", "scopes": ["issue:read"]},
                headers=_auth(other_token),
            )).json()["data"]["token"]
        )
    )
    assert foreign.status_code == 404


async def test_unauthenticated_is_401(client):
    response = await client.post(
        "/api/v1/attachments/upload-requests",
        json={"file_name": "x.png", "file_size": 1, "mime_type": "image/png"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_delete_emits_attachment_deleted(client, session_factory):
    token = await _register_and_login(client, f"del-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    issue_id = await _seed_issue(session_factory, uuid.UUID(workspace_id))
    response = await _request_upload(
        client, token, workspace_id, link_to={"type": "issue", "id": str(issue_id)}
    )
    attachment_id = response.json()["data"]["id"]

    deleted = await client.delete(
        f"/api/v1/attachments/{attachment_id}", headers=_auth(token)
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True

    from mesh.db.models.outbox import OutboxEvent  # noqa: PLC0415

    async with session_factory() as session:
        events = (await session.scalars(select(OutboxEvent))).all()
        deleted_events = [
            e for e in events
            if e.event_type == "realtime.publish"
            and e.payload.get("event") == "attachment.deleted"
        ]
        assert len(deleted_events) == 1
        assert deleted_events[0].payload["channel"] == f"issue:{issue_id}"

    gone = await client.get(f"/api/v1/attachments/{attachment_id}", headers=_auth(token))
    assert gone.status_code == 404


async def test_comment_listing_is_404_before_module_lands(client):
    token = await _register_and_login(client, f"cmt-{uuid.uuid4().hex[:8]}@x.io")
    response = await client.get(
        f"/api/v1/comments/{uuid.uuid4()}/attachments", headers=_auth(token)
    )
    assert response.status_code == 404


async def test_validation_errors_through_http(client):
    token = await _register_and_login(client, f"val-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    too_big = await _request_upload(
        client, token, workspace_id, data=b"\x00", name="huge.png"
    )
    # file_size comes from the body; craft an oversized declaration directly.
    oversized = await client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "workspace_id": workspace_id,
            "file_name": "huge.png",
            "file_size": 500 * 1024 * 1024,
            "mime_type": "image/png",
        },
        headers=_auth(token),
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "file_too_large"
    bad_mime = await client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "workspace_id": workspace_id,
            "file_name": "run.exe",
            "file_size": 10,
            "mime_type": "application/x-msdownload",
        },
        headers=_auth(token),
    )
    assert bad_mime.status_code == 415
    assert too_big.status_code == 201  # 1-byte png is fine


# ----------------------------------------------------------------------
# multipart / abort / download / thumbnail over HTTP (§2.5 / §3.4)
# ----------------------------------------------------------------------


async def test_multipart_http_flow_completes_and_quarantines(
    db_url, redis_url, attachment_settings_kwargs, object_storage
):
    # Dedicated app with a low multipart threshold/part size (MinIO's minimum
    # part is 5 MB) so the chunked path is exercised without 64 MB payloads.
    from mesh.api.app import create_app

    mp_app = create_app(load_settings(**{
        **attachment_settings_kwargs,
        "attachment_multipart_threshold": 5 * 1024 * 1024,
        "attachment_multipart_part_bytes": 5 * 1024 * 1024,
    }))
    transport = httpx.ASGITransport(app=mp_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await _multipart_flow(client)
    await mp_app.state.redis.aclose()
    await mp_app.state.engine.dispose()


async def _multipart_flow(client):
    token = await _register_and_login(client, f"mp-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    part_size = 5 * 1024 * 1024
    body = b"m" * part_size + b"n" * 2048  # two parts: 5 MB + 2 KB

    requested = await client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "workspace_id": workspace_id,
            "file_name": "bundle.zip",
            "file_size": len(body),
            "mime_type": "application/zip",
        },
        headers=_auth(token),
    )
    assert requested.status_code == 201, requested.text
    data = requested.json()["data"]
    assert "upload_id" in data["upload"]
    attachment_id = data["id"]

    # Sign the next batch through the dedicated endpoint.
    parts_endpoint = await client.post(
        f"/api/v1/multipart/{attachment_id}/parts",
        json={"part_numbers": [2]},
        headers=_auth(token),
    )
    assert parts_endpoint.status_code == 200, parts_endpoint.text
    urls = {p["part_number"]: p["url"] for p in parts_endpoint.json()["data"]["part_urls"]}

    # PUT both parts straight to object storage, collecting ETags.
    etags = []
    async with httpx.AsyncClient(timeout=60) as external:
        first_url = data["upload"]["part_urls"][0]["url"]
        r1 = await external.put(first_url, content=body[:part_size])
        assert r1.status_code == 200
        etags.append({"part_number": 1, "etag": r1.headers["ETag"]})
        r2 = await external.put(urls[2], content=body[part_size:])
        assert r2.status_code == 200
        etags.append({"part_number": 2, "etag": r2.headers["ETag"]})

    merged = await client.post(
        f"/api/v1/multipart/{attachment_id}/complete",
        json={"parts": etags},
        headers=_auth(token),
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["data"]["upload_status"] == "completed"
    assert merged.json()["data"]["scan_status"] == "pending"

    # Plain complete on a multipart attachment is a conflict (wrong endpoint).
    again = await client.post(
        f"/api/v1/attachments/{attachment_id}/complete", json={}, headers=_auth(token)
    )
    assert again.status_code == 409


async def test_abort_http_flow(client):
    token = await _register_and_login(client, f"ab-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    requested = await _request_upload(client, token, workspace_id)
    attachment_id = requested.json()["data"]["id"]

    aborted = await client.post(
        f"/api/v1/attachments/{attachment_id}/abort", headers=_auth(token)
    )
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()["data"]["upload_status"] == "failed"

    # Second abort → state machine conflict.
    again = await client.post(
        f"/api/v1/attachments/{attachment_id}/abort", headers=_auth(token)
    )
    assert again.status_code == 409


async def test_download_and_thumbnail_after_scan_release(client, app, session_factory):
    token = await _register_and_login(client, f"dl-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    requested = await _request_upload(client, token, workspace_id)
    data = requested.json()["data"]
    attachment_id = data["id"]
    async with httpx.AsyncClient() as external:
        put = await external.put(
            data["upload"]["url"], content=PNG, headers={"Content-Type": "image/png"}
        )
        assert put.status_code == 200
    await client.post(
        f"/api/v1/attachments/{attachment_id}/complete", json={}, headers=_auth(token)
    )

    # Run the real quarantine pipeline in-process (worker brain, §3.3).
    from mesh.attachment.processing import claim_pending_blobs, process_blob

    async with session_factory() as session, session.begin():
        for blob in await claim_pending_blobs(session, batch=5):
            await process_blob(
                session, blob,
                storage=app.state.storage, settings=app.state.settings,
            )

    downloaded = await client.get(
        f"/api/v1/attachments/{attachment_id}/download", headers=_auth(token)
    )
    assert downloaded.status_code == 200, downloaded.text
    url = downloaded.json()["data"]["url"]
    async with httpx.AsyncClient() as external:
        got = await external.get(url)
        assert got.status_code == 200
        assert got.content == PNG
        # Images download inline; the sniffed MIME is authoritative.
        assert got.headers["content-type"].startswith("image/png")

    thumb = await client.get(
        f"/api/v1/attachments/{attachment_id}/thumbnail?size=sm", headers=_auth(token)
    )
    assert thumb.status_code == 200, thumb.text
    async with httpx.AsyncClient() as external:
        got = await external.get(thumb.json()["data"]["url"])
        assert got.status_code == 200
        assert got.content[:8] == PNG_HEADER

    bad_size = await client.get(
        f"/api/v1/attachments/{attachment_id}/thumbnail?size=xxl", headers=_auth(token)
    )
    assert bad_size.status_code == 400


async def test_thumbnail_on_non_image_is_404(client, app, session_factory):
    token = await _register_and_login(client, f"ti-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    text_bytes = b"just some plain text\n"
    requested = await _request_upload(
        client, token, workspace_id, name="notes.txt", mime="text/plain", data=text_bytes
    )
    data = requested.json()["data"]
    attachment_id = data["id"]
    async with httpx.AsyncClient() as external:
        put = await external.put(
            data["upload"]["url"], content=text_bytes, headers={"Content-Type": "text/plain"}
        )
        assert put.status_code == 200
    await client.post(
        f"/api/v1/attachments/{attachment_id}/complete", json={}, headers=_auth(token)
    )
    from mesh.attachment.processing import claim_pending_blobs, process_blob

    async with session_factory() as session, session.begin():
        for blob in await claim_pending_blobs(session, batch=5):
            await process_blob(
                session, blob, storage=app.state.storage, settings=app.state.settings,
            )
    thumb = await client.get(
        f"/api/v1/attachments/{attachment_id}/thumbnail", headers=_auth(token)
    )
    assert thumb.status_code == 404
    # Text whitelist → skipped → download opens.
    downloaded = await client.get(
        f"/api/v1/attachments/{attachment_id}/download", headers=_auth(token)
    )
    assert downloaded.status_code == 200


async def test_upload_request_link_to_missing_issue_is_404(client):
    token = await _register_and_login(client, f"li-{uuid.uuid4().hex[:8]}@x.io")
    await _create_workspace(client, token, f"att-{uuid.uuid4().hex[:10]}")
    response = await client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "file_name": "pic.png",
            "file_size": len(PNG),
            "mime_type": "image/png",
            "link_to": {"type": "issue", "id": str(uuid.uuid4())},
        },
        headers=_auth(token),
    )
    assert response.status_code == 404


async def test_prefixless_endpoints_uniform_404_message(client, session_factory):
    """L3 product-wide parity (workspace.md §5.3): every attachment path
    whose tenant is resolved from the resource itself returns the SAME 404
    message for "unknown id" and "exists in another tenant" — the gate 404
    is rewritten to the resource message, so no existence oracle survives.
    The explicit-workspace branch of upload-request is the exception BY
    DESIGN: the caller names the workspace, so it keeps "workspace not
    found" exactly like require_workspace."""
    from mesh.db.models.comment import Comment

    owner_a = await _register_and_login(client, f"l3a-{uuid.uuid4().hex[:8]}@x.io")
    owner_b = await _register_and_login(client, f"l3b-{uuid.uuid4().hex[:8]}@x.io")
    await _create_workspace(client, owner_a, f"att-{uuid.uuid4().hex[:10]}")
    ws_b = await _create_workspace(client, owner_b, f"att-{uuid.uuid4().hex[:10]}")

    # Tenant-B resources: a pending attachment record, an issue, a comment.
    attachment_b = (await _request_upload(client, owner_b, ws_b)).json()["data"]["id"]
    issue_b = await _seed_issue(session_factory, uuid.UUID(ws_b))
    async with session_factory() as session, session.begin():
        # author_kind='system' satisfies the identity CHECK without needing
        # a roster row; the resolver only cares that the row exists.
        comment = Comment(
            workspace_id=uuid.UUID(ws_b),
            issue_id=issue_b,
            author_kind="system",
            body_markdown="x",
        )
        session.add(comment)
        await session.flush()
        comment_b = comment.id
    random_id = str(uuid.uuid4())

    def upload_body(link_id: str | None = None, workspace_id: str | None = None) -> dict:
        body = {"file_name": "pic.png", "file_size": len(PNG), "mime_type": "image/png"}
        if link_id is not None:
            body["link_to"] = {"type": "issue", "id": link_id}
        if workspace_id is not None:
            body["workspace_id"] = workspace_id
        return body

    probes = (
        # (existing-id probe, existing id, resource message)
        (
            lambda target: client.get(f"/api/v1/attachments/{target}", headers=_auth(owner_a)),
            attachment_b,
            "attachment not found",
        ),
        (
            lambda target: client.get(
                f"/api/v1/issues/{target}/attachments", headers=_auth(owner_a)
            ),
            str(issue_b),
            "issue not found",
        ),
        (
            lambda target: client.get(
                f"/api/v1/comments/{target}/attachments", headers=_auth(owner_a)
            ),
            str(comment_b),
            "comment not found",
        ),
        (
            # upload-request with a link_to-derived tenant: gate 404 carries
            # the host resource message
            lambda target: client.post(
                "/api/v1/attachments/upload-requests",
                json=upload_body(link_id=target),
                headers=_auth(owner_a),
            ),
            str(issue_b),
            "issue not found",
        ),
    )
    for call, existing_id, message in probes:
        existing = await call(existing_id)  # exists, owner_a is NOT a member
        missing = await call(random_id)  # does not exist anywhere
        assert existing.status_code == 404, existing.text
        assert missing.status_code == 404, missing.text
        # Both states are indistinguishable and carry the resource message.
        assert existing.json()["error"]["message"] == message
        assert missing.json()["error"]["message"] == message

    # Explicit-workspace branch keeps the workspace 404 (caller named the
    # workspace — same contract as require_workspace).
    explicit = await client.post(
        "/api/v1/attachments/upload-requests",
        json=upload_body(workspace_id=ws_b),
        headers=_auth(owner_a),
    )
    assert explicit.status_code == 404
    assert explicit.json()["error"]["message"] == "workspace not found"

    # Deleted attachment + non-member → same message (whichever layer
    # answers, the contract is one 404 text).
    deleted = await client.delete(f"/api/v1/attachments/{attachment_b}", headers=_auth(owner_b))
    assert deleted.status_code in (200, 204), deleted.text
    deleted_probe = await client.get(
        f"/api/v1/attachments/{attachment_b}", headers=_auth(owner_a)
    )
    assert deleted_probe.status_code == 404
    assert deleted_probe.json()["error"]["message"] == "attachment not found"
