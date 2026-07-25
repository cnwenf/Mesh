"""Workspace schema contract tests (workspace.md §2/§5, README §6.2/§9 T1/T32).

Asserts the migrated database carries the canonical constraints: same-tenant
composite FKs that reject cross-workspace references at INSERT, the unique
keys the module's idempotency relies on, fail-closed RLS policies, the
append-only audit trail, and the locale single-source model (no
``default_language`` column — T32).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.unit


async def _composite_fks(db_session, table: str) -> str:
    rows = (
        await db_session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t) AND contype = 'f'"
            ),
            {"t": table},
        )
    ).scalars().all()
    return "\n".join(rows)


async def test_same_tenant_composite_fks_exist(db_session):
    """§6.2 / §9 T1: every cross-module reference is a composite FK."""
    invitations = await _composite_fks(db_session, "workspace_invitations")
    assert "FOREIGN KEY (workspace_id, invited_by) REFERENCES members(workspace_id, id)" in invitations

    redemptions = await _composite_fks(db_session, "workspace_invitation_redemptions")
    assert (
        "FOREIGN KEY (workspace_id, invitation_id) "
        "REFERENCES workspace_invitations(workspace_id, id) ON DELETE CASCADE"
    ) in redemptions
    assert "FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id)" in redemptions

    audit = await _composite_fks(db_session, "audit_logs")
    assert (
        "FOREIGN KEY (workspace_id, actor_member_id) REFERENCES members(workspace_id, id)"
    ) in audit

    access = await _composite_fks(db_session, "member_project_access")
    assert (
        "FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE"
    ) in access


async def test_unique_keys_for_reference_and_idempotency(db_session):
    indexes = (
        await db_session.execute(
            text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
        )
    ).all()
    by_name = {name: definition for _, name, definition in indexes}

    # Composite-FK reference targets (README §6.2).
    assert "uq_members_ws_id" in by_name
    assert "UNIQUE" in by_name["uq_members_ws_id"].upper()
    assert "workspace_id, id" in by_name["uq_members_ws_id"]
    assert "uq_ws_invitations_ws_id" in by_name

    # Prefix registry permanent exclusivity (README §6.3 / §9 T19).
    assert "uq_prefix_registry_ws_key" in by_name
    assert "workspace_id, key" in by_name["uq_prefix_registry_ws_key"]

    # Acceptance idempotency basis (§2.4 / §3.2).
    assert "uq_ws_inv_redemptions_inv_user" in by_name
    assert "invitation_id, user_id" in by_name["uq_ws_inv_redemptions_inv_user"]

    # Slug redirect history is global-unique.
    assert "uq_slug_history_old_slug" in by_name

    # At most one active directed invitation per (workspace, email).
    active_email = by_name["uq_ws_invitations_active_email"]
    assert "UNIQUE" in active_email.upper()
    assert "(email IS NOT NULL)" in active_email
    assert "(status = 'active'::text)" in active_email


async def test_workspace_has_no_default_language_column(db_session):
    """T32 / R4: the locale single source is settings.default_locale only."""
    columns = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'workspaces'"
            )
        )
    ).scalars().all()
    assert "default_language" not in columns
    assert "settings" in columns


async def test_invitation_limits_are_not_null(db_session):
    """MES-4: max_uses / expires_at can never be NULL (no unlimited links)."""
    nullable = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'workspace_invitations' "
                "AND is_nullable = 'YES'"
            )
        )
    ).scalars().all()
    assert "max_uses" not in nullable
    assert "expires_at" not in nullable
    assert "status" not in nullable


async def test_rls_enabled_with_tenant_policies(db_session):
    rls = (
        await db_session.execute(
            text(
                "SELECT tablename, rowsecurity FROM pg_tables WHERE tablename IN "
                "('members', 'workspace_invitations', 'workspace_invitation_redemptions', "
                "'workspace_slug_history', 'identifier_prefix_registry', "
                "'member_project_access', 'audit_logs')"
            )
        )
    ).all()
    assert {table for table, enabled in rls if enabled} == {
        "members",
        "workspace_invitations",
        "workspace_invitation_redemptions",
        "workspace_slug_history",
        "identifier_prefix_registry",
        "member_project_access",
        "audit_logs",
    }
    policies = {
        name
        for name, in (
            await db_session.execute(text("SELECT polname FROM pg_policy"))
        ).all()
    }
    assert {
        "mesh_members_tenant",
        "mesh_workspace_invitations_tenant",
        "mesh_workspace_invitation_redemptions_tenant",
        "mesh_workspace_slug_history_tenant",
        "mesh_identifier_prefix_registry_tenant",
        "mesh_member_project_access_tenant",
        "mesh_audit_logs_tenant",
    } <= policies


async def test_audit_logs_is_append_only_at_db_level(db_session):
    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO audit_logs (actor_kind, action) "
                "VALUES ('system', 'test.append_only')"
            )
        )
    with pytest.raises(DBAPIError):
        async with db_session.begin():
            await db_session.execute(
                text("UPDATE audit_logs SET action = 'test.tampered'")
            )
    with pytest.raises(DBAPIError):
        async with db_session.begin():
            await db_session.execute(text("DELETE FROM audit_logs"))


async def test_member_polymorphic_check_enforced(db_session, workspace_factory):
    workspace = await workspace_factory()
    # human member without user_id is rejected by the CHECK.
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, role) "
                    "VALUES (:ws, 'human', 'member')"
                ),
                {"ws": workspace.id},
            )
    # agent member with role='owner' is rejected by the CHECK.
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, agent_id, role) "
                    "VALUES (:ws, 'agent', gen_random_uuid(), 'owner')"
                ),
                {"ws": workspace.id},
            )


async def _seed_member(db_session, workspace_id: uuid.UUID, email: str, role: str = "member"):
    """Insert a user + human member; return ``(user_id, member_id)``."""
    user_id = (
        await db_session.execute(
            text("INSERT INTO users (email, display_name) VALUES (:e, 'M') RETURNING id"),
            {"e": email},
        )
    ).scalar_one()
    member_id = (
        await db_session.execute(
            text(
                "INSERT INTO members (workspace_id, member_type, user_id, role) "
                "VALUES (:ws, 'human', :u, :role) RETURNING id"
            ),
            {"ws": workspace_id, "u": user_id, "role": role},
        )
    ).scalar_one()
    return user_id, member_id


async def test_cross_tenant_composite_fk_rejected(db_session, workspace_factory):
    """§9 T1: referencing another workspace's member/link is an INSERT error."""
    ws_a = await workspace_factory(name="A", slug="ws-a-composite")
    ws_b = await workspace_factory(name="B", slug="ws-b-composite")

    async with db_session.begin():
        _, member_a = await _seed_member(
            db_session, ws_a.id, f"a-{uuid.uuid4().hex[:10]}@corp.com", "owner"
        )
        user_b, member_b = await _seed_member(
            db_session, ws_b.id, f"b-{uuid.uuid4().hex[:10]}@corp.com", "admin"
        )
        invitation_a = (
            await db_session.execute(
                text(
                    "INSERT INTO workspace_invitations "
                    "(workspace_id, token_hash, token_prefix, role, invited_by, "
                    " max_uses, expires_at) "
                    "VALUES (:ws, :h, 'invtk_y', 'member', :m, 10, "
                    "now() + interval '7 days') RETURNING id"
                ),
                {"ws": ws_a.id, "h": uuid.uuid4().hex, "m": member_a},
            )
        ).scalar_one()

    # Invitation in A claiming an inviter from B → composite FK rejects.
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO workspace_invitations "
                    "(workspace_id, token_hash, token_prefix, role, invited_by, "
                    " max_uses, expires_at) "
                    "VALUES (:ws_a, :h, 'invtk_z', 'member', :member_b, 10, "
                    "now() + interval '7 days')"
                ),
                {"ws_a": ws_a.id, "h": uuid.uuid4().hex, "member_b": member_b},
            )

    # Redemption in B pointing at A's invitation → composite FK rejects.
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO workspace_invitation_redemptions "
                    "(workspace_id, invitation_id, user_id, member_id) "
                    "VALUES (:ws_b, :inv, :u, :mb)"
                ),
                {"ws_b": ws_b.id, "inv": invitation_a, "u": user_b, "mb": member_b},
            )

    # Redemption in A pointing at B's member → composite FK rejects.
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO workspace_invitation_redemptions "
                    "(workspace_id, invitation_id, user_id, member_id) "
                    "VALUES (:ws_a, :inv, :u, :mb)"
                ),
                {"ws_a": ws_a.id, "inv": invitation_a, "u": user_b, "mb": member_b},
            )


