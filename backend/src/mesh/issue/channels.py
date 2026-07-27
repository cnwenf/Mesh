"""Per-channel resource authorization for ``issue:{id}`` channels.

Every subscription re-runs resource-level authorization (README §6.7): an
``issue:{id}`` channel requires workspace membership PLUS project visibility —
an issue inside a private project is subscribable only by project members,
granted guests and workspace admins. The channel string is never the
isolation boundary (§6.2 rule 8). Registered on BOTH the API and the realtime
gateway factories so the independently-deployed processes cannot drift
(CWE-862).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select

from mesh.auth.rbac import role_satisfies
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_issue_checkers(authorizer: _CheckerRegistrar, session_factory) -> None:
    """Register the ``issue`` entity checker everywhere at once."""
    authorizer.register_prefix_checker("issue", make_issue_channel_checker(session_factory))


def make_issue_channel_checker(session_factory) -> PrefixChecker:
    """Build the ``issue`` entity checker bound to a session factory."""

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        key = info.key
        # ``issue:{id}:runs`` carries the execution state stream (agent.md
        # §3.6) — same visibility boundary as the issue detail channel.
        if key.endswith(":runs"):
            key = key[: -len(":runs")]
        try:
            issue_id = uuid.UUID(key)
        except ValueError:
            return False
        for workspace_id in sorted(principal.workspace_ids):
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                issue = await session.scalar(
                    select(Issue).where(
                        Issue.id == issue_id,
                        Issue.workspace_id == workspace_id,
                    )
                )
                if issue is None:
                    continue
                if issue.deleted_at is not None:
                    return False
                if issue.project_id is None:
                    return True
                project = await session.scalar(
                    select(Project).where(
                        Project.id == issue.project_id,
                        Project.workspace_id == workspace_id,
                    )
                )
                if project is None or project.deleted_at is not None:
                    # Project gone: issue is effectively inbox-level now.
                    return True
                if project.visibility == "public":
                    return True
                return await _private_issue_allowed(
                    session, principal=principal, project=project, workspace_id=workspace_id,
                    issue=issue,
                )
        return False

    return check


async def _private_issue_allowed(
    session,
    *,
    principal: Principal,
    project: Project,
    workspace_id: uuid.UUID,
    issue: Issue,
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
    # Guests and non-members: explicit grant, or direct involvement.
    grant = await session.scalar(
        select(MemberProjectAccess.id).where(
            MemberProjectAccess.project_id == project.id,
            MemberProjectAccess.member_id == member.id,
        )
    )
    if grant is not None:
        return True
    project_role = await session.scalar(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id, ProjectMember.member_id == member.id
        )
    )
    if project_role is not None:
        return True
    return issue.assignee_id == member.id or issue.reporter_id == member.id


__all__ = ["make_issue_channel_checker", "register_issue_checkers"]
