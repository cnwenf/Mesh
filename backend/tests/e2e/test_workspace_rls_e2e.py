"""RLS defense-in-depth e2e for the workspace tenant tables (README §6.2 rule 5).

Proves the policies on the stage-2 tables are fail-closed against a non-owner
login role: without the ``mesh.workspace_id`` GUC nothing is visible; with it,
only the named tenant's rows. Cross-tenant composite FK inserts are rejected
at the database level regardless of RLS (§9 T1).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.e2e

RLS_ROLE = "mesh_ws_rls_test"
RLS_PASSWORD = "mesh_ws_rls_test_pw"

TENANT_TABLES = (
    "members",
    "workspace_invitations",
    "workspace_invitation_redemptions",
    "workspace_slug_history",
    "identifier_prefix_registry",
    "member_project_access",
)


def _role_url(db_url: str) -> str:
    without_scheme = db_url.split("://", 1)[1]
    host_and_db = without_scheme.split("@", 1)[1]
    return f"postgresql+asyncpg://{RLS_ROLE}:{RLS_PASSWORD}@{host_and_db}"


@pytest_asyncio.fixture
async def rls_role(session_factory, db_url):
    """Non-owner login role with SELECT on the new tenant tables.

    INSERT is granted on identifier_prefix_registry so the wrong-tenant write
    test fails on the RLS policy itself (not on missing table privileges).
    """
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{RLS_ROLE}') "
                f"THEN CREATE ROLE {RLS_ROLE} LOGIN PASSWORD '{RLS_PASSWORD}'; END IF; END $$"
            )
        )
        await session.execute(text(f"GRANT USAGE ON SCHEMA public TO {RLS_ROLE}"))
        await session.execute(
            text(f"GRANT SELECT ON {', '.join(TENANT_TABLES)} TO {RLS_ROLE}")
        )
        await session.execute(
            text(f"GRANT INSERT ON identifier_prefix_registry TO {RLS_ROLE}")
        )
        await session.execute(
            text(f"GRANT EXECUTE ON FUNCTION mesh_my_workspaces(uuid) TO {RLS_ROLE}")
        )
    yield _role_url(db_url)


async def _seed_two_tenants(session_factory):
    """Two workspaces, each with a member; return (ws_a, ws_b, user_a id)."""
    async with session_factory() as session, session.begin():
        ws_a = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) VALUES ('A', :s) RETURNING id"
                ),
                {"s": f"rls-a-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        ws_b = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) VALUES ('B', :s) RETURNING id"
                ),
                {"s": f"rls-b-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        user_a = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) VALUES (:e, 'A') RETURNING id"
                ),
                {"e": f"rls-a-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()
        user_b = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) VALUES (:e, 'B') RETURNING id"
                ),
                {"e": f"rls-b-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO members (workspace_id, member_type, user_id, role) "
                "VALUES (:ws, 'human', :u, 'owner')"
            ),
            {"ws": ws_a, "u": user_a},
        )
        await session.execute(
            text(
                "INSERT INTO members (workspace_id, member_type, user_id, role) "
                "VALUES (:ws, 'human', :u, 'owner')"
            ),
            {"ws": ws_b, "u": user_b},
        )
        await session.execute(
            text(
                "INSERT INTO identifier_prefix_registry (workspace_id, key, kind) "
                "VALUES (:ws, 'WS', 'inbox')"
            ),
            {"ws": ws_a},
        )
    return ws_a, ws_b, user_a


async def test_rls_hides_other_tenant_rows(rls_role, session_factory):
    ws_a, ws_b, user_a = await _seed_two_tenants(session_factory)

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(rls_role)
    try:
        async with engine.connect() as conn:
            # Without the GUC the policy cannot even be evaluated — fail-closed
            # (same convention as the baseline realtime RLS tests).
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT count(*) FROM members"))
            await conn.rollback()

            # With the GUC set to tenant A: only A's rows, never B's.
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                    {"ws": str(ws_a)},
                )
                visible_users = (
                    await conn.execute(text("SELECT user_id FROM members"))
                ).scalars().all()
                assert visible_users == [user_a]
                visible_prefixes = (
                    await conn.execute(
                        text("SELECT workspace_id FROM identifier_prefix_registry")
                    )
                ).scalars().all()
                assert visible_prefixes == [ws_a]

            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                    {"ws": str(ws_b)},
                )
                visible_users = (
                    await conn.execute(text("SELECT user_id FROM members"))
                ).scalars().all()
                assert visible_users != [user_a]
                assert len(visible_users) == 1  # only B's own member
    finally:
        await engine.dispose()


async def test_definer_functions_scope_to_caller(rls_role, session_factory):
    """mesh_my_workspaces returns only the given user's memberships."""
    ws_a, ws_b, user_a = await _seed_two_tenants(session_factory)

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(rls_role)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT workspace_id FROM mesh_my_workspaces(:u)"),
                    {"u": user_a},
                )
            ).scalars().all()
            assert rows == [ws_a]
    finally:
        await engine.dispose()