async def test_prefix_registry_key_exclusive_per_workspace(db_session, workspace_factory):
    """§2.6 / §9 T19: one workspace's prefix (any kind) cannot be re-registered."""
    workspace = await workspace_factory()
    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO identifier_prefix_registry (workspace_id, key, kind) "
                "VALUES (:ws, 'WS', 'inbox')"
            ),
            {"ws": workspace.id},
        )
    with pytest.raises(IntegrityError):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO identifier_prefix_registry (workspace_id, key, kind) "
                    "VALUES (:ws, 'WS', 'retired')"
                ),
                {"ws": workspace.id},
            )
    # A different workspace may use the same key (exclusivity is per workspace).
    other = await workspace_factory(name="Other", slug="ws-prefix-other")
    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO identifier_prefix_registry (workspace_id, key, kind) "
                "VALUES (:ws, 'WS', 'inbox')"
            ),
            {"ws": other.id},
        )


async def test_definer_functions_exist_and_are_restricted(db_session):
    functions = (
        await db_session.execute(
            text(
                "SELECT p.proname, p.prosecdef "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname IN "
                "('mesh_invitation_by_token_hash', 'mesh_my_workspaces')"
            )
        )
    ).all()
    by_name = {name: is_definer for name, is_definer in functions}
    assert by_name.get("mesh_invitation_by_token_hash") is True
    assert by_name.get("mesh_my_workspaces") is True
    # PUBLIC cannot execute the tenant-bypassing functions (a PUBLIC grant
    # shows up as an ACL entry with an empty grantee, e.g. "=X/mesh").
    acl = (
        await db_session.execute(
            text(
                "SELECT proacl::text FROM pg_proc WHERE proname = 'mesh_my_workspaces'"
            )
        )
    ).scalar_one()
    entries = acl.strip("{}").split(",")
    assert not any(entry.startswith("=X") for entry in entries)
    assert any(entry.startswith("mesh_app=X") for entry in entries)
