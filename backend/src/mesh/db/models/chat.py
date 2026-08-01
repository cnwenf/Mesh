"""Chat session models (chat-session.md §2 — this module owns these tables).

``chat_sessions`` / ``chat_messages`` implement 形态 A: the real-time 1:1
conversation between a human member and an agent teammate. Messages carry the
shared generation state machine (``streaming/done/failed/interrupted``,
§4.4) and the candidate-reply branching design (§2.3): several agent
candidates hang off the SAME ``parent_id`` (the user message they answer);
exactly one carries ``selected_candidate=true``; regenerate adds a candidate,
never rewrites history.

Tenant + same-parent enforcement (README §6.2):

- every cross-module reference is a composite FK ``(workspace_id, x_id)``;
- ``parent_id`` / ``quote_message_id`` use the overlapping composite self-FK
  ``(workspace_id, session_id, <ref>) → chat_messages(workspace_id,
  session_id, id)`` (rule 7) so cross-session parenting / quoting is rejected
  at INSERT time — the referenced key is ``uq_chat_messages_ws_session_id``;
- nullable context references use column-level ``ON DELETE SET NULL (<col>)``
  (rule 6) so the tenant key is never nulled.

Human/agent distinction is NEVER stored (README §6.1): ``owner_id`` references
``members.id`` (the human initiator) and the agent side references
``agents.id``; message roles (``user/agent/system``) describe the
conversation turn, not an identity discriminator — API responses carry a
server-computed ``member_type`` snapshot (true source: ``members``).

Pinning has NO storage here (R3, README §6.19): the unique truth for "pinned"
is ``favorites(target_type='chat_session')``; the list endpoint computes the
requester-scoped ``pinned`` snapshot via an EXISTS subquery.

``chat_messages.idempotency_key`` implements receiver de-duplication for the
idempotent writes of §3.5 / README §6.14 (send / regenerate); a duplicate key
returns the first stored result. Attachments are linked through the unified
``attachment_links`` (``linked_type='chat_message'``, attachment.md) — no
attachment columns live here.
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
    Integer,
    text,
)
from sqlalchemy.dialects.postgresql import TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

SESSION_STATUS_VALUES = ("active", "archived", "deleted")
MESSAGE_ROLE_VALUES = ("user", "agent", "system")
GENERATION_STATUS_VALUES = ("streaming", "done", "failed", "interrupted")
FAVORITE_TARGET_TYPE_VALUES = ("issue", "project", "view", "chat_session")


class ChatSession(Base):
    """A 1:1 conversation between a human member and an agent (§2.2)."""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'新对话'"))
    title_is_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    context_issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    context_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    last_message_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    last_message_preview: Mapped[str | None] = mapped_column(TEXT, default=None)
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(f"status IN {SESSION_STATUS_VALUES!r}", name="chat_sessions_status"),
        CheckConstraint("message_count >= 0", name="chat_sessions_message_count_nonneg"),
        # Composite-FK reference target (README §6.2 rule 1).
        Index("uq_chat_sessions_ws_id", "workspace_id", "id", unique=True),
        # Search indexes (search-command-palette.md §2.2).
        Index(
            "idx_chat_sessions_title_trgm",
            text("(public.mesh_search_norm(title)) gin_trgm_ops"),
            postgresql_using="gin",
        ),
        Index(
            "idx_chat_sessions_title_prefix",
            "workspace_id",
            text("(public.mesh_search_norm(title)) text_pattern_ops"),
        ),
        # §2.8 list indexes: owner timeline / per-agent filter / issue backref.
        Index(
            "idx_chat_sessions_owner_list",
            "owner_id",
            text("last_message_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_chat_sessions_owner_agent",
            "owner_id",
            "agent_id",
            text("last_message_at DESC"),
        ),
        Index(
            "idx_chat_sessions_context_issue",
            "context_issue_id",
            postgresql_where=text("context_issue_id IS NOT NULL"),
        ),
        # Search indexes (migration 0035, search-command-palette.md §2.2):
        # expression indexes over the single normalization entry point.
        Index(
            "idx_chat_sessions_title_trgm",
            text("mesh_search_norm(title)"),
            postgresql_using="gin",
            postgresql_ops={"mesh_search_norm(title)": "gin_trgm_ops"},
        ),
        Index(
            "idx_chat_sessions_title_prefix",
            "workspace_id",
            text("mesh_search_norm(title)"),
            postgresql_ops={"mesh_search_norm(title)": "text_pattern_ops"},
        ),
        # Owner history must never dangle (members are soft-removed → RESTRICT).
        ForeignKeyConstraint(
            ("workspace_id", "owner_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="chat_sessions_owner_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "agent_id"),
            ("agents.workspace_id", "agents.id"),
            ondelete="RESTRICT",
            name="chat_sessions_agent_id_agents",
        ),
        # Context references are optional: column-level SET NULL keeps the
        # tenant key intact when the referenced issue/project goes away
        # (README §6.2 rule 6).
        ForeignKeyConstraint(
            ("workspace_id", "context_issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="SET NULL (context_issue_id)",
            name="chat_sessions_context_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "context_project_id"),
            ("projects.workspace_id", "projects.id"),
            ondelete="SET NULL (context_project_id)",
            name="chat_sessions_context_project_id_projects",
        ),
    )


class ChatMessage(Base):
    """A chat message; agent replies branch as candidates per §2.3."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(TEXT, nullable=False)
    content: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("''"))
    generation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    generation_status: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'done'")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    selected_candidate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    quote_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    error_message: Mapped[str | None] = mapped_column(TEXT, default=None)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    idempotency_key: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"role IN {MESSAGE_ROLE_VALUES!r}", name="chat_messages_role"),
        CheckConstraint(
            f"generation_status IN {GENERATION_STATUS_VALUES!r}",
            name="chat_messages_generation_status",
        ),
        CheckConstraint("parent_id <> id", name="chat_messages_parent_not_self"),
        CheckConstraint("quote_message_id <> id", name="chat_messages_quote_not_self"),
        # Composite-FK reference target (README §6.2 rule 1).
        Index("uq_chat_messages_ws_id", "workspace_id", "id", unique=True),
        # Overlapping key the same-session self-FKs reference (rule 7).
        Index(
            "uq_chat_messages_ws_session_id", "workspace_id", "session_id", "id", unique=True
        ),
        # Receiver de-dup for idempotent writes (§3.5 / README §6.14), scoped
        # by session so a key cannot collide/leak across sessions (M1).
        Index(
            "uq_chat_messages_idempotency",
            "workspace_id",
            "session_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "idx_chat_messages_session_time", "session_id", text("created_at DESC")
        ),
        Index(
            "idx_chat_messages_parent",
            "parent_id",
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
        # Single-concurrency invariant (§3.5 / §5.3): UNIQUE partial index so a
        # racing second streaming insert fails (M4; service maps to 409).
        Index(
            "uq_chat_messages_one_streaming",
            "session_id",
            unique=True,
            postgresql_where=text("generation_status = 'streaming'"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "session_id"),
            ("chat_sessions.workspace_id", "chat_sessions.id"),
            ondelete="CASCADE",
            name="chat_messages_session_id_chat_sessions",
        ),
        # Same-session overlapping composite self-FKs (README §6.2 rule 7):
        # candidate parent / quoted message MUST share this message's session.
        ForeignKeyConstraint(
            ("workspace_id", "session_id", "parent_id"),
            ("chat_messages.workspace_id", "chat_messages.session_id", "chat_messages.id"),
            ondelete="SET NULL (parent_id)",
            name="chat_messages_parent_id_chat_messages",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "session_id", "quote_message_id"),
            ("chat_messages.workspace_id", "chat_messages.session_id", "chat_messages.id"),
            ondelete="SET NULL (quote_message_id)",
            name="chat_messages_quote_message_id_chat_messages",
        ),
    )


class Favorite(Base):
    """A member-private favorite (README §6.19 — the pinning truth source).

    ``target_type='chat_session'`` rows are the UNIQUE truth for chat pinning
    (R3: no ``is_pinned`` snapshot exists on ``chat_sessions``). The target is
    a polymorphic logical FK (README §6.2 rule 4): the row carries
    ``workspace_id``; deletion consistency is soft-delete + service layer.
    """

    __tablename__ = "favorites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"target_type IN {FAVORITE_TARGET_TYPE_VALUES!r}",
            name="favorites_target_type",
        ),
        # One favorite per (member, target) — PUT is idempotent.
        Index("uq_favorites_member_target", "member_id", "target_type", "target_id", unique=True),
        Index("uq_favorites_ws_id", "workspace_id", "id", unique=True),
        Index("idx_favorites_member", "workspace_id", "member_id", text("created_at DESC")),
        ForeignKeyConstraint(
            ("workspace_id", "member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="favorites_member_id_members",
        ),
    )
