"""auth increment 2: api_tokens (personal / agent access tokens)

auth.md §2.5 (PAT / agent runtime credentials). Holder is de-polymorphised
(README §6.1): ``owner_member_id`` is a same-tenant composite FK to
``members(workspace_id, id)`` — a human PAT points at the owner's member row,
an agent credential at the agent's member row. Plaintext is shown once; only the
SHA-256 ``token_hash`` is stored. ``role_override`` may not exceed the holder's
role (enforced in the service, at creation AND use — auth.md §5.5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE api_tokens (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          owner_member_id UUID NOT NULL,
          name            TEXT NOT NULL,
          token_hash      TEXT NOT NULL,
          prefix          TEXT NOT NULL,
          scopes          TEXT[] NOT NULL DEFAULT '{}',
          role_override   TEXT NULL,
          last_used_at    TIMESTAMPTZ NULL,
          last_used_ip    INET NULL,
          expires_at      TIMESTAMPTZ NULL,
          revoked_at      TIMESTAMPTZ NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, owner_member_id) REFERENCES members(workspace_id, id)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_api_token_hash ON api_tokens(token_hash)")
    op.execute(
        "CREATE INDEX idx_api_tokens_owner ON api_tokens(workspace_id, owner_member_id) "
        "WHERE revoked_at IS NULL"
    )
    # Composite-FK reference target (README §6.2).
    op.execute("CREATE UNIQUE INDEX uq_api_tokens_ws_id ON api_tokens(workspace_id, id)")

    # RLS defense-in-depth (README §6.2 rule 5): fail-closed without the GUC.
    op.execute("ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mesh_api_tokens_tenant ON api_tokens "
        "USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
    )

    # App role: full DML (create / read / revoke / touch last_used).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON api_tokens TO {APP_ROLE}")

    # SECURITY DEFINER bootstrap: a presented token is looked up by hash BEFORE
    # its workspace is known, so the read cannot carry the tenant GUC yet — the
    # fail-closed RLS policy would hide every row. A narrow definer function
    # (mirrors mesh_invitation_by_token_hash) does the bootstrap read; the caller
    # sets the GUC afterwards for the member read + last_used touch.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_api_token_by_hash(p_hash text)
        RETURNS TABLE (
          id uuid, workspace_id uuid, owner_member_id uuid, name text,
          scopes text[], role_override text,
          revoked_at timestamptz, expires_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT t.id, t.workspace_id, t.owner_member_id, t.name,
                 t.scopes, t.role_override, t.revoked_at, t.expires_at
          FROM api_tokens t
          WHERE t.token_hash = p_hash
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_api_token_by_hash(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_api_token_by_hash(text) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_api_token_by_hash(text) FROM mesh_app")
    op.execute("DROP FUNCTION IF EXISTS mesh_api_token_by_hash(text)")
    op.execute("DROP POLICY IF EXISTS mesh_api_tokens_tenant ON api_tokens")
    op.execute("DROP TABLE IF EXISTS api_tokens")
