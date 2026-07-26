"""Move/bulk authorization security regressions (MES-46 H1/H2, issue.md §3.8).

The unconfirmed (``confirm: false``) move path and the bulk preview loop used
to compute the §3.8 plan WITHOUT any source/target authorization, letting any
workspace member (guests included) read arbitrary issues' field manifests via
the 422 ``move_confirmation_required`` envelope and enumerate private
projects. These tests pin the fixed negative matrix:

- private source issue: non-member member move (confirm=false) → 403, NO plan;
  guest → 404, NO plan;
- move-preview endpoint keeps the same matrix (existing correct behavior);
- invisible private target: member → 403, guest → 404 (confirm=false too);
- target in another workspace → 404;
- bulk unconfirmed with mixed ids → error markers ONLY for unauthorized items,
  never a plan;
- authorized users' positive flows unchanged;
- §3.8 contract hygiene: preview carries ``version``, confirmed move requires
  it, bulk moves leave an audit trail with the mapping/clearing manifest and
  sync ``completed_at`` exactly like the explicit move.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from mesh.db.models.member import Member
from mesh.errors import (
    BusinessRuleError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.issue.bulk import BulkService
from mesh.issue.move import MoveService
from mesh.issue.schemas import BulkChanges, BulkRequest, CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import StatusService
from mesh.member.service import MemberService
from mesh.project.schemas import AddProjectMemberRequest, CreateProjectRequest
from mesh.project.service import ProjectService

pytestmark = pytest.mark.unit


def _is_manager(member: Member) -> bool:
    return member.role in ("owner", "admin")


@pytest.fixture
def issue_service(session_factory) -> IssueService:
    return IssueService(session_factory)


@pytest.fixture
def project_service(session_factory) -> ProjectService:
    return ProjectService(session_factory)


@pytest.fixture
def member_service(session_factory) -> MemberService:
    return MemberService(session_factory)


@pytest.fixture
def status_service(session_factory) -> StatusService:
    return StatusService(session_factory, is_workspace_manager=_is_manager)


@pytest.fixture
def move_service(issue_service) -> MoveService:
    return MoveService(issue_service)


@pytest.fixture
def bulk_service(issue_service, move_service) -> BulkService:
    return BulkService(issue_service, move_service)


async def _make_workspace(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Sec WS", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _make_member(session_factory, workspace, *, role="member") -> Member:
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com", password_hash="x", display_name="Sec"
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role=role
        )
        session.add(member)
    return member


async def _make_project(
    project_service, *, actor, workspace, key=None, visibility="public"
) -> dict:
    return await project_service.create_project(
        actor=actor,
        workspace_id=workspace.id,
        body=CreateProjectRequest(
            name=f"Project {uuid.uuid4().hex[:6]}",
            key=key or f"K{uuid.uuid4().hex[:4].upper()}",
            visibility=visibility,
        ),
    )


async def _make_issue(issue_service, *, actor, workspace, title="t", **kwargs) -> dict:
    return await issue_service.create_issue(
        actor=actor,
        workspace_id=workspace.id,
        body=CreateIssueRequest(title=title, **kwargs),
    )


async def _join_project(project_service, *, actor, workspace, project, member) -> None:
    await project_service.add_project_member(
        actor=actor,
        workspace_id=workspace.id,
        project_id=uuid.UUID(project["id"]),
        body=AddProjectMemberRequest(member_id=str(member.id), role="member"),
    )


async def _grant(
    member_service, *, actor, workspace, member, project, permission="read"
) -> None:
    await member_service.grant_project_access(
        actor=actor,
        workspace_id=workspace.id,
        member_id=member.id,
        project_id=uuid.UUID(project["id"]),
        permission=permission,
    )


def _assert_no_plan(exc: BusinessRuleError | ForbiddenError | NotFoundError) -> None:
    """The error envelope must not smuggle a preview plan (the leak surface)."""
    details = getattr(exc, "details", None) or {}
    assert "preview" not in details
    assert "previews" not in details


# ---------------------------------------------------------------------------
# H1: move unconfirmed path must authorize source + target BEFORE the plan
# ---------------------------------------------------------------------------


async def _private_source_setup(
    session_factory, issue_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    outsider = await _make_member(session_factory, workspace, role="member")
    guest = await _make_member(session_factory, workspace, role="guest")
    source = await _make_project(
        project_service, actor=owner, workspace=workspace, key="PRV", visibility="private"
    )
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="secret", project_id=source["id"]
    )
    return workspace, owner, outsider, guest, source, issue


@pytest.mark.unit
async def test_move_unconfirmed_private_source_member_forbidden_no_plan(
    session_factory, issue_service, move_service, project_service
):
    workspace, _owner, outsider, _guest, _source, issue = await _private_source_setup(
        session_factory, issue_service, project_service
    )
    with pytest.raises(ForbiddenError) as exc_info:
        await move_service.move(
            actor=outsider,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=None,
            confirm=False,
        )
    _assert_no_plan(exc_info.value)


@pytest.mark.unit
async def test_move_unconfirmed_private_source_guest_not_found_no_plan(
    session_factory, issue_service, move_service, project_service
):
    workspace, _owner, _outsider, guest, _source, issue = await _private_source_setup(
        session_factory, issue_service, project_service
    )
    with pytest.raises(NotFoundError) as exc_info:
        await move_service.move(
            actor=guest,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=None,
            confirm=False,
        )
    _assert_no_plan(exc_info.value)


@pytest.mark.unit
async def test_move_unconfirmed_invisible_source_leaks_no_target_existence_guest(
    session_factory, issue_service, move_service, project_service
):
    """The source read gate fires BEFORE target resolution: a guest holding
    an invisible issue UUID gets a message-identical 404 whether the swept
    target_project_id exists or not — no project-existence oracle."""
    workspace, owner, _outsider, guest, _source, issue = await _private_source_setup(
        session_factory, issue_service, project_service
    )
    real_target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="REAL", visibility="private"
    )
    errors = []
    for target in (uuid.UUID(real_target["id"]), uuid.uuid4()):
        with pytest.raises(NotFoundError) as exc_info:
            await move_service.move(
                actor=guest,
                workspace_id=workspace.id,
                issue_id=uuid.UUID(issue["id"]),
                target_project_id=target,
                confirm=False,
            )
        _assert_no_plan(exc_info.value)
        errors.append(exc_info.value.message)
    assert errors[0] == errors[1]


@pytest.mark.unit
async def test_move_unconfirmed_invisible_source_leaks_no_target_existence_member(
    session_factory, issue_service, move_service, project_service
):
    """Same guarantee for members: 403 'project is private' regardless of
    whether the swept target exists."""
    workspace, owner, outsider, _guest, _source, issue = await _private_source_setup(
        session_factory, issue_service, project_service
    )
    real_target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="RAL2", visibility="private"
    )
    errors = []
    for target in (uuid.UUID(real_target["id"]), uuid.uuid4()):
        with pytest.raises(ForbiddenError) as exc_info:
            await move_service.move(
                actor=outsider,
                workspace_id=workspace.id,
                issue_id=uuid.UUID(issue["id"]),
                target_project_id=target,
                confirm=False,
            )
        _assert_no_plan(exc_info.value)
        errors.append(exc_info.value.message)
    assert errors[0] == errors[1]


@pytest.mark.unit
async def test_move_preview_private_source_member_forbidden(
    session_factory, issue_service, move_service, project_service
):
    workspace, _owner, outsider, _guest, _source, issue = await _private_source_setup(
        session_factory, issue_service, project_service
    )
    with pytest.raises(ForbiddenError):
        await move_service.preview(
            viewer=outsider,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=None,
        )


@pytest.mark.unit
async def test_move_preview_private_source_guest_not_found(
    session_factory, issue_service, move_service, project_service
):
    workspace, _owner, _outsider, guest, _source, issue = await _private_source_setup(
        session_factory, issue_service, project_service
    )
    with pytest.raises(NotFoundError):
        await move_service.preview(
            viewer=guest,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=None,
        )


@pytest.mark.unit
async def test_move_unconfirmed_invisible_target_member_forbidden(
    session_factory, issue_service, move_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    member = await _make_member(session_factory, workspace, role="member")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="PUB")
    private_target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="HID", visibility="private"
    )
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    with pytest.raises(ForbiddenError) as exc_info:
        await move_service.move(
            actor=member,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(private_target["id"]),
            confirm=False,
        )
    _assert_no_plan(exc_info.value)


@pytest.mark.unit
async def test_move_unconfirmed_invisible_target_guest_not_found(
    session_factory, issue_service, move_service, project_service, member_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    guest = await _make_member(session_factory, workspace, role="guest")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="GSRC")
    private_target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="GHID", visibility="private"
    )
    # Guest can see the source issue via an explicit read grant…
    await _grant(
        member_service, actor=owner, workspace=workspace, member=guest, project=source
    )
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    # …but the target project is invisible to them → 404, not 403 (no oracle).
    with pytest.raises(NotFoundError) as exc_info:
        await move_service.move(
            actor=guest,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(private_target["id"]),
            confirm=False,
        )
    _assert_no_plan(exc_info.value)


@pytest.mark.unit
async def test_move_unconfirmed_target_other_workspace_not_found(
    session_factory, issue_service, move_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="OWN")
    # Issue created while this is the ONLY workspace in the database: the
    # default-status fallback query relies on RLS for tenant filtering and
    # the unit session connects as the superuser (which bypasses RLS).
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    other_ws = await _make_workspace(session_factory)
    other_owner = await _make_member(session_factory, other_ws, role="owner")
    foreign = await _make_project(
        project_service, actor=other_owner, workspace=other_ws, key="FOR"
    )
    with pytest.raises(NotFoundError):
        await move_service.move(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(foreign["id"]),
            confirm=False,
        )


@pytest.mark.unit
async def test_move_preview_target_other_workspace_not_found(
    session_factory, issue_service, move_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="HM")
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    other_ws = await _make_workspace(session_factory)
    other_owner = await _make_member(session_factory, other_ws, role="owner")
    foreign = await _make_project(project_service, actor=other_owner, workspace=other_ws, key="FX")
    with pytest.raises(NotFoundError):
        await move_service.preview(
            viewer=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(foreign["id"]),
        )


@pytest.mark.unit
async def test_move_unconfirmed_authorized_owner_still_gets_preview(
    session_factory, issue_service, move_service, project_service
):
    """Over-blocking guard: the authorized unconfirmed path keeps its 422+plan."""
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="SA")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="TA")
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    with pytest.raises(BusinessRuleError) as exc_info:
        await move_service.move(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(target["id"]),
            confirm=False,
        )
    assert exc_info.value.code == "move_confirmation_required"
    assert exc_info.value.details["preview"]["issue_id"] == issue["id"]


# ---------------------------------------------------------------------------
# H2: bulk unconfirmed preview must authorize each source issue
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_bulk_unconfirmed_mixed_ids_forbidden_marker_not_plan(
    session_factory, issue_service, bulk_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    member = await _make_member(session_factory, workspace, role="member")
    mine = await _make_project(project_service, actor=owner, workspace=workspace, key="MINE")
    theirs = await _make_project(
        project_service, actor=owner, workspace=workspace, key="THRS", visibility="private"
    )
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="DST")
    my_issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=mine["id"]
    )
    secret_issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="secret", project_id=theirs["id"]
    )
    # the actor may write their own project and the destination — but is NOT
    # a member of the private "theirs" project
    await _join_project(
        project_service, actor=owner, workspace=workspace, project=mine, member=member
    )
    await _join_project(
        project_service, actor=owner, workspace=workspace, project=target, member=member
    )
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=member,
            workspace_id=workspace.id,
            body=BulkRequest(
                issue_ids=[my_issue["id"], secret_issue["id"]],
                changes=BulkChanges(project_id=target["id"]),
            ),
        )
    assert exc_info.value.code == "move_confirmation_required"
    previews = exc_info.value.details["previews"]
    by_id = {p["issue_id"]: p for p in previews}
    # authorized item → plan; unauthorized → marker ONLY (no plan fields)
    assert "mapped_fields" in by_id[my_issue["id"]]
    marker = by_id[secret_issue["id"]]
    assert marker["error"] == "forbidden"
    assert "mapped_fields" not in marker and "identifier" not in marker


@pytest.mark.unit
async def test_bulk_unconfirmed_private_issue_guest_not_found_marker(
    session_factory, issue_service, bulk_service, project_service, member_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    guest = await _make_member(session_factory, workspace, role="guest")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="BSRC")
    theirs = await _make_project(
        project_service, actor=owner, workspace=workspace, key="BPRV", visibility="private"
    )
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="BDST")
    # guest: read on source (sees own card), write on target (allowed destination)
    await _grant(
        member_service, actor=owner, workspace=workspace, member=guest, project=source
    )
    await _grant(
        member_service,
        actor=owner,
        workspace=workspace,
        member=guest,
        project=target,
        permission="write",
    )
    visible = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    secret = await _make_issue(
        issue_service, actor=owner, workspace=workspace, title="secret", project_id=theirs["id"]
    )
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=guest,
            workspace_id=workspace.id,
            body=BulkRequest(
                issue_ids=[visible["id"], secret["id"]],
                changes=BulkChanges(project_id=target["id"]),
            ),
        )
    previews = exc_info.value.details["previews"]
    by_id = {p["issue_id"]: p for p in previews}
    assert "mapped_fields" in by_id[visible["id"]]
    assert by_id[secret["id"]]["error"] == "not_found"
    assert "identifier" not in by_id[secret["id"]]


@pytest.mark.unit
async def test_bulk_unconfirmed_invisible_target_member_forbidden(
    session_factory, issue_service, bulk_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    member = await _make_member(session_factory, workspace, role="member")
    private_target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="BTGT", visibility="private"
    )
    issue = await _make_issue(issue_service, actor=owner, workspace=workspace)
    with pytest.raises(ForbiddenError) as exc_info:
        await bulk_service.execute(
            actor=member,
            workspace_id=workspace.id,
            body=BulkRequest(
                issue_ids=[issue["id"]],
                changes=BulkChanges(project_id=private_target["id"]),
            ),
        )
    _assert_no_plan(exc_info.value)


@pytest.mark.unit
async def test_bulk_unconfirmed_invisible_target_guest_not_found(
    session_factory, issue_service, bulk_service, project_service, member_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    guest = await _make_member(session_factory, workspace, role="guest")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="BGS")
    private_target = await _make_project(
        project_service, actor=owner, workspace=workspace, key="BGH", visibility="private"
    )
    await _grant(
        member_service, actor=owner, workspace=workspace, member=guest, project=source
    )
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    with pytest.raises(NotFoundError) as exc_info:
        await bulk_service.execute(
            actor=guest,
            workspace_id=workspace.id,
            body=BulkRequest(
                issue_ids=[issue["id"]],
                changes=BulkChanges(project_id=private_target["id"]),
            ),
        )
    _assert_no_plan(exc_info.value)


@pytest.mark.unit
async def test_bulk_unconfirmed_unknown_id_marker_unchanged(
    session_factory, issue_service, bulk_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="UNK")
    issue = await _make_issue(issue_service, actor=owner, workspace=workspace)
    missing = str(uuid.uuid4())
    with pytest.raises(BusinessRuleError) as exc_info:
        await bulk_service.execute(
            actor=owner,
            workspace_id=workspace.id,
            body=BulkRequest(
                issue_ids=[issue["id"], missing],
                changes=BulkChanges(project_id=target["id"]),
            ),
        )
    by_id = {p["issue_id"]: p for p in exc_info.value.details["previews"]}
    assert by_id[missing]["error"] == "not_found"
    assert "mapped_fields" in by_id[issue["id"]]


# ---------------------------------------------------------------------------
# L1: assert_can_write guest convention — invisible project is 404, not 403
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_assert_can_write_guest_invisible_project_not_found(
    session_factory, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    guest = await _make_member(session_factory, workspace, role="guest")
    private = await _make_project(
        project_service, actor=owner, workspace=workspace, visibility="private"
    )
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, workspace.id)
        project = await project_service._load_project(
            session, workspace_id=workspace.id, project_id=uuid.UUID(private["id"])
        )
        with pytest.raises(NotFoundError):
            await project_service.assert_can_write(session, viewer=guest, project=project)


@pytest.mark.unit
async def test_assert_can_write_guest_read_grant_forbidden(
    session_factory, project_service, member_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    guest = await _make_member(session_factory, workspace, role="guest")
    private = await _make_project(
        project_service, actor=owner, workspace=workspace, visibility="private"
    )
    await _grant(
        member_service, actor=owner, workspace=workspace, member=guest, project=private
    )
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, workspace.id)
        project = await project_service._load_project(
            session, workspace_id=workspace.id, project_id=uuid.UUID(private["id"])
        )
        with pytest.raises(ForbiddenError):
            await project_service.assert_can_write(session, viewer=guest, project=project)


@pytest.mark.unit
async def test_assert_can_write_guest_write_grant_allowed(
    session_factory, project_service, member_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    guest = await _make_member(session_factory, workspace, role="guest")
    private = await _make_project(
        project_service, actor=owner, workspace=workspace, visibility="private"
    )
    await _grant(
        member_service,
        actor=owner,
        workspace=workspace,
        member=guest,
        project=private,
        permission="write",
    )
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, workspace.id)
        project = await project_service._load_project(
            session, workspace_id=workspace.id, project_id=uuid.UUID(private["id"])
        )
        await project_service.assert_can_write(session, viewer=guest, project=project)


# ---------------------------------------------------------------------------
# M1: §3.8 — preview carries the version; confirmed move requires it
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_preview_plan_carries_current_version(
    session_factory, issue_service, move_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="VS")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="VT")
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    preview = await move_service.preview(
        viewer=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(issue["id"]),
        target_project_id=uuid.UUID(target["id"]),
    )
    assert preview["version"] == issue["version"]
    # the unconfirmed 422 envelope carries the same field so clients that
    # skipped step 1 can still echo it back
    with pytest.raises(BusinessRuleError) as exc_info:
        await move_service.move(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(target["id"]),
            confirm=False,
        )
    assert exc_info.value.details["preview"]["version"] == issue["version"]


@pytest.mark.unit
async def test_confirmed_move_requires_version(
    session_factory, issue_service, move_service, project_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="RS")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="RT")
    issue = await _make_issue(
        issue_service, actor=owner, workspace=workspace, project_id=source["id"]
    )
    with pytest.raises(ValidationError) as exc_info:
        await move_service.move(
            actor=owner,
            workspace_id=workspace.id,
            issue_id=uuid.UUID(issue["id"]),
            target_project_id=uuid.UUID(target["id"]),
            confirm=True,
        )
    assert exc_info.value.code == "validation_error"


# ---------------------------------------------------------------------------
# M3/L2/L3: bulk move audit trail, manifest rows, completed_at parity
# ---------------------------------------------------------------------------


async def _activity_rows(session_factory, issue_id: str) -> list:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT field, old_value, new_value FROM issue_activity"
                        " WHERE issue_id = :id ORDER BY created_at, id"
                    ),
                    {"id": uuid.UUID(issue_id)},
                )
            ).all()
        )


@pytest.mark.unit
async def test_bulk_confirmed_move_writes_activity_with_manifest(
    session_factory, issue_service, bulk_service, project_service, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="AS")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="AT")
    private_status = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="BulkAudited",
        category="in_progress",
        project_id=uuid.UUID(source["id"]),
    )
    from mesh.project.schemas import CreateMilestoneRequest

    milestone = await project_service.create_milestone(
        actor=owner,
        workspace_id=workspace.id,
        project_id=uuid.UUID(source["id"]),
        body=CreateMilestoneRequest(title="audit"),
    )
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        project_id=source["id"],
        status_id=private_status["id"],
        milestone_id=milestone["id"],
    )
    result = await bulk_service.execute(
        actor=owner,
        workspace_id=workspace.id,
        body=BulkRequest(
            issue_ids=[issue["id"]],
            changes=BulkChanges(project_id=target["id"]),
            confirm=True,
        ),
    )
    assert result["succeeded"] == 1
    rows = {r[0]: (r[1], r[2]) for r in await _activity_rows(session_factory, issue["id"])}
    # M3: the project change itself is audited (parity with explicit move)
    assert rows["project_id"] == (source["id"], target["id"])
    # L2: mapping + clearing manifests land in the trail
    assert rows["status"][0] == private_status["id"] and rows["status"][1] != private_status["id"]
    assert rows["milestone_id"] == (milestone["id"], None)


@pytest.mark.unit
async def test_move_activity_contains_mapping_and_clearing_manifest(
    session_factory, issue_service, move_service, project_service, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="MS")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="MT")
    private_status = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="MoveAudited",
        category="in_progress",
        project_id=uuid.UUID(source["id"]),
    )
    from mesh.project.schemas import CreateMilestoneRequest

    milestone = await project_service.create_milestone(
        actor=owner,
        workspace_id=workspace.id,
        project_id=uuid.UUID(source["id"]),
        body=CreateMilestoneRequest(title="manifest"),
    )
    issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        project_id=source["id"],
        status_id=private_status["id"],
        milestone_id=milestone["id"],
    )
    await move_service.move(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(issue["id"]),
        target_project_id=uuid.UUID(target["id"]),
        confirm=True,
        expected_version=issue["version"],
    )
    rows = {r[0]: (r[1], r[2]) for r in await _activity_rows(session_factory, issue["id"])}
    assert rows["project_id"] == (source["id"], target["id"])
    assert rows["status"][0] == private_status["id"]
    assert rows["milestone_id"] == (milestone["id"], None)


@pytest.mark.unit
async def test_bulk_move_syncs_completed_at_like_explicit_move(
    session_factory, issue_service, bulk_service, move_service, project_service, status_service
):
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace, role="owner")
    source = await _make_project(project_service, actor=owner, workspace=workspace, key="CS")
    target = await _make_project(project_service, actor=owner, workspace=workspace, key="CT")
    private_status = await status_service.create_status(
        actor=owner,
        workspace_id=workspace.id,
        name="StaleDone",
        category="in_progress",
        project_id=uuid.UUID(source["id"]),
    )

    async def _stale_completed(issue_id: str) -> None:
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE issues SET completed_at = :ts WHERE id = :id"
                ),
                {"ts": datetime(2026, 7, 1, tzinfo=UTC), "id": uuid.UUID(issue_id)},
            )

    # bulk path: a stale completed_at must be cleared when the mapped status
    # is not a done-category one (exactly what the explicit move already does)
    bulk_issue = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        project_id=source["id"],
        status_id=private_status["id"],
    )
    await _stale_completed(bulk_issue["id"])
    await bulk_service.execute(
        actor=owner,
        workspace_id=workspace.id,
        body=BulkRequest(
            issue_ids=[bulk_issue["id"]],
            changes=BulkChanges(project_id=target["id"]),
            confirm=True,
        ),
    )
    got = await issue_service.get_issue(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(bulk_issue["id"])
    )
    assert got["completed_at"] is None

    # parity guard: explicit move keeps the same behavior
    move_issue_dict = await _make_issue(
        issue_service,
        actor=owner,
        workspace=workspace,
        project_id=source["id"],
        status_id=private_status["id"],
        title="parity",
    )
    await _stale_completed(move_issue_dict["id"])
    await move_service.move(
        actor=owner,
        workspace_id=workspace.id,
        issue_id=uuid.UUID(move_issue_dict["id"]),
        target_project_id=uuid.UUID(target["id"]),
        confirm=True,
        expected_version=move_issue_dict["version"],
    )
    got2 = await issue_service.get_issue(
        viewer=owner, workspace_id=workspace.id, issue_id=uuid.UUID(move_issue_dict["id"])
    )
    assert got2["completed_at"] is None
