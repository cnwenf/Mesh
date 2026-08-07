"""Shared issue/project read boundary for REST, realtime and notifications."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.auth.rbac import role_satisfies
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.errors import NotFoundError


def issue_visibility_clause(viewer: Member, workspace_id: uuid.UUID):
    """Return an Issue SQL predicate, or ``None`` for workspace managers."""
    if role_satisfies(viewer.role, "project:manage"):
        return None
    if viewer.role == "guest":
        granted = select(MemberProjectAccess.project_id).where(
            MemberProjectAccess.member_id == viewer.id,
            MemberProjectAccess.workspace_id == workspace_id,
        )
        return or_(
            Issue.project_id.in_(granted),
            Issue.assignee_id == viewer.id,
            Issue.reporter_id == viewer.id,
        )
    member_projects = select(ProjectMember.project_id).where(
        ProjectMember.member_id == viewer.id,
        ProjectMember.workspace_id == workspace_id,
    )
    visible_projects = select(Project.id).where(
        Project.workspace_id == workspace_id,
        Project.visibility == "public",
        Project.deleted_at.is_(None),
    )
    return or_(
        Issue.project_id.is_(None),
        Issue.project_id.in_(member_projects),
        Issue.project_id.in_(visible_projects),
    )


async def assert_member_can_view_issue(
    session: AsyncSession,
    *,
    viewer: Member,
    issue: Issue,
) -> None:
    """Enforce the same visibility semantics as issue list/detail routes."""
    if role_satisfies(viewer.role, "project:manage"):
        return
    project = None
    if issue.project_id is not None:
        project = await session.scalar(
            select(Project).where(
                Project.id == issue.project_id,
                Project.workspace_id == issue.workspace_id,
                Project.deleted_at.is_(None),
            )
        )
    if viewer.role == "guest":
        if issue.assignee_id == viewer.id or issue.reporter_id == viewer.id:
            return
        if project is None:
            raise NotFoundError("issue not found")
        grant = await session.scalar(
            select(MemberProjectAccess.id).where(
                MemberProjectAccess.project_id == project.id,
                MemberProjectAccess.member_id == viewer.id,
            )
        )
        if grant is None:
            raise NotFoundError("issue not found")
        return
    if project is None or project.visibility == "public":
        return
    project_role = await session.scalar(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id,
            ProjectMember.member_id == viewer.id,
        )
    )
    if project_role is None:
        # LOW-S2: unify the member branch with the guest branch — a private
        # project the viewer cannot see is reported as 404, not 403, so the
        # response does not become an existence oracle for private projects
        # (project.md §3.3 「其他成员访问返回 403/404」, §5.3 no-existence-oracle).
        raise NotFoundError("issue not found")


__all__ = ["assert_member_can_view_issue", "issue_visibility_clause"]
