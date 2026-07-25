"""Multi-tenant building blocks (§6.2)."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from mesh.db.models.realtime import RealtimeEvent
from mesh.db.tenant import (
    GLOBAL_TABLES,
    TENANT_GUC,
    composite_fk_constraint,
    composite_fk_sql,
    set_tenant_context,
    tenant_scope,
    unique_workspace_id_sql,
)


def test_global_tables_exemption_list():
    # §6.1/§6.2-5: global identity tables carry no workspace_id ownership.
    assert GLOBAL_TABLES == frozenset({"users", "external_identities"})


def test_unique_workspace_id_migration_template():
    assert (
        unique_workspace_id_sql("issues")
        == "CREATE UNIQUE INDEX uq_issues_ws_id ON issues (workspace_id, id)"
    )


def test_composite_fk_migration_template():
    sql = composite_fk_sql("issues", "members", "assignee_id")
    assert "FOREIGN KEY (workspace_id, assignee_id)" in sql
    assert "REFERENCES members (workspace_id, id)" in sql


def test_composite_fk_orm_template():
    constraint = composite_fk_constraint("projects", "project_id")
    targets = [element.target_fullname for element in constraint.elements]
    assert targets == ["projects.workspace_id", "projects.id"]


def test_tenant_scope_appends_workspace_filter():
    workspace_id = uuid.uuid4()
    stmt = tenant_scope(select(RealtimeEvent), RealtimeEvent, workspace_id)
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "workspace_id" in str(compiled)
    assert workspace_id in compiled.params.values()


async def test_set_tenant_context_sets_transaction_guc(db_session):
    workspace_id = uuid.uuid4()
    async with db_session.begin():
        await set_tenant_context(db_session, workspace_id)
        value = (
            await db_session.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
        ).scalar()
        assert value == str(workspace_id)