async def test_cross_tenant_composite_fk_rejected_without_rls(session_factory):
    """T1: the composite FKs reject cross-workspace references at INSERT —
    independent of RLS (runs as the owner role, which bypasses policies)."""
    ws_a, ws_b, user_a = await _seed_two_tenants(session_factory)
    async with session_factory() as session:
        member_b = (
            await session.execute(
                text(
                    "SELECT id FROM members WHERE workspace_id = :ws"
                ),
                {"ws": ws_b},
            )
        ).scalar_one()
        member_a = (
            await session.execute(
                text("SELECT id FROM members WHERE workspace_id = :ws"),
                {"ws": ws_a},
            )
        ).scalar_one()

    # Invitation in A with B's member as inviter → rejected.
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO workspace_invitations "
                    "(workspace_id, token_hash, token_prefix, role, invited_by, "
                    " max_uses, expires_at) "
                    "VALUES (:ws_a, :h, 'invtk_x', 'member', :member_b, 10, "
                    "now() + interval '7 days')"
                ),
                {
                    "ws_a": ws_a,
                    "h": uuid.uuid4().hex,
                    "member_b": member_b,
                },
            )

    # Redemption in B pointing at an A-scoped invitation → rejected.
    async with session_factory() as session, session.begin():
        invitation_a = (
            await session.execute(
                text(
                    "INSERT INTO workspace_invitations "
                    "(workspace_id, token_hash, token_prefix, role, invited_by, "
                    " max_uses, expires_at) "
                    "VALUES (:ws_a, :h, 'invtk_y', 'member', :member_a, 10, "
                    "now() + interval '7 days') RETURNING id"
                ),
                {"ws_a": ws_a, "h": uuid.uuid4().hex, "member_a": member_a},
            )
        ).scalar_one()
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO workspace_invitation_redemptions "
                    "(workspace_id, invitation_id, user_id, member_id) "
                    "VALUES (:ws_b, :inv, :u, :mb)"
                ),
                {"ws_b": ws_b, "inv": invitation_a, "u": user_a, "mb": member_a},
            )


async def test_rls_blocks_writes_to_other_tenant(rls_role, session_factory):
    """INSERT under the wrong GUC is invisible/rejected for the app role."""
    ws_a, ws_b, _ = await _seed_two_tenants(session_factory)

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(rls_role)
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                    {"ws": str(ws_a)},
                )
                # A write claiming tenant B while the GUC says A must fail the
                # policy's WITH CHECK.
                with pytest.raises(DBAPIError):
                    await conn.execute(
                        text(
                            "INSERT INTO identifier_prefix_registry (workspace_id, key, kind) "
                            "VALUES (:ws_b, 'X', 'inbox')"
                        ),
                        {"ws_b": ws_b},
                    )
    finally:
        await engine.dispose()
