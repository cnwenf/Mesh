"""验收第二轮回归(打回项 B3 + 必修 1/2/4 + F5):

- B3:`sort=due_date` 分页对 NULL due_date 安全(NULL 两个方向均排末尾,不 500);
- 必修1:批量 changes 畸形 UUID → 逐条 422(非 500 毒化整事务);
- 必修2:`list_children` 游标与排序一致(翻页无跳行/重复);
- 必修4:`BulkRequest.issue_ids` 上限 100;
- F5:assignee/status 变更后事件载荷携带渲染快照(显示名免 refetch)。
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.errors import BusinessRuleError
from mesh.issue.bulk import BulkService
from mesh.issue.move import MoveService
from mesh.issue.schemas import BulkChanges, BulkRequest, CreateIssueRequest
from mesh.issue.service import IssuePatch, IssueService
from mesh.project.service import ProjectService

pytestmark = pytest.mark.unit


def _mgr(member: Member) -> bool:
    return member.role in ("owner", "admin")


@pytest.fixture
def issue_service(session_factory):
    return IssueService(session_factory)


@pytest.fixture
def project_service(session_factory):
    return ProjectService(session_factory)


@pytest.fixture
def bulk_service(issue_service, move_service):
    return BulkService(issue_service, move_service)


@pytest.fixture
def move_service(issue_service):
    return MoveService(issue_service)


async def _ws(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws = Workspace(name="R2", slug=f"r2-{uuid.uuid4().hex[:10]}")
        session.add(ws)
    return ws


async def _member(session_factory, ws, *, role="owner"):
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:10]}@corp.com", password_hash="x", display_name="R2"
        )
        session.add(user)
        await session.flush()
        member = Member(workspace_id=ws.id, member_type="human", user_id=user.id, role=role)
        session.add(member)
    return member


async def _issue(issue_service, *, actor, ws, **fields):
    fields.setdefault("title", "t")
    return await issue_service.create_issue(
        actor=actor, workspace_id=ws.id, body=CreateIssueRequest(**fields)
    )


# ---------------------------------------------------------------------------
# B3 — sort=due_date with NULL due dates
# ---------------------------------------------------------------------------


async def test_sort_due_date_null_safe_both_directions(session_factory, issue_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    with_due = await _issue(issue_service, actor=owner, ws=ws, due_date="2026-08-10")
    no_due = await _issue(issue_service, actor=owner, ws=ws)  # NULL due_date

    # desc: dated first, NULL last; paged across the boundary without error
    p1 = await issue_service.list_issues(
        viewer=owner, workspace_id=ws.id, sort="due_date", order="desc", limit=1
    )
    assert [i["id"] for i in p1["data"]] == [with_due["id"]]
    assert p1["next_cursor"]
    p2 = await issue_service.list_issues(
        viewer=owner,
        workspace_id=ws.id,
        sort="due_date",
        order="desc",
        limit=1,
        cursor=p1["next_cursor"],
    )
    assert [i["id"] for i in p2["data"]] == [no_due["id"]]

    # asc: dated first, NULL last as well
    a1 = await issue_service.list_issues(
        viewer=owner, workspace_id=ws.id, sort="due_date", order="asc", limit=1
    )
    assert [i["id"] for i in a1["data"]] == [with_due["id"]]
    a2 = await issue_service.list_issues(
        viewer=owner,
        workspace_id=ws.id,
        sort="due_date",
        order="asc",
        limit=1,
        cursor=a1["next_cursor"],
    )
    assert [i["id"] for i in a2["data"]] == [no_due["id"]]

    # all-NULL page boundary (cursor value is the sentinel)
    await _issue(issue_service, actor=owner, ws=ws)  # second NULL-due issue
    q1 = await issue_service.list_issues(
        viewer=owner, workspace_id=ws.id, sort="due_date", order="desc", limit=2
    )
    assert q1["next_cursor"]
    q2 = await issue_service.list_issues(
        viewer=owner,
        workspace_id=ws.id,
        sort="due_date",
        order="desc",
        limit=2,
        cursor=q1["next_cursor"],
    )
    assert len(q2["data"]) == 1  # the remaining NULL-due issue


# ---------------------------------------------------------------------------
# 必修1 — bulk malformed UUID → per-item 422, not 500
# ---------------------------------------------------------------------------


async def test_bulk_malformed_uuid_per_item_validation(session_factory, issue_service, bulk_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    issue = await _issue(issue_service, actor=owner, ws=ws)
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner,
            workspace_id=ws.id,
            body=BulkRequest(
                issue_ids=[issue["id"]], changes=BulkChanges(status_id="not-a-uuid")
            ),
        )
    assert exc_info.value.code == "bulk_partial_failure"
    errors = exc_info.value.details["errors"]
    assert errors[0]["issue_id"] == issue["id"]
    assert errors[0]["code"] == "validation_error"
    # malformed assignee / cycle ids → per-item 422 validation_error
    for field, value in (("assignee_id", "garbage"), ("cycle_id", "garbage")):
        with pytest.raises(BusinessRuleError) as exc:
            await bulk_service.execute(
                actor=owner,
                workspace_id=ws.id,
                body=BulkRequest(issue_ids=[issue["id"]], changes=BulkChanges(**{field: value})),
            )
        assert exc.value.details["errors"][0]["code"] == "validation_error"
    # malformed project id fails at request level (preview branch, before items)
    from mesh.errors import ValidationError

    with pytest.raises(ValidationError):
        await bulk_service.execute(
            actor=owner,
            workspace_id=ws.id,
            body=BulkRequest(
                issue_ids=[issue["id"]], changes=BulkChanges(project_id="garbage")
            ),
        )


# ---------------------------------------------------------------------------
# 必修2 — list_children cursor consistent with ordering
# ---------------------------------------------------------------------------


async def test_list_children_pagination_no_skip_no_dup(session_factory, issue_service):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    parent = await _issue(issue_service, actor=owner, ws=ws)
    ids = []
    for _ in range(5):
        child = await _issue(issue_service, actor=owner, ws=ws, parent_id=parent["id"])
        ids.append(child["id"])

    seen: list[str] = []
    cursor = None
    for _ in range(5):  # safety bound
        page, cursor = await issue_service.list_children(
            viewer=owner, workspace_id=ws.id, issue_id=uuid.UUID(parent["id"]),
            limit=2, cursor=cursor,
        )
        seen.extend(item["id"] for item in page)
        if cursor is None:
            break
    assert sorted(seen) == sorted(ids)  # no skips, no duplicates
    assert len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# M7 后端:依赖列表携带对端标识符
# ---------------------------------------------------------------------------


async def test_list_dependencies_carries_identifiers(session_factory, issue_service):
    from mesh.issue.dependencies import DependencyService

    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    a = await _issue(issue_service, actor=owner, ws=ws)
    b = await _issue(issue_service, actor=owner, ws=ws)
    deps = DependencyService(issue_service)
    await deps.add_dependency(
        actor=owner,
        workspace_id=ws.id,
        issue_id=uuid.UUID(a["id"]),
        depends_on_id=uuid.UUID(b["id"]),
        dep_type="blocked_by",
    )
    listing = await deps.list_dependencies(
        viewer=owner, workspace_id=ws.id, issue_id=uuid.UUID(a["id"])
    )
    assert len(listing) == 1
    assert listing[0]["depends_on_identifier"] == b["identifier"]


# ---------------------------------------------------------------------------
# 必修4 — bulk issue_ids capped
# ---------------------------------------------------------------------------


def test_bulk_issue_ids_capped_at_100():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        BulkRequest(issue_ids=[str(uuid.uuid4()) for _ in range(101)])
    # at the cap is fine
    BulkRequest(issue_ids=[str(uuid.uuid4()) for _ in range(100)])


# ---------------------------------------------------------------------------
# F5 — assignee/status snapshots in the updated event payload
# ---------------------------------------------------------------------------


async def test_updated_event_carries_assignee_and_status_snapshots(
    session_factory, issue_service
):
    ws = await _ws(session_factory)
    owner = await _member(session_factory, ws)
    assignee = await _member(session_factory, ws)
    issue = await _issue(issue_service, actor=owner, ws=ws)

    from mesh.db.models.issue import IssueStatus

    async with session_factory() as session:
        done = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.workspace_id == ws.id, IssueStatus.category == "done"
            )
        )

    await issue_service.update_issue(
        actor=owner,
        workspace_id=ws.id,
        issue_id=uuid.UUID(issue["id"]),
        patch=IssuePatch(assignee_id=assignee.id, status_id=done.id),
    )
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.workspace_id == ws.id)
                )
            )
            .scalars()
            .all()
        )
    updated_payloads = [
        e.payload
        for e in events
        if e.event_type == "realtime.publish" and e.payload.get("event") == "issue.updated"
    ]
    assert updated_payloads
    changes = updated_payloads[-1]["data"]["changes"]
    assert changes["assignee"]["id"] == str(assignee.id)
    assert changes["assignee"]["member_type"] == "human"
    assert changes["status"]["id"] == str(done.id)
    assert changes["status"]["category"] == "done"
    # snapshots are JSON-safe (datetimes iso-encoded)
    json.dumps(changes)
