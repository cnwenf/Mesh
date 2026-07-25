"""Realtime persistence (README §6.7 — 唯一权威).

``realtime_channels`` holds the per-channel monotonic ``last_seq`` (the durable
source of truth for seq allocation). ``realtime_events`` is the replay log:
the projector writes it using ``outbox_event_id`` as the de-duplication key and
allocates the channel seq inside the same transaction. Both tables carry the
``workspace_id`` tenant key and are protected by RLS (§6.2 rule 8).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base


class RealtimeChannel(Base):
    """A channel plus its monotonic per-channel seq watermark."""

    __tablename__ = "realtime_channels"

    channel: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    last_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "channel", name="uq_realtime_channels_ws_channel"),
    )


class RealtimeEvent(Base):
    """One persisted realtime event; replay source of truth."""

    __tablename__ = "realtime_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)
    # Monotonic within the channel — allocated by the projector (§6.7).
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Single write path: outbox_events.id, at-least-once → exactly-once record.
    outbox_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        UniqueConstraint("channel", "seq", name="uq_realtime_events_channel_seq"),
        UniqueConstraint("outbox_event_id", name="uq_realtime_events_outbox_event_id"),
        # Tenant mismatch between event row and channel row is rejected at INSERT.
        ForeignKeyConstraint(
            ["workspace_id", "channel"],
            ["realtime_channels.workspace_id", "realtime_channels.channel"],
            ondelete="CASCADE",
            name="fk_realtime_events_ws_channel",
        ),
        Index("idx_realtime_events_replay", "channel", "seq"),
        Index("idx_realtime_events_ws_created", "workspace_id", "created_at"),
    )
