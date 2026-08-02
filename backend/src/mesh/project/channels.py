"""Per-channel resource authorization for ``project:{id}`` channels.

Every subscription re-runs resource-level authorization (README §6.7): a
``project:{id}`` channel requires workspace membership PLUS the same project
visibility predicate as API reads: guests require an explicit grant even for
public projects, members see public projects or private memberships, and
workspace admins see every live project. The channel string is never the
isolation boundary (§6.2 rule 8); the check runs under the tenant GUC with
an explicit ``workspace_id`` filter, and RLS backstops the restricted role.

Development principals (``mesh-dev:<workspace-uuid>``) carry no user
identity — by definition they hold full workspace access, so private
projects are visible to them once workspace ownership matches.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select

from mesh.auth.rbac import role_satisfies
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_resource_checkers(authorizer: _CheckerRegistrar, session_factory) -> None:
    """Register every project-module resource checker on ``authorizer``.

    Single source of truth shared by the API and the realtime gateway factories
    (README §2.2) so the two independently-deployed processes can never drift:
    the gateway must enforce the same resource-level visibility as the API, else
    a private ``project:{id}`` channel leaks on the production ``/ws`` path
    (CWE-862). Adding a new resource entity here registers it everywhere at once.
    """
    authorizer.register_prefix_checker("project", make_project_channel_checker(session_factory))


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
                return await _project_allowed(
                    session, principal=principal, project=project, workspace_id=workspace_id
                )
        return False

    return check


async def _project_allowed(
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
    if member.role == "guest":
        grant = await session.scalar(
            select(MemberProjectAccess.id).where(
                MemberProjectAccess.workspace_id == workspace_id,
                MemberProjectAccess.project_id == project.id,
                MemberProjectAccess.member_id == member.id,
            )
        )
        return grant is not None
    if project.visibility == "public":
        return True
    project_role = await session.scalar(
        select(ProjectMember.role).where(
            ProjectMember.workspace_id == workspace_id,
            ProjectMember.project_id == project.id,
            ProjectMember.member_id == member.id,
        )
    )
    return project_role is not None


__all__ = ["make_project_channel_checker", "register_resource_checkers"]
