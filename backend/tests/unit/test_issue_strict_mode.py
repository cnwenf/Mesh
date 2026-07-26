"""严格模式状态流转测试(issue.md §3.4/§4.4/§5.2,迁移 0009)。

- 默认模式(工作区未开 status_strict_mode):任意状态可切任意状态;
- 严格模式:仅当前状态 allowed_transitions 列出的目标可达;空数组不可转出;
  违规 409 invalid_status_transition(details 带 from/to/allowed);
- allowed_transitions 经 status CRUD 读写,非法条目 400。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.issue import IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import ConflictError, ValidationError
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssuePatch, IssueService
from mesh.issue.statuses import StatusService
from mesh.workspace.service import WorkspacePatch, WorkspaceService

pytestmark = pytest.mark.unit


def _mgr(m: Member) -> bool:
    return m.role in ("owner", "admin")


@pytest.fixture
def issue_service(session_factory):
    return IssueService(session_factory)


@pytest.fixture
def status_service(session_factory):
    return StatusService(session_factory, is_workspace_manager=_mgr)


@pytest.fixture
def workspace_service(session_factory):
    return WorkspaceService(session_factory)


async def _ws(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws = Workspace(name="Strict", slug=f"strict-{uuid.uuid4().hex[:8]}")
        session.add(ws)
    return ws


async def _member(session_factory, ws, *, role="owner"):
    async with session_factory() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:10]}@corp.com", password_hash="x", display_name="S")
        session.add(user)
        await session.flush()
        member = Member(workspace_id=ws.id, member_type="human", user_id=user.id, role=role)
        session.add(member)
    return member


async def _enable_strict(workspace_service, *, actor, ws, enabled: bool = True) -> None:
    from mesh.db.models.workspace import Workspace

    async with workspace_service._factory() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.id == ws.id))
    await workspace_service.update_workspace(
        actor=actor,
        workspace=workspace,
        patch=WorkspacePatch(settings={"status_strict_mode": enabled}),
    )


async def _statuses_by_category(session_factory, ws) -> dict:
    from mesh.issue.statuses import ensure_scope_seeded

    async with session_factory() as session, session.begin():
        await ensure_scope_seeded(session, workspace_id=ws.id)
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(IssueStatus).where(
                        IssueStatus.workspace_id == ws.id,
                        IssueStatus.project_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    return {row.category: row for row in rows}


async def _create_issue(issue_service, *, actor, ws) -> dict:
    return await issue_service.create_issue(
        actor=actor, workspace_id=ws.id, body=CreateIssueRequest(title="strict target")
    )


async def test_default_mode_allows_any_transition(
    session_factory, issue_service, workspace_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    by_cat = await _statuses_by_category(session_factory, ws)
    issue = await _create_issue(issue_service, actor=owner, ws=ws)
    # default mode (strict off): todo → done directly is fine
    updated = await issue_service.update_issue(
        actor=owner,
        workspace_id=ws.id,
        issue_id=uuid.UUID(issue["id"]),
        patch=IssuePatch(status_id=by_cat["done"].id),
    )
    assert updated["state_category"] == "done"


async def test_strict_mode_allows_configured_transition(
    session_factory, issue_service, status_service, workspace_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    by_cat = await _statuses_by_category(session_factory, ws)
    # Todo 的允许下一步配置为 In Progress
    patched = await status_service.update_status(
        actor=owner,
        workspace_id=ws.id,
        status_id=by_cat["todo"].id,
        patch=_status_patch(allowed_transitions=[str(by_cat["in_progress"].id)]),
        is_unset=_unset,
    )
    assert patched["allowed_transitions"] == [str(by_cat["in_progress"].id)]
    await _enable_strict(workspace_service, actor=owner, ws=ws)

    issue = await _create_issue(issue_service, actor=owner, ws=ws)
    updated = await issue_service.update_issue(
        actor=owner,
        workspace_id=ws.id,
        issue_id=uuid.UUID(issue["id"]),
        patch=IssuePatch(status_id=by_cat["in_progress"].id),
    )
    assert updated["state_category"] == "in_progress"


async def test_strict_mode_rejects_unconfigured_transition(
    session_factory, issue_service, status_service, workspace_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    by_cat = await _statuses_by_category(session_factory, ws)
    # Todo 仅允许 → In Progress
    await status_service.update_status(
        actor=owner,
        workspace_id=ws.id,
        status_id=by_cat["todo"].id,
        patch=_status_patch(allowed_transitions=[str(by_cat["in_progress"].id)]),
        is_unset=_unset,
    )
    await _enable_strict(workspace_service, actor=owner, ws=ws)

    issue = await _create_issue(issue_service, actor=owner, ws=ws)
    with pytest.raises(ConflictError) as exc_info:
        await issue_service.update_issue(
            actor=owner,
            workspace_id=ws.id,
            issue_id=uuid.UUID(issue["id"]),
            patch=IssuePatch(status_id=by_cat["done"].id),  # not allowed
        )
    assert exc_info.value.code == "invalid_status_transition"
    details = exc_info.value.details
    assert details["to"] == str(by_cat["done"].id)
    assert str(by_cat["in_progress"].id) in details["allowed"]


async def test_strict_mode_empty_allowed_blocks_all_transitions(
    session_factory, issue_service, workspace_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    by_cat = await _statuses_by_category(session_factory, ws)
    await _enable_strict(workspace_service, actor=owner, ws=ws)
    issue = await _create_issue(issue_service, actor=owner, ws=ws)
    # 种子状态 allowed_transitions 默认为空 → 严格模式下不可转出
    with pytest.raises(ConflictError) as exc_info:
        await issue_service.update_issue(
            actor=owner,
            workspace_id=ws.id,
            issue_id=uuid.UUID(issue["id"]),
            patch=IssuePatch(status_id=by_cat["in_progress"].id),
        )
    assert exc_info.value.code == "invalid_status_transition"
    assert exc_info.value.details["allowed"] == []


async def test_strict_mode_toggle_off_restores_free_flow(
    session_factory, issue_service, workspace_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    by_cat = await _statuses_by_category(session_factory, ws)
    await _enable_strict(workspace_service, actor=owner, ws=ws)
    await _enable_strict(workspace_service, actor=owner, ws=ws, enabled=False)
    issue = await _create_issue(issue_service, actor=owner, ws=ws)
    updated = await issue_service.update_issue(
        actor=owner,
        workspace_id=ws.id,
        issue_id=uuid.UUID(issue["id"]),
        patch=IssuePatch(status_id=by_cat["done"].id),
    )
    assert updated["state_category"] == "done"


async def test_allowed_transitions_crud_and_validation(
    session_factory, status_service, workspace_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    by_cat = await _statuses_by_category(session_factory, ws)
    created = await status_service.create_status(
        actor=owner,
        workspace_id=ws.id,
        name="Configured",
        category="in_review",
        allowed_transitions=[str(by_cat["done"].id)],
    )
    assert created["allowed_transitions"] == [str(by_cat["done"].id)]
    # 非 UUID 条目 → 400
    with pytest.raises(ValidationError):
        await status_service.create_status(
            actor=owner,
            workspace_id=ws.id,
            name="Bad",
            category="todo",
            allowed_transitions=["not-a-uuid"],
        )
    with pytest.raises(ValidationError):
        await status_service.create_status(
            actor=owner,
            workspace_id=ws.id,
            name="Bad2",
            category="todo",
            allowed_transitions={"a": 1},  # not an array
        )
    # 更新清空
    cleared = await status_service.update_status(
        actor=owner,
        workspace_id=ws.id,
        status_id=uuid.UUID(created["id"]),
        patch=_status_patch(allowed_transitions=[]),
        is_unset=_unset,
    )
    assert cleared["allowed_transitions"] == []
    # settings 类型校验
    with pytest.raises(ValidationError):
        await _enable_strict_bad(workspace_service, actor=owner, ws=ws)


async def _enable_strict_bad(workspace_service, *, actor, ws) -> None:
    from mesh.db.models.workspace import Workspace

    async with workspace_service._factory() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.id == ws.id))
    await workspace_service.update_workspace(
        actor=actor, workspace=workspace, patch=WorkspacePatch(settings={"status_strict_mode": "yes"})
    )


def _status_patch(**kwargs):
    from mesh.issue.statuses import StatusPatch

    base = {
        "name": None,
        "color": None,
        "position": None,
        "category": None,
        "is_default": None,
        "allowed_transitions": None,
    }
    base.update(kwargs)
    return StatusPatch(**base)


def _unset(v):
    return v is None
