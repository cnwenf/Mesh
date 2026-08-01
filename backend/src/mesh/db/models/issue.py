"""Issue models — the system's atomic work unit (issue.md §2 owns these tables).

``issues`` carries the R2 immutable-numbering namespace
(``identifier_namespace_key`` + ``number`` + ``identifier``, fixed at creation
and NEVER changed by cross-project moves, README §6.3), the two-layer state
model (custom ``status_id`` → ``issue_statuses`` with a stable
``state_category`` denormalized for aggregation/kanban columns), the
self-referencing parent tree (composite self-FK, README §6.2 rule 7) and the
optimistic-concurrency ``version``.

``issue_dependencies`` is a SEPARATE directed graph (weak "order" relation)
from the parent tree (strong "composition" relation). Cycle prevention is
serialized by a workspace-level advisory transaction lock plus a reachability
walk (issue.md §2.5 rules 3–4, README §9 T12).

Every cross-module reference is a same-tenant composite FK (README §6.2);
nullable references use PG16 column-level ``ON DELETE SET NULL (col)`` so
deleting the referenced row only clears the reference column and keeps
``workspace_id`` non-NULL (rule 6, README §9 T18); ``status_id`` is RESTRICT
(issues must be migrated off a status before it can be deleted).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

# Stable semantic categories (issue.md §1.2.3) — the aggregation/kanban layer.
STATE_CATEGORY_VALUES = (
    "backlog",
    "todo",
    "in_progress",
    "in_review",
    "blocked",
    "done",
    "cancelled",
)
ISSUE_PRIORITY_VALUES = ("none", "low", "medium", "high", "urgent")
ESTIMATE_UNIT_VALUES = ("points", "hours")
DEPENDENCY_TYPE_VALUES = ("blocks", "blocked_by", "relates_to", "duplicates")

# Partial expression unique indexes COALESCE NULL project scopes onto this
# sentinel (COALESCE expressions cannot appear in table-level UNIQUE
# constraints, README §6.3).
NULL_PROJECT_SENTINEL = "00000000-0000-0000-0000-000000000000"

TITLE_MIN_LENGTH = 1
TITLE_MAX_LENGTH = 255
STATUS_NAME_MIN_LENGTH = 1
STATUS_NAME_MAX_LENGTH = 50
TEMPLATE_NAME_MIN_LENGTH = 1
TEMPLATE_NAME_MAX_LENGTH = 120

DEFAULT_INBOX_PREFIX = "WS"


class IssueStatus(Base):
    """A custom status definition scoped to a workspace or one project.

    Exactly one ``is_default`` per scope is enforced by the partial expression
    unique index ``uq_issue_statuses_default``; at-least-one-default is
    guaranteed transactionally by the seeding/self-heal helpers (README §6.3).
    """

    __tablename__ = "issue_statuses"

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
    category: Mapped[str] = mapped_column(TEXT, nullable=False)
    color: Mapped[str | None] = mapped_column(TEXT, default=None)
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # 严格模式「允许的下一步」目标状态 id 列表(§4.4;JSON 字符串数组,
    # 空数组 = 未配置;迁移 0009,README §6.14 invalid_status_transition)
    allowed_transitions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"category IN {STATE_CATEGORY_VALUES!r}", name="ck_issue_statuses_category"
        ),
        CheckConstraint(
            f"char_length(name) BETWEEN {STATUS_NAME_MIN_LENGTH} AND {STATUS_NAME_MAX_LENGTH}",
            name="ck_issue_statuses_name_len",
        ),
        CheckConstraint(
            "jsonb_typeof(allowed_transitions) = 'array'",
            name="ck_issue_statuses_allowed_transitions",
        ),
        # Composite-FK reference target for issues.status_id (README §6.2).
        Index("uq_issue_statuses_ws_id", "workspace_id", "id", unique=True),
        # Scope-unique name / unique default via partial expression indexes
        # (COALESCE cannot appear in a table-level UNIQUE, README §6.3).
        Index(
            "uq_issue_statuses_name",
            "workspace_id",
            text(f"COALESCE(project_id, '{NULL_PROJECT_SENTINEL}')"),
            "name",
            unique=True,
        ),
        Index(
            "uq_issue_statuses_default",
            "workspace_id",
            text(f"COALESCE(project_id, '{NULL_PROJECT_SENTINEL}')"),
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index(
            "idx_issue_statuses_scope",
            "workspace_id",
            text(f"COALESCE(project_id, '{NULL_PROJECT_SENTINEL}')"),
            "category",
        ),
        # Project-private statuses cascade with their project.
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="issue_statuses_project_id_projects",
        ),
    )


class Issue(Base):
    """The atomic work unit (issue.md §2.2)."""

    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Current project membership ONLY — cross-project moves change this column
    # and nothing about the identifier (README §6.3).
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    # Immutable numbering namespace (README §6.3): fixed at creation.
    identifier_namespace_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    identifier: Mapped[str] = mapped_column(TEXT, nullable=False)
    title: Mapped[str] = mapped_column(TEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(TEXT, default=None)
    status_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    state_category: Mapped[str] = mapped_column(TEXT, nullable=False)
    priority: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'none'")
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    estimate: Mapped[Decimal | None] = mapped_column(Numeric, default=None)
    estimate_unit: Mapped[str | None] = mapped_column(TEXT, default=None)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)
    start_date: Mapped[date | None] = mapped_column(Date, default=None)
    milestone_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    position: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
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
        # Numbering uniqueness (README §6.3): namespace-level replaces the
        # abolished UNIQUE(project_id, number); workspace-level catches
        # everything else (including project-less inbox issues).
        Index(
            "uq_issue_namespace_number",
            "workspace_id",
            "identifier_namespace_key",
            "number",
            unique=True,
        ),
        Index("uq_issues_identifier", "workspace_id", "identifier", unique=True),
        # Composite-FK reference target for dependencies/activity/labels.
        Index("uq_issues_ws_id", "workspace_id", "id", unique=True),
        CheckConstraint("parent_id <> id", name="ck_issues_no_self_parent"),
        CheckConstraint(
            "due_date IS NULL OR start_date IS NULL OR due_date >= start_date",
            name="ck_issues_due_after_start",
        ),
        CheckConstraint(
            f"state_category IN {STATE_CATEGORY_VALUES!r}", name="ck_issues_state_category"
        ),
        CheckConstraint(f"priority IN {ISSUE_PRIORITY_VALUES!r}", name="ck_issues_priority"),
        CheckConstraint(
            f"estimate_unit IN {ESTIMATE_UNIT_VALUES!r}", name="ck_issues_estimate_unit"
        ),
        CheckConstraint(
            f"char_length(title) BETWEEN {TITLE_MIN_LENGTH} AND {TITLE_MAX_LENGTH}",
            name="ck_issues_title_len",
        ),
        CheckConstraint("version >= 1", name="ck_issues_version_pos"),
        # -- composite FKs (README §6.2) -------------------------------------
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="SET NULL (project_id)",
            name="issues_project_id_projects",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "status_id"),
            ("issue_statuses.workspace_id", "issue_statuses.id"),
            ondelete="RESTRICT",
            name="issues_status_id_issue_statuses",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "assignee_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (assignee_id)",
            name="issues_assignee_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "reporter_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (reporter_id)",
            name="issues_reporter_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "milestone_id"),
            ("milestones.workspace_id", "milestones.id"),
            ondelete="SET NULL (milestone_id)",
            name="issues_milestone_id_milestones",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "cycle_id"),
            ("cycles.workspace_id", "cycles.id"),
            ondelete="SET NULL (cycle_id)",
            name="issues_cycle_id_cycles",
        ),
        # Parent: composite SELF-FK — explicit same-tenant, not "natural"
        # (README §6.2 rule 7).
        ForeignKeyConstraint(
            ("workspace_id", "parent_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="issues_parent_id_issues",
        ),
        # -- §2.3 performance indexes -----------------------------------------
        Index(
            "idx_issues_workspace",
            "workspace_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_issues_project_status",
            "project_id",
            "state_category",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_issues_assignee",
            "assignee_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_issues_reporter",
            "reporter_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_issues_parent",
            "parent_id",
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
        Index(
            "idx_issues_cycle",
            "cycle_id",
            postgresql_where=text("cycle_id IS NOT NULL"),
        ),
        Index(
            "idx_issues_milestone",
            "milestone_id",
            postgresql_where=text("milestone_id IS NOT NULL"),
        ),
        Index(
            "idx_issues_due",
            "due_date",
            postgresql_where=text("due_date IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("idx_issues_position", "project_id", "state_category", "position"),
        Index(
            "idx_issues_priority",
            "workspace_id",
            "priority",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # -- Search indexes (migration 0035, search-command-palette.md §2.2) --
        # Expression indexes over the SINGLE normalization entry point; the
        # query path must call public.mesh_search_norm() verbatim to match.
        Index(
            "idx_issues_title_trgm",
            text("mesh_search_norm(title)"),
            postgresql_using="gin",
            postgresql_ops={"mesh_search_norm(title)": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_issues_title_prefix",
            "workspace_id",
            text("mesh_search_norm(title)"),
            postgresql_ops={"mesh_search_norm(title)": "text_pattern_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_issues_identifier_prefix",
            "workspace_id",
            text("mesh_search_norm(identifier)"),
            postgresql_ops={"mesh_search_norm(identifier)": "text_pattern_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Tenant/status support index for the issue search query (§2.2).
        Index(
            "idx_issues_ws_not_deleted",
            "workspace_id",
            "project_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class IssueDependency(Base):
    """A directed dependency edge (issue.md §2.2 issue_dependencies).

    Stored normalized: ``blocks`` edges as ``(issue_id blocks depends_on_id)``;
    the API normalizes ``blocked_by`` requests into the inverted ``blocks``
    edge and expands both directions on read (issue.md §2.2 note). Cycle
    prevention: workspace advisory lock + reachability walk (§2.5 rule 4).
    """

    __tablename__ = "issue_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    depends_on_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'relates_to'")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("uq_issue_dependencies_edge", "issue_id", "depends_on_id", "type", unique=True),
        CheckConstraint("issue_id <> depends_on_id", name="ck_issue_deps_no_self_edge"),
        CheckConstraint(f"type IN {DEPENDENCY_TYPE_VALUES!r}", name="ck_issue_deps_type"),
        Index("idx_issue_deps_issue", "issue_id"),
        Index("idx_issue_deps_on", "depends_on_id"),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="issue_deps_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "depends_on_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="issue_deps_depends_on_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (created_by)",
            name="issue_deps_created_by_members",
        ),
    )


class IssueActivity(Base):
    """Append-only change trail (issue.md §2.2 issue_activity).

    The service writes one row per changed field after every successful PATCH
    (old/new JSONB values + actor). High-frequency fields (position drags) may
    skip the trail to avoid noise (§2.2 note).
    """

    __tablename__ = "issue_activity"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    field: Mapped[str] = mapped_column(TEXT, nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONB, default=None)
    new_value: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("idx_issue_activity_issue", "issue_id", text("created_at DESC")),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="issue_activity_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "actor_member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (actor_member_id)",
            name="issue_activity_actor_member_id_members",
        ),
    )


class IssueTemplate(Base):
    """Issue template (issue.md §3.9): prefilled issue blueprints.

    ``template_body`` carries the prefill set (title_prefix, description,
    state_category/status_id, priority, label_ids, custom_field_values,
    estimate/estimate_unit, parent_strategy). Instantiation runs the SAME
    creation path as ``POST /workspaces/{ws}/issues``; references that have
    gone stale (deleted status/label/field) degrade into ``skipped_fields``
    instead of failing the whole instantiation.
    """

    __tablename__ = "issue_templates"

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
    description: Mapped[str | None] = mapped_column(TEXT, default=None)
    template_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"char_length(name) BETWEEN {TEMPLATE_NAME_MIN_LENGTH} AND {TEMPLATE_NAME_MAX_LENGTH}",
            name="ck_issue_templates_name_len",
        ),
        # Composite-FK reference target (README §6.2).
        Index("uq_issue_templates_ws_id", "workspace_id", "id", unique=True),
        # Scope-unique name via partial expression index (README §6.3).
        Index(
            "uq_issue_templates_name",
            "workspace_id",
            text(f"COALESCE(project_id, '{NULL_PROJECT_SENTINEL}')"),
            "name",
            unique=True,
        ),
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="SET NULL (project_id)",
            name="issue_templates_project_id_projects",
        ),
        # Creator never dangles: members are soft-deleted (RESTRICT).
        ForeignKeyConstraint(
            ("workspace_id", "created_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="issue_templates_created_by_members",
        ),
    )
