"""Workspace model — the multi-tenancy isolation root (workspace.md owns).

DDL mirrors docs/specs/validation/schema_r2_validation.sql (workspaces table)
so the runtime schema and the spec validation script stay in lockstep.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

WORKSPACE_NAME_MIN = 1
WORKSPACE_NAME_MAX = 80
DEFAULT_WORKSPACE_SETTINGS = '{"default_locale": "en"}'


class Workspace(Base):
    """A tenant: every business table references ``workspaces.id``."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    logo_url: Mapped[str | None] = mapped_column(String, default=None)
    timezone: Mapped[str] = mapped_column(String, server_default=text("'UTC'"))
    settings: Mapped[dict] = mapped_column(
        JSONB, server_default=text(f"'{DEFAULT_WORKSPACE_SETTINGS}'")
    )
    inbox_issue_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"char_length(name) BETWEEN {WORKSPACE_NAME_MIN} AND {WORKSPACE_NAME_MAX}",
            name="workspaces_name_len",
        ),
        CheckConstraint("inbox_issue_seq >= 0", name="workspaces_inbox_issue_seq_nonneg"),
        # Slug is unique among non-deleted workspaces (partial unique index).
        Index(
            "uq_workspaces_slug",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
