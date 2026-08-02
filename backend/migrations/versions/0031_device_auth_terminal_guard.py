"""auth: device_authorizations terminal-state immutability trigger

The device grant state machine (auth.md §3.1.1 / MES-80 A2) is enforced by
conditional application-layer updates (approve/deny/consume/expire/invalidate
each carry their from-state predicate). That leaves a gap: ANY new code path
that omits the predicate — or direct operational access — could resurrect a
terminal grant (``consumed`` → ``approved``) and redeem a device code twice.
Close it at the database layer: a BEFORE UPDATE trigger rejects every status
change away from a terminal state (consumed / expired / invalidated), so the
invariant holds regardless of the writer.

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION device_authorizations_terminal_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.status IN ('consumed', 'expired', 'invalidated')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION
                    'device authorization % is terminal (%) — '
                    'state transitions out of terminal states are forbidden',
                    OLD.id, OLD.status
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER device_authorizations_terminal_guard
        BEFORE UPDATE ON device_authorizations
        FOR EACH ROW
        EXECUTE FUNCTION device_authorizations_terminal_guard();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS device_authorizations_terminal_guard ON device_authorizations")
    op.execute("DROP FUNCTION IF EXISTS device_authorizations_terminal_guard()")
