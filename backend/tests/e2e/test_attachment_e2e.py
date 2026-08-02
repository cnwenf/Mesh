"""Attachment REAL end-to-end tests (attachment.md §5 acceptance).

Real uvicorn API subprocess + REAL worker subprocess (relay + quarantine
pipeline) + real PostgreSQL + real MinIO. No mocks on any contract path:

- three-stage direct upload — signed PUT straight to MinIO, complete, worker
  processes the quarantine and releases the blob;
- T14 visibility gate — quarantined content is refused until released;
- infected content — permanent refusal with a critical audit entry;
- T24 possession — hash probes never short-circuit unreadable content;
  readable content instant-uploads onto the shared blob (ref_count atomic);
- multi-tenant isolation — foreign credentials learn nothing (uniform 404).
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.db.models.attachment import AttachmentBlob
from mesh.db.models.audit import AuditLog
from tests.conftest import get_test_database_url, get_test_redis_url
from tests.e2e.conftest import _drain_stdout, pin_code_under_test

pytestmark = pytest.mark.e2e

PASSWORD = "S3cure-passw0rd!"
WORKER_READY_WAIT_SECONDS = 4.0


def _make_png(width: int = 48, height: int = 36) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(20, 160, 80)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _make_png()
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client: httpx.AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "E2E Attach"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


async def _create_workspace(client: httpx.AsyncClient, token: str) -> str:
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "E2E Attachments", "slug": f"e2e-att-{uuid.uuid4().hex[:10]}"},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _storage_env() -> dict[str, str]:
    endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    return {
        "MESH_STORAGE_ENDPOINT": endpoint,
        "MESH_STORAGE_PUBLIC_ENDPOINT": endpoint,
        "MESH_STORAGE_ACCESS_KEY": os.environ.get("MESH_STORAGE_ACCESS_KEY", ""),
        "MESH_STORAGE_SECRET_KEY": os.environ.get("MESH_STORAGE_SECRET_KEY", ""),
        "MESH_STORAGE_BUCKET": os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e"),
    }


@pytest_asyncio.fixture(scope="module")
async def attachment_worker(provision_database):
    """A REAL worker subprocess — relay + quarantine pipeline (README §2.2).

    Scan-skip is disabled so the EICAR test exercises the infected verdict
    instead of the plain-text whitelist.

    MODULE scope (not session): the outbox relay polls the shared test
    database while alive; a session-scoped relay would overlap the
    between-test TRUNCATE isolation of LATER, unrelated e2e files, and the
    relay's outbox row locks cycle with TRUNCATE's realtime_events lock
    (deadlock). Module scope keeps the worker alive exactly for this file.
    """
    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_ATTACHMENT_SCAN_SKIP_TEXT"] = "false"
    env["MESH_ATTACHMENT_SCAN_INTERVAL"] = "0.5"
    env.update(_storage_env())
    pin_code_under_test(env)
    process = subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _drain_stdout(process)
    await asyncio.sleep(WORKER_READY_WAIT_SECONDS)
    assert process.poll() is None, "worker process died on startup"
    yield process
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def _upload_three_stage(
    client: httpx.AsyncClient, token: str, workspace_id: str,
    *, data: bytes, name: str, mime: str, content_hash: str | None = None,
) -> dict:
    """Drive the full client-side flow: request → PUT → complete."""
    import hashlib

    body = {
        "workspace_id": workspace_id,
        "file_name": name,
        "file_size": len(data),
        "mime_type": mime,
        "content_hash": content_hash or hashlib.sha256(data).hexdigest(),
    }
    requested = await client.post(
        "/api/v1/attachments/upload-requests", json=body, headers=_auth(token)
    )
    assert requested.status_code == 201, requested.text
    payload = requested.json()["data"]
    if payload["upload"] is not None:
        async with httpx.AsyncClient(timeout=30) as external:
            put = await external.put(
                payload["upload"]["url"], content=data,
                headers=payload["upload"]["headers"],
            )
            assert put.status_code == 200, put.text
        done = await client.post(
            f"/api/v1/attachments/{payload['id']}/complete", json={}, headers=_auth(token)
        )
        assert done.status_code == 200, done.text
        payload = done.json()["data"]
    return payload


async def _wait_scan_status(
    client: httpx.AsyncClient, token: str, attachment_id: str,
    expected: frozenset[str], *, timeout: float = 30.0,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = await client.get(
            f"/api/v1/attachments/{attachment_id}", headers=_auth(token)
        )
        assert response.status_code == 200, response.text
        last = response.json()["data"]
        if last["scan_status"] in expected:
            return last
        await asyncio.sleep(0.3)
    raise AssertionError(f"scan_status never reached {expected}: last={last}")


async def test_full_pipeline_real_worker_releases_clean_image(
    api_client, attachment_worker, session_factory
):
    token = await _register_and_login(api_client, f"e2e-clean-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)

    attachment = await _upload_three_stage(
        api_client, token, workspace_id, data=PNG, name="graph.png", mime="image/png"
    )
    assert attachment["scan_status"] == "pending"

    # The REAL worker picks up the quarantine row and releases it.
    released = await _wait_scan_status(
        api_client, token, attachment["id"], frozenset({"clean"})
    )
    assert released["mime_type"] == "image/png"  # magic-byte sniffed server-side
    assert released["is_image"] is True
    assert released["thumbnail_url"] is not None

    # Download through the short-lived signed URL returns the exact bytes.
    download = await api_client.get(
        f"/api/v1/attachments/{attachment['id']}/download", headers=_auth(token)
    )
    assert download.status_code == 200, download.text
    async with httpx.AsyncClient(timeout=30) as external:
        got = await external.get(download.json()["data"]["url"])
        assert got.status_code == 200
        assert got.content == PNG

    # Thumbnail signed URL serves a real PNG rendition.
    thumb = await api_client.get(
        f"/api/v1/attachments/{attachment['id']}/thumbnail?size=md", headers=_auth(token)
    )
    assert thumb.status_code == 200, thumb.text
    async with httpx.AsyncClient(timeout=30) as external:
        got = await external.get(thumb.json()["data"]["url"])
        assert got.status_code == 200
        assert got.content[:8] == b"\x89PNG\r\n\x1a\n"

    # The blob truth row in the real DB carries the worker's verdict.
    async with session_factory() as session:
        blob = await session.scalar(
            select(AttachmentBlob).where(AttachmentBlob.id == uuid.UUID(released["blob_id"]))
        )
        assert blob.scan_status == "clean"
        assert blob.ref_count == 1
        assert set(blob.thumbnail_keys) == {"sm", "md", "lg"}


async def test_t14_quarantine_refuses_until_released(api_client, attachment_worker):
    token = await _register_and_login(api_client, f"e2e-t14-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)

    # Request + PUT but NEVER complete: the blob stays in quarantine forever,
    # so the gate check below is deterministic (no race with the worker).
    requested = await api_client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "workspace_id": workspace_id,
            "file_name": "held.png",
            "file_size": len(PNG),
            "mime_type": "image/png",
        },
        headers=_auth(token),
    )
    payload = requested.json()["data"]
    async with httpx.AsyncClient(timeout=30) as external:
        put = await external.put(
            payload["upload"]["url"], content=PNG, headers={"Content-Type": "image/png"}
        )
        assert put.status_code == 200

    refused = await api_client.get(
        f"/api/v1/attachments/{payload['id']}/download", headers=_auth(token)
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "scan_pending"
    refused_thumb = await api_client.get(
        f"/api/v1/attachments/{payload['id']}/thumbnail", headers=_auth(token)
    )
    assert refused_thumb.status_code == 403
    assert refused_thumb.json()["error"]["code"] == "scan_pending"


async def test_infected_content_is_permanently_refused_with_critical_audit(
    api_client, attachment_worker, session_factory
):
    token = await _register_and_login(api_client, f"e2e-av-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)

    attachment = await _upload_three_stage(
        api_client, token, workspace_id,
        data=EICAR, name="report.txt", mime="text/plain",
    )
    verdict = await _wait_scan_status(
        api_client, token, attachment["id"], frozenset({"infected"})
    )
    assert verdict["scan_status"] == "infected"

    refused = await api_client.get(
        f"/api/v1/attachments/{attachment['id']}/download", headers=_auth(token)
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "scan_infected"

    async with session_factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "attachment.scan_infected")
        )
        assert audit is not None
        assert audit.metadata_["severity"] == "critical"


async def test_t24_possession_and_shared_blob_ref_count(
    api_client, attachment_worker, session_factory
):
    owner_token = await _register_and_login(api_client, f"e2e-own-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, owner_token)

    original = await _upload_three_stage(
        api_client, owner_token, workspace_id, data=PNG, name="orig.png", mime="image/png"
    )
    await _wait_scan_status(api_client, owner_token, original["id"], frozenset({"clean"}))

    import hashlib

    digest = hashlib.sha256(PNG).hexdigest()

    # A second credential scoped to a DIFFERENT workspace (used for the
    # negative RED-LINE assertion below).
    probe_token = await _register_and_login(api_client, f"e2e-probe-{uuid.uuid4().hex[:8]}@x.io")
    await _create_workspace(api_client, probe_token)

    # POSITIVE: the owner already reads the blob → instant upload, bytes skipped.
    instant = await api_client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "workspace_id": workspace_id,
            "file_name": "instant.png",
            "file_size": len(PNG),
            "mime_type": "image/png",
            "content_hash": digest,
        },
        headers=_auth(owner_token),
    )
    assert instant.status_code == 201, instant.text
    instant_data = instant.json()["data"]
    assert instant_data["upload"] is None  # bytes skipped — possession held
    assert instant_data["id"] != original["id"]  # independent record
    assert instant_data["blob_id"] == original["blob_id"]  # shared blob truth

    async with session_factory() as session:
        blob = await session.scalar(
            select(AttachmentBlob).where(AttachmentBlob.id == uuid.UUID(original["blob_id"]))
        )
        assert blob.ref_count == 2

    # Deleting the copy leaves the original fully intact (independent delete).
    deleted = await api_client.delete(
        f"/api/v1/attachments/{instant_data['id']}", headers=_auth(owner_token)
    )
    assert deleted.status_code == 200
    still = await api_client.get(
        f"/api/v1/attachments/{original['id']}/download", headers=_auth(owner_token)
    )
    assert still.status_code == 200
    async with session_factory() as session:
        blob = await session.scalar(
            select(AttachmentBlob).where(AttachmentBlob.id == uuid.UUID(original["blob_id"]))
        )
        assert blob.ref_count == 1

    # NEGATIVE (RED LINE): a credential from ANOTHER workspace cannot probe or
    # reuse the content by hash — uniform 404, no short-circuit, no leak.
    foreign_request = await api_client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "workspace_id": workspace_id,  # not the probe token's workspace
            "file_name": "steal.png",
            "file_size": len(PNG),
            "mime_type": "image/png",
            "content_hash": digest,
        },
        headers=_auth(probe_token),
    )
    assert foreign_request.status_code == 404


async def test_multi_tenant_isolation_uniform_404(api_client, attachment_worker):
    token_a = await _register_and_login(api_client, f"e2e-tena-{uuid.uuid4().hex[:8]}@x.io")
    workspace_a = await _create_workspace(api_client, token_a)
    attachment = await _upload_three_stage(
        api_client, token_a, workspace_a, data=PNG, name="a.png", mime="image/png"
    )

    token_b = await _register_and_login(api_client, f"e2e-tenb-{uuid.uuid4().hex[:8]}@x.io")
    await _create_workspace(api_client, token_b)

    for path in (
        f"/api/v1/attachments/{attachment['id']}",
        f"/api/v1/attachments/{attachment['id']}/download",
    ):
        denied = await api_client.get(path, headers=_auth(token_b))
        assert denied.status_code == 404
        assert denied.json()["error"]["code"] == "not_found"

    deleted = await api_client.delete(
        f"/api/v1/attachments/{attachment['id']}", headers=_auth(token_b)
    )
    assert deleted.status_code == 404
