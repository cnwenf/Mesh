"""Per-channel resource authorization for ``project:{id}`` channels.

Every subscription re-runs resource-level authorization (README §6.7): a
``project:{id}`` channel requires workspace membership PLUS project
visibility — private projects are subscribable only by project members,
granted guests and workspace admins. The channel string is never the
isolation boundary (§6.2 rule 8); the check runs under the tenant GUC with
an explicit ``workspace_id`` filter, and RLS backstops the restricted role.

Development principals (``mesh-dev:<workspace-uuid>``) carry no user
identity — by definition they hold full workspace access, so private
projects are visible to them once workspace ownership matches.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from mesh.auth.rbac import role_satisfies
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel


def make_project_channel_checker(session_factory) -> PrefixChecker:
    """Build the ``project`` entity checker bound to a session factory."""

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        try:
            project_id = uuid.UUID(info.key)
        except ValueError:
            return False
        for workspace_id in sorted(principal.workspace_ids):
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                project = await session.scalar(
                    select(Project).where(
                        Project.id == project_id,
                        Project.workspace_id == workspace_id,
                    )
                )
                if project is None:
                    continue
                if project.deleted_at is not None:
                    return False
                if project.visibility == "public":
                    return True
                return await _private_project_allowed(
                    session, principal=principal, project=project, workspace_id=workspace_id
                )
        return False

    return check


async def _private_project_allowed(
    session, *, principal: Principal, project: Project, workspace_id: uuid.UUID
) -> bool:
    try:
        user_id = uuid.UUID(principal.subject)
    except ValueError:
        # Development principal: full workspace access by definition.
        return True
    member = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.user_id == user_id,
            Member.status == "active",
        )
    )
    if member is None:
        return False
    if role_satisfies(member.role, "project:manage"):
        return True
    project_role = await session.scalar(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id,
            ProjectMember.member_id == member.id,
        )
    )
    if project_role is not None:
        return True
    grant = await session.scalar(
        select(MemberProjectAccess.id).where(
            MemberProjectAccess.project_id == project.id,
            MemberProjectAccess.member_id == member.id,
        )
    )
    return grant is not None


__all__ = ["make_project_channel_checker"]
