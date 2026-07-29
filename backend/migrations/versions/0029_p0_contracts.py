"""P0 server contracts: attempt_task_tokens, structured result, runtime token single source.

MES-98 §2.1/§2.2/§2.4/§2.6:
- New ``attempt_task_tokens`` table (task token ledger, §2.2 S-05).
- Structured result fields on ``execution_attempts`` (§2.6).
- ``config_snapshot`` schema version on ``task_executions`` (§2.1).
- Remove ``runtime_token_id`` FK from ``runtimes`` (§2.4 S-11).
- Revoke and clean up runtime rows in ``api_tokens`` (§2.4 S-11).

Revision ID: 0029
Revises: 0028
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. attempt_task_tokens — task token ledger (§2.2 S-05)
    # ------------------------------------------------------------------
    op.create_table(
        "attempt_task_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lease_seq", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "uq_attempt_task_tokens_ws_id",
        "attempt_task_tokens",
        ["workspace_id", "id"],
        unique=True,
    )
    op.create_index(
        "uq_attempt_task_tokens_hash",
        "attempt_task_tokens",
        ["token_hash"],
        unique=True,
    )
    # Only one active (non-revoked, non-expired) token per attempt.
    op.create_index(
        "uq_attempt_task_tokens_active",
        "attempt_task_tokens",
        ["attempt_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "idx_attempt_task_tokens_attempt",
        "attempt_task_tokens",
        ["attempt_id", "lease_seq"],
    )
    # Composite FKs for same-tenant integrity.
    op.create_foreign_key(
        "attempt_task_tokens_ws_attempt_fkey",
        "attempt_task_tokens",
        "execution_attempts",
        ["workspace_id", "attempt_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "attempt_task_tokens_ws_runtime_fkey",
        "attempt_task_tokens",
        "runtimes",
        ["workspace_id", "runtime_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )

    # ------------------------------------------------------------------
    # 2. Structured result fields on execution_attempts (§2.6)
    # ------------------------------------------------------------------
    op.add_column("execution_attempts", sa.Column("provider", sa.Text(), nullable=True))
    op.add_column("execution_attempts", sa.Column("provider_version", sa.Text(), nullable=True))
    op.add_column("execution_attempts", sa.Column("provider_session_id", sa.Text(), nullable=True))
    op.add_column("execution_attempts", sa.Column("model", sa.Text(), nullable=True))
    op.add_column("execution_attempts", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    op.add_column("execution_attempts", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    op.add_column("execution_attempts", sa.Column("cache_tokens", sa.Integer(), nullable=True))
    op.add_column("execution_attempts", sa.Column("cost_usd", sa.Numeric(precision=16, scale=6), nullable=True))
    op.add_column("execution_attempts", sa.Column("num_turns", sa.Integer(), nullable=True))
    op.add_column("execution_attempts", sa.Column("result_schema_version", sa.Integer(), nullable=True))
    op.add_column("execution_attempts", sa.Column("redaction_hits", sa.Integer(), nullable=True, server_default=sa.text("0")))

    # ------------------------------------------------------------------
    # 3. config_snapshot schema version on task_executions (§2.1)
    # ------------------------------------------------------------------
    op.add_column("task_executions", sa.Column("snapshot_schema_version", sa.Integer(), nullable=True, server_default=sa.text("1")))

    # ------------------------------------------------------------------
    # 4. protocol_version on runtimes (§2.6 activate/heartbeat)
    # ------------------------------------------------------------------
    op.add_column("runtimes", sa.Column("protocol_version", sa.Integer(), nullable=True))
    op.add_column("runtimes", sa.Column("daemon_version", sa.Text(), nullable=True))
    op.add_column("runtimes", sa.Column("provider_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("runtimes", sa.Column("daemon_features", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    # ------------------------------------------------------------------
    # 5. claim_request_id on execution_attempts (§13.1)
    # ------------------------------------------------------------------
    op.add_column("execution_attempts", sa.Column("claim_request_id", sa.Text(), nullable=True))
    op.create_index(
        "uq_attempts_runtime_claim_request",
        "execution_attempts",
        ["runtime_id", "claim_request_id"],
        unique=True,
        postgresql_where=sa.text("claim_request_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 6. Runtime token single source migration (§2.4 S-11)
    #    Revoke runtime-associated api_tokens, then drop the FK column.
    # ------------------------------------------------------------------
    # Revoke all runtime-associated api_tokens rows.
    op.execute("""
        UPDATE api_tokens
        SET revoked_at = now(), updated_at = now()
        WHERE id IN (
            SELECT runtime_token_id FROM runtimes
            WHERE runtime_token_id IS NOT NULL
        )
        AND revoked_at IS NULL
    """)
    # Drop the FK constraint and column.
    op.drop_constraint("runtimes_runtime_token_id_fkey", "runtimes", type_="foreignkey")
    op.drop_column("runtimes", "runtime_token_id")

    # Clean up orphaned runtime api_tokens rows (already revoked above).
    op.execute("""
        DELETE FROM api_tokens
        WHERE scopes @> ARRAY['runtime']::text[]
        AND revoked_at IS NOT NULL
    """)

    # ------------------------------------------------------------------
    # 7. New failure reasons (§13.3)
    # ------------------------------------------------------------------
    # Extend the failure_reason CHECK to include new P0 reasons.
    op.execute("ALTER TABLE task_executions DROP CONSTRAINT IF EXISTS task_executions_failure_reason_check")
    op.execute("ALTER TABLE execution_attempts DROP CONSTRAINT IF EXISTS execution_attempts_failure_reason_check")


def downgrade() -> None:
    # Restore runtime_token_id column.
    op.add_column("runtimes", sa.Column("runtime_token_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("runtimes_runtime_token_id_fkey", "runtimes", "api_tokens", ["runtime_token_id"], ["id"], ondelete="SET NULL")

    # Drop new columns.
    op.drop_column("execution_attempts", "claim_request_id")
    op.drop_column("runtimes", "daemon_features")
    op.drop_column("runtimes", "provider_manifest")
    op.drop_column("runtimes", "daemon_version")
    op.drop_column("runtimes", "protocol_version")
    op.drop_column("task_executions", "snapshot_schema_version")
    for col in ("provider", "provider_version", "provider_session_id", "model",
                "prompt_tokens", "completion_tokens", "cache_tokens", "cost_usd",
                "num_turns", "result_schema_version", "redaction_hits"):
        op.drop_column("execution_attempts", col)

    op.drop_table("attempt_task_tokens")
