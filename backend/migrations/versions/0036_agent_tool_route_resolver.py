"""agent tool exact-route tenant resolver.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `/api/v1/agents/{id}/tools` has no workspace segment.  This narrowly
    # scoped definer resolves a tenant only when the authenticated human is an
    # active member of that same workspace; all subsequent reads/writes still
    # run under the normal tenant GUC and RLS policies.
    op.execute(
        """
        CREATE FUNCTION public.mesh_agent_workspace(p_agent_id UUID, p_user_id UUID)
        RETURNS UUID
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = off
        AS $function$
          SELECT a.workspace_id
          FROM public.agents AS a
          JOIN public.workspaces AS w
            ON w.id = a.workspace_id
           AND w.deleted_at IS NULL
          JOIN public.members AS m
            ON m.workspace_id = a.workspace_id
           AND m.user_id = p_user_id
           AND m.member_type = 'human'
           AND m.status = 'active'
          WHERE a.id = p_agent_id
            AND a.deleted_at IS NULL
          LIMIT 1
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.mesh_agent_workspace(UUID, UUID) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.mesh_agent_workspace(UUID, UUID) TO mesh_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.mesh_agent_workspace(UUID, UUID)")
