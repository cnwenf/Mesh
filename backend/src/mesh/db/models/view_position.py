"""View projection write-side models (kanban.md §2.7/§3.2).

A single ``issues.position`` would leak one view's drag order into every other
view that shows the same issue. ``view_issue_positions`` gives EACH view its
own (view_id, issue_id) ordering row so a drag in view A never reorders view B
(README §6.14 ordering contract). Views/issues without a stored row fall back
to the canonical ``issues.position`` order at projection time (manual order
wins, canonical default otherwise); ``issues.position`` itself is never written
by a view drag.

Multi-tenancy follows README §6.2: ``workspace_id`` is stored, both cross-module
references are same-tenant composite FKs (views/issues each expose
``UNIQUE(workspace_id, id)``), and RLS (migration 0012) adds defense-in-depth.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base


class ViewIssuePosition(Base):
    """A card's manual order within ONE view + group (kanban §2.7)."""

    __tablename__ = "view_issue_positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Composite-FK columns (constraints live in __table_args__).
    view_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    group_key: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("''"))
    # Empty string is the compatibility cell for one-dimensional views.
    sub_group_key: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("''"))
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # One ordering row per (view, issue) — a redrag upserts, never dupes.
        Index("uq_vip_view_issue", "view_id", "issue_id", unique=True),
        # In-view, in-group ordering path for the projection query.
        Index(
            "idx_vip_view_group_pos",
            "view_id",
            "group_key",
            "sub_group_key",
            "position",
        ),
        # Same-tenant composite FKs (README §6.2): the ordering row follows its
        # view and its issue (both ON DELETE CASCADE — deleting a view/issue
        # drops its ordering rows, kanban §2.7).
        ForeignKeyConstraint(
            ("workspace_id", "view_id"),
            ("views.workspace_id", "views.id"),
            ondelete="CASCADE",
            name="view_issue_positions_view_id_views",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="view_issue_positions_issue_id_issues",
        ),
    )


class ViewQuickCreateRequest(Base):
    """Creator-scoped quick-create idempotency ledger (README §6.14).

    The view row is locked for every quick-create, so recording the first
    issue in the same transaction makes concurrent retries converge without
    a second issue, counter increment, position row, or outbox event.
    """

    __tablename__ = "view_quick_create_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    view_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index(
            "uq_view_quick_create_idem",
            "view_id",
            "actor_member_id",
            "idempotency_key",
            unique=True,
        ),
        ForeignKeyConstraint(
            ("workspace_id", "view_id"),
            ("views.workspace_id", "views.id"),
            ondelete="CASCADE",
            name="view_quick_create_requests_view_id_views",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "actor_member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="view_quick_create_requests_actor_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="view_quick_create_requests_issue_id_issues",
        ),
    )
