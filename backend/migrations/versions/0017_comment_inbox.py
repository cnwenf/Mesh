"""comment-inbox: comments, comment_mentions, comment_reactions,
issue_subscriptions, notifications, notification_preferences,
notification_delivery

Stage-5 collaboration increment (comment-inbox.md §2 full — this module owns
all seven tables). DDL mirrors the ORM models in
``mesh/db/models/comment.py`` and ``mesh/db/models/notification.py`` (the
drift guard compares them).

- comments: single-level folded threading. ``parent_id`` /
  ``thread_root_id`` use the OVERLAPPING composite self-FK
  ``(workspace_id, issue_id, parent_id) → comments(workspace_id, issue_id, id)``
  so cross-issue parenting is rejected at INSERT time (README §6.2 rule 7,
  fed by ``UNIQUE (workspace_id, issue_id, id)``). Reply depth = 1 is a
  service-layer invariant. ``author_kind ∈ {member, system}`` + NULL author
  FK is the §6.1-permitted system-activity exception, NOT a human/agent
  discriminator (no such column exists). ``idempotency_key`` implements the
  §6.5/§6.14 receiver de-dup for agent comment reflow.
- comment_mentions: ``uq_mentions(comment_id, mentioned_id)`` is the §6.9
  same-comment trigger de-dup; ``triggered_execution_id`` is the deferred
  composite FK to ``task_executions`` (runtime.md) storing the
  ``execution.enqueue`` outbox event id meanwhile.
- notifications: ``priority`` derived from the README §6.13 matrix;
  ``payload`` JSONB snapshot keeps notifications readable after source
  deletion; ``execution_id`` deferred FK (runtime.md).
- notification_delivery: R3 destination-grain ledger —
  ``UNIQUE(notification_id, channel, destination_key)``; structured routing
  columns (``provider``/``external_target``/``integration_id``/``binding_id``,
  deferred FKs to integrations.md) with ``error`` holding failure reasons
  only.
- RLS defense-in-depth (README §6.2 rule 5) on all seven tables, plus narrow
  SECURITY DEFINER workspace resolvers for the workspace-less paths
  (``/comments/{id}``, ``/inbox/{notification_id}``).

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-27
"""
from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "comments",
    "comment_mentions",
    "comment_reactions",
    "issue_subscriptions",
    "notifications",
    "notification_preferences",
    "notification_delivery",
)


