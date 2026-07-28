"""Notification & inbox models (comment-inbox.md §2.5-§2.8 — this module owns
these tables and is the single notification authority).

``notifications`` is the inbox data source. ``payload`` carries a renderable
SNAPSHOT (actor name, preview, title, count) so notifications stay readable
after the source issue/comment is deleted. ``priority`` is derived
server-side from the README §6.13 unique priority matrix — no module defines
its own tiers.

Deferred composite FKs (codebase precedent — ``members.agent_id``):
``notifications.execution_id`` → ``task_executions(workspace_id, id)``
(runtime.md), ``notification_delivery.integration_id`` →
``integrations(workspace_id, id)`` and ``binding_id`` →
``integration_bindings(workspace_id, id)`` (integrations.md). The columns
exist now with the §6.13 semantics; the physical FKs land with their owning
modules.

``notification_delivery`` follows the R3 destination-grain ledger:
``UNIQUE(notification_id, channel, destination_key)`` — in_app/websocket are
single-destination (``destination_key=''``), im/email carry one row per
destination. Routing lives in the STRUCTURED columns
(``provider``/``external_target``/``integration_id``/``binding_id``);
``error`` records failure reasons ONLY.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

NOTIFICATION_TYPE_VALUES = (
    "assigned",
    "mentioned",
    "subscribed_update",
    "comment_created",
    "status_changed",
    "execution_finished",
    "review_requested",
    "due_soon",
    # autopilot.md §4.6 / README §6.13: circuit-break alerts (critical) and
    # plain autopilot notices (normal) — the matrix is the authority, these
    # two are its autopilot-domain rows.
    "autopilot_alert",
    "autopilot_notice",
)
NOTIFICATION_PRIORITY_VALUES = ("critical", "normal")
ACTOR_KIND_VALUES = ("member", "system")
SUBSCRIPTION_REASON_VALUES = ("creator", "assignee", "mentioned", "participated", "manual")
EMAIL_POLICY_VALUES = ("none", "realtime", "digest")
DELIVERY_CHANNEL_VALUES = ("in_app", "email", "websocket", "im")
DELIVERY_PROVIDER_VALUES = ("feishu", "slack", "email_smtp")
DELIVERY_STATE_VALUES = ("pending", "sent", "failed")


class IssueSubscription(Base):
    """Notification routing: who follows an issue (comment-inbox.md §2.5).

    ``muted=true`` keeps the subscription row but suppresses notification
    generation (README §6.13 per-issue mute).
    """

    __tablename__ = "issue_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'manual'")
    )
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"reason IN {SUBSCRIPTION_REASON_VALUES!r}", name="issue_subscriptions_reason"
        ),
        Index("uq_subscription", "issue_id", "subscriber_id", unique=True),
        Index(
            "idx_subscriptions_issue",
            "workspace_id",
            "issue_id",
            postgresql_where=text("NOT muted"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="CASCADE",
            name="issue_subscriptions_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "subscriber_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="issue_subscriptions_subscriber_id_members",
        ),
    )


class Notification(Base):
    """One inbox notification (comment-inbox.md §2.6)."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(TEXT, nullable=False)
    priority: Mapped[str] = mapped_column(TEXT, nullable=False)
    actor_kind: Mapped[str | None] = mapped_column(TEXT, default=None)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    comment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    # Deferred composite FK → task_executions(workspace_id, id) (runtime.md).
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    group_key: Mapped[str | None] = mapped_column(TEXT, default=None)
    read_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"type IN {NOTIFICATION_TYPE_VALUES!r}", name="notifications_type"),
        CheckConstraint(
            f"priority IN {NOTIFICATION_PRIORITY_VALUES!r}", name="notifications_priority"
        ),
        CheckConstraint(
            f"actor_kind IS NULL OR actor_kind IN {ACTOR_KIND_VALUES!r}",
            name="notifications_actor_kind",
        ),
        # §6.1 exception: system actors have a NULL member FK, member actors
        # MUST have one. NOT a human/agent discriminator.
        CheckConstraint(
            "(actor_kind = 'member' AND actor_id IS NOT NULL) "
            "OR ((actor_kind IS NULL OR actor_kind = 'system') AND actor_id IS NULL)",
            name="notifications_actor_identity",
        ),
        # Composite-FK reference target for notification_delivery (README §6.2).
        Index("uq_notifications_ws_id", "workspace_id", "id", unique=True),
        Index(
            "idx_notifications_inbox",
            "workspace_id",
            "recipient_id",
            "archived_at",
            text("created_at DESC"),
        ),
        Index(
            "idx_notifications_unread",
            "workspace_id",
            "recipient_id",
            postgresql_where=text("read_at IS NULL AND archived_at IS NULL"),
        ),
        Index(
            "idx_notifications_group",
            "recipient_id",
            "group_key",
            text("created_at DESC"),
        ),
        Index("idx_notifications_payload", "payload", postgresql_using="gin"),
        ForeignKeyConstraint(
            ("workspace_id", "recipient_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="notifications_recipient_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "actor_id"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="notifications_actor_id_members",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "issue_id"),
            ("issues.workspace_id", "issues.id"),
            ondelete="SET NULL (issue_id)",
            name="notifications_issue_id_issues",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "comment_id"),
            ("comments.workspace_id", "comments.id"),
            ondelete="SET NULL (comment_id)",
            name="notifications_comment_id_comments",
        ),
    )


class NotificationPreference(Base):
    """Per-member per-event delivery preferences (comment-inbox.md §2.7).

    ``event_type='all'`` is the fallback row; quiet hours are user-level and
    read from whichever row carries them (critical events pierce quiet hours
    regardless, README §6.13).
    """

    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    in_app: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    email: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'digest'")
    )
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, default=None)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"email IN {EMAIL_POLICY_VALUES!r}", name="notification_preferences_email"),
        Index("uq_notif_pref", "workspace_id", "member_id", "event_type", unique=True),
        ForeignKeyConstraint(
            ("workspace_id", "member_id"),
            ("members.workspace_id", "members.id"),
            ondelete="CASCADE",
            name="notification_preferences_member_id_members",
        ),
    )


class NotificationDelivery(Base):
    """Per-destination delivery ledger (comment-inbox.md §2.8, R3)."""

    __tablename__ = "notification_delivery"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    notification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(TEXT, nullable=False)
    destination_key: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("''")
    )
    provider: Mapped[str | None] = mapped_column(TEXT, default=None)
    external_target: Mapped[str | None] = mapped_column(TEXT, default=None)
    # Deferred composite FKs → integrations / integration_bindings
    # (integrations.md); column-level SET NULL lands with that module.
    integration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    state: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'pending'"))
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    error: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            f"channel IN {DELIVERY_CHANNEL_VALUES!r}", name="notification_delivery_channel"
        ),
        CheckConstraint(
            f"provider IS NULL OR provider IN {DELIVERY_PROVIDER_VALUES!r}",
            name="notification_delivery_provider",
        ),
        CheckConstraint(
            f"state IN {DELIVERY_STATE_VALUES!r}", name="notification_delivery_state"
        ),
        # R3: idempotency at destination grain (README §6.5/§6.13).
        Index("uq_delivery", "notification_id", "channel", "destination_key", unique=True),
        Index("idx_delivery_pending", "state", "created_at"),
        ForeignKeyConstraint(
            ("workspace_id", "notification_id"),
            ("notifications.workspace_id", "notifications.id"),
            ondelete="CASCADE",
            name="notification_delivery_notification_id_notifications",
        ),
    )
