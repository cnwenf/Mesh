"""Database-level agent constraint tests (README §6.2 rules 2/6/7, §9 T1/T27).

Real PostgreSQL, raw SQL: the same-tenant composite FKs, the same-parent
OVERLAPPING FK on ``agents.active_config_version_id``, and the column-level
``ON DELETE SET NULL`` behavior must all be enforced by the database
itself — service code is the first line, these constraints the last.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


async def _seed_world(db_session) -> dict:
    """Two workspaces, one user, agents A/B in ws1, C in ws2, member M in ws1."""
    ws1, ws2, user, member, a, b, c = (uuid.uuid4() for _ in range(7))
    await db_session.execute(
        text("INSERT INTO workspaces (id, name, slug) VALUES (:w, 'WS1', :s1), (:w2, 'WS2', :s2)"),
        {"w": ws1, "s1": f"ws1-{ws1.hex[:8]}", "w2": ws2, "s2": f"ws2-{ws2.hex[:8]}"},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, display_name) VALUES (:u, :e, 'T27 Owner')"
        ),
        {"u": user, "e": f"t27-{user.hex[:8]}@corp.com"},
    )
    await db_session.execute(
        text(
            "INSERT INTO agents (id, workspace_id, name, owner_user_id) VALUES "
            "(:a, :w1, 'Agent A', :u), (:b, :w1, 'Agent B', :u), (:c, :w2, 'Agent C', :u)"
        ),
        {"a": a, "b": b, "c": c, "w1": ws1, "w2": ws2, "u": user},
    )
    await db_session.execute(
        text(
            "INSERT INTO members (id, workspace_id, member_type, user_id) "
            "VALUES (:m, :w1, 'human', :u)"
        ),
        {"m": member, "w1": ws1, "u": user},
    )
    return {"ws1": ws1, "ws2": ws2, "user": user, "member": member, "a": a, "b": b, "c": c}


async def _seed_version(db_session, *, version_id, ws, agent, member) -> None:
    await db_session.execute(
        text(
            "INSERT INTO agent_config_versions (id, workspace_id, agent_id, snapshot, changed_by) "
            "VALUES (:v, :w, :a, '{}'::jsonb, :m)"
        ),
        {"v": version_id, "w": ws, "a": agent, "m": member},
    )


@pytest.mark.unit
async def test_t27_cross_agent_active_pointer_rejected(db_session):
    ids = await _seed_world(db_session)
    v_a, v_b = uuid.uuid4(), uuid.uuid4()
    await _seed_version(db_session, version_id=v_a, ws=ids["ws1"], agent=ids["a"], member=ids["member"])
    await _seed_version(db_session, version_id=v_b, ws=ids["ws1"], agent=ids["b"], member=ids["member"])
    # Pointing agent A at agent B's version — the overlapping composite FK
    # (workspace_id, id, active_config_version_id) → (ws, agent_id, id) rejects.
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE agents SET active_config_version_id = :v WHERE id = :a"),
            {"v": v_b, "a": ids["a"]},
        )


@pytest.mark.unit
async def test_t27_cross_workspace_active_pointer_rejected(db_session):
    ids = await _seed_world(db_session)
    # A member of ws2 is needed for the version's changed_by composite FK.
    member2 = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO members (id, workspace_id, member_type, user_id) "
            "VALUES (:m, :w2, 'human', :u)"
        ),
        {"m": member2, "w2": ids["ws2"], "u": ids["user"]},
    )
    v_c = uuid.uuid4()
    await _seed_version(db_session, version_id=v_c, ws=ids["ws2"], agent=ids["c"], member=member2)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE agents SET active_config_version_id = :v WHERE id = :a"),
            {"v": v_c, "a": ids["a"]},
        )


@pytest.mark.unit
async def test_t27_own_version_pointer_allowed_and_set_null_on_delete(db_session):
    ids = await _seed_world(db_session)
    v_a = uuid.uuid4()
    await _seed_version(db_session, version_id=v_a, ws=ids["ws1"], agent=ids["a"], member=ids["member"])
    # POS: the agent's OWN version passes the overlap FK.
    await db_session.execute(
        text("UPDATE agents SET active_config_version_id = :v WHERE id = :a"),
        {"v": v_a, "a": ids["a"]},
    )
    # §6.2 rule 6: deleting the version nulls ONLY the reference column
    # (PG16 column-level SET NULL) — the tenant key stays intact.
    await db_session.execute(text("DELETE FROM agent_config_versions WHERE id = :v"), {"v": v_a})
    row = (
        await db_session.execute(
            text(
                "SELECT active_config_version_id IS NULL AS ptr_null, "
                "workspace_id IS NOT NULL AS ws_kept FROM agents WHERE id = :a"
            ),
            {"a": ids["a"]},
        )
    ).one()
    assert row.ptr_null is True
    assert row.ws_kept is True


@pytest.mark.unit
async def test_t1_cross_tenant_composite_fks_rejected(db_session):
    ids = await _seed_world(db_session)
    # Version row: ws2 agent with a ws1 audit member → composite FK rejects.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO agent_config_versions (id, workspace_id, agent_id, snapshot, changed_by) "
                    "VALUES (:v, :w2, :c, '{}'::jsonb, :m_ws1)"
                ),
                {"v": uuid.uuid4(), "w2": ids["ws2"], "c": ids["c"], "m_ws1": ids["member"]},
            )
    # Roster row: ws2 referencing ws1's agent → composite FK rejects.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO members (id, workspace_id, member_type, agent_id) "
                    "VALUES (:m, :w2, 'agent', :a_ws1)"
                ),
                {"m": uuid.uuid4(), "w2": ids["ws2"], "a_ws1": ids["a"]},
            )


@pytest.mark.unit
async def test_members_agent_fk_rejects_unknown_agent(db_session):
    ids = await _seed_world(db_session)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO members (id, workspace_id, member_type, agent_id) "
                    "VALUES (:m, :w1, 'agent', :ghost)"
                ),
                {"m": uuid.uuid4(), "w1": ids["ws1"], "ghost": uuid.uuid4()},
            )
