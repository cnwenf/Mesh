"""Realtime channel authorization for project:{id} channels (README §6.7).

Resource-level subscription checks: workspace membership is the floor;
private projects additionally require project membership, a guest grant or
workspace admin. The channel string is never the isolation boundary.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.project.channels import make_project_channel_checker
from mesh.realtime.auth import Principal

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 25, tzinfo=UTC)


async def _workspace_with(session_factory, *, visibility: str = "private", deleted: bool = False):
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="W", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    async with session_factory() as session, session.begin():
        member_user = User(
            email=f"{uuid.uuid4().hex[:10]}@x.io",
            display_name="M",
            password_hash="x",
            status="active",
        )
        outsider_user = User(
            email=f"{uuid.uuid4().hex[:10]}@x.io",
            display_name="O",
            password_hash="x",
            status="active",
        )
        admin_user = User(
            email=f"{uuid.uuid4().hex[:10]}@x.io",
            display_name="A",
            password_hash="x",
            status="active",
        )
        session.add_all([member_user, outsider_user, admin_user])
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=member_user.id,
            role="member",
            status="active",
            joined_at=NOW,
        )
        outsider = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=outsider_user.id,
            role="member",
            status="active",
            joined_at=NOW,
        )
        admin = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=admin_user.id,
            role="admin",
            status="active",
            joined_at=NOW,
        )
        session.add_all([member, outsider, admin])
    async with session_factory() as session, session.begin():
        project = Project(
            workspace_id=workspace.id,
            name="P",
            key="CHX",
            visibility=visibility,
        )
        if deleted:
            project.deleted_at = NOW
        session.add(project)
    return workspace, project, member, outsider, admin


def _principal(member, workspace) -> Principal:
    # Real authenticators put the global user id in the subject.
    return Principal(subject=str(member.user_id), workspace_ids=frozenset({workspace.id}))


async def test_public_project_allows_workspace_member(session_factory):
    workspace, project, _member, outsider, _admin = await _workspace_with(
        session_factory, visibility="public"
    )
    checker = make_project_channel_checker(session_factory)
    assert await checker(_principal(outsider, workspace), f"project:{project.id}") is True


async def test_private_project_member_allowed_outsider_denied(session_factory):
    workspace, project, member, outsider, _admin = await _workspace_with(session_factory)
    async with session_factory() as session, session.begin():
        session.add(
            ProjectMember(
                workspace_id=workspace.id, project_id=project.id, member_id=member.id,
                role="member",
            )
        )
    checker = make_project_channel_checker(session_factory)
    channel = f"project:{project.id}"
    assert await checker(_principal(member, workspace), channel) is True
    assert await checker(_principal(outsider, workspace), channel) is False


async def test_private_project_admin_allowed(session_factory):
    workspace, project, _member, _outsider, admin = await _workspace_with(session_factory)
    checker = make_project_channel_checker(session_factory)
    assert await checker(_principal(admin, workspace), f"project:{project.id}") is True


async def test_private_project_guest_grant_allowed(session_factory):
    workspace, project, _member, outsider, _admin = await _workspace_with(session_factory)
    async with session_factory() as session, session.begin():
        session.add(
            MemberProjectAccess(
                workspace_id=workspace.id, member_id=outsider.id, project_id=project.id
            )
        )
    checker = make_project_channel_checker(session_factory)
    assert await checker(_principal(outsider, workspace), f"project:{project.id}") is True


async def test_dev_principal_gets_workspace_level_access(session_factory):
    workspace, project, _member, _outsider, _admin = await _workspace_with(session_factory)
    checker = make_project_channel_checker(session_factory)
    dev = Principal(subject="dev-user", workspace_ids=frozenset({workspace.id}))
    assert await checker(dev, f"project:{project.id}") is True


async def test_deleted_project_denied(session_factory):
    workspace, project, _member, _outsider, _admin = await _workspace_with(
        session_factory, visibility="public", deleted=True
    )
    checker = make_project_channel_checker(session_factory)
    dev = Principal(subject="dev-user", workspace_ids=frozenset({workspace.id}))
    assert await checker(dev, f"project:{project.id}") is False


async def test_other_workspace_and_malformed_channels(session_factory):
    workspace, project, _member, _outsider, _admin = await _workspace_with(
        session_factory, visibility="public"
    )
    checker = make_project_channel_checker(session_factory)
    # Principal without this workspace.
    other = Principal(subject="dev-user", workspace_ids=frozenset({uuid.uuid4()}))
    assert await checker(other, f"project:{project.id}") is False
    # Malformed channel / non-UUID key.
    dev = Principal(subject="dev-user", workspace_ids=frozenset({workspace.id}))
    assert await checker(dev, "project:not-a-uuid") is False
    assert await checker(dev, "nonsense") is False


# --- DefaultChannelAuthorizer: gateway parity (CWE-862) ----------------------


async def _seed_channel_row(session_factory, workspace_id, channel):
    """Materialise the ``realtime_channels`` row the row-probe path relies on."""
    from mesh.db.models.realtime import RealtimeChannel

    async with session_factory() as session, session.begin():
        session.add(RealtimeChannel(channel=channel, workspace_id=workspace_id))


def _make_authorizer(session_factory, *, with_project_checker: bool):
    from mesh.project.channels import make_project_channel_checker
    from mesh.realtime.auth import DefaultChannelAuthorizer

    authorizer = DefaultChannelAuthorizer(session_factory)
    if with_project_checker:
        authorizer.register_prefix_checker(
            "project", make_project_channel_checker(session_factory)
        )
    return authorizer


async def test_authorizer_workspace_floor_without_channel_row(session_factory):
    """A workspace-scoped channel is authorised from the key alone — no row needed.

    Fixes the first-subscribe race: a member subscribing before the projector has
    materialised ``realtime_channels`` must not race to ``forbidden``.
    """
    workspace, _project, _member, _outsider, _admin = await _workspace_with(
        session_factory, visibility="private"
    )
    authorizer = _make_authorizer(session_factory, with_project_checker=True)
    principal = Principal(subject="dev-user", workspace_ids=frozenset({workspace.id}))
    # No realtime_channels row exists for this channel.
    assert await authorizer.authorize(principal, f"workspace:{workspace.id}:projects") == workspace.id
    # A principal without the workspace is denied even though the key parses.
    other = Principal(subject="dev-user", workspace_ids=frozenset({uuid.uuid4()}))
    assert await authorizer.authorize(other, f"workspace:{workspace.id}:projects") is None


async def test_authorizer_private_project_denies_non_member_with_checker(session_factory):
    """With the checker registered (gateway parity), a non-member is denied even
    when the channel row exists — the CWE-862 leak path is closed."""
    workspace, project, member, outsider, _admin = await _workspace_with(
        session_factory, visibility="private"
    )
    channel = f"project:{project.id}"
    await _seed_channel_row(session_factory, workspace.id, channel)
    async with session_factory() as session, session.begin():
        session.add(
            ProjectMember(
                workspace_id=workspace.id, project_id=project.id, member_id=member.id
            )
        )
    authorizer = _make_authorizer(session_factory, with_project_checker=True)
    member_p = Principal(subject=str(member.user_id), workspace_ids=frozenset({workspace.id}))
    outsider_p = Principal(
        subject=str(outsider.user_id), workspace_ids=frozenset({workspace.id})
    )
    assert await authorizer.authorize(member_p, channel) == workspace.id
    assert await authorizer.authorize(outsider_p, channel) is None


async def test_authorizer_fail_closed_for_resource_entity_without_checker(session_factory):
    """A declared resource entity with NO registered checker is denied (fail-closed),
    while an unknown entity keeps the workspace-membership floor."""
    workspace, project, _member, _outsider, _admin = await _workspace_with(
        session_factory, visibility="public"
    )
    project_channel = f"project:{project.id}"
    issue_channel = f"issue:{workspace.id}"
    await _seed_channel_row(session_factory, workspace.id, project_channel)
    await _seed_channel_row(session_factory, workspace.id, issue_channel)
    authorizer = _make_authorizer(session_factory, with_project_checker=False)
    principal = Principal(subject="dev-user", workspace_ids=frozenset({workspace.id}))
    # project is resource-scoped but has no checker here → fail-closed deny.
    assert await authorizer.authorize(principal, project_channel) is None
    # issue is not declared resource-scoped → workspace floor applies.
    assert await authorizer.authorize(principal, issue_channel) == workspace.id
