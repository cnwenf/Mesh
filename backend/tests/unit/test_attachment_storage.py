"""Object storage client unit tests — real MinIO (attachment.md §3/§4.6)."""

from __future__ import annotations

import uuid

import httpx
import pytest

from mesh.attachment.storage import (
    STORAGE_PROVIDER,
    generate_storage_key,
    generate_thumbnail_key,
)

pytestmark = pytest.mark.unit


async def test_presigned_put_then_get_roundtrip(object_storage):
    key = generate_storage_key(uuid.uuid4(), "ab" * 32)
    payload = b"roundtrip-bytes" * 10
    put_url = await object_storage.presign_put(key, content_type="text/plain", expires_in=60)
    async with httpx.AsyncClient() as client:
        put = await client.put(put_url, content=payload, headers={"Content-Type": "text/plain"})
        assert put.status_code == 200

    assert await object_storage.object_exists(key) is True
    assert await object_storage.head_size(key) == len(payload)
    got = await object_storage.get_bytes(key, max_bytes=10_000)
    assert got == payload

    get_url = await object_storage.presign_get(
        key, expires_in=60, content_disposition="attachment; filename=\"x.txt\""
    )
    async with httpx.AsyncClient() as client:
        response = await client.get(get_url)
        assert response.status_code == 200
        assert response.content == payload
        assert "attachment" in response.headers.get("content-disposition", "")


async def test_head_size_missing_object(object_storage):
    assert await object_storage.head_size(f"missing/{uuid.uuid4().hex}") is None
    assert await object_storage.object_exists(f"missing/{uuid.uuid4().hex}") is False


async def test_get_bytes_enforces_max(object_storage):
    key = f"guard/{uuid.uuid4().hex}"
    await object_storage.put_bytes(key, b"x" * 100, content_type="application/octet-stream")
    from mesh.errors import StorageError

    with pytest.raises(StorageError):
        await object_storage.get_bytes(key, max_bytes=50)
    # Within the bound it works.
    assert await object_storage.get_bytes(key, max_bytes=100) == b"x" * 100


async def test_delete_object_is_idempotent(object_storage):
    key = f"del/{uuid.uuid4().hex}"
    await object_storage.put_bytes(key, b"gone", content_type="text/plain")
    await object_storage.delete_object(key)
    assert await object_storage.object_exists(key) is False
    # Deleting again never raises (GC / orphan cleanup are best-effort).
    await object_storage.delete_object(key)


async def test_multipart_lifecycle(object_storage):
    key = f"multi/{uuid.uuid4().hex}"
    part_size = 5 * 1024 * 1024  # S3 minimum part size
    body = b"a" * part_size + b"b" * 1024
    upload_id = await object_storage.create_multipart_upload(key, content_type="application/octet-stream")
    assert upload_id

    etags = []
    async with httpx.AsyncClient(timeout=30) as client:
        for part_number, chunk in enumerate(
            [body[:part_size], body[part_size:]], start=1
        ):
            url = await object_storage.presign_upload_part(
                key, upload_id=upload_id, part_number=part_number, expires_in=300
            )
            response = await client.put(url, content=chunk)
            assert response.status_code == 200
            etags.append({"part_number": part_number, "etag": response.headers["ETag"]})

    await object_storage.complete_multipart_upload(key, upload_id=upload_id, parts=etags)
    assert await object_storage.head_size(key) == len(body)
    assert await object_storage.get_bytes(key, max_bytes=len(body)) == body


async def test_abort_multipart_is_best_effort(object_storage):
    key = f"abort/{uuid.uuid4().hex}"
    upload_id = await object_storage.create_multipart_upload(key, content_type="text/plain")
    await object_storage.abort_multipart_upload(key, upload_id=upload_id)
    # Aborting an unknown upload never raises.
    await object_storage.abort_multipart_upload(key, upload_id="no-such-upload")


async def test_ensure_bucket_is_idempotent(object_storage):
    await object_storage.ensure_bucket()
    await object_storage.ensure_bucket()
    assert object_storage.bucket.startswith("mesh-test-")


