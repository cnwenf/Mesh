"""Member models — the unified roster (member.md §2.2 owns the table, README §6.1).

Created by the workspace increment because invitations, audit and RBAC all
reference ``members``; the member.md increment builds roster CRUD on top.

``members.id`` is the system-wide reference key (assignee, author, recipient —
all point here). The ``agent_id`` composite FK to ``agents(workspace_id, id)``
was added by the agent.md increment once the agents table existed (the
validation script's own deferred-FK pattern); the polymorphic CHECK below
enforces the human/agent shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, text
from sqlalchemy.dialects.postgresql import TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

MEMBER_TYPE_VALUES = ("human", "agent")
MEMBER_ROLE_VALUES = ("owner", "admin", "member", "guest")
MEMBER_STATUS_VALUES = ("active", "disabled", "removed")
MEMBER_PROJECT_ACCESS_PERMISSIONS = ("read", "write")


class Member(Base):
    """A roster entry: exactly one of a human user or an AI agent."""

    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), default=None
    )
    # Composite FK → agents(workspace_id, id) (agent.md increment, README §6.2):
    # an agent roster row can only reference an agent of the SAME workspace.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    role: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'member'"))
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    display_override: Mapped[str | None] = mapped_column(TEXT, default=None)
    # Search-only projection = public.mesh_search_norm(README §6.1 display-name
    # resolution chain). NEVER used for rendering; kept in sync by database
    # triggers (migration 0034, search-command-palette.md §2.2).
    search_name: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("''")
    )
    joined_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    disabled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"member_type IN {MEMBER_TYPE_VALUES!r}", name="members_member_type"),
        CheckConstraint(f"role IN {MEMBER_ROLE_VALUES!r}", name="members_role"),
        CheckConstraint(f"status IN {MEMBER_STATUS_VALUES!r}", name="members_status"),
        # Exactly one identity pointer (README §6.1 polymorphic CHECK).
        CheckConstraint(
            "(member_type = 'human' AND user_id IS NOT NULL AND agent_id IS NULL) "
            "OR (member_type = 'agent' AND agent_id IS NOT NULL AND user_id IS NULL)",
            name="members_identity_exactly_one",
        ),
        # Agents can never be owners (member.md §2.2).
        CheckConstraint("member_type = 'human' OR role <> 'owner'", name="members_agent_not_owner"),
        # Composite-FK reference target for the whole system (README §6.2).
        Index("uq_members_ws_id", "workspace_id", "id", unique=True),
        # One roster row per user / agent per workspace (partial uniques).
        Index(
            "uq_members_ws_user",
            "workspace_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_members_ws_agent",
            "workspace_id",
            "agent_id",
            unique=True,
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
        Index("idx_members_workspace", "workspace_id", "status"),
        Index("idx_members_user", "user_id"),
        Index("idx_members_agent", "agent_id"),
        Index("idx_members_type", "workspace_id", "member_type"),
        # Same-tenant composite FK (README §6.1 / §6.2): agent roster rows
        # reference an agent of THIS workspace; cross-workspace references
        # fail at INSERT (T1).
        ForeignKeyConstraint(
            ("workspace_id", "agent_id"),
            ("agents.workspace_id", "agents.id"),
            ondelete="CASCADE",
            name="members_agent_id_agents",
        ),
    )


class MemberProjectAccess(Base):
    """Guest project-level visibility grants (member.md §2.3 / M12).

    The hook the project module consults to decide whether a guest may see a
    project. The ``project_id`` composite FK to ``projects(workspace_id, id)``
    (ON DELETE CASCADE) was added by the project.md increment.
    """

    __tablename__ = "member_project_access"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    permission: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'read'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"permission IN {MEMBER_PROJECT_ACCESS_PERMISSIONS!r}",
            name="member_project_access_permission",
        ),
        Index("uq_member_access", "member_id", "project_id", unique=True),
        Index("idx_member_access_member", "member_id"),
        # Guest and shared project must belong to the same workspace (§6.2).
        ForeignKeyConstraint(
            ("workspace_id", "member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="member_access_member_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="CASCADE",
            name="member_access_project_id_projects",
        ),
    )
