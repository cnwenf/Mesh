"""Squad models — the multi-agent orchestration unit (squad.md §2 owns these).

A ``squad`` groups roster members (agents and humans) under one or more
leaders. Handing an issue to a squad creates a root ``squad_task``; the leader
decomposes it into a tree of child ``squad_task`` rows (self-referencing
``parent_task_id`` / redundant ``root_task_id``) ordered by a dependency DAG
(``squad_task_dependencies``) and coarse ``stage`` batches. Orchestration chatter
lives in ``squad_messages`` and every meaningful action is appended to
``squad_activity``.

``issue_squad_assignments`` is the AUTHORITATIVE identity of "which squad is
carrying this issue" (squad.md §2.5 / README §6.9): each issue has AT MOST one
``status='active'`` row (partial unique index), and reassignment is judged by
this row — never by the ``issues.assignee_id`` value, because one leader may lead
several squads.

Per README §6.1 NO ``*_type``/``*_kind`` discriminator columns are stored —
human/agent is resolved by JOINing ``members.member_type``. System actors use the
``('member','system')`` null-FK pattern (messages ``kind='system'`` /
activity ``actor_kind='system'``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

SQUAD_KIND_VALUES = ("standing", "adhoc", "task_scoped")
SQUAD_STATUS_VALUES = ("active", "archived")
SQUAD_LEADER_MODE_VALUES = ("single", "multi")
SQUAD_MEMBER_ROLE_VALUES = ("leader", "member", "observer")
SQUAD_TASK_STATUS_VALUES = (
    "pending",
    "decomposing",
    "awaiting_plan_approval",
    "dispatching",
    "in_progress",
    "blocked",
    "aggregating",
    "done",
    "failed",
    "cancelled",
)
SQUAD_ASSIGNMENT_STATUS_VALUES = ("active", "cancelled", "completed")
SQUAD_MESSAGE_KIND_VALUES = ("chat", "instruction", "report", "system", "context")
SQUAD_ACTIVITY_ACTOR_KIND_VALUES = ("member", "system")


class Squad(Base):
    """A squad — the orchestration unit (squad.md §2.2)."""

    __tablename__ = "squads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(TEXT, default=None)
    # Persistent leader instructions (§2.2): standing directives the leader
    # reads on every takeover, beyond per-task briefs and pinned context.
    instructions: Mapped[str | None] = mapped_column(TEXT, default=None)
    avatar_url: Mapped[str | None] = mapped_column(TEXT, default=None)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'standing'"))
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    leader_mode: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'single'"))
    primary_leader_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    require_plan_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    max_decompose_depth: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("2")
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    archived_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 80", name="squads_name_length"
        ),
        CheckConstraint(f"kind IN {SQUAD_KIND_VALUES!r}", name="squads_kind"),
        CheckConstraint(f"status IN {SQUAD_STATUS_VALUES!r}", name="squads_status"),
        CheckConstraint(
            f"leader_mode IN {SQUAD_LEADER_MODE_VALUES!r}", name="squads_leader_mode"
        ),
        CheckConstraint(
            "max_decompose_depth BETWEEN 1 AND 4", name="squads_max_decompose_depth"
        ),
        # Composite-FK reference target (README §6.2) — a UNIQUE CONSTRAINT
        # (not a unique index) so the migration DDL and the model agree.
        UniqueConstraint("workspace_id", "id", name="uq_squads_ws_id"),
        # §2.9 indexes.
        Index(
            "uq_squads_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND status = 'active'"),
        ),
        Index(
            "idx_squads_list",
            "workspace_id",
            "status",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_squads_kind", "workspace_id", "kind", "status"),
        # Member FKs (README §6.1/§6.2): leader / creator / archived_by → members.
        ForeignKeyConstraint(
            ("workspace_id", "primary_leader_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (primary_leader_id)",
            name="squads_primary_leader_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "creator_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="squads_creator_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "archived_by_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (archived_by_id)",
            name="squads_archived_by_id_members",
        ),
    )


class SquadMember(Base):
    """Squad membership with role; soft-deleted via ``left_at`` (squad.md §2.3)."""

    __tablename__ = "squad_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    squad_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'member'"))
    joined_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    left_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    added_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"role IN {SQUAD_MEMBER_ROLE_VALUES!r}", name="squad_members_role"),
        # One active membership per member per squad (re-join inserts a new row).
        Index(
            "uq_squad_member_active",
            "squad_id",
            "member_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
        Index(
            "idx_squad_members_active",
            "squad_id",
            "role",
            postgresql_where=text("left_at IS NULL"),
        ),
        Index("idx_squad_members_member", "member_id", postgresql_where=text("left_at IS NULL")),
        ForeignKeyConstraint(
            ("workspace_id", "squad_id"),
            ("squads.workspace_id", "squads.id"),
            ondelete="CASCADE",
            name="squad_members_squad_id_squads",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="squad_members_member_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "added_by_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (added_by_id)",
            name="squad_members_added_by_id_members",
        ),
    )


class SquadTask(Base):
    """An orchestration record wrapping an issue (squad.md §2.4)."""

    __tablename__ = "squad_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    squad_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    root_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    title_snapshot: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'pending'"))
    orchestrator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    stage: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    plan_markdown: Mapped[str | None] = mapped_column(TEXT, default=None)
    result_summary: Mapped[str | None] = mapped_column(TEXT, default=None)
    dispatched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    failure_reason: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"status IN {SQUAD_TASK_STATUS_VALUES!r}", name="squad_tasks_status"),
        CheckConstraint("depth BETWEEN 0 AND 4", name="squad_tasks_depth"),
        # Composite-FK reference target (self-refs, deps, messages, activity) —
        # a UNIQUE CONSTRAINT matching the migration DDL (drift gate, M4).
        UniqueConstraint("workspace_id", "id", name="uq_squad_tasks_ws_id"),
        # §2.9 indexes.
        Index(
            "idx_squad_tasks_squad",
            "workspace_id",
            "squad_id",
            "status",
            text("created_at DESC"),
        ),
        Index("idx_squad_tasks_tree", "root_task_id", "depth", "created_at"),
        Index("idx_squad_tasks_parent", "parent_task_id", "status"),
        Index("idx_squad_tasks_assignee", "assignee_id", "status"),
        Index("idx_squad_tasks_issue", "workspace_id", "issue_id"),
        Index(
            "idx_squad_tasks_active",
            "squad_id",
            postgresql_where=text("status NOT IN ('done','failed','cancelled')"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "squad_id"),
            ("squads.workspace_id", "squads.id"),
            ondelete="CASCADE",
            name="squad_tasks_squad_id_squads",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="squad_tasks_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "parent_task_id"),
            ("squad_tasks.workspace_id", "squad_tasks.id"),
            ondelete="CASCADE",
            name="squad_tasks_parent_task_id_squad_tasks",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "root_task_id"),
            ("squad_tasks.workspace_id", "squad_tasks.id"),
            ondelete="CASCADE",
            name="squad_tasks_root_task_id_squad_tasks",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "orchestrator_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (orchestrator_id)",
            name="squad_tasks_orchestrator_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "assignee_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (assignee_id)",
            name="squad_tasks_assignee_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "execution_id"),
            ("task_executions.workspace_id", "task_executions.id"),
            ondelete="SET NULL (execution_id)",
            name="squad_tasks_execution_id_task_executions",
        ),
    )


class IssueSquadAssignment(Base):
    """Unique-active identity of which squad carries an issue (squad.md §2.5)."""

    __tablename__ = "issue_squad_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    squad_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    root_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    leader_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    cancel_reason: Mapped[str | None] = mapped_column(TEXT, default=None)
    assigned_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN {SQUAD_ASSIGNMENT_STATUS_VALUES!r}",
            name="issue_squad_assignments_status",
        ),
        # Composite-FK reference target (README §6.2) — a UNIQUE CONSTRAINT
        # matching the migration DDL (drift gate, M4).
        UniqueConstraint("workspace_id", "id", name="uq_issue_squad_assignments_ws_id"),
        # THE unique-identity guarantee: at most one active assignment per issue.
        Index(
            "uq_issue_squad_active",
            "issue_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("idx_issue_squad_assignments_squad", "squad_id", "status"),
        Index("idx_issue_squad_assignments_issue", "issue_id", text("assigned_at DESC")),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="issue_squad_assignments_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "squad_id"),
            ("squads.workspace_id", "squads.id"),
            ondelete="CASCADE",
            name="issue_squad_assignments_squad_id_squads",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "root_task_id"),
            ("squad_tasks.workspace_id", "squad_tasks.id"),
            ondelete="SET NULL (root_task_id)",
            name="issue_squad_assignments_root_task_id_squad_tasks",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "leader_member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="issue_squad_assignments_leader_member_id_members",
        ),
    )


class SquadTaskDependency(Base):
    """DAG edge: ``task_id`` waits for ``depends_on_task_id`` (squad.md §2.6)."""

    __tablename__ = "squad_task_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("task_id <> depends_on_task_id", name="squad_task_deps_not_self"),
        Index("uq_task_dep", "task_id", "depends_on_task_id", unique=True),
        Index("idx_dep_task", "task_id"),
        Index("idx_dep_blocker", "depends_on_task_id"),
        ForeignKeyConstraint(
            ("workspace_id", "task_id"),
            ("squad_tasks.workspace_id", "squad_tasks.id"),
            ondelete="CASCADE",
            name="squad_task_deps_task_id_squad_tasks",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "depends_on_task_id"),
            ("squad_tasks.workspace_id", "squad_tasks.id"),
            ondelete="CASCADE",
            name="squad_task_deps_depends_on_task_id_squad_tasks",
        ),
    )


class SquadMessage(Base):
    """Group-chat style orchestration message (squad.md §2.7)."""

    __tablename__ = "squad_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    squad_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    sender_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'chat'"))
    body_markdown: Mapped[str] = mapped_column(TEXT, nullable=False)
    body_html: Mapped[str | None] = mapped_column(TEXT, default=None)
    body_text: Mapped[str | None] = mapped_column(TEXT, default=None)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    attachment_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"kind IN {SQUAD_MESSAGE_KIND_VALUES!r}", name="squad_messages_kind"),
        # System messages carry a NULL sender; member messages MUST have one.
        CheckConstraint(
            "kind = 'system' OR sender_id IS NOT NULL", name="squad_messages_sender_identity"
        ),
        Index(
            "idx_messages_squad",
            "workspace_id",
            "squad_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_messages_task",
            "squad_id",
            "task_id",
            "created_at",
            postgresql_where=text("task_id IS NOT NULL"),
        ),
        Index("idx_messages_recipient", "recipient_id", "created_at"),
        Index("idx_messages_pinned", "squad_id", postgresql_where=text("pinned = true")),
        ForeignKeyConstraint(
            ("workspace_id", "squad_id"),
            ("squads.workspace_id", "squads.id"),
            ondelete="CASCADE",
            name="squad_messages_squad_id_squads",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "task_id"),
            ("squad_tasks.workspace_id", "squad_tasks.id"),
            ondelete="CASCADE",
            name="squad_messages_task_id_squad_tasks",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "sender_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (sender_id)",
            name="squad_messages_sender_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "recipient_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (recipient_id)",
            name="squad_messages_recipient_id_members",
        ),
    )


class SquadActivity(Base):
    """Append-only collaboration timeline / audit (squad.md §2.8)."""

    __tablename__ = "squad_activity"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    squad_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    actor_kind: Mapped[str] = mapped_column(TEXT, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    action: Mapped[str] = mapped_column(TEXT, nullable=False)
    target_type: Mapped[str | None] = mapped_column(TEXT, default=None)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"actor_kind IN {SQUAD_ACTIVITY_ACTOR_KIND_VALUES!r}",
            name="squad_activity_actor_kind",
        ),
        # System actors carry a NULL actor_id; member actors MUST have one.
        CheckConstraint(
            "actor_kind = 'system' OR actor_id IS NOT NULL",
            name="squad_activity_actor_identity",
        ),
        Index("idx_activity_squad", "workspace_id", "squad_id", text("created_at DESC")),
        Index(
            "idx_activity_task",
            "squad_id",
            "task_id",
            "created_at",
            postgresql_where=text("task_id IS NOT NULL"),
        ),
        Index("idx_activity_actor", "actor_kind", "actor_id", "created_at"),
        ForeignKeyConstraint(
            ("workspace_id", "squad_id"),
            ("squads.workspace_id", "squads.id"),
            ondelete="CASCADE",
            name="squad_activity_squad_id_squads",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "task_id"),
            ("squad_tasks.workspace_id", "squad_tasks.id"),
            ondelete="CASCADE",
            name="squad_activity_task_id_squad_tasks",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "actor_id"),
            ("members.workspace_id", "members.id"),
            ondelete="SET NULL (actor_id)",
            name="squad_activity_actor_id_members",
        ),
    )


__all__ = [
    "Squad",
    "SquadMember",
    "SquadTask",
    "IssueSquadAssignment",
    "SquadTaskDependency",
    "SquadMessage",
    "SquadActivity",
    "SQUAD_KIND_VALUES",
    "SQUAD_STATUS_VALUES",
    "SQUAD_LEADER_MODE_VALUES",
    "SQUAD_MEMBER_ROLE_VALUES",
    "SQUAD_TASK_STATUS_VALUES",
    "SQUAD_ASSIGNMENT_STATUS_VALUES",
    "SQUAD_MESSAGE_KIND_VALUES",
    "SQUAD_ACTIVITY_ACTOR_KIND_VALUES",
]
