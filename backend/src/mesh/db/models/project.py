"""Project models — the work aggregation layer (project.md §2 owns these tables).

``projects`` is the "goal box": it groups issues, owns the human-readable
identifier prefix ``key`` (``WEB-123``) and the project-level numbering
counter ``issue_seq`` (issue.md §2.4). The prefix is PERMANENTLY reserved
(README §6.3): ``uq_projects_key`` is a plain (non-partial) unique index, so
a soft-deleted or archived project's key can never be re-issued, and the
workspace-level ``identifier_prefix_registry`` agrees.

Every reference to ``members``/``projects`` is a same-tenant composite FK
(README §6.2); ``projects``/``milestones``/``cycles`` expose
``UNIQUE(workspace_id, id)`` so downstream tables (issues, views, …) can
reference them with composite FKs. Deletion semantics follow README §6.2
rule 6: column-level ``ON DELETE SET NULL (col)`` for the nullable lead,
``RESTRICT`` for the append-only update author (members are soft-deleted,
so the trail signature is permanent), ``CASCADE`` for owned child rows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

PROJECT_STATUS_VALUES = ("planning", "active", "paused", "completed", "cancelled")
PROJECT_HEALTH_VALUES = ("on_track", "at_risk", "off_track")
PROJECT_VISIBILITY_VALUES = ("public", "private")
PROJECT_MEMBER_ROLE_VALUES = ("lead", "member", "viewer")
MILESTONE_STATE_VALUES = ("open", "closed")
CYCLE_STATE_VALUES = ("planned", "active", "completed")


class Project(Base):
    """A project: goal box + identifier prefix source (project.md §2.2)."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    key: Mapped[str] = mapped_column(TEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(TEXT, default=None)
    icon: Mapped[str | None] = mapped_column(TEXT, default=None)
    color: Mapped[str | None] = mapped_column(TEXT, default=None)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'planning'"))
    health: Mapped[str | None] = mapped_column(TEXT, default=None)
    visibility: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'public'")
    )
    lead_member_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    start_date: Mapped[date | None] = mapped_column(Date, default=None)
    target_date: Mapped[date | None] = mapped_column(Date, default=None)
    progress_cache: Mapped[float | None] = mapped_column(REAL, default=None)
    issue_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"status IN {PROJECT_STATUS_VALUES!r}", name="projects_status"),
        CheckConstraint(f"health IN {PROJECT_HEALTH_VALUES!r}", name="projects_health"),
        CheckConstraint(
            f"visibility IN {PROJECT_VISIBILITY_VALUES!r}", name="projects_visibility"
        ),
        CheckConstraint(
            "target_date IS NULL OR start_date IS NULL OR target_date >= start_date",
            name="projects_target_after_start",
        ),
        CheckConstraint("issue_seq >= 0", name="projects_issue_seq_nonneg"),
        # Composite-FK reference target for the whole system (README §6.2).
        Index("uq_projects_ws_id", "workspace_id", "id", unique=True),
        # Prefix permanent reservation (README §6.3): plain (NON-partial)
        # unique index — soft-deleted/archived keys are never re-issued.
        Index("uq_projects_key", "workspace_id", "key", unique=True),
        # Optional same-workspace name de-dup over live projects only.
        Index(
            "uq_projects_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_projects_workspace",
            "workspace_id",
            "status",
            postgresql_where=text("deleted_at IS NULL AND archived_at IS NULL"),
        ),
        Index("idx_projects_lead", "lead_member_id"),
        # Search indexes (migration 0035, search-command-palette.md §2.2):
        # expression indexes over the single normalization entry point.
        Index(
            "idx_projects_name_trgm",
            text("mesh_search_norm(name)"),
            postgresql_using="gin",
            postgresql_ops={"mesh_search_norm(name)": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_projects_name_prefix",
            "workspace_id",
            text("mesh_search_norm(name)"),
            postgresql_ops={"mesh_search_norm(name)": "text_pattern_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Lead: same-tenant composite FK, column-level SET NULL so deleting a
        # member only clears the reference column (PG16, README §6.2 rule 6).
        ForeignKeyConstraint(
            ("workspace_id", "lead_member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (lead_member_id)",
            name="projects_lead_member_id_members",
        ),
    )


class ProjectUpdate(Base):
    """Append-only health/status trail (project.md §2.2 project_updates).

    Insert-only at the service level; each row snapshots the health/status
    the author recorded, and the service writes the values back to
    ``projects`` in the same transaction. The author FK is NOT NULL +
    RESTRICT — members are soft-deleted, so a trail signature never dangles.
    """

    __tablename__ = "project_updates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    author_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    health: Mapped[str | None] = mapped_column(TEXT, default=None)
    status: Mapped[str | None] = mapped_column(TEXT, default=None)
    message: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"health IN {PROJECT_HEALTH_VALUES!r}", name="project_updates_health"
        ),
        CheckConstraint(
            f"status IN {PROJECT_STATUS_VALUES!r}", name="project_updates_status"
        ),
        Index("idx_project_updates_project", "project_id", text("created_at DESC")),
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="project_updates_project_id_projects",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "author_member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="project_updates_author_member_id_members",
        ),
    )