def upgrade() -> None:
    # -- comments: the issue collaboration timeline (§2.2) ---------------------
    op.execute(
        """
        CREATE TABLE comments (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          issue_id        UUID NOT NULL,
          parent_id       UUID NULL,
          thread_root_id  UUID NULL,
          author_kind     TEXT NOT NULL DEFAULT 'member',
          author_id       UUID NULL,
          body_markdown   TEXT NOT NULL,
          body_html       TEXT NULL,
          body_text       TEXT NULL,
          edited_at       TIMESTAMPTZ NULL,
          resolved_at     TIMESTAMPTZ NULL,
          resolved_by_id  UUID NULL,
          deleted_at      TIMESTAMPTZ NULL,
          idempotency_key TEXT NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT comments_author_kind CHECK (author_kind IN ('member','system')),
          CONSTRAINT comments_author_identity CHECK (
            (author_kind = 'member' AND author_id IS NOT NULL)
            OR (author_kind = 'system' AND author_id IS NULL)
          ),
          CONSTRAINT comments_body_not_empty CHECK (char_length(body_markdown) > 0),
          CONSTRAINT comments_parent_not_self CHECK (parent_id <> id),
          CONSTRAINT comments_thread_root_not_self CHECK (thread_root_id <> id),
          CONSTRAINT comments_issue_id_issues
            FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id)
            ON DELETE CASCADE,
          CONSTRAINT comments_author_id_members
            FOREIGN KEY (workspace_id, author_id) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT comments_resolved_by_id_members
            FOREIGN KEY (workspace_id, resolved_by_id) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT
          -- the overlapping same-issue self-FKs (README §6.2 rule 7) are added
          -- after uq_comments_ws_issue_id exists (FK needs the unique target)
        )
        """
    )
    # Composite-FK reference target + the overlapping unique key the
    # same-issue self-FKs reference (README §6.2 rules 1 & 7).
    op.execute("CREATE UNIQUE INDEX uq_comments_ws_id ON comments(workspace_id, id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_comments_ws_issue_id ON comments(workspace_id, issue_id, id)"
    )
    op.execute(
        "ALTER TABLE comments ADD CONSTRAINT comments_parent_id_comments "
        "FOREIGN KEY (workspace_id, issue_id, parent_id) "
        "REFERENCES comments(workspace_id, issue_id, id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE comments ADD CONSTRAINT comments_thread_root_id_comments "
        "FOREIGN KEY (workspace_id, issue_id, thread_root_id) "
        "REFERENCES comments(workspace_id, issue_id, id) ON DELETE CASCADE"
    )
    # Receiver de-dup for agent comment reflow (README §6.5 / §6.14).
    op.execute(
        "CREATE UNIQUE INDEX uq_comments_idempotency ON comments(workspace_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    # §2.2 performance indexes.
    op.execute(
        "CREATE INDEX idx_comments_issue_created ON comments(workspace_id, issue_id, created_at)"
    )
    op.execute(
        "CREATE INDEX idx_comments_thread ON comments(workspace_id, thread_root_id, created_at)"
    )
    op.execute(
        "CREATE INDEX idx_comments_author ON comments(workspace_id, author_id, created_at)"
    )
    op.execute(
        "CREATE INDEX idx_comments_active ON comments(issue_id, created_at) "
        "WHERE deleted_at IS NULL"
    )

    # -- comment_mentions: server-parsed mention resolution (§2.3) --------------
    op.execute(
        """
        CREATE TABLE comment_mentions (
          id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id            UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          comment_id              UUID NOT NULL,
          mentioned_id            UUID NOT NULL,
          triggered_execution_id  UUID NULL,
          deleted_at              TIMESTAMPTZ NULL,
          created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT comment_mentions_comment_id_comments
            FOREIGN KEY (workspace_id, comment_id) REFERENCES comments(workspace_id, id)
            ON DELETE CASCADE,
          CONSTRAINT comment_mentions_mentioned_id_members
            FOREIGN KEY (workspace_id, mentioned_id) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT
        )
        """
    )
    # Same-comment same-member once — the §6.9 trigger de-dup.
    op.execute("CREATE UNIQUE INDEX uq_mentions ON comment_mentions(comment_id, mentioned_id)")
    op.execute(
        "CREATE INDEX idx_mentions_target ON comment_mentions(mentioned_id, created_at)"
    )
    op.execute(
        "CREATE INDEX idx_mentions_chain ON comment_mentions(workspace_id, mentioned_id, created_at)"
    )

    # -- comment_reactions: emoji reactions (§2.4) ------------------------------
    op.execute(
        """
        CREATE TABLE comment_reactions (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          comment_id    UUID NOT NULL,
          actor_id      UUID NOT NULL,
          emoji         TEXT NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT comment_reactions_emoji_length
            CHECK (char_length(emoji) BETWEEN 1 AND 32),
          CONSTRAINT comment_reactions_comment_id_comments
            FOREIGN KEY (workspace_id, comment_id) REFERENCES comments(workspace_id, id)
            ON DELETE CASCADE,
          CONSTRAINT comment_reactions_actor_id_members
            FOREIGN KEY (workspace_id, actor_id) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_reaction ON comment_reactions(comment_id, actor_id, emoji)"
    )
    op.execute("CREATE INDEX idx_reactions_comment ON comment_reactions(workspace_id, comment_id)")

    # -- issue_subscriptions: notification routing (§2.5) ------------------------
    op.execute(
        """
        CREATE TABLE issue_subscriptions (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          issue_id       UUID NOT NULL,
          subscriber_id  UUID NOT NULL,
          reason         TEXT NOT NULL DEFAULT 'manual',
          muted          BOOLEAN NOT NULL DEFAULT false,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT issue_subscriptions_reason
            CHECK (reason IN ('creator','assignee','mentioned','participated','manual')),
          CONSTRAINT issue_subscriptions_issue_id_issues
            FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id)
            ON DELETE CASCADE,
          CONSTRAINT issue_subscriptions_subscriber_id_members
            FOREIGN KEY (workspace_id, subscriber_id) REFERENCES members(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_subscription ON issue_subscriptions(issue_id, subscriber_id)"
    )
    op.execute(
        "CREATE INDEX idx_subscriptions_issue ON issue_subscriptions(workspace_id, issue_id) "
        "WHERE NOT muted"
    )

    # -- notifications: the inbox data source (§2.6) -----------------------------
    op.execute(
        """
        CREATE TABLE notifications (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          recipient_id  UUID NOT NULL,
          type          TEXT NOT NULL,
          priority      TEXT NOT NULL,
          actor_kind    TEXT NULL,
          actor_id      UUID NULL,
          issue_id      UUID NULL,
          comment_id    UUID NULL,
          execution_id  UUID NULL,
          payload       JSONB NOT NULL DEFAULT '{}',
          group_key     TEXT NULL,
          read_at       TIMESTAMPTZ NULL,
          archived_at   TIMESTAMPTZ NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT notifications_type CHECK (type IN
            ('assigned','mentioned','subscribed_update','comment_created','status_changed',
             'execution_finished','review_requested','due_soon')),
          CONSTRAINT notifications_priority CHECK (priority IN ('critical','normal')),
          CONSTRAINT notifications_actor_kind
            CHECK (actor_kind IS NULL OR actor_kind IN ('member','system')),
          CONSTRAINT notifications_actor_identity CHECK (
            (actor_kind = 'member' AND actor_id IS NOT NULL)
            OR ((actor_kind IS NULL OR actor_kind = 'system') AND actor_id IS NULL)
          ),
          CONSTRAINT notifications_recipient_id_members
            FOREIGN KEY (workspace_id, recipient_id) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT notifications_actor_id_members
            FOREIGN KEY (workspace_id, actor_id) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT,
          CONSTRAINT notifications_issue_id_issues
            FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id)
            ON DELETE SET NULL (issue_id),
          CONSTRAINT notifications_comment_id_comments
            FOREIGN KEY (workspace_id, comment_id) REFERENCES comments(workspace_id, id)
            ON DELETE SET NULL (comment_id)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_notifications_ws_id ON notifications(workspace_id, id)")
    op.execute(
        "CREATE INDEX idx_notifications_inbox "
        "ON notifications(workspace_id, recipient_id, archived_at, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_notifications_unread ON notifications(workspace_id, recipient_id) "
        "WHERE read_at IS NULL AND archived_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_notifications_group "
        "ON notifications(recipient_id, group_key, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_notifications_payload ON notifications USING gin (payload)")

    # -- notification_preferences: per-member delivery settings (§2.7) ----------
    op.execute(
        """
        CREATE TABLE notification_preferences (
          id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          member_id          UUID NOT NULL,
          event_type         TEXT NOT NULL,
          in_app             BOOLEAN NOT NULL DEFAULT true,
          email              TEXT NOT NULL DEFAULT 'digest',
          quiet_hours_start  TIME NULL,
          quiet_hours_end    TIME NULL,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT notification_preferences_email
            CHECK (email IN ('none','realtime','digest')),
          CONSTRAINT notification_preferences_member_id_members
            FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_notif_pref "
        "ON notification_preferences(workspace_id, member_id, event_type)"
    )

    # -- notification_delivery: destination-grain ledger (§2.8, R3) -------------
    op.execute(
        """
        CREATE TABLE notification_delivery (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          notification_id  UUID NOT NULL,
          channel          TEXT NOT NULL,
          destination_key  TEXT NOT NULL DEFAULT '',
          provider         TEXT NULL,
          external_target  TEXT NULL,
          integration_id   UUID NULL,
          binding_id       UUID NULL,
          state            TEXT NOT NULL DEFAULT 'pending',
          sent_at          TIMESTAMPTZ NULL,
          error            TEXT NULL,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT notification_delivery_channel
            CHECK (channel IN ('in_app','email','websocket','im')),
          CONSTRAINT notification_delivery_provider
            CHECK (provider IS NULL OR provider IN ('feishu','slack','email_smtp')),
          CONSTRAINT notification_delivery_state
            CHECK (state IN ('pending','sent','failed')),
          CONSTRAINT notification_delivery_notification_id_notifications
            FOREIGN KEY (workspace_id, notification_id)
            REFERENCES notifications(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_delivery "
        "ON notification_delivery(notification_id, channel, destination_key)"
    )
    op.execute(
        "CREATE INDEX idx_delivery_pending ON notification_delivery(state, created_at)"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- SECURITY DEFINER resolvers for workspace-less paths --------------------
    # comment-inbox.md §3 exposes /comments/{id}* and /inbox/{notification_id}*
    # without a workspace prefix; the tenant workspace must be resolved before
    # a fail-closed RLS context can be set (same pattern as 0009). The
    # membership gate afterwards still runs under the policies.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_comment_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT c.workspace_id FROM comments c WHERE c.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_notification_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT n.workspace_id FROM notifications n WHERE n.id = p_id
        $$
        """
    )
    for fn in (
        "mesh_comment_workspace_id(uuid)",
        "mesh_notification_workspace_id(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {APP_ROLE}")

    # -- app-role privileges ----------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"comments, comment_mentions, comment_reactions, issue_subscriptions, "
        f"notifications, notification_preferences, notification_delivery "
        f"TO {APP_ROLE}"
    )


def downgrade() -> None:
    for fn in (
        "mesh_notification_workspace_id(uuid)",
        "mesh_comment_workspace_id(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM {APP_ROLE}")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS notification_delivery")
    op.execute("DROP TABLE IF EXISTS notification_preferences")
    op.execute("DROP TABLE IF EXISTS notifications")
    op.execute("DROP TABLE IF EXISTS issue_subscriptions")
    op.execute("DROP TABLE IF EXISTS comment_reactions")
    op.execute("DROP TABLE IF EXISTS comment_mentions")
    op.execute("ALTER TABLE comments DROP CONSTRAINT IF EXISTS comments_thread_root_id_comments")
    op.execute("ALTER TABLE comments DROP CONSTRAINT IF EXISTS comments_parent_id_comments")
    op.execute("DROP TABLE IF EXISTS comments")
