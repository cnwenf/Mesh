"""attempt_task_tokens: enable fail-closed tenant RLS (runtime-executor.md §2.2).

Migration 0029 created ``attempt_task_tokens`` (the task token ledger, §2.2 S-05)
with same-tenant composite FKs and app-role grants, but did NOT enable row level
security — even though runtime-executor.md §2.2 declares the table fail-closed
RLS. This migration closes that gap with the exact tenant policy template used
by every other tenant table (0004 / 0008 / 0033): the policy compares
``workspace_id`` against the ``mesh.workspace_id`` GUC set per-transaction by
``set_tenant_context``. Without the GUC the policy cannot be evaluated, so an
unscoped app-role read errors out (fail-closed) instead of leaking rows across
tenants.

The task-token bearer chain discovers the workspace FROM the presented token
(the workspace is unknown until the lookup succeeds), so the bootstrap lookup
cannot run under the policy. Exactly like the runtime-token path (0019's
``mesh_runtime_by_token_hash``), we add a ``SECURITY DEFINER`` function that
reads the token row as the table owner (bypassing RLS for that one indexed
lookup); the caller then sets the tenant GUC and every subsequent read runs
fail-closed under the policy.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    op.execute("ALTER TABLE attempt_task_tokens ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mesh_attempt_task_tokens_tenant ON attempt_task_tokens "
        "USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
    )

    # SECURITY DEFINER bootstrap read (RLS is fail-closed; the presented task
    # token's workspace is unknown until this lookup succeeds). Mirrors
    # mesh_runtime_by_token_hash (0019): runs as the table owner for the one
    # indexed token-hash probe, then the caller sets the tenant GUC and all
    # further reads run under the policy.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_attempt_task_token_workspace_id(p_hash text)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT workspace_id FROM attempt_task_tokens WHERE token_hash = p_hash
        $$
        """
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION mesh_attempt_task_token_workspace_id(text) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION mesh_attempt_task_token_workspace_id(text) TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION mesh_attempt_task_token_workspace_id(text) FROM {APP_ROLE}"
    )
    op.execute("DROP FUNCTION IF EXISTS mesh_attempt_task_token_workspace_id(text)")
    op.execute("DROP POLICY IF EXISTS mesh_attempt_task_tokens_tenant ON attempt_task_tokens")
    op.execute("ALTER TABLE attempt_task_tokens DISABLE ROW LEVEL SECURITY")
