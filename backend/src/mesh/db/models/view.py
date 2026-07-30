"""View model — the saved projection config (kanban.md §2.2 owns this table).

A view is a SAVED "how to project issues" configuration: filters + group +
sort + display fields + board settings persisted as JSONB. Views never
persist issue sets themselves — every open re-runs the config against issues
at query time (the projection query itself lands with the issue-coupled
increment; this model is the issue-decoupled definition layer).

Multi-tenancy follows README §6.2: ``UNIQUE (workspace_id, id)`` exposes the
composite-FK reference target (``view_issue_positions.view_id`` etc.), both
cross-module references (project, owner member) are same-tenant composite
FKs with ON DELETE CASCADE, and RLS (migration 0011) adds defense-in-depth.

Scope uniqueness (README §6.3): "workspace-level OR project-level" name and
default-view uniqueness use partial EXPRESSION unique indexes over
``COALESCE(project_id, nil-uuid)`` — a table-level UNIQUE constraint cannot
carry a COALESCE expression.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

VIEW_LAYOUT_VALUES = ("board", "list", "timeline", "table")
VIEW_VISIBILITY_VALUES = ("private", "shared")

# NULL project_id = workspace-level scope; the sentinel stands in for it in
# the partial expression unique indexes (README §6.3).
_NULL_SCOPE_SENTINEL = "00000000-0000-0000-0000-000000000000"


class View(Base):
    """A saved view: JSONB projection config + board settings (kanban §2.2)."""

    __tablename__ = "views"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Composite-FK columns (constraints live in __table_args__): NULL =
    # workspace-level view.
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    owner_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    layout: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'board'"))
    visibility: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'private'")
    )
    filters: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    group_by: Mapped[str | None] = mapped_column(TEXT, default=None)
    sub_group_by: Mapped[str | None] = mapped_column(TEXT, default=None)
    sort: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    display_fields: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    board_settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"layout IN {VIEW_LAYOUT_VALUES!r}", name="views_layout"
        ),
        CheckConstraint(
            f"visibility IN {VIEW_VISIBILITY_VALUES!r}", name="views_visibility"
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100", name="views_name_length"
        ),
        # Composite-FK reference target (README §6.2).
        Index("uq_views_ws_id", "workspace_id", "id", unique=True),
        # "Workspace-level OR project-level" name uniqueness (README §6.3):
        # partial expression index — COALESCE cannot sit in a table UNIQUE.
        Index(
            "uq_views_name",
            "workspace_id",
            text(f"COALESCE(project_id, '{_NULL_SCOPE_SENTINEL}')"),
            "name",
            unique=True,
        ),
        # One default view per scope (kanban §2.2 / README §6.3); the service
        # clears the previous default in the same transaction, the index is
        # the backstop.
        Index(
            "uq_views_default",
            "workspace_id",
            text(f"COALESCE(project_id, '{_NULL_SCOPE_SENTINEL}')"),
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index("idx_views_workspace", "workspace_id", "position"),
        Index(
            "idx_views_project",
            "project_id",
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        Index("idx_views_owner", "owner_member_id"),
        Index("idx_views_visibility", "workspace_id", "visibility"),
        # Search indexes (migration 0035, search-command-palette.md §2.2):
        # expression indexes over the single normalization entry point.
        Index(
            "idx_views_name_trgm",
            text("mesh_search_norm(name)"),
            postgresql_using="gin",
            postgresql_ops={"mesh_search_norm(name)": "gin_trgm_ops"},
        ),
        Index(
            "idx_views_name_prefix",
            "workspace_id",
            text("mesh_search_norm(name)"),
            postgresql_ops={"mesh_search_norm(name)": "text_pattern_ops"},
        ),
        # Same-tenant composite FKs (README §6.2): project-scoped views follow
        # the project, owner references follow the member (members are
        # soft-deleted in practice; CASCADE matches kanban §2.2 DDL).
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="views_project_id_projects",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "owner_member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="views_owner_member_id_members",
        ),
    )
