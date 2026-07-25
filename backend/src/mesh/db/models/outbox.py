"""Transactional outbox (README §6.6 — 唯一权威).

Business transactions INSERT ``outbox_events`` in the same transaction as the
business rows; the relay worker claims pending rows with
``FOR UPDATE SKIP LOCKED`` and dispatches them to handlers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

OUTBOX_STATUS_PENDING = "pending"
OUTBOX_STATUS_PUBLISHED = "published"
OUTBOX_STATUS_FAILED = "failed"
OUTBOX_STATUSES = (OUTBOX_STATUS_PENDING, OUTBOX_STATUS_PUBLISHED, OUTBOX_STATUS_FAILED)


class OutboxEvent(Base):
    """One outbox row: a derived action committed atomically with business data."""

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Processor de-duplication key (§6.5). NULL never conflicts.
    idempotency_key: Mapped[str | None] = mapped_column(TEXT, unique=True, default=None)
    status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text(f"'{OUTBOX_STATUS_PENDING}'")
    )
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','published','failed')", name="outbox_events_status"
        ),
        # Pending backlog scan index (§6.6).
        Index(
            "idx_outbox_pending",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )
