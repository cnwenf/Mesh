"""Unified execution-visibility scope (analytics.md §2.3.1, R4/R5, T33)."""

from __future__ import annotations

import pytest

from mesh.analytics.visibility import (
    VISIBLE_EXECUTIONS_CTE,
    analytics_exec_visible_to,
    compute_exec_scope_key,
    compute_issue_scope_key,
    is_workspace_manager,
    requester_cte_params,
    visible_agent_ids,
    visible_executions_cte,
    visible_project_ids,
)
from tests.unit.analytics_support import seed_world

pytestmark = pytest.mark.unit


async def test_visible_projects_plain_member_excludes_private(
    session_factory, workspace_factory, member_factory
):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        ids = await visible_project_ids(session, workspace_id=world.ws.id, member=world.m1)
        assert ids == [world.pub.id]  # priv 不可见


async def test_visible_projects_project_member_includes_private(
    session_factory, workspace_factory, member_factory
):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        ids = await visible_project_ids(session, workspace_id=world.ws.id, member=world.m2)
        assert set(ids) == {world.pub.id, world.priv.id}


async def test_visible_projects_admin_is_full_workspace(
    session_factory, workspace_factory, member_factory
):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        assert await visible_project_ids(session, workspace_id=world.ws.id, member=world.admin) is None
        assert is_workspace_manager(world.admin)
        assert not is_workspace_manager(world.m1)


async def test_visible_agents_private_only_owner_and_admin(
    session_factory, workspace_factory, member_factory
):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        m1_agents = await visible_agent_ids(session, workspace_id=world.ws.id, member=world.m1)
        assert world.wa.id in m1_agents
        assert world.pa.id not in m1_agents
        m3_agents = await visible_agent_ids(session, workspace_id=world.ws.id, member=world.m3)
        assert world.pa.id in m3_agents
        assert (
            await visible_agent_ids(session, workspace_id=world.ws.id, member=world.admin)
        ) is None


async def test_exec_predicate_private_project_issue_execution(
    session_factory, workspace_factory, member_factory
):
    """wa 执行挂在 private 项目 issue 上:m1 剔除、m2 可见、admin 可见。"""
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        assert not await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_priv.id, member_id=world.m1.id,
            workspace_id=world.ws.id)
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_priv.id, member_id=world.m2.id,
            workspace_id=world.ws.id)
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_priv.id, member_id=world.admin.id,
            workspace_id=world.ws.id)


async def test_exec_predicate_public_project_issue_execution_visible_to_all(
    session_factory, workspace_factory, member_factory
):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_pub.id, member_id=world.m1.id,
            workspace_id=world.ws.id)


async def test_exec_predicate_no_issue_execution_belongs_to_agent(
    session_factory, workspace_factory, member_factory
):
    """无 issue 执行(manual)归属 agent:workspace agent 全员可见,private 仅 owner/admin。"""
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_queued.id, member_id=world.m1.id,
            workspace_id=world.ws.id)
        assert not await analytics_exec_visible_to(
            session, execution_id=world.exec_pa_manual.id, member_id=world.m1.id,
            workspace_id=world.ws.id)
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_pa_manual.id, member_id=world.m3.id,
            workspace_id=world.ws.id)
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_pa_manual.id, member_id=world.admin.id,
            workspace_id=world.ws.id)


async def test_exec_predicate_unknown_member(session_factory, workspace_factory, member_factory):
    import uuid

    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        assert not await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_pub.id, member_id=uuid.uuid4(),
            workspace_id=world.ws.id)


async def test_scope_keys_differ_across_permissions(
    session_factory, workspace_factory, member_factory
):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        admin_issue = await compute_issue_scope_key(
            session, workspace_id=world.ws.id, member=world.admin)
        m1_issue = await compute_issue_scope_key(
            session, workspace_id=world.ws.id, member=world.m1)
        m2_issue = await compute_issue_scope_key(
            session, workspace_id=world.ws.id, member=world.m2)
        assert admin_issue == "ws_admin"
        assert m1_issue.startswith("projects:")
        assert m2_issue.startswith("projects:")
        assert m1_issue != m2_issue  # 可见集不同 → 键不同

        admin_exec = await compute_exec_scope_key(
            session, workspace_id=world.ws.id, member=world.admin)
        m1_exec = await compute_exec_scope_key(
            session, workspace_id=world.ws.id, member=world.m1)
        m3_exec = await compute_exec_scope_key(
            session, workspace_id=world.ws.id, member=world.m3)
        assert admin_exec == "ws_admin"
        assert m1_exec.startswith("exec:p") and ":a" in m1_exec
        assert m3_exec.startswith("exec:p") and m3_exec != m1_exec  # 可见 agent 集不同


def test_cte_is_single_authoritative_source():
    cte = visible_executions_cte()
    assert cte is VISIBLE_EXECUTIONS_CTE
    assert "visible_executions AS" in cte
    # ① agent 可见性先行
    assert "a.visibility = 'workspace'" in cte
    assert "a.visibility = 'private'" in cte
    # ② 项目可见性继承(两路:project_members + member_project_access)
    assert "p.visibility = 'public'" in cte
    assert cte.count("FROM project_members pm") == 1
    assert cte.count("FROM member_project_access mx") == 1
    # 三个请求者参数具名绑定
    assert ":requester_member_id" in cte
    assert ":requester_user_id" in cte
    assert ":requester_role" in cte


def test_requester_cte_params():
    class _Member:
        id = "mid"
        user_id = "uid"
        role = "member"

    params = requester_cte_params(_Member())
    assert params == {
        "requester_member_id": "mid",
        "requester_user_id": "uid",
        "requester_role": "member",
    }
