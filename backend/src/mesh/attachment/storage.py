"""S3-compatible object storage client for attachments (attachment.md §3).

The bucket is PRIVATE; the API never proxies bytes on the upload path. Two
clients are kept: an *internal* one (compose-network endpoint, used for
server-side reads — quarantine scanning, thumbnails, deletes) and a *public*
one (browser-reachable endpoint) whose credentials are only used to PRESIGN
URLs handed to clients. Presigning is a local HMAC computation — no network
call — so the async wrappers stay cheap.

Object keys are non-enumerable (§4.6): ``ws/<workspace_id>/<hash-prefix>/<uuid>``.

Every storage failure surfaces as ``StorageError`` (502) with a neutral
message — internal endpoint/host/bucket details never reach the client.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from mesh.errors import StorageError

logger = logging.getLogger("mesh.attachment.storage")

# Provider identifier stored on attachment_blobs.storage_provider — vendor
# neutral naming, not bound to a specific product (attachment.md §1.3).
STORAGE_PROVIDER = "s3"


@dataclass(frozen=True)
class StorageConfig:
    """Immutable storage settings resolved from ``Settings``."""

    endpoint: str
    public_endpoint: str
    region: str
    access_key: str
    secret_key: str
    bucket: str


def _build_client(config: StorageConfig, endpoint: str) -> Any:
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=config.region,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 2}),
    )


class ObjectStorage:
    """Thin async wrapper over an S3 API client (MinIO-compatible)."""

    def __init__(self, config: StorageConfig) -> None:
        self._config = config
        self._internal = _build_client(config, config.endpoint)
        # Presigned URLs must be signed against the endpoint the CLIENT can
        # reach; fall back to the internal endpoint for single-NIC setups.
        self._public = _build_client(config, config.public_endpoint or config.endpoint)
        self._bucket_ready = False

    @property
    def bucket(self) -> str:
        return self._config.bucket

    async def ensure_bucket(self) -> None:
        """Create the private bucket if missing (idempotent, dev convenience)."""
        if self._bucket_ready:
            return

        def _ensure() -> None:
            try:
                self._internal.head_bucket(Bucket=self._config.bucket)
                return
            except Exception:  # noqa: BLE001 — any miss falls through to create
                pass
            try:
                self._internal.create_bucket(Bucket=self._config.bucket)
            except Exception as exc:  # noqa: BLE001 — map to neutral 502
                _raise_storage_error("bucket provisioning failed", exc)

        await asyncio.to_thread(_ensure)
        self._bucket_ready = True

    # -- signing ----------------------------------------------------------------

    async def presign_put(
        self,
        key: str,
        *,
        content_type: str,
        expires_in: int,
    ) -> str:
        """Short-lived signed PUT bound to method, key and declared Content-Type."""

        def _sign() -> str:
            return self._public.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._config.bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                HttpMethod="PUT",
                ExpiresIn=expires_in,
            )

        try:
            return await asyncio.to_thread(_sign)
        except Exception as exc:  # noqa: BLE001
            return _raise_storage_error("presign put failed", exc)

    async def presign_upload_part(
        self,
        key: str,
        *,
        upload_id: str,
        part_number: int,
        expires_in: int,
    ) -> str:
        def _sign() -> str:
            return self._public.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._config.bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                HttpMethod="PUT",
                ExpiresIn=expires_in,
            )

        try:
            return await asyncio.to_thread(_sign)
        except Exception as exc:  # noqa: BLE001
            return _raise_storage_error("presign part failed", exc)

    async def presign_get(
        self,
        key: str,
        *,
        expires_in: int,
        content_disposition: str | None = None,
        content_type: str | None = None,
    ) -> str:
        """Short-lived signed GET (§3.4) — single purpose, method+key bound."""

        def _sign() -> str:
            params: dict[str, Any] = {"Bucket": self._config.bucket, "Key": key}
            if content_disposition is not None:
                params["ResponseContentDisposition"] = content_disposition
            if content_type is not None:
                params["ResponseContentType"] = content_type
            return self._public.generate_presigned_url(
                "get_object", Params=params, HttpMethod="GET", ExpiresIn=expires_in
            )

        try:
            return await asyncio.to_thread(_sign)
        except Exception as exc:  # noqa: BLE001
            return _raise_storage_error("presign get failed", exc)

    # -- multipart ----------------------------------------------------------------

    async def create_multipart_upload(self, key: str, *, content_type: str) -> str:
        def _create() -> str:
            response = self._internal.create_multipart_upload(
                Bucket=self._config.bucket, Key=key, ContentType=content_type
            )
            return str(response["UploadId"])

        try:
            return await asyncio.to_thread(_create)
        except Exception as exc:  # noqa: BLE001
            return _raise_storage_error("multipart create failed", exc)

    async def complete_multipart_upload(
        self, key: str, *, upload_id: str, parts: list[dict[str, Any]]
    ) -> None:
        def _complete() -> None:
            self._internal.complete_multipart_upload(
                Bucket=self._config.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [
                        {"PartNumber": int(p["part_number"]), "ETag": str(p["etag"])}
                        for p in sorted(parts, key=lambda p: int(p["part_number"]))
                    ]
                },
            )

        try:
            await asyncio.to_thread(_complete)
        except Exception as exc:  # noqa: BLE001
            _raise_storage_error("multipart complete failed", exc)

    async def abort_multipart_upload(self, key: str, *, upload_id: str) -> None:
        def _abort() -> None:
            self._internal.abort_multipart_upload(
                Bucket=self._config.bucket, Key=key, UploadId=upload_id
            )

        try:
            await asyncio.to_thread(_abort)
        except Exception as exc:  # noqa: BLE001 — abort is best-effort cleanup
            logger.warning("multipart abort failed for key=%s: %s", key, type(exc).__name__)

    # -- server-side object I/O (quarantine worker / maintenance) ------------------

    async def head_size(self, key: str) -> int | None:
        """Object size in bytes, or None when the object does not exist."""

        def _head() -> int | None:
            try:
                response = self._internal.head_object(Bucket=self._config.bucket, Key=key)
            except Exception as exc:  # noqa: BLE001 — botocore ClientError
                status = getattr(getattr(exc, "response", None), "get", lambda *_: None)(
                    "ResponseMetadata", {}
                ).get("HTTPStatusCode")
                if status == 404:
                    return None
                # MinIO also surfaces missing keys as 404 inside the error code.
                code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                if code in {"404", "NoSuchKey", "NotFound"}:
                    return None
                _raise_storage_error("head failed", exc)
            return int(response["ContentLength"])

        return await asyncio.to_thread(_head)

    async def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        """Read the whole object (quarantine scanning); bounded by max_bytes."""

        def _get() -> bytes:
            response = self._internal.get_object(Bucket=self._config.bucket, Key=key)
            try:
                data = response["Body"].read(max_bytes + 1)
            finally:
                response["Body"].close()
            if len(data) > max_bytes:
                raise StorageError(
                    "object exceeds processing limit", code="storage_error"
                )
            return data

        try:
            return await asyncio.to_thread(_get)
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _raise_storage_error("object read failed", exc)

    async def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        """Server-side write (thumbnails)."""

        def _put() -> None:
            self._internal.put_object(
                Bucket=self._config.bucket, Key=key, Body=data, ContentType=content_type
            )

        try:
            await asyncio.to_thread(_put)
        except Exception as exc:  # noqa: BLE001
            _raise_storage_error("object write failed", exc)

    async def delete_object(self, key: str) -> None:
        """Best-effort delete (GC / orphan cleanup / post-dedup)."""

        def _delete() -> None:
            self._internal.delete_object(Bucket=self._config.bucket, Key=key)

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:  # noqa: BLE001 — cleanup must not wedge loops
            logger.warning("object delete failed for key=%s: %s", key, type(exc).__name__)

    async def object_exists(self, key: str) -> bool:
        return await self.head_size(key) is not None


def _raise_storage_error(context: str, exc: Exception) -> Any:
    """Log server-side detail, raise the neutral 502 (§3.5 storage_error)."""
    logger.error("storage error (%s): %s: %s", context, type(exc).__name__, exc)
    raise StorageError("object storage request failed") from exc


def generate_storage_key(workspace_id: uuid.UUID, content_hash: str) -> str:
    """Non-enumerable object key: ws/<workspace_id>/<hash-prefix>/<uuid> (§4.6)."""
    prefix = content_hash[:2] if content_hash and not content_hash.startswith("staging:") else "00"
    return f"ws/{workspace_id}/{prefix}/{uuid.uuid4().hex}"


def generate_thumbnail_key(workspace_id: uuid.UUID, size: str) -> str:
    """Non-enumerable key for a generated thumbnail rendition."""
    return f"ws/{workspace_id}/thumbs/{uuid.uuid4().hex}_{size}"
