"""onboarding: onboarding_states + onboarding_state_steps

Platform-capability increment (onboarding.md §2 / §3.5, README §6.1 / §6.2 /
§6.6 / §6.7). Single-head chain 0001 → 0027.

Tables (all tenant-scoped, RLS fail-closed per README §6.2 rule 5):

- ``onboarding_states`` — per member × workspace × checklist progress record
  (onboarding.md §2.2). ``UNIQUE(workspace_id, member_id, checklist)`` is the
  database basis for idempotent create/get (§3.5); ``aha_reached_at`` is set
  exactly once by conditional UPDATE. Composite FK
  ``(workspace_id, member_id) → members(workspace_id, id)`` ON DELETE CASCADE
  (README §6.1 / §6.2 — member ownership verifiable at INSERT time).
- ``onboarding_state_steps`` — step detail child table (§2.3), one row per
  step per checklist. The ``(status='completed') = (completed_at IS NOT
  NULL)`` CHECK keeps completion state and timestamp consistent; the partial
  index ``idx_onboarding_steps_pending`` scopes the precise UPDATE the domain
  -event consumers run (§5.2). Composite FK
  ``(workspace_id, state_id) → onboarding_states(workspace_id, id)`` ON
  DELETE CASCADE.

The module holds NO FKs to business entities (issues / agents / executions /
comments): auto-completion consumes outbox events plus workspace-scoped
queries (§3.6).

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = ("onboarding_states", "onboarding_state_steps")
DML_TABLES = ", ".join(TENANT_TABLES)

STEP_KEYS = (
    "create_workspace",
    "invite_member_or_add_agent",
    "create_first_issue",
    "dispatch_or_mention_agent",
    "see_agent_reply_in_inbox",
)
STEP_KEYS_SQL = ", ".join(f"'{key}'" for key in STEP_KEYS)


def upgrade() -> None:
    # -- onboarding_states (onboarding.md §2.2) --------------------------------
    op.execute(
        """
        CREATE TABLE onboarding_states (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          member_id      UUID NOT NULL,
          checklist      TEXT NOT NULL DEFAULT 'activation'
                         CHECK (char_length(checklist) BETWEEN 1 AND 40),
          aha_reached_at TIMESTAMPTZ NULL,
          dismissed_at   TIMESTAMPTZ NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # One record per member per workspace per checklist (idempotent seed).
    op.execute(
        "CREATE UNIQUE INDEX uq_onboarding_states_ws_member_checklist "
        "ON onboarding_states (workspace_id, member_id, checklist)"
    )
    # Composite-FK reference target for onboarding_state_steps (README §6.2).
    op.execute(
        "CREATE UNIQUE INDEX uq_onboarding_states_ws_id ON onboarding_states (workspace_id, id)"
    )
    # Checklists without aha, per workspace (admin reset / funnel inspection).
    op.execute(
        "CREATE INDEX idx_onboarding_states_ws_aha "
        "ON onboarding_states (workspace_id, created_at) WHERE aha_reached_at IS NULL"
    )
    # Member ownership — cross-tenant member_id rejected at INSERT (README §6.2).
    op.execute(
        "ALTER TABLE onboarding_states ADD CONSTRAINT onboarding_states_member_id_members "
        "FOREIGN KEY (workspace_id, member_id) "
        "REFERENCES members(workspace_id, id) ON DELETE CASCADE"
    )

    # -- onboarding_state_steps (onboarding.md §2.3) ---------------------------
    op.execute(
        f"""
        CREATE TABLE onboarding_state_steps (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          state_id      UUID NOT NULL,
          step_key      TEXT NOT NULL CHECK (step_key IN ({STEP_KEYS_SQL})),
          status        TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','completed','skipped')),
          completed_via TEXT NULL CHECK (completed_via IS NULL
                        OR completed_via IN ('auto','manual')),
          completed_at  TIMESTAMPTZ NULL,
          evidence      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT onboarding_state_steps_completed_consistency
            CHECK ((status = 'completed') = (completed_at IS NOT NULL))
        )
        """
    )
    # One row per step per checklist.
    op.execute(
        "CREATE UNIQUE INDEX uq_onboarding_steps_ws_state_step "
        "ON onboarding_state_steps (workspace_id, state_id, step_key)"
    )
    # Composite-FK reference target shape (README §6.2).
    op.execute(
        "CREATE UNIQUE INDEX uq_onboarding_steps_ws_id "
        "ON onboarding_state_steps (workspace_id, id)"
    )
    # Auto-detection precise-UPDATE scope (§5.2 — no full scans).
    op.execute(
        "CREATE INDEX idx_onboarding_steps_pending "
        "ON onboarding_state_steps (workspace_id, step_key) WHERE status <> 'completed'"
    )
    op.execute(
        "ALTER TABLE onboarding_state_steps "
        "ADD CONSTRAINT onboarding_state_steps_state_id_onboarding_states "
        "FOREIGN KEY (workspace_id, state_id) "
        "REFERENCES onboarding_states(workspace_id, id) ON DELETE CASCADE"
    )

    # -- RLS (defense-in-depth, README §6.2 rule 5) -----------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- app-role privileges -----------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DML_TABLES} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {DML_TABLES} FROM {APP_ROLE}")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS onboarding_state_steps")
    op.execute("DROP TABLE IF EXISTS onboarding_states")
