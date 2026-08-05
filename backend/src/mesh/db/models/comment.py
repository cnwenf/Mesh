"""Comment models (comment-inbox.md §2.2-§2.4 — this module owns these tables).

``comments`` is the issue-detail collaboration timeline: human members and
agent teammates post in the same thread. Single-level folded threading —
``parent_id`` is always a TOP-LEVEL comment (reply depth is exactly 1, README
§6.2 rule 7 enforces same-issue parenting at the database layer; the depth
itself is a service-layer invariant). ``thread_root_id`` is a denormalized
pointer for recursion-free thread aggregation.

Human/agent distinction is NEVER stored here (README §6.1): every author /
actor / mention target references ``members.id`` via composite FK and the type
is JOINed from ``members.member_type`` (API responses carry a computed
snapshot, labelled as such). ``author_kind ∈ {member, system}`` is the
§6.1-permitted CHECK + NULL FK exception for system-activity comments, NOT a
human/agent discriminator.

Deferred composite FKs (codebase precedent — ``members.agent_id``):
``comment_mentions.triggered_execution_id`` references
``task_executions(workspace_id, id)`` once runtime.md lands. The enqueue
itself already travels through the transactional outbox (README §6.6) as an
``execution.enqueue`` event with the §6.5 idempotency key; the column stores
that enqueue event id as the skeleton trace until the executions table
exists.

``comments.idempotency_key`` implements the receiver-side de-duplication for
agent comment reflow (README §6.5 / §6.14 幂等写): the agent runtime sends
``Idempotency-Key: sha256(execution_id|attempt_number|'comment'|client_seq)``
and a duplicate key returns the first stored result.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

AUTHOR_KIND_VALUES = ("member", "system")


class Comment(Base):
    """A comment on an issue; top-level or a single-level reply."""

    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    thread_root_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    author_kind: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'member'"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    body_markdown: Mapped[str] = mapped_column(TEXT, nullable=False)
    body_html: Mapped[str | None] = mapped_column(TEXT, default=None)
    body_text: Mapped[str | None] = mapped_column(TEXT, default=None)
    edited_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    idempotency_key: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"author_kind IN {AUTHOR_KIND_VALUES!r}", name="comments_author_kind"),
        # §6.1 exception: system authors have a NULL member FK, member authors
        # MUST have one. NOT a human/agent discriminator.
        CheckConstraint(
            "(author_kind = 'member' AND author_id IS NOT NULL) "
            "OR (author_kind = 'system' AND author_id IS NULL)",
            name="comments_author_identity",
        ),
        CheckConstraint("char_length(body_markdown) > 0", name="comments_body_not_empty"),
        CheckConstraint("parent_id <> id", name="comments_parent_not_self"),
        CheckConstraint("thread_root_id <> id", name="comments_thread_root_not_self"),
        # Composite-FK reference targets (README §6.2 rules 1 & 7).
        Index("uq_comments_ws_id", "workspace_id", "id", unique=True),
        # Overlapping unique key so parent_id / thread_root_id composite FKs can
        # prove same-issue parenting (README §6.2 rule 7).
        Index("uq_comments_ws_issue_id", "workspace_id", "issue_id", "id", unique=True),
        # Receiver de-dup for agent comment reflow (README §6.5 / §6.14).
        Index(
            "uq_comments_idempotency",
            "workspace_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("idx_comments_issue_created", "workspace_id", "issue_id", "created_at"),
        Index("idx_comments_thread", "workspace_id", "thread_root_id", "created_at"),
        Index("idx_comments_author", "workspace_id", "author_id", "created_at"),
        Index(
            "idx_comments_active",
            "issue_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="comments_issue_id_issues",
        ),
        # Same-issue overlapping composite self-FKs (README §6.2 rule 7): a
        # parent / thread root MUST belong to the same issue — cross-issue
        # parenting is rejected at INSERT time.
        ForeignKeyConstraint(
            ("workspace_id", "issue_id", "parent_id"),
            ("comments.workspace_id", "comments.issue_id", "comments.id"),
            ondelete="CASCADE",
            name="comments_parent_id_comments",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id", "thread_root_id"),
            ("comments.workspace_id", "comments.issue_id", "comments.id"),
            ondelete="CASCADE",
            name="comments_thread_root_id_comments",
        ),
        # Authorship history must never dangle: members are soft-deleted via
        # status='removed' (README §6.2 rule 6 — RESTRICT).
        ForeignKeyConstraint(
            ("workspace_id", "author_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="comments_author_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "resolved_by_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="comments_resolved_by_id_members",
        ),
    )


class CommentMention(Base):
    """Server-parsed mention of a member in a comment (comment-inbox.md §2.3).

    ``uq_mentions`` guarantees a member is mentioned at most once per comment,
    which naturally suppresses "same agent @ twice in one comment → one
    execution" (README §6.9). Removing a mention on edit SOFT-deletes the row
    (``deleted_at``) and never cancels an already-enqueued execution.
    """

    __tablename__ = "comment_mentions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    comment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mentioned_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Canonical logical execution only. While the outbox row awaits runtime
    # materialization, ``pending_trigger_event_id`` carries the correlation;
    # an outbox id must never masquerade as a TaskExecution id in this field.
    triggered_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    pending_trigger_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # One mention per (comment, member) — the §6.9 same-comment de-dup.
        Index("uq_mentions", "comment_id", "mentioned_id", unique=True),
        Index("idx_mentions_target", "mentioned_id", "created_at"),
        Index("idx_mentions_chain", "workspace_id", "mentioned_id", "created_at"),
        ForeignKeyConstraint(
            ("workspace_id", "comment_id"),
            ("comments.workspace_id", "comments.id"),
            ondelete="CASCADE",
            name="comment_mentions_comment_id_comments",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "mentioned_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="comment_mentions_mentioned_id_members",
        ),
    )


class CommentReaction(Base):
    """An emoji reaction on a comment (comment-inbox.md §2.4)."""

    __tablename__ = "comment_reactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    comment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    emoji: Mapped[str] = mapped_column(TEXT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(emoji) BETWEEN 1 AND 32", name="comment_reactions_emoji_length"
        ),
        # Same person, same comment, same emoji — exactly once.
        Index("uq_reaction", "comment_id", "actor_id", "emoji", unique=True),
        Index("idx_reactions_comment", "workspace_id", "comment_id"),
        ForeignKeyConstraint(
            ("workspace_id", "comment_id"),
            ("comments.workspace_id", "comments.id"),
            ondelete="CASCADE",
            name="comment_reactions_comment_id_comments",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "actor_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="comment_reactions_actor_id_members",
        ),
    )
