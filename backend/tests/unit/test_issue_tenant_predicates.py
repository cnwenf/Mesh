"""多租查询谓词统一回归(MES-51 L1/L2,README §6.2 rule 5/6)。

复合 FK 已保证 issues → statuses/projects/milestones/cycles 行级同租,
RLS 在 app 连接上兜底;本文件覆盖**签名允许传入异租 id** 的路径:补
workspace_id 谓词后,owner 回退形态(无 RLS)下异租对象也一律查不到
(分组标签回退 key、严格模式视当前状态为空),不产生跨租读取。
"""

from __future__ import annotations

import uuid

import pytest

from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import ConflictError
from mesh.issue.service import IssueService
from mesh.project.schemas import CreateProjectRequest
from mesh.project.service import ProjectService

pytestmark = pytest.mark.unit


@pytest.fixture
def issue_service(session_factory) -> IssueService:
    return IssueService(session_factory)


async def _make_workspace(session_factory, slug: str):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name=f"WS {slug}", slug=slug)
        session.add(workspace)
    return workspace


async def _make_admin(session_factory, workspace) -> Member:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Admin"
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role="admin"
        )
        session.add(member)
    return member


async def test_group_label_does_not_leak_cross_tenant_project_name(
    session_factory, issue_service
):
    # Arrange: ws_b 里有一个叫 Secret 的项目;以 ws_a 为租户锚解析它的 id。
    ws_a = await _make_workspace(session_factory, f"a-{uuid.uuid4().hex[:8]}")
    ws_b = await _make_workspace(session_factory, f"b-{uuid.uuid4().hex[:8]}")
    actor = await _make_admin(session_factory, ws_b)
    project_b = await ProjectService(session_factory).create_project(
        actor=actor,
        workspace_id=ws_b.id,
        body=CreateProjectRequest(name="Secret", key="SEC"),
    )

    # Act
    async with session_factory() as session:
        label = await issue_service._group_label(session, project_b["id"], "project", ws_a.id)

    # Assert: 查不到 → 原样回退 key,不回显异租项目名
    assert label == project_b["id"]


async def test_group_label_does_not_leak_cross_tenant_cycle_name(
    session_factory, issue_service
):
    from datetime import date

    from mesh.db.models.project import Cycle

    ws_a = await _make_workspace(session_factory, f"a-{uuid.uuid4().hex[:8]}")
    ws_b = await _make_workspace(session_factory, f"b-{uuid.uuid4().hex[:8]}")
    async with session_factory() as session, session.begin():
        cycle = Cycle(
            workspace_id=ws_b.id,
            name="Sprint B",
            starts_at=date(2026, 7, 1),
            ends_at=date(2026, 7, 14),
        )
        session.add(cycle)

    async with session_factory() as session:
        label = await issue_service._group_label(session, str(cycle.id), "cycle", ws_a.id)

    assert label == str(cycle.id)


async def test_strict_mode_ignores_cross_tenant_current_status(session_factory, issue_service):
    """严格模式下 current_status_id 指向异租状态:补谓词后视为不存在,
    allowed 为空 → 409;旧实现会读取异租状态的 allowed_transitions 放行。"""
    from mesh.db.models.issue import IssueStatus
    from mesh.db.models.workspace import Workspace

    ws_a = await _make_workspace(session_factory, f"a-{uuid.uuid4().hex[:8]}")
    ws_b = await _make_workspace(session_factory, f"b-{uuid.uuid4().hex[:8]}")
    async with session_factory() as session, session.begin():
        ws_row = await session.get(Workspace, ws_a.id)
        ws_row.settings = {**(ws_row.settings or {}), "status_strict_mode": True}
        target = IssueStatus(
            workspace_id=ws_a.id, name="Target", category="todo", position=1.0
        )
        session.add(target)
        await session.flush()
        foreign = IssueStatus(
            workspace_id=ws_b.id,
            name="Foreign",
            category="todo",
            position=0.0,
            # 旧实现会读到这份异租放行清单 → 不抛异常(测试失败)
            allowed_transitions=[str(target.id)],
        )
        session.add(foreign)

    async with session_factory() as session:
        with pytest.raises(ConflictError) as exc_info:
            await issue_service._assert_transition_allowed(
                session,
                workspace_id=ws_a.id,
                current_status_id=foreign.id,
                target_status=target,
            )
    assert exc_info.value.code == "invalid_status_transition"
    assert exc_info.value.details["allowed"] == []


async def test_visibility_subqueries_carry_workspace_predicate(session_factory, issue_service):
    """L2:可见性 OR 子查询锚定 workspace_id(收敛跨租全表扫描面)。"""
    ws = await _make_workspace(session_factory, f"v-{uuid.uuid4().hex[:8]}")
    async with session_factory() as session, session.begin():
        user_a = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Viewer"
        )
        user_b = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Guest"
        )
        session.add_all([user_a, user_b])
        await session.flush()
        member = Member(
            workspace_id=ws.id, member_type="human", user_id=user_a.id, role="member"
        )
        guest = Member(
            workspace_id=ws.id, member_type="human", user_id=user_b.id, role="guest"
        )
        session.add_all([member, guest])

    member_clause = str(issue_service._base_visibility_clause(member, ws.id))
    assert "project_members.workspace_id" in member_clause
    assert "projects.workspace_id" in member_clause

    guest_clause = str(issue_service._base_visibility_clause(guest, ws.id))
    assert "member_project_access.workspace_id" in guest_clause