def test_key_shapes_are_non_enumerable():
    workspace_id = uuid.uuid4()
    key = generate_storage_key(workspace_id, "9f" * 32)
    assert key.startswith(f"ws/{workspace_id}/9f/")
    assert len(key.rsplit("/", 1)[-1]) == 32  # uuid hex segment
    thumb = generate_thumbnail_key(workspace_id, "md")
    assert "/thumbs/" in thumb and "_md" in thumb
    # Staging markers never leak into the key prefix.
    staging_key = generate_storage_key(workspace_id, "staging:x")
    assert "/00/" in staging_key
    assert STORAGE_PROVIDER == "s3"


# ----------------------------------------------------------------------
# failure paths (neutral 502 mapping, §3.5 storage_error)
# ----------------------------------------------------------------------


def _dead_storage(bucket: str = "mesh-dead"):
    """An ObjectStorage pointing at a closed port — every I/O call fails."""
    from mesh.attachment.storage import ObjectStorage, StorageConfig

    return ObjectStorage(
        StorageConfig(
            endpoint="http://127.0.0.1:1",  # port 1 — nothing listens
            public_endpoint="http://127.0.0.1:1",
            region="us-east-1",
            access_key="x",
            secret_key="y",
            bucket=bucket,
        )
    )


async def test_io_failures_map_to_neutral_storage_error():
    from mesh.errors import StorageError

    storage = _dead_storage()
    with pytest.raises(StorageError):
        await storage.put_bytes("k", b"x", content_type="text/plain")
    with pytest.raises(StorageError):
        await storage.get_bytes("k", max_bytes=10)
    with pytest.raises(StorageError):
        await storage.create_multipart_upload("k", content_type="text/plain")
    with pytest.raises(StorageError):
        await storage.complete_multipart_upload(
            "k", upload_id="u", parts=[{"part_number": 1, "etag": "e"}]
        )
    # delete is best-effort: no raise, just a logged warning.
    await storage.delete_object("k")
    # abort likewise never raises.
    await storage.abort_multipart_upload("k", upload_id="u")


async def test_ensure_bucket_failure_raises(monkeypatch):
    from mesh.errors import StorageError

    storage = _dead_storage()
    with pytest.raises(StorageError):
        await storage.ensure_bucket()


async def test_ensure_bucket_create_path(monkeypatch, object_storage):
    """head_bucket misses → create_bucket is attempted (dev bootstrap path)."""
    calls = []

    def _head_missing(**kwargs):
        raise RuntimeError("404 simulated")

    def _create(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(object_storage._internal, "head_bucket", _head_missing)
    monkeypatch.setattr(object_storage._internal, "create_bucket", _create)
    object_storage._bucket_ready = False
    await object_storage.ensure_bucket()
    assert calls and calls[0]["Bucket"] == object_storage.bucket
    object_storage._bucket_ready = False
    await object_storage.ensure_bucket()  # cached after first success path


async def test_presign_failures_map_to_storage_error(monkeypatch, object_storage):
    from mesh.errors import StorageError

    def _boom(*args, **kwargs):
        raise RuntimeError("signing exploded")

    monkeypatch.setattr(object_storage._public, "generate_presigned_url", _boom)
    with pytest.raises(StorageError):
        await object_storage.presign_put("k", content_type="text/plain", expires_in=60)
    with pytest.raises(StorageError):
        await object_storage.presign_get("k", expires_in=60)
    with pytest.raises(StorageError):
        await object_storage.presign_upload_part(
            "k", upload_id="u", part_number=1, expires_in=60
        )


async def test_head_non_404_error_maps_to_storage_error(monkeypatch, object_storage):
    from mesh.errors import StorageError

    class _FakeError(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 500}, "Error": {"Code": "InternalError"}}

    def _head(**kwargs):
        raise _FakeError("boom")

    monkeypatch.setattr(object_storage._internal, "head_object", _head)
    with pytest.raises(StorageError):
        await object_storage.head_size("k")


async def test_palette_png_thumbnail_conversion():
    """Palette/alpha sources are converted before JPEG encoding."""
    import io

    from PIL import Image

    from mesh.attachment.thumbnails import make_thumbnails

    buffer = io.BytesIO()
    Image.new("P", (40, 40)).save(buffer, format="PNG")
    info = await make_thumbnails(buffer.getvalue(), source_mime="image/png")
    assert info.width == 40
    assert len(info.renditions) == 3
