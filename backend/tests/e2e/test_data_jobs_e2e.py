"""Data import/export REAL end-to-end tests (import-export.md §5, T31 red lines).

Real uvicorn API subprocess + REAL worker subprocess (relay + data-job
pipeline + reaper) + real PostgreSQL + real MinIO — no mocks on any
contract path:

- two-phase import over HTTP: upload → validate (dry-run, no entities) →
  run → partial success with exact counts and an error-report attachment;
- run-before-validate → 422 validation_required;
- source replacement after validate → 422 source_changed (API precheck)
  and failed(source_changed) with a CRITICAL data_job_finished
  notification when the worker detects it (§6.13 data-job row 3 / T25);
- T31 crash recovery: kill-after-batch-1 → lease expiry → resume from
  checkpoint with lease_seq+1 → NO duplicate entities; stale-worker batch
  commit rejected wholesale (fencing); replayed committed batch creates
  nothing (row-ledger idempotency);
- source attachment physical delete → RESTRICT (T18/T31-①);
- async export: create → worker generates → product registered as
  attachment → signed download URL actually serves the CSV from MinIO.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob, DataJobRow
from mesh.db.models.issue import Issue
from mesh.db.models.notification import Notification
from tests.conftest import get_test_database_url, get_test_redis_url
from tests.e2e.conftest import pin_code_under_test

pytestmark = pytest.mark.e2e

PASSWORD = "S3cure-passw0rd!"
WORKER_READY_WAIT_SECONDS = 4.0

CSV_GOOD = (
    "Title,State,Priority,Key\n"
    "Login crash,Todo,High,EXT-1\n"
    "Fix button,Todo,Low,EXT-2\n"
    ",Todo,Low,EXT-3\n"  # missing title → row error
)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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
async def data_job_worker(provision_database):
    """REAL worker subprocess — relay with the data-job handlers + reaper."""
    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_DATA_JOB_REAPER_INTERVAL"] = "1.0"
    env.update(_storage_env())
    # Force code under test (avoid stale editable install of another ws).
    pin_code_under_test(env)
    process = subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    await asyncio.sleep(WORKER_READY_WAIT_SECONDS)
    assert process.poll() is None, "worker process died on startup"
    yield process
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def _register_and_login(client: httpx.AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "E2E DataJobs"},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


async def _create_workspace(client: httpx.AsyncClient, token: str) -> str:
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "E2E Data Jobs", "slug": f"e2e-dj-{uuid.uuid4().hex[:10]}"},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def _upload_csv(
    client: httpx.AsyncClient, token: str, workspace_id: str, data: bytes, name: str = "issues.csv"
) -> str:
    """Three-stage direct upload; csv is text-whitelisted → released as skipped."""
    requested = await client.post(
        "/api/v1/attachments/upload-requests",
        json={
            "workspace_id": workspace_id,
            "file_name": name,
            "file_size": len(data),
            "mime_type": "text/csv",
            "content_hash": hashlib.sha256(data).hexdigest(),
        },
        headers=_auth(token),
    )
    assert requested.status_code == 201, requested.text
    payload = requested.json()["data"]
    if payload["upload"] is not None:
        async with httpx.AsyncClient(timeout=30) as external:
            put = await external.put(
                payload["upload"]["url"],
                content=data,
                headers=payload["upload"]["headers"],
            )
            assert put.status_code == 200, put.text
        done = await client.post(
            f"/api/v1/attachments/{payload['id']}/complete", json={}, headers=_auth(token)
        )
        assert done.status_code == 200, done.text
        payload = done.json()["data"]
    # wait for the quarantine pipeline to release the text blob (skipped)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        info = await client.get(f"/api/v1/attachments/{payload['id']}", headers=_auth(token))
        if info.status_code == 200 and info.json()["data"]["scan_status"] in ("clean", "skipped"):
            break
        await asyncio.sleep(0.3)
    return payload["id"]


_MAPPING = {
    "columns": [
        {"source": "Title", "target": "title", "transform": {"type": "direct"}},
        {
            "source": "State",
            "target": "status",
            "transform": {"type": "status_by_name", "fallback": "default"},
        },
        {
            "source": "Priority",
            "target": "priority",
            "transform": {"type": "value_map", "map": {"High": "high", "Low": "low"}, "default": "none"},
        },
        {"source": "Key", "target": "external_ref", "transform": {"type": "direct"}},
    ]
}


async def _wait_job_status(
    client: httpx.AsyncClient, token: str, job_id: str, expected: frozenset[str], *, timeout: float = 45.0
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/data-jobs/{job_id}", headers=_auth(token))
        assert response.status_code == 200, response.text
        last = response.json()["data"]
        if last["status"] in expected:
            return last
        await asyncio.sleep(0.4)
    raise AssertionError(f"job status never reached {expected}: last={last}")


async def test_import_two_phase_partial_success_real_worker(api_client, data_job_worker, session_factory):
    token = await _register_and_login(api_client, f"e2e-import-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    source_id = await _upload_csv(api_client, token, workspace_id, CSV_GOOD.encode())

    created = await api_client.post(
        "/api/v1/data-jobs/import",
        json={
            "workspace_id": workspace_id,
            "format": "csv",
            "source_attachment_id": source_id,
            "mapping": _MAPPING,
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["data"]["id"]

    # dry-run: worker validates and returns the job to pending
    validated = await api_client.post(f"/api/v1/data-jobs/import/{job_id}/validate", headers=_auth(token))
    assert validated.status_code == 200, validated.text
    dry = await _wait_job_status(api_client, token, job_id, frozenset({"pending"}))
    assert dry["total_rows"] == 3
    assert dry["params"]["predicted_failed_rows"] == 1
    assert dry["params"]["validated_at"]
    # dry-run created NOTHING (§5.1)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Issue)) == 0

    # run: partial success — 2 created, 1 failed
    ran = await api_client.post(f"/api/v1/data-jobs/import/{job_id}/run", headers=_auth(token))
    assert ran.status_code == 202, ran.text
    final = await _wait_job_status(
        api_client, token, job_id, frozenset({"completed", "completed_with_errors"})
    )
    assert final["status"] == "completed_with_errors"
    assert final["succeeded_rows"] == 2
    assert final["failed_rows"] == 1
    assert final["succeeded_rows"] + final["failed_rows"] == final["total_rows"]
    assert final["result_attachment_id"] is not None  # error report archived

    async with session_factory() as session:
        titles = set((await session.execute(select(Issue.title))).scalars().all())
        assert titles == {"Login crash", "Fix button"}
        # numbering via the normal path (inbox namespace)
        identifiers = set((await session.execute(select(Issue.identifier))).scalars().all())
        assert len(identifiers) == 2
        created_rows = await session.scalar(
            select(func.count()).select_from(DataJobRow).where(DataJobRow.status == "created")
        )
        failed_rows = await session.scalar(
            select(func.count()).select_from(DataJobRow).where(DataJobRow.status == "failed")
        )
        assert created_rows == 2 and failed_rows == 1

    # error report downloadable via the signed attachment channel
    download = await api_client.get(f"/api/v1/data-jobs/{job_id}/download", headers=_auth(token))
    assert download.status_code == 200, download.text
    url = download.json()["data"]["url"]
    async with httpx.AsyncClient(timeout=30) as external:
        fetched = await external.get(url)
        assert fetched.status_code == 200, fetched.text
        assert "required_field_missing" in fetched.text


async def test_run_before_validate_rejected(api_client, data_job_worker):
    token = await _register_and_login(api_client, f"e2e-nodry-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    source_id = await _upload_csv(api_client, token, workspace_id, CSV_GOOD.encode())
    created = await api_client.post(
        "/api/v1/data-jobs/import",
        json={
            "workspace_id": workspace_id,
            "format": "csv",
            "source_attachment_id": source_id,
            "mapping": _MAPPING,
        },
        headers=_auth(token),
    )
    job_id = created.json()["data"]["id"]
    ran = await api_client.post(f"/api/v1/data-jobs/import/{job_id}/run", headers=_auth(token))
    assert ran.status_code == 422
    assert ran.json()["error"]["code"] == "validation_required"


async def test_source_replaced_after_validate_api_rejects_and_worker_fails_critical(
    api_client, data_job_worker, session_factory
):
    token = await _register_and_login(api_client, f"e2e-swap-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    source_id = await _upload_csv(api_client, token, workspace_id, CSV_GOOD.encode())
    created = await api_client.post(
        "/api/v1/data-jobs/import",
        json={
            "workspace_id": workspace_id,
            "format": "csv",
            "source_attachment_id": source_id,
            "mapping": _MAPPING,
        },
        headers=_auth(token),
    )
    job_id = created.json()["data"]["id"]
    await api_client.post(f"/api/v1/data-jobs/import/{job_id}/validate", headers=_auth(token))
    await _wait_job_status(api_client, token, job_id, frozenset({"pending"}))

    # replace the source object bytes in MinIO (same key, different content)
    async with session_factory() as session:
        attachment = await session.get(Attachment, uuid.UUID(source_id))
        blob = await session.get(AttachmentBlob, attachment.blob_id)
        storage_key = blob.storage_key
    from mesh.attachment.storage import ObjectStorage, StorageConfig

    endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    storage = ObjectStorage(
        StorageConfig(
            endpoint=endpoint,
            public_endpoint=endpoint,
            region="us-east-1",
            access_key=os.environ.get("MESH_STORAGE_ACCESS_KEY", ""),
            secret_key=os.environ.get("MESH_STORAGE_SECRET_KEY", ""),
            bucket=os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e"),
        )
    )
    await storage.put_bytes(storage_key, b"Title,Key\nTotally different,X-9\n", content_type="text/csv")

    # API precheck detects the change → 422 source_changed
    ran = await api_client.post(f"/api/v1/data-jobs/import/{job_id}/run", headers=_auth(token))
    assert ran.status_code == 422
    assert ran.json()["error"]["code"] == "source_changed"

    # worker-side detection: simulate a worker that claimed before the swap
    # and died — ghost expired lease; the reaper re-dispatches resume and the
    # REAL worker's claim-time hash verification must fail the job, and the
    # §6.13 critical notification must fan out (T25 data-job row 3).
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE data_jobs SET status='running', started_at=now(), "
                "lease_owner='ghost-worker', lease_seq=3, "
                "lease_expires_at = now() - interval '1 minute' WHERE id=:id"
            ),
            {"id": job_id},
        )
    failed = await _wait_job_status(api_client, token, job_id, frozenset({"failed"}))
    assert failed["failure_reason"] == "source_changed"
    async with session_factory() as session:
        notification = (
            (await session.execute(select(Notification).where(Notification.type == "data_job_finished")))
            .scalars()
            .first()
        )
        assert notification is not None
        assert notification.priority == "critical"


async def test_t31_kill_resume_fencing_and_replay(api_client, data_job_worker, session_factory):
    """T31 red lines driven against the REAL db/storage: checkpoint resume,
    stale-worker fencing, row-ledger replay idempotency."""
    from mesh.attachment.service import AttachmentService
    from mesh.config import load_settings
    from mesh.data_jobs.runner import DataJobWorker

    token = await _register_and_login(api_client, f"e2e-t31-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    rows = "".join(f"issue {i},Todo,Low,K{i}\n" for i in range(1, 6))
    content = f"Title,State,Priority,Key\n{rows}".encode()
    source_id = await _upload_csv(api_client, token, workspace_id, content)

    # seed statuses + create the job via HTTP (no run yet — we drive the worker)
    created = await api_client.post(
        "/api/v1/data-jobs/import",
        json={
            "workspace_id": workspace_id,
            "format": "csv",
            "source_attachment_id": source_id,
            "mapping": _MAPPING,
        },
        headers=_auth(token),
    )
    job_id = uuid.UUID(created.json()["data"]["id"])
    from mesh.issue.statuses import ensure_scope_seeded

    async with session_factory() as session, session.begin():
        await ensure_scope_seeded(session, workspace_id=uuid.UUID(workspace_id))
        # the test skips the validate round-trip: seed total_rows as the
        # dry-run would (the counts CHECK needs it before batch commits)
        await session.execute(text("UPDATE data_jobs SET total_rows=5 WHERE id=:id"), {"id": job_id})

    settings = load_settings(
        database_url=get_test_database_url(),
        redis_url=get_test_redis_url(),
        auth_mode="dev",
        data_job_batch_size=2,
        **_storage_settings_kwargs(),
    )
    from mesh.api.app import build_object_storage

    storage = build_object_storage(settings)
    worker_a = DataJobWorker(
        session_factory,
        settings,
        storage,
        AttachmentService(session_factory, settings, storage),
        worker_id="worker-A",
    )

    # Worker A claims and commits ONLY batch 1, then "dies" (lease kept).
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE data_jobs SET status='running', started_at=now() WHERE id=:id"), {"id": job_id}
        )
    claim_a = await worker_a._claim(job_id, "import-run")
    assert claim_a is not None
    job = await _load_job(session_factory, job_id)
    context = await worker_a._build_context(job)
    from mesh.data_jobs.parser import RowKeyAllocator, iter_source_rows

    scratch = f"/tmp/e2e-t31-{uuid.uuid4().hex}"
    os.makedirs(scratch, exist_ok=True)
    source_path = f"{scratch}/source"
    await worker_a._fetch_source(job, source_path)
    allocator = RowKeyAllocator()
    batch: list = []
    for row_number, raw in iter_source_rows(source_path, "csv"):
        batch.append((row_number, raw))
        if len(batch) == 2:
            break
    ext_def = await worker_a._ensure_external_ref_field(claim_a)
    await worker_a._run_batch(claim_a, batch, 1, context, ext_def, allocator, skip=False)
    job = await _load_job(session_factory, job_id)
    assert job.checkpoint.get("last_committed_batch") == 1
    assert job.succeeded_rows == 2

    # Lease expires; the reaper pass clears the owner and re-dispatches resume.
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE data_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id=:id"),
            {"id": job_id},
        )
    from mesh.data_jobs.reaper import run_reaper_pass

    reclaimed = await run_reaper_pass(session_factory, settings=settings)
    assert reclaimed >= 1
    job = await _load_job(session_factory, job_id)
    assert job.lease_owner is None and job.lease_seq == 1  # preserved, not reset

    # Worker B resumes (lease_seq +1) and finishes from the checkpoint.
    worker_b = DataJobWorker(
        session_factory,
        settings,
        storage,
        AttachmentService(session_factory, settings, storage),
        worker_id="worker-B",
    )
    claim_b = await worker_b._claim(job_id, "resume")
    assert claim_b is not None and claim_b.lease_seq == 2 and claim_b.resumed
    await worker_b.run_import(claim_b)
    job = await _load_job(session_factory, job_id)
    assert job.status == "completed"
    assert job.succeeded_rows == 5  # no duplicates from the replayed batch 1

    # STALE worker A "resurrects" and tries to commit batch 2 → fenced out.
    from mesh.data_jobs.runner import FenceLostError

    allocator_a = RowKeyAllocator()
    batches: list[list] = []
    current: list = []
    for row_number, raw in iter_source_rows(source_path, "csv"):
        current.append((row_number, raw))
        if len(current) == 2:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    with pytest.raises(FenceLostError):
        await worker_a._run_batch(claim_a, batches[1], 2, context, ext_def, allocator_a, skip=False)
    job = await _load_job(session_factory, job_id)
    assert job.succeeded_rows == 5  # stale commit changed nothing

    # REPLAY the already-committed batch 3 as worker B → creates nothing.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE data_jobs SET status='running', lease_owner=NULL, "
                "checkpoint='{}', succeeded_rows=0, failed_rows=0 WHERE id=:id"
            ),
            {"id": job_id},
        )
    claim_c = await worker_b._claim(job_id, "resume")
    await worker_b.run_import(claim_c)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Issue)) == 5


async def test_source_attachment_delete_restricted(api_client, data_job_worker, session_factory):
    token = await _register_and_login(api_client, f"e2e-restrict-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    source_id = await _upload_csv(api_client, token, workspace_id, CSV_GOOD.encode())
    await api_client.post(
        "/api/v1/data-jobs/import",
        json={
            "workspace_id": workspace_id,
            "format": "csv",
            "source_attachment_id": source_id,
            "mapping": _MAPPING,
        },
        headers=_auth(token),
    )
    # T18/T31-①: physical delete of the source attachment is REFUSED while
    # the job exists (ON DELETE RESTRICT).
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(text("DELETE FROM attachments WHERE id = :id"), {"id": source_id})
            await session.flush()


async def test_export_real_worker_product_download(api_client, data_job_worker, session_factory):
    token = await _register_and_login(api_client, f"e2e-export-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    # seed a few issues to export
    from mesh.issue.statuses import ensure_scope_seeded

    async with session_factory() as session, session.begin():
        ws_uuid = uuid.UUID(workspace_id)
        await ensure_scope_seeded(session, workspace_id=ws_uuid)
        status_id = (
            await session.execute(
                select(__import__("mesh.db.models.issue", fromlist=["IssueStatus"]).IssueStatus.id).limit(1)
            )
        ).scalar_one()
        for i in range(3):
            session.add(
                Issue(
                    workspace_id=ws_uuid,
                    identifier_namespace_key="WS",
                    number=i + 1,
                    identifier=f"WS-{i + 1}",
                    title=f"export me {i}",
                    status_id=status_id,
                    state_category="todo",
                )
            )
    created = await api_client.post(
        "/api/v1/data-jobs/export",
        json={"workspace_id": workspace_id, "scope": "workspace", "format": "csv"},
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["data"]["id"]
    final = await _wait_job_status(api_client, token, job_id, frozenset({"completed"}))
    assert final["total_rows"] == 3
    assert final["result_attachment_id"] is not None

    download = await api_client.get(f"/api/v1/data-jobs/{job_id}/download", headers=_auth(token))
    assert download.status_code == 200, download.text
    url = download.json()["data"]["url"]
    async with httpx.AsyncClient(timeout=30) as external:
        fetched = await external.get(url)
        assert fetched.status_code == 200, fetched.text
        assert "export me 0" in fetched.text and "WS-2" in fetched.text


async def test_export_filtered_by_state_category_real_worker(
    api_client, data_job_worker, session_factory
):
    """§3.5/E3 HIGH regression: a filtered export must actually filter.

    The flat filter dict (§2.4) is translated onto ``list_issues`` typed
    kwargs + a ``state_category`` ``in`` tree node. Before the fix the raw
    flat dict was passed straight through as ``filters=`` and
    ``compile_filter_tree`` rejected it, so every filtered export failed at
    runtime as ``storage_error``. Real worker + real list query here.
    """
    token = await _register_and_login(api_client, f"e2e-fexport-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    from mesh.issue.statuses import ensure_scope_seeded

    async with session_factory() as session, session.begin():
        ws_uuid = uuid.UUID(workspace_id)
        await ensure_scope_seeded(session, workspace_id=ws_uuid)
        status_id = (
            await session.execute(
                select(__import__("mesh.db.models.issue", fromlist=["IssueStatus"]).IssueStatus.id).limit(1)
            )
        ).scalar_one()
        for i, category in enumerate(["todo", "todo", "done"]):
            session.add(
                Issue(
                    workspace_id=ws_uuid,
                    identifier_namespace_key="WS",
                    number=i + 1,
                    identifier=f"WS-{i + 1}",
                    title=f"filtered {i} {category}",
                    status_id=status_id,
                    state_category=category,
                )
            )
    created = await api_client.post(
        "/api/v1/data-jobs/export",
        json={
            "workspace_id": workspace_id,
            "scope": "workspace",
            "format": "csv",
            "filters": {"state_category": ["todo"]},
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["data"]["id"]
    final = await _wait_job_status(api_client, token, job_id, frozenset({"completed", "failed"}))
    # A broken filter translation fails the job; the fix yields exactly the 2 todos.
    assert final["status"] == "completed", final
    assert final["total_rows"] == 2
    download = await api_client.get(f"/api/v1/data-jobs/{job_id}/download", headers=_auth(token))
    assert download.status_code == 200, download.text
    url = download.json()["data"]["url"]
    async with httpx.AsyncClient(timeout=30) as external:
        fetched = await external.get(url)
        assert fetched.status_code == 200, fetched.text
        assert "filtered 0 todo" in fetched.text
        assert "filtered 1 todo" in fetched.text
        assert "filtered 2 done" not in fetched.text


def _storage_settings_kwargs() -> dict:
    endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    return {
        "storage_endpoint": endpoint,
        "storage_public_endpoint": endpoint,
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", ""),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", ""),
        "storage_bucket": os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e"),
    }


async def _load_job(session_factory, job_id: uuid.UUID) -> DataJob:
    async with session_factory() as session:
        job = await session.get(DataJob, job_id)
        assert job is not None
        return job



async def test_h2_double_crash_at_same_checkpoint_does_not_wedge(
    api_client, session_factory
):
    """T31⑤ / H2 (real DB + real MinIO + real worker code, deterministic): two
    simulated hard crashes at the SAME checkpoint leave 'wasted' published
    resume rows; the bucketed re-arm key lets the reaper re-insert a FRESH
    pending resume the worker consumes, so the job COMPLETES instead of
    stalling for the outbox retention window. Without bucketing the 2nd reaper
    emit would dedup against the non-bucketed wedge row and wedge the job.

    Driven with an in-process worker (real object storage + real DB) so no
    competing subprocess reaper perturbs the bucket assertions."""
    from datetime import UTC, datetime, timedelta

    from mesh.api.app import build_object_storage
    from mesh.attachment.service import AttachmentService
    from mesh.config import load_settings
    from mesh.data_jobs.reaper import _rearm_bucket, run_reaper_pass
    from mesh.data_jobs.runner import DataJobWorker, resume_idempotency_key
    from mesh.db.models.outbox import OutboxEvent
    from mesh.issue.statuses import ensure_scope_seeded
    from mesh.outbox.service import scope_idempotency_key

    token = await _register_and_login(api_client, f"e2e-h2-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    ws_uuid = uuid.UUID(workspace_id)
    content = b"Title,State,Priority,Key\none,Todo,Low,K1\ntwo,Todo,High,K2\n"
    source_id = await _upload_csv(api_client, token, workspace_id, content)
    async with session_factory() as session, session.begin():
        await ensure_scope_seeded(session, workspace_id=ws_uuid)
        # No scan worker in this test: release the text blob by hand.
        await session.execute(
            text(
                "UPDATE attachment_blobs SET scan_status='skipped' "
                "WHERE id = (SELECT blob_id FROM attachments WHERE id = :a)"
            ),
            {"a": uuid.UUID(source_id)},
        )

    settings = load_settings(
        database_url=get_test_database_url(),
        redis_url=get_test_redis_url(),
        storage_endpoint=os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        storage_public_endpoint=os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        storage_bucket=os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e"),
    )
    storage = build_object_storage(settings)
    worker = DataJobWorker(
        session_factory, settings, storage, AttachmentService(session_factory, settings, storage)
    )

    created = await api_client.post(
        "/api/v1/data-jobs/import",
        json={
            "workspace_id": workspace_id,
            "format": "csv",
            "source_attachment_id": source_id,
            "mapping": _MAPPING,
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    job_id = uuid.UUID(created.json()["data"]["id"])

    # Validate via the in-process worker (freezes the source hash).
    async with session_factory() as session, session.begin():
        await session.execute(text("UPDATE data_jobs SET status='validating' WHERE id=:id"), {"id": job_id})
    await worker.process(job_id, "import-validate")
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        job = await session_factory_get(session_factory, job_id)
        if job.status == "pending":
            break
        await asyncio.sleep(0.2)
    assert (await session_factory_get(session_factory, job_id)).source_content_hash

    inner = resume_idempotency_key(job_id, 0)  # non-bucketed (pre-fix reaper emit)
    t1 = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
    t2 = t1 + timedelta(seconds=300)  # different rearm bucket than t1

    async def set_crashed(owner: str, expires: datetime) -> None:
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE data_jobs SET status='running', lease_owner=:o, "
                    "lease_expires_at=:e, checkpoint='{}' WHERE id=:id"
                ),
                {"o": owner, "e": expires, "id": job_id},
            )

    async def outbox_keys() -> set:
        async with session_factory() as session:
            return set((await session.execute(select(OutboxEvent.idempotency_key))).scalars().all())

    # CRASH #1 at checkpoint 0: wasted published resume row at the non-bucketed
    # key (what the pre-fix reaper would have emitted).
    await set_crashed("dead-1", t1 - timedelta(minutes=1))
    async with session_factory() as session, session.begin():
        session.add(
            OutboxEvent(
                workspace_id=ws_uuid,
                event_type="data_job.resume",
                payload={"data_job_id": str(job_id), "action": "resume"},
                idempotency_key=scope_idempotency_key(ws_uuid, inner),
                status="published",
            )
        )
    await run_reaper_pass(session_factory, settings=settings, clock=lambda: t1)
    bucketed_t1 = scope_idempotency_key(
        ws_uuid, resume_idempotency_key(job_id, 0, bucket=_rearm_bucket(t1, settings))
    )
    assert bucketed_t1 in await outbox_keys()  # not wedged by the non-bucketed wedge

    # CRASH #2 at the SAME checkpoint 0: the t1 re-arm row is now 'wasted'.
    await set_crashed("dead-2", t2 - timedelta(minutes=1))
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE outbox_events SET status='published' WHERE idempotency_key=:k"),
            {"k": bucketed_t1},
        )
    await run_reaper_pass(session_factory, settings=settings, clock=lambda: t2)
    bucketed_t2 = scope_idempotency_key(
        ws_uuid, resume_idempotency_key(job_id, 0, bucket=_rearm_bucket(t2, settings))
    )
    assert bucketed_t2 != bucketed_t1
    assert bucketed_t2 in await outbox_keys()  # fresh re-arm despite two wedge rows

    # The worker consumes the fresh resume (checkpoint 0) and COMPLETES.
    await worker.process(job_id, "resume")
    job = await session_factory_get(session_factory, job_id)
    assert job.status in ("completed", "completed_with_errors")
    assert job.succeeded_rows == 2 and job.failed_rows == 0
    async with session_factory() as session:
        assert (await session.scalar(select(func.count()).select_from(Issue))) == 2  # no dupes


async def session_factory_get(session_factory, job_id):
    async with session_factory() as session:
        from mesh.db.models.data_job import DataJob

        return await session.get(DataJob, job_id)


async def test_auto_infer_default_flow_works_under_app_role_rls(
    api_client, data_job_worker, session_factory
):
    """CRITICAL-1 regression: the wizard's default first step posts
    auto_infer=true with NO mapping, which makes the API open a fresh session
    to read the source blob's headers. Under the restricted app role that
    session MUST set the tenant GUC or the RLS-protected attachment_blobs get
    500s. Pre-fix this returned 500; post-fix it returns 201 with an inferred
    mapping. Runs against the real app-role server (api_client)."""
    token = await _register_and_login(api_client, f"e2e-infer-{uuid.uuid4().hex[:8]}@x.io")
    workspace_id = await _create_workspace(api_client, token)
    content = b"Title,State,Priority,Key\na,Todo,Low,K1\nb,Todo,High,K2\n"
    source_id = await _upload_csv(api_client, token, workspace_id, content)

    created = await api_client.post(
        "/api/v1/data-jobs/import",
        json={
            "workspace_id": workspace_id,
            "format": "csv",
            "source_attachment_id": source_id,
            "auto_infer": True,
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text  # was 500 before CRITICAL-1 fix
    body = created.json()["data"]
    assert body["status"] == "pending"
    cols = body["mapping"]["columns"]
    inferred_targets = {c["target"] for c in cols}
    assert "title" in inferred_targets  # header 'Title' inferred
    # The inferred mapping must then validate cleanly via the real worker.
    job_id = body["id"]
    await api_client.post(f"/api/v1/data-jobs/import/{job_id}/validate", headers=_auth(token))
    final = await _wait_job_status(api_client, token, job_id, {"pending"})
    assert final["total_rows"] == 2 and final["failed_rows"] == 0
