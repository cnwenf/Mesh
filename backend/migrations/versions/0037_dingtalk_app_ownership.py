"""DingTalk app-key ownership lookup for cross-tenant admission.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    # Existing conflicting ownership is unsafe to guess. Abort migration and
    # require an operator to resolve it explicitly instead of choosing a
    # tenant based on row/UUID order.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT i.config->>'app_key'
            FROM integrations i
            WHERE i.kind = 'im_dingtalk'
              AND i.deleted_at IS NULL
              AND COALESCE(i.config->>'app_key', '') <> ''
            GROUP BY i.config->>'app_key'
            HAVING count(DISTINCT i.workspace_id) > 1
          ) THEN
            RAISE EXCEPTION 'DingTalk app_key is claimed by multiple workspaces'
              USING ERRCODE = '23505';
          END IF;
        END
        $$
        """
    )
    # SECURITY DEFINER is the narrow cross-tenant read seam used after the
    # caller takes a transaction advisory lock. API sessions otherwise run as
    # mesh_app under tenant RLS and cannot see a foreign claimant.
    op.execute(
        """
        CREATE FUNCTION mesh_dingtalk_app_owner_workspace(p_app_key text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT i.workspace_id
          FROM integrations i
          WHERE i.kind = 'im_dingtalk'
            AND i.deleted_at IS NULL
            AND i.config->>'app_key' = p_app_key
          ORDER BY i.created_at ASC, i.id ASC
          LIMIT 1
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION mesh_dingtalk_app_owner_workspace(text) FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION mesh_dingtalk_app_owner_workspace(text) TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION mesh_dingtalk_app_owner_workspace(text) FROM {APP_ROLE}"
    )
    op.execute("DROP FUNCTION mesh_dingtalk_app_owner_workspace(text)")
