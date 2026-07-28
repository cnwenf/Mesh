"""chat: chat_sessions, chat_messages + favorites (README §6.19)

Stage-7 collaboration-layer increment C (chat-session.md §2 / README §6.2
rules 1/4/6/7 / §6.19). Tables (all tenant-scoped, RLS fail-closed per
README §6.2 rule 5):

- ``chat_sessions`` — the 1:1 human↔agent conversation. Composite FKs to
  members (owner) / agents / issues / projects; nullable context references
  use column-level ``ON DELETE SET NULL (<col>)`` (rule 6) so the tenant key
  survives. NO pinning column: the pinning truth source is
  ``favorites(target_type='chat_session')`` (R3 / §6.19).
- ``chat_messages`` — candidate-reply branching (§2.3): ``parent_id`` /
  ``quote_message_id`` use overlapping composite self-FKs
  ``(workspace_id, session_id, <ref>) → chat_messages(workspace_id,
  session_id, id)`` so cross-session parenting / quoting is rejected at
  INSERT time (rule 7; referenced key ``uq_chat_messages_ws_session_id``).
  ``generation_status`` carries the §4.4 state machine; the partial index
  ``idx_chat_messages_streaming`` is the single-concurrency guard fast path.
  ``idempotency_key`` implements receiver de-dup for the §3.5 idempotent
  writes (partial unique, NULLs never collide).
- ``favorites`` — the unified member-private favorite model (§6.19);
  polymorphic logical FK target (rule 4 — row carries ``workspace_id``,
  consistency via soft-delete + service layer).

Bootstrap read (RLS is fail-closed; workspace unknown until the lookup):
- ``mesh_chat_session_workspace_id`` — session id → workspace id for the
  workspace-less internals (same SECURITY DEFINER pattern as 0018).

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-28
"""
from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "chat_sessions",
    "chat_messages",
    "favorites",
)


