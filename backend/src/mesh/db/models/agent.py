"""Agent models — the AI teammate identity and configuration (agent.md §2).

``agents`` (agent.md §2.3) holds the agent-specific configuration: profile
fields, model / inference parameters (``model_config`` JSONB, §2.4),
lifecycle state machine (§4.8), visibility (§3.5) and the pointer to the
currently effective configuration version. ``agent_config_versions`` (§2.7)
is the immutable audit / rollback history — one snapshot per ``PATCH
/config`` and per rollback (rollback COPIES an old snapshot into a new
version; history is never rewritten).

Roster linkage follows README §6.1: the relation direction is
``members.agent_id → agents.id``; ``agents`` carries NO ``member_id``
reverse column. ``members.agent_id`` gained its deferred composite FK
``(workspace_id, agent_id) → agents(workspace_id, id)`` in the same
increment that created this table (README §6.2).

Same-tenant / same-parent constraints (README §6.2 rules 2/7):
* ``agent_config_versions.agent_id`` composite FK → ``agents(workspace_id, id)``
  — a version can never reference an agent in another workspace;
* ``agent_config_versions.changed_by`` composite FK → ``members(workspace_id, id)``
  — the audit actor is always a roster member of the SAME workspace;
* ``agents.active_config_version_id`` is referenced through the OVERLAPPING
  composite FK ``(workspace_id, id, active_config_version_id) →
  agent_config_versions(workspace_id, agent_id, id)`` backed by the
  ``UNIQUE(workspace_id, agent_id, id)`` overlap key — pointing agent A's
  active pointer at agent B's version (or another workspace's version) is
  rejected at INSERT time (integration test T27). The FK uses PostgreSQL 16
  column-level ``ON DELETE SET NULL (active_config_version_id)`` so a version
  cleanup never nulls the tenant key (README §6.2 rule 6).
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

AGENT_LIFECYCLE_VALUES = ("active", "paused", "disabled", "archived")
AGENT_VISIBILITY_VALUES = ("workspace", "private")
AGENT_BADGE_KIND_VALUES = ("ai",)

# Lifecycle state machine edges (agent.md §4.8). Keys are source states;
# values map action verb → target state. Any transition absent from this map
# is illegal and the service rejects it with 409 conflict.
AGENT_LIFECYCLE_TRANSITIONS: dict[str, dict[str, str]] = {
    "active": {"pause": "paused", "disable": "disabled", "archive": "archived"},
    "paused": {"resume": "active", "disable": "disabled", "archive": "archived"},
    "disabled": {"enable": "active", "archive": "archived"},
    "archived": {"restore": "active"},
}


class Agent(Base):
    """An AI teammate's workspace-scoped identity and configuration."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(TEXT, default=None)
    role_tag: Mapped[str | None] = mapped_column(TEXT, default=None)
    # Creator / owner — a human login identity (users is a global table, so
    # this FK is NOT composite; the owner must still be validated as an
    # active roster member of this workspace by the service layer).
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    slug: Mapped[str | None] = mapped_column(TEXT, default=None)
    bio: Mapped[str | None] = mapped_column(TEXT, default=None)
    badge_kind: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'ai'")
    )
    lifecycle_status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'active'")
    )
    visibility: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'workspace'")
    )
    system_instructions: Mapped[str | None] = mapped_column(TEXT, default=None)
    model_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Reserved field (agent.md §2.3): the composite FK → runtimes(workspace_id, id)
    # lands with the runtime.md increment, exactly like members.agent_id was
    # deferred until this table existed. The column exists now so agents keep
    # a stable default-runtime binding surface.
    default_runtime_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    trigger_on_assign: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # Overlapping composite FK → agent_config_versions(workspace_id, agent_id, id)
    # (same-parent constraint, README §6.2 rule 7, T27). Declared in
    # __table_args__ because it spans the primary-key column.
    active_config_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(
            f"lifecycle_status IN {AGENT_LIFECYCLE_VALUES!r}", name="agents_lifecycle_status"
        ),
        CheckConstraint(f"visibility IN {AGENT_VISIBILITY_VALUES!r}", name="agents_visibility"),
        CheckConstraint(f"badge_kind IN {AGENT_BADGE_KIND_VALUES!r}", name="agents_badge_kind"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="agents_name_len"),
        CheckConstraint(
            "slug IS NULL OR char_length(slug) BETWEEN 1 AND 64", name="agents_slug_len"
        ),
        CheckConstraint(
            "role_tag IS NULL OR char_length(role_tag) BETWEEN 1 AND 64",
            name="agents_role_tag_len",
        ),
        # Composite-FK reference target for members.agent_id and
        # agent_config_versions.agent_id (README §6.2).
        Index("uq_agents_ws_id", "workspace_id", "id", unique=True),
        # Same-parent overlap: the active config pointer must belong to THIS
        # agent (README §6.2 rule 7). Column-level SET NULL so deleting a
        # version row never nulls the tenant key (README §6.2 rule 6).
        ForeignKeyConstraint(
            ("workspace_id", "id", "active_config_version_id"),
            ("agent_config_versions.workspace_id", "agent_config_versions.agent_id",
             "agent_config_versions.id"),
            ondelete="SET NULL (active_config_version_id)",
            name="agents_active_config_version_id_agent_config_versions",
        ),
        Index("idx_agents_owner", "owner_user_id"),
        Index(
            "idx_agents_lifecycle",
            "workspace_id",
            "lifecycle_status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_agents_visibility", "workspace_id", "visibility"),
        Index("idx_agents_default_runtime", "default_runtime_id"),
    )


class AgentConfigVersion(Base):
    """An immutable configuration snapshot (agent.md §2.7).

    ``snapshot`` freezes ``{"system_instructions", "model_config",
    "skill_versions", "capability_grants"}`` at version time; enqueue later
    pins the active version id into ``task_executions.config_snapshot``
    (README §6.11) so in-flight runs are immune to later edits.
    """

    __tablename__ = "agent_config_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Redundant tenant column: same workspace as the owning agent, required
    # for the composite FKs and the overlap unique key (README §6.2).
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(TEXT, default=None)
    changed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Owning agent must live in the SAME workspace (README §6.2 rule 2).
        ForeignKeyConstraint(
            ("workspace_id", "agent_id"),
            ("agents.workspace_id", "agents.id"),
            ondelete="CASCADE",
            name="agent_config_versions_agent_id_agents",
        ),
        # Audit actor must be a roster member of the SAME workspace; members
        # are soft-deleted so the trail survives (RESTRICT backstop).
        ForeignKeyConstraint(
            ("workspace_id", "changed_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="agent_config_versions_changed_by_members",
        ),
        # OVERLAP unique key: target of agents.active_config_version_id's
        # overlapping composite FK — enforces "active pointer only ever
        # references this agent's own versions" at INSERT (README §6.2
        # rule 7, T27).
        Index("uq_agent_config_versions_ws_agent_id", "workspace_id", "agent_id", "id", unique=True),
        # Generic composite-FK reference target (README §6.2 rule 1).
        Index("uq_agent_config_versions_ws_id", "workspace_id", "id", unique=True),
        Index("idx_config_versions_agent_time", "agent_id", text("created_at DESC")),
    )
