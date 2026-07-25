"""Audit log writer tests (auth.md §2.6/§5.5)."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from mesh.auth.audit import write_audit
from mesh.db.models.audit import AuditLog

pytestmark = pytest.mark.unit


async def test_write_audit_inserts_in_caller_transaction(db_session, workspace_factory):
    workspace = await workspace_factory()
    async with db_session.begin():
        actor_id = (
            await db_session.execute(
                text(
                    "INSERT INTO users (email, display_name) VALUES (:e, 'Actor') "
                    "RETURNING id"
                ),
                {"e": "actor@corp.com"},
            )
        ).scalar_one()
        member_id = (
            await db_session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role) "
                    "VALUES (:ws, 'human', :u, 'admin') RETURNING id"
                ),
                {"ws": workspace.id, "u": actor_id},
            )
        ).scalar_one()
        await write_audit(
            db_session,
            workspace_id=workspace.id,
            actor_member_id=member_id,
            actor_kind="member",
            action="member.role_changed",
            resource_type="member",
            resource_id=member_id,
            metadata={"old_role": "member", "new_role": "admin"},
            ip_address="203.0.113.9",
            user_agent="pytest",
        )

    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.workspace_id == workspace.id
    assert row.actor_member_id == member_id
    assert row.actor_kind == "member"
    assert row.action == "member.role_changed"
    assert row.resource_type == "member"
    assert row.resource_id == member_id
    assert row.metadata_ == {"old_role": "member", "new_role": "admin"}
    assert str(row.ip_address) == "203.0.113.9"
    assert row.user_agent == "pytest"


async def test_write_audit_system_actor_without_workspace(db_session):
    async with db_session.begin():
        await write_audit(
            db_session,
            workspace_id=None,
            actor_member_id=None,
            actor_kind="system",
            action="invitation.expired_sweep",
        )
    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.workspace_id is None
    assert row.actor_member_id is None
    assert row.actor_kind == "system"
    assert row.metadata_ == {}


async def test_write_audit_rejects_unknown_actor_kind(db_session, workspace_factory):
    workspace = await workspace_factory()
    with pytest.raises(DBAPIError):
        async with db_session.begin():
            await write_audit(
                db_session,
                workspace_id=workspace.id,
                actor_member_id=None,
                actor_kind="agent",  # not in ('member', 'system')
                action="x.y",
            )


async def test_write_audit_cross_tenant_actor_rejected(db_session, workspace_factory):
    """Composite FK: an actor member from another workspace is rejected (T1)."""
    ws_a = await workspace_factory(name="A", slug="audit-a")
    ws_b = await workspace_factory(name="B", slug="audit-b")
    async with db_session.begin():
        user_b = (
            await db_session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'B') RETURNING id"),
                {"e": "b-audit@corp.com"},
            )
        ).scalar_one()
        member_b = (
            await db_session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role) "
                    "VALUES (:ws, 'human', :u, 'admin') RETURNING id"
                ),
                {"ws": ws_b.id, "u": user_b},
            )
        ).scalar_one()
    with pytest.raises(DBAPIError):
        async with db_session.begin():
            await write_audit(
                db_session,
                workspace_id=ws_a.id,
                actor_member_id=member_b,
                actor_kind="member",
                action="workspace.updated",
            )


async def test_audit_rows_cannot_be_updated_or_deleted(db_session):
    async with db_session.begin():
        await write_audit(
            db_session,
            workspace_id=None,
            actor_member_id=None,
            actor_kind="system",
            action="test.immutable",
        )
    with pytest.raises(DBAPIError):
        async with db_session.begin():
            await db_session.execute(text("UPDATE audit_logs SET action = 'tampered'"))
    with pytest.raises(DBAPIError):
        async with db_session.begin():
            await db_session.execute(text("DELETE FROM audit_logs"))