def upgrade() -> None:
    # -- chat_sessions ----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE chat_sessions (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          owner_id             UUID NOT NULL,
          agent_id             UUID NOT NULL,
          title                TEXT NOT NULL DEFAULT '新对话',
          title_is_auto        BOOLEAN NOT NULL DEFAULT true,
          context_issue_id     UUID NULL,
          context_project_id   UUID NULL,
          status               TEXT NOT NULL DEFAULT 'active',
          last_message_at      TIMESTAMPTZ NULL,
          last_message_preview TEXT NULL,
          message_count        INT NOT NULL DEFAULT 0,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at           TIMESTAMPTZ NULL,
          CONSTRAINT chat_sessions_status
            CHECK (status IN ('active', 'archived', 'deleted')),
          CONSTRAINT chat_sessions_message_count_nonneg
            CHECK (message_count >= 0)
        )
        """
    )
    # Composite-FK reference target (README §6.2 rule 1) — created BEFORE the
    # child FKs that reference it.
    op.execute(
        "CREATE UNIQUE INDEX uq_chat_sessions_ws_id ON chat_sessions(workspace_id, id)"
    )
    op.execute(
        """
        CREATE INDEX idx_chat_sessions_owner_list
          ON chat_sessions(owner_id, last_message_at DESC)
          WHERE deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX idx_chat_sessions_owner_agent
          ON chat_sessions(owner_id, agent_id, last_message_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_chat_sessions_context_issue
          ON chat_sessions(context_issue_id)
          WHERE context_issue_id IS NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE chat_sessions
          ADD CONSTRAINT chat_sessions_owner_id_members
          FOREIGN KEY (workspace_id, owner_id)
          REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        """
    )
    op.execute(
        """
        ALTER TABLE chat_sessions
          ADD CONSTRAINT chat_sessions_agent_id_agents
          FOREIGN KEY (workspace_id, agent_id)
          REFERENCES agents(workspace_id, id) ON DELETE RESTRICT
        """
    )
    # Column-level SET NULL (README §6.2 rule 6): only the nullable reference
    # column is nulled; workspace_id stays intact.
    op.execute(
        """
        ALTER TABLE chat_sessions
          ADD CONSTRAINT chat_sessions_context_issue_id_issues
          FOREIGN KEY (workspace_id, context_issue_id)
          REFERENCES issues(workspace_id, id) ON DELETE SET NULL (context_issue_id)
        """
    )
    op.execute(
        """
        ALTER TABLE chat_sessions
          ADD CONSTRAINT chat_sessions_context_project_id_projects
          FOREIGN KEY (workspace_id, context_project_id)
          REFERENCES projects(workspace_id, id) ON DELETE SET NULL (context_project_id)
        """
    )

    # -- chat_messages ----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE chat_messages (
          id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          session_id         UUID NOT NULL,
          role               TEXT NOT NULL,
          content            TEXT NOT NULL DEFAULT '',
          generation_id      UUID NULL,
          generation_status  TEXT NOT NULL DEFAULT 'done',
          parent_id          UUID NULL,
          selected_candidate BOOLEAN NOT NULL DEFAULT true,
          quote_message_id   UUID NULL,
          prompt_tokens      INT NULL,
          completion_tokens  INT NULL,
          error_message      TEXT NULL,
          started_at         TIMESTAMPTZ NULL,
          finished_at        TIMESTAMPTZ NULL,
          idempotency_key    TEXT NULL,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT chat_messages_role
            CHECK (role IN ('user', 'agent', 'system')),
          CONSTRAINT chat_messages_generation_status
            CHECK (generation_status IN ('streaming', 'done', 'failed', 'interrupted')),
          CONSTRAINT chat_messages_parent_not_self
            CHECK (parent_id <> id),
          CONSTRAINT chat_messages_quote_not_self
            CHECK (quote_message_id <> id),
          CONSTRAINT chat_messages_session_id_chat_sessions
            FOREIGN KEY (workspace_id, session_id)
            REFERENCES chat_sessions(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_chat_messages_ws_id ON chat_messages(workspace_id, id)"
    )
    # Overlapping key referenced by the same-session self-FKs (rule 7).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_chat_messages_ws_session_id
          ON chat_messages(workspace_id, session_id, id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_chat_messages_idempotency
          ON chat_messages(workspace_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX idx_chat_messages_session_time
          ON chat_messages(session_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_chat_messages_parent
          ON chat_messages(parent_id) WHERE parent_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX idx_chat_messages_streaming
          ON chat_messages(session_id) WHERE generation_status = 'streaming'
        """
    )
    # Same-session overlapping composite self-FKs (rule 7) with column-level
    # SET NULL (rule 6) — deleting a referenced message only unlinks the
    # reference column, never the tenant key or the session binding.
    op.execute(
        """
        ALTER TABLE chat_messages
          ADD CONSTRAINT chat_messages_parent_id_chat_messages
          FOREIGN KEY (workspace_id, session_id, parent_id)
          REFERENCES chat_messages(workspace_id, session_id, id)
          ON DELETE SET NULL (parent_id)
        """
    )
    op.execute(
        """
        ALTER TABLE chat_messages
          ADD CONSTRAINT chat_messages_quote_message_id_chat_messages
          FOREIGN KEY (workspace_id, session_id, quote_message_id)
          REFERENCES chat_messages(workspace_id, session_id, id)
          ON DELETE SET NULL (quote_message_id)
        """
    )

    # -- favorites (README §6.19) -----------------------------------------------
    op.execute(
        """
        CREATE TABLE favorites (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          member_id    UUID NOT NULL,
          target_type  TEXT NOT NULL,
          target_id    UUID NOT NULL,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT favorites_target_type
            CHECK (target_type IN ('issue', 'project', 'view', 'chat_session')),
          CONSTRAINT favorites_member_id_members
            FOREIGN KEY (workspace_id, member_id)
            REFERENCES members(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_favorites_member_target
          ON favorites(member_id, target_type, target_id)
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_favorites_ws_id ON favorites(workspace_id, id)")
    op.execute(
        """
        CREATE INDEX idx_favorites_member
          ON favorites(workspace_id, member_id, created_at DESC)
        """
    )

    # -- RLS (fail-closed, README §6.2 rule 5) -----------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- SECURITY DEFINER resolver for workspace-less lookups --------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_chat_session_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT s.workspace_id FROM chat_sessions s WHERE s.id = p_id
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_chat_session_workspace_id(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_chat_session_workspace_id(uuid) TO {APP_ROLE}")

    # -- app-role privileges ------------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"chat_sessions, chat_messages, favorites TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute(f"REVOKE EXECUTE ON FUNCTION mesh_chat_session_workspace_id(uuid) FROM {APP_ROLE}")
    op.execute("DROP FUNCTION IF EXISTS mesh_chat_session_workspace_id(uuid)")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS favorites")
    op.execute(
        "ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS "
        "chat_messages_quote_message_id_chat_messages"
    )
    op.execute(
        "ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS "
        "chat_messages_parent_id_chat_messages"
    )
    op.execute("DROP TABLE IF EXISTS chat_messages")
    op.execute(
        "ALTER TABLE chat_sessions DROP CONSTRAINT IF EXISTS "
        "chat_sessions_context_project_id_projects"
    )
    op.execute(
        "ALTER TABLE chat_sessions DROP CONSTRAINT IF EXISTS "
        "chat_sessions_context_issue_id_issues"
    )
    op.execute("DROP TABLE IF EXISTS chat_sessions")
