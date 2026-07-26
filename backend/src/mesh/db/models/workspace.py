"""Workspace models — the multi-tenancy isolation root (workspace.md owns).

DDL mirrors docs/specs/validation/schema_r2_validation.sql (workspaces /
invitations / redemptions / slug-history / prefix-registry sections) so the
runtime schema and the spec validation script stay in lockstep.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

WORKSPACE_NAME_MIN = 1
WORKSPACE_NAME_MAX = 80
DEFAULT_WORKSPACE_SETTINGS = '{"default_locale": "en"}'

# workspace.md §2.3 — invitation link lifecycle states. There is deliberately
# no pending/accepted: link lifecycle and redemption records are separated
# (§2.4/§4.4, README §9 T11).
INVITATION_STATUS_VALUES = ("active", "revoked", "expired", "exhausted")
INVITATION_ROLE_VALUES = ("admin", "member", "guest")

# workspace.md §2.6 / README §6.3 — identifier prefix registry kinds.
PREFIX_KIND_VALUES = ("project", "inbox", "retired")


class Workspace(Base):
    """A tenant: every business table references ``workspaces.id``."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(TEXT)
    slug: Mapped[str] = mapped_column(TEXT)
    logo_url: Mapped[str | None] = mapped_column(TEXT, default=None)
    timezone: Mapped[str] = mapped_column(TEXT, server_default=text("'UTC'"))
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


class WorkspaceInvitation(Base):
    """An invitation link (workspace.md §2.3). Stores the token hash only.

    ``max_uses``/``expires_at`` are NOT NULL — no unlimited or never-expiring
    links (MES-4). ``status`` tracks the LINK lifecycle
    (active/revoked/expired/exhausted); who joined via the link is recorded in
    :class:`WorkspaceInvitationRedemption` (§2.4, README §9 T11).
    """

    __tablename__ = "workspace_invitations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str | None] = mapped_column(TEXT, default=None)
    token_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    token_prefix: Mapped[str] = mapped_column(TEXT, nullable=False)
    role: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'member'"))
    invited_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"role IN {INVITATION_ROLE_VALUES!r}", name="ws_invitations_role"),
        CheckConstraint(f"status IN {INVITATION_STATUS_VALUES!r}", name="ws_invitations_status"),
        CheckConstraint("max_uses > 0", name="ws_invitations_max_uses_pos"),
        CheckConstraint("used_count >= 0", name="ws_invitations_used_count_nonneg"),
        # Inviter must be a member of the SAME workspace (README §6.2 / §9 T1).
        ForeignKeyConstraint(
            ("workspace_id", "invited_by"),
            ("members.workspace_id", "members.id"),
            name="invited_by_members",
        ),
        Index("uq_ws_invitations_token_hash", "token_hash", unique=True),
        # Referenced by redemptions' composite FK (README §6.2).
        Index("uq_ws_invitations_ws_id", "workspace_id", "id", unique=True),
        Index("idx_ws_invitations_workspace", "workspace_id", "status"),
        Index(
            "idx_ws_invitations_email",
            "workspace_id",
            "email",
            postgresql_where=text("email IS NOT NULL"),
        ),
        # At most one active directed invitation per (workspace, email) (§2.7).
        Index(
            "uq_ws_invitations_active_email",
            "workspace_id",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL AND status = 'active'"),
        ),
    )


class WorkspaceInvitationRedemption(Base):
    """One user joining via one invitation link (workspace.md §2.4).

    Separated from the link lifecycle (README §9 T11): a multi-use link never
    flips to a single accepted terminal state; each acceptance adds a row here
    and atomically increments ``workspace_invitations.used_count``.
    """

    __tablename__ = "workspace_invitation_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    invitation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    redeemed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Idempotent acceptance: one row per (link, user) (§2.4 / §3.2).
        Index("uq_ws_inv_redemptions_inv_user", "invitation_id", "user_id", unique=True),
        Index("idx_ws_inv_redemptions_member", "workspace_id", "member_id"),
        # Same-tenant composite FKs (README §6.2 / §9 T1).
        ForeignKeyConstraint(
            ("workspace_id", "invitation_id"),
            ("workspace_invitations.workspace_id", "workspace_invitations.id"),
            ondelete="CASCADE",
            name="redemptions_invitation_id_workspace_invitations",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "member_id"),
            ("members.workspace_id", "members.id"),
            name="redemptions_member_id_members",
        ),
    )


class WorkspaceSlugHistory(Base):
    """Released slugs kept for redirects (workspace.md §2.5 / W6)."""

    __tablename__ = "workspace_slug_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    old_slug: Mapped[str] = mapped_column(TEXT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (Index("uq_slug_history_old_slug", "old_slug", unique=True),)


class IdentifierPrefixRegistry(Base):
    """Workspace-level exclusive identifier prefixes (workspace.md §2.6).

    Every identifier prefix — project keys and current/historic inbox prefixes —
    is registered here, permanently exclusive per workspace (README §6.3), so
    ``UNIQUE(workspace_id, identifier)`` on issues can never be "randomly"
    violated. Retired prefixes are never re-issued.

    The ``project_id`` composite FK to ``projects(workspace_id, id)`` uses
    column-level ``ON DELETE SET NULL (project_id)`` (added by the project.md
    increment, README §6.2 rule 6): physically deleting a project keeps the
    registry row — the prefix stays permanently reserved with a NULL pointer.
    """

    __tablename__ = "identifier_prefix_registry"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(TEXT, nullable=False)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"kind IN {PREFIX_KIND_VALUES!r}", name="prefix_registry_kind"),
        # Prefixes are workspace-level permanently exclusive (README §6.3).
        Index("uq_prefix_registry_ws_key", "workspace_id", "key", unique=True),
        Index("idx_prefix_registry_ws", "workspace_id", "kind"),
        # Same-tenant project reference; column-level SET NULL keeps the row
        # (prefix permanently reserved) when a project is physically deleted
        # (project.md §2.5, README §6.2 rule 6).
        ForeignKeyConstraint(
            ("workspace_id", "project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="SET NULL (project_id)",
            name="prefix_registry_project_id_projects",
        ),
    )
