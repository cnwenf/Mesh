"""Attachment models (attachment.md §2 — 协作层附件模块).

Five tables, mirroring docs/specs/validation/schema_r2_validation.sql (附件
section) plus the §2.5/§2.6 optional ledger tables:

- ``attachment_blobs`` — the CONTENT truth table. One row per unique content
  per workspace (``UNIQUE (workspace_id, content_hash)``); scan verdict,
  magic-byte MIME, thumbnails and the atomic ``ref_count`` live here so a
  quarantine scan happens once for every sharer (R2).
- ``attachments`` — independent attachment RECORDS (independent uploader,
  links, lifecycle, deletion) referencing a shared blob via the composite FK
  ``(workspace_id, blob_id) → attachment_blobs(workspace_id, id)`` (README
  §6.2). Carries the session-level ``upload_status`` state machine.
- ``attachment_links`` — polymorphic logical FK (issue / comment /
  chat_message); no physical FK for the polymorphic target (README §6.2
  rule 4), but the row carries ``workspace_id`` and consistency is enforced
  by soft-delete + service-layer existence checks.
- ``upload_sessions`` — multipart upload ledger (§2.5).
- ``attachment_quotas`` — optional per-workspace limit overrides (§2.6).

Two orthogonal state machines (attachment.md §2.2/§2.3): ``upload_status``
(did the bytes arrive — session level, on ``attachments``) and
``scan_status`` (quarantine verdict — content level, on ``attachment_blobs``;
a scan happens once and every sharer sees the result).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

# attachment.md §2.2 — quarantine state machine (content level, on blobs).
BLOB_SCAN_STATUS_VALUES = ("pending", "clean", "infected", "error", "skipped")

# attachment.md §2.3 — direct-upload state machine (session level).
ATTACHMENT_UPLOAD_STATUS_VALUES = ("pending", "uploading", "completed", "failed", "expired")

# attachment.md §2.4 — polymorphic logical-FK target registry.
LINKED_TYPE_VALUES = ("issue", "comment", "chat_message")

# attachment.md §2.4 — rendering hint for the host surface.
LINK_DISPLAY_VALUES = ("inline", "card")


class AttachmentBlob(Base):
    """The blob truth row: one row per unique content per workspace (§2.2).

    ``content_hash`` is content-addressed and worker-authoritative — at
    upload-request time it carries the CLIENT-DECLARED hash (pre-validation
    and the instant-upload possession check); the quarantine worker recomputes
    the full SHA-256 from object bytes, overwrites with the true value and
    post-dedupes against an existing blob of the same hash (§3.2/§3.3).
    ``ref_count`` counts the LIVE ``attachments`` rows referencing this blob
    and is maintained atomically in the same transaction as the referencing
    row's create / soft-delete / hard-delete (§4.6).
    """

    __tablename__ = "attachment_blobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    storage_provider: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'s3'")
    )
    storage_bucket: Mapped[str] = mapped_column(TEXT, nullable=False)
    storage_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(TEXT, default=None)
    extension: Mapped[str | None] = mapped_column(TEXT, default=None)
    is_image: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    image_width: Mapped[int | None] = mapped_column(Integer, default=None)
    image_height: Mapped[int | None] = mapped_column(Integer, default=None)
    thumbnail_keys: Mapped[dict | None] = mapped_column(JSONB, default=None)
    scan_status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'pending'")
    )
    scan_detail: Mapped[dict | None] = mapped_column(JSONB, default=None)
    ref_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("file_size > 0", name="attachment_blobs_file_size_pos"),
        CheckConstraint(
            f"scan_status IN {BLOB_SCAN_STATUS_VALUES!r}",
            name="attachment_blobs_scan_status",
        ),
        CheckConstraint("ref_count >= 0", name="attachment_blobs_ref_count_nn"),
        # Concurrent de-dup serialization (T24): one blob row per content.
        UniqueConstraint(
            "workspace_id", "content_hash", name="uq_attachment_blobs_ws_hash"
        ),
        # Composite-FK reference target for attachments.blob_id (README §6.2).
        Index("uq_attachment_blobs_ws_id", "workspace_id", "id", unique=True),
        # Quarantine sweep (worker SKIP LOCKED, README §2.2).
        Index(
            "idx_blobs_quarantine",
            "created_at",
            postgresql_where=text("scan_status = 'pending'"),
        ),
        # GC candidates: unreferenced blobs whose object may be deleted.
        Index(
            "idx_blobs_refcount",
            "storage_key",
            postgresql_where=text("ref_count = 0"),
        ),
    )


class Attachment(Base):
    """An independent attachment record referencing a shared blob (§2.3).

    ``upload_status='completed'`` means the bytes arrived — NOT that the file
    is usable: the visibility gate is the referenced blob's ``scan_status``
    (§2.2 CRITICAL). Dedup only SHARES the blob; this row's uploader, links,
    lifecycle and deletion are always independent (§4.6).
    """

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    uploader_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    blob_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    file_name: Mapped[str] = mapped_column(TEXT, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'pending'")
    )
    # Client-supplied Idempotency-Key for upload-request de-dup (README
    # §6.5/§6.14 — duplicate keys return the first result). Workspace-scoped
    # partial unique index; NULL (no key) never collides.
    idempotency_key: Mapped[str | None] = mapped_column(TEXT, default=None)
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("file_size > 0", name="attachments_file_size_pos"),
        CheckConstraint(
            f"upload_status IN {ATTACHMENT_UPLOAD_STATUS_VALUES!r}",
            name="attachments_upload_status",
        ),
        # Composite-FK reference target for attachment_links / upload_sessions
        # (README §6.2).
        Index("uq_attachments_ws_id", "workspace_id", "id", unique=True),
        Index(
            "idx_attachments_uploader", "workspace_id", "uploader_id", "created_at"
        ),
        # Orphan sweep: incomplete uploads past expires_at (README §9).
        Index(
            "idx_attachments_pending",
            "expires_at",
            postgresql_where=text("upload_status <> 'completed'"),
        ),
        Index(
            "idx_attachments_active",
            "workspace_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Idempotent upload-request (README §6.5/§6.14). F6: scoped per
        # UPLOADER — replaying another member's client key must not return
        # their record; the service replays per uploader and converts a
        # concurrent same-key insert conflict into a first-result replay.
        Index(
            "uq_attachments_idem",
            "workspace_id",
            "uploader_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # Same-tenant composite FKs (README §6.2): blob truth + unified roster.
        ForeignKeyConstraint(
            ("workspace_id", "blob_id"),
            ("attachment_blobs.workspace_id", "attachment_blobs.id"),
            ondelete="RESTRICT",
            name="attachments_blob_id_attachment_blobs",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "uploader_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="attachments_uploader_id_members",
        ),
    )


class AttachmentLink(Base):
    """Polymorphic association to the host entity (§2.4).

    ``linked_id`` is a LOGICAL FK (issue / comment / chat_message) — no
    physical constraint for the polymorphic target (README §6.2 rule 4); the
    row carries ``workspace_id`` and the service layer validates existence
    per ``linked_type`` plus host-level read/write authorization.
    """

    __tablename__ = "attachment_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    attachment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    linked_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    linked_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    display: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'card'")
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"linked_type IN {LINKED_TYPE_VALUES!r}", name="attachment_links_linked_type"),
        CheckConstraint(f"display IN {LINK_DISPLAY_VALUES!r}", name="attachment_links_display"),
        # One association per (attachment, target).
        UniqueConstraint(
            "attachment_id", "linked_type", "linked_id", name="uq_attachment_link"
        ),
        # Fetch attachments of a host, ordered.
        Index("idx_links_target", "workspace_id", "linked_type", "linked_id", "position"),
        ForeignKeyConstraint(
            ("workspace_id", "attachment_id"),
            ("attachments.workspace_id", "attachments.id"),
            ondelete="CASCADE",
            name="attachment_links_attachment_id_attachments",
        ),
    )


class UploadSession(Base):
    """Multipart upload ledger (§2.5 — resumable chunked uploads)."""

    __tablename__ = "upload_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    attachment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    upload_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    part_size: Mapped[int] = mapped_column(Integer, nullable=False)
    parts: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("part_size > 0", name="upload_sessions_part_size_pos"),
        Index("uq_upload_sessions_ws_id", "workspace_id", "id", unique=True),
        ForeignKeyConstraint(
            ("workspace_id", "attachment_id"),
            ("attachments.workspace_id", "attachments.id"),
            ondelete="CASCADE",
            name="upload_sessions_attachment_id_attachments",
        ),
    )


class AttachmentQuota(Base):
    """Optional per-workspace quota overrides (§2.6).

    No row → the deployment defaults from ``Settings`` apply. ``allowed_mimes``
    NULL → the module default allowlist. ``used_bytes`` is a cached aggregate;
    the upload-request pre-check recomputes usage from blob truth (ref_count >
    0) under the quota row's lock so concurrent uploads cannot race past the
    total quota.
    """

    __tablename__ = "attachment_quotas"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    max_file_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    allowed_mimes: Mapped[list | None] = mapped_column(JSONB, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("max_file_bytes > 0", name="attachment_quotas_max_file_pos"),
        CheckConstraint("total_bytes > 0", name="attachment_quotas_total_pos"),
        CheckConstraint("used_bytes >= 0", name="attachment_quotas_used_nn"),
    )
