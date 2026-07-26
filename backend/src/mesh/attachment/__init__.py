"""Attachment module (attachment.md — stage 5 collaboration layer).

Public surface: the service orchestrator, the S3-compatible storage client,
the quarantine processing pipeline and the router. See module docstrings for
the per-file contracts.
"""

from __future__ import annotations

from mesh.attachment.processing import claim_pending_blobs, process_blob
from mesh.attachment.service import AttachmentService
from mesh.attachment.storage import ObjectStorage, StorageConfig

__all__ = [
    "AttachmentService",
    "ObjectStorage",
    "StorageConfig",
    "claim_pending_blobs",
    "process_blob",
]
