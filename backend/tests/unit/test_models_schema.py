"""Schema assertions: the migrated database matches the spec DDL contract."""

from __future__ import annotations

from sqlalchemy import text


async def test_baseline_tables_exist(db_session):
    rows = (
        await db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
            )
        )
    ).scalars().all()
    assert {"workspaces", "outbox_events", "realtime_channels", "realtime_events"} <= set(rows)


async def test_outbox_unique_idempotency_key_and_partial_index(db_session):
    unique = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'outbox_events' AND indexdef ILIKE '%unique%'"
            )
        )
    ).scalars().all()
    assert any("idempotency_key" in definition for definition in unique)
    pending_index = (
        await db_session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_outbox_pending'")
        )
    ).scalar_one()
    assert "status = 'pending'" in pending_index


async def test_realtime_events_constraints(db_session):
    uniques = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'realtime_events'"
            )
        )
    ).scalars().all()
    joined = "\n".join(uniques)
    assert "channel, seq" in joined  # UNIQUE (channel, seq)
    assert "outbox_event_id" in joined  # UNIQUE (outbox_event_id) — exactly-once record
    fks = (
        await db_session.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'realtime_events'::regclass AND contype = 'f'"
            )
        )
    ).all()
    fk_defs = " ".join(defn for _, defn in fks)
    assert "workspace_id, channel" in fk_defs  # composite FK to realtime_channels


async def test_rls_enabled_with_tenant_policies(db_session):
    rls = (
        await db_session.execute(
            text(
                "SELECT tablename, rowsecurity FROM pg_tables "
                "WHERE tablename IN ('realtime_channels', 'realtime_events')"
            )
        )
    ).all()
    assert {table for table, enabled in rls if enabled} == {
        "realtime_channels",
        "realtime_events",
    }
    policies = (
        await db_session.execute(
            text("SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy")
        )
    ).all()
    names = {name for name, _ in policies}
    assert {"mesh_rt_channels_tenant", "mesh_rt_events_tenant"} <= names
    assert all("mesh.workspace_id" in (qual or "") for _, qual in policies)
