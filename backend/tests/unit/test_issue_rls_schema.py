"""issue 五表 RLS 启用断言(MES-51 L4,README §6.2 rule 5)。

与 test_models_schema.py(realtime)、test_workspace_schema.py(workspace
域)的 rowsecurity 断言同层:issue 五表策略由迁移 0009 同模板生成,
此处补齐覆盖——RLS 已启用、每表一条 mesh_<table>_tenant 租户策略、
策略表达式锚定 mesh.workspace_id GUC。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.unit

ISSUE_TENANT_TABLES = (
    "issues",
    "issue_statuses",
    "issue_dependencies",
    "issue_activity",
    "issue_templates",
)


async def test_issue_tables_rls_enabled(db_session):
    rls = (
        await db_session.execute(
            text(
                "SELECT tablename, rowsecurity FROM pg_tables WHERE tablename IN "
                "('issues', 'issue_statuses', 'issue_dependencies', 'issue_activity', "
                "'issue_templates')"
            )
        )
    ).all()
    assert {table for table, enabled in rls if enabled} == set(ISSUE_TENANT_TABLES)


async def test_issue_tables_have_tenant_policies_on_workspace_guc(db_session):
    policies = (
        await db_session.execute(
            text("SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy")
        )
    ).all()
    expected = {f"mesh_{table}_tenant" for table in ISSUE_TENANT_TABLES}
    quals = {name: qual for name, qual in policies if name in expected}
    assert set(quals) == expected
    assert all("mesh.workspace_id" in (qual or "") for qual in quals.values())
