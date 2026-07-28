"""Skill content storage (skill.md §2.7 ``content_ref`` → attachment.md).

Script bodies and reference documents live in object storage, never inline
in the database rows (``content_ref`` points at the object). Two backends:

* :class:`ObjectStorageContentStore` — production path over the shared
  S3-compatible bucket (attachment.md owns the client);
* :class:`InMemoryContentStore` — tests without a storage dependency.

Refs are opaque strings; the ``obj:`` / ``mem:`` prefixes make the backend
unambiguous when debugging and keep the two namespaces collision-free.
"""

from __future__ import annotations

from typing import Protocol

from mesh.attachment.storage import ObjectStorage

REF_PREFIX_OBJECT = "obj:"
REF_PREFIX_MEMORY = "mem:"

MAX_CONTENT_BYTES = 10 * 1024 * 1024  # 10 MiB per script / reference body


class SkillContentStore(Protocol):
    """A body store keyed by logical paths under the skill namespace."""

    async def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return the opaque content_ref."""
        ...

    async def get(self, content_ref: str) -> bytes:
        """Fetch a body by its content_ref (empty bytes when absent)."""
        ...


class ObjectStorageContentStore:
    """Stores bodies in the shared object bucket under ``skills/<key>``."""

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    def _object_key(self, key: str) -> str:
        return f"skills/{key}"

    async def put(self, key: str, data: bytes) -> str:
        await self._storage.put_bytes(self._object_key(key), data, content_type="text/plain")
        return f"{REF_PREFIX_OBJECT}{key}"

    async def get(self, content_ref: str) -> bytes:
        if not content_ref.startswith(REF_PREFIX_OBJECT):
            return b""
        key = content_ref[len(REF_PREFIX_OBJECT):]
        try:
            return await self._storage.get_bytes(
                self._object_key(key), max_bytes=MAX_CONTENT_BYTES
            )
        except Exception:  # noqa: BLE001 — absent object reads as empty
            return b""


class InMemoryContentStore:
    """An ephemeral body store for unit tests."""

    def __init__(self) -> None:
        self._bodies: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> str:
        self._bodies[key] = bytes(data)
        return f"{REF_PREFIX_MEMORY}{key}"

    async def get(self, content_ref: str) -> bytes:
        if not content_ref.startswith(REF_PREFIX_MEMORY):
            return b""
        return self._bodies.get(content_ref[len(REF_PREFIX_MEMORY):], b"")


__all__ = [
    "MAX_CONTENT_BYTES",
    "InMemoryContentStore",
    "ObjectStorageContentStore",
    "SkillContentStore",
]
