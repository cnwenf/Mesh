"""Authorization-safe integration binding and event visibility.

Project-scoped integration data follows the project read contract.  The
predicates in this module are deliberately SQL expressions: callers apply
them before counting or keyset pagination, so hidden rows cannot shorten a
page, advance a cursor, or leak through an aggregate.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.auth.rbac import role_satisfies
from mesh.db.models.integration import IntegrationBinding, IntegrationEvent
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember


def _visible_live_project_ids(viewer: Member):
    """Project ids visible to one non-manager workspace member."""
    if viewer.role == "guest":
        granted_ids = select(MemberProjectAccess.project_id).where(
            MemberProjectAccess.workspace_id == viewer.workspace_id,
            MemberProjectAccess.member_id == viewer.id,
        )
        return select(Project.id).where(
            Project.workspace_id == viewer.workspace_id,
            Project.deleted_at.is_(None),
            Project.id.in_(granted_ids),
        )

    member_project_ids = select(ProjectMember.project_id).where(
        ProjectMember.workspace_id == viewer.workspace_id,
        ProjectMember.member_id == viewer.id,
    )
    return select(Project.id).where(
        Project.workspace_id == viewer.workspace_id,
        Project.deleted_at.is_(None),
        or_(Project.visibility == "public", Project.id.in_(member_project_ids)),
    )


def scoped_visibility_clause(*, scope_column, project_id_column, viewer: Member):
    """Return the shared workspace/project SQL predicate, or ``None`` for managers.

    Only the exact ``workspace`` and ``project`` shapes are readable by a
    non-manager.  This makes any future/legacy ``unknown`` scope fail closed.
    A snapshotted project id must still identify a live, visible project;
    deleted projects remain available only to owner/admin audit readers.
    """
    if role_satisfies(viewer.role, "project:manage"):
        return None
    visible_project_ids = _visible_live_project_ids(viewer)
    return or_(
        and_(scope_column == "workspace", project_id_column.is_(None)),
        and_(scope_column == "project", project_id_column.in_(visible_project_ids)),
    )


def binding_visibility_clause(viewer: Member):
    return scoped_visibility_clause(
        scope_column=IntegrationBinding.scope,
        project_id_column=IntegrationBinding.project_id,
        viewer=viewer,
    )


def event_visibility_clause(viewer: Member):
    return scoped_visibility_clause(
        scope_column=IntegrationEvent.visibility_scope,
        project_id_column=IntegrationEvent.project_id_snapshot,
        viewer=viewer,
    )


async def resolve_event_visibility(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    provider: str,
    provider_tenant_key: str,
    external_ref: str,
) -> tuple[str, uuid.UUID | None]:
    """Snapshot a verified event's binding scope without inspecting payload data.

    Binding status is intentionally irrelevant: a disabled binding still owns
    its global external identity and therefore remains the authoritative
    privacy boundary.  Missing or malformed shapes fail closed as ``unknown``.
    """
    row = (
        await session.execute(
            select(IntegrationBinding.scope, IntegrationBinding.project_id)
            .where(
                IntegrationBinding.workspace_id == workspace_id,
                IntegrationBinding.integration_id == integration_id,
                IntegrationBinding.provider == provider,
                IntegrationBinding.provider_tenant_key == provider_tenant_key,
                IntegrationBinding.external_ref == external_ref,
            )
            # Shared row lock keeps the scope snapshot and the later routing
            # match in the same ingest transaction from observing different
            # bindings during a concurrent delete/rebind.  Other inbound
            # readers still proceed concurrently.
            .with_for_update(read=True)
        )
    ).one_or_none()
    if row is None:
        return "unknown", None
    if row.scope == "workspace" and row.project_id is None:
        return "workspace", None
    if row.scope == "project" and row.project_id is not None:
        return "project", row.project_id
    return "unknown", None


__all__ = [
    "binding_visibility_clause",
    "event_visibility_clause",
    "resolve_event_visibility",
    "scoped_visibility_clause",
]