class Milestone(Base):
    """A target-date goal box inside a project (project.md §2.2 milestones).

    Overdue is a DERIVED state (``state='open' AND target_date < today``) —
    computed in responses, never stored.
    """

    __tablename__ = "milestones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(TEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(TEXT, default=None)
    target_date: Mapped[date | None] = mapped_column(Date, default=None)
    state: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'open'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"state IN {MILESTONE_STATE_VALUES!r}", name="milestones_state"),
        # Composite-FK reference target for issues.milestone_id (README §6.2).
        Index("uq_milestones_ws_id", "workspace_id", "id", unique=True),
        Index("idx_milestones_project", "project_id", "state"),
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="milestones_project_id_projects",
        ),
    )


class Cycle(Base):
    """An iteration / sprint time box (project.md §2.2 cycles).

    Workspace-level by default; may bind to one project. Orthogonal to
    projects: an issue can belong to one project AND one cycle.
    """

    __tablename__ = "cycles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    starts_at: Mapped[date] = mapped_column(Date, nullable=False)
    ends_at: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'planned'"))
    auto_roll: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"state IN {CYCLE_STATE_VALUES!r}", name="cycles_state"),
        CheckConstraint("ends_at >= starts_at", name="cycles_ends_after_starts"),
        # Composite-FK reference target for issues.cycle_id (README §6.2).
        Index("uq_cycles_ws_id", "workspace_id", "id", unique=True),
        Index("idx_cycles_workspace", "workspace_id", "starts_at"),
        Index("idx_cycles_state", "workspace_id", "state"),
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="cycles_project_id_projects",
        ),
    )


class ProjectMember(Base):
    """Project-level membership / visibility (project.md §2.2 project_members)."""

    __tablename__ = "project_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'member'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"role IN {PROJECT_MEMBER_ROLE_VALUES!r}", name="project_members_role"),
        Index("uq_project_members", "project_id", "member_id", unique=True),
        Index("idx_project_members_member", "member_id"),
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="project_members_project_id_projects",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="project_members_member_id_members",
        ),
    )


class ProjectTemplate(Base):
    """Project template (project.md §3.2b): prefilled project blueprints.

    ``template_body`` carries the prefill set (description, default
    visibility, key suggestion, initial milestones/cycles, default view
    config, status-set seed). Instantiation creates a real project in one
    transaction (key validated against the prefix registry) and degrades
    gracefully for prefill items whose owning module is not built yet.
    """

    __tablename__ = "project_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    template_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Composite-FK reference target (README §6.2).
        Index("uq_project_templates_ws_id", "workspace_id", "id", unique=True),
        Index("uq_project_templates_ws_name", "workspace_id", "name", unique=True),
        # Creator never dangles: members are soft-deleted (RESTRICT).
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="project_templates_created_by_members",
        ),
    )
