"""Unified execution-visibility scope (analytics.md §2.3.1, R4/R5 HIGH-6/HIGH-3).

``VISIBLE_EXECUTIONS_CTE`` is the SINGLE authoritative SQL text for every
execution-metric aggregation: workload-B (§2.2.4), agent stats main query /
retry-rate subquery / token aggregation (§2.3) and the workspace dashboard
agent-stats + workload-execution sections (§3.1/§4.3). No endpoint may
aggregate ``task_executions`` by ``workspace_id + agent_id + window`` alone —
every builder in ``mesh.analytics.queries`` inlines this CTE verbatim.

The predicate is two serial layers:
  ① agent visibility first — a private agent's statistics never leak to
     non-owner / non-admin requesters;
  ② executions linked to an issue inherit that issue's CURRENT project
     visibility (same "current attribution" caliber as §2.2.2/§2.2.5);
     executions without an issue (manual/chat/integration direct dispatch)
     belong to the agent itself and carry no project side channel.

``analytics_exec_visible_to`` is the per-execution boolean form of the same
predicate (executable reference for validation / structural negatives); the
aggregate form above is authoritative and the two are line-for-line
equivalent (T33).
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select, text

from mesh.analytics.scope import hash_id_set
from mesh.auth.rbac import role_satisfies
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember

# R5 authoritative CTE — reuse verbatim; do not rewrite or drop filters.
VISIBLE_EXECUTIONS_CTE = """
visible_executions AS (
  SELECT e.*
  FROM task_executions e
  JOIN agents a        ON a.id = e.agent_id AND a.workspace_id = e.workspace_id
  LEFT JOIN issues i   ON i.id = e.issue_id AND i.workspace_id = e.workspace_id
  LEFT JOIN projects p ON p.id = i.project_id AND p.workspace_id = i.workspace_id
  WHERE e.workspace_id = :ws
    AND (a.visibility = 'workspace'
         OR (a.visibility = 'private'
             AND (a.owner_user_id = :requester_user_id
                  OR :requester_role IN ('owner', 'admin'))))
    AND (i.id IS NULL
         OR p.id IS NULL
         OR p.visibility = 'public'
         OR :requester_role IN ('owner', 'admin')
         OR EXISTS (SELECT 1 FROM project_members pm
                     WHERE pm.workspace_id = e.workspace_id AND pm.project_id = p.id
                       AND pm.member_id = :requester_member_id)
         OR EXISTS (SELECT 1 FROM member_project_access mx
                     WHERE mx.workspace_id = e.workspace_id AND mx.project_id = p.id
                       AND mx.member_id = :requester_member_id))
)
"""


def visible_executions_cte() -> str:
    """The authoritative CTE text (single source; verbatim reuse everywhere)."""
    return VISIBLE_EXECUTIONS_CTE


def requester_cte_params(member) -> dict:
    """Bind values for the three requester parameters of the CTE."""
    return {
        "requester_member_id": member.id,
        "requester_user_id": member.user_id,
        "requester_role": member.role,
    }


def is_workspace_manager(member) -> bool:
    """owner/admin see the full workspace aggregate (scope_key ``ws_admin``)."""
    return role_satisfies(member.role, "project:manage")


async def visible_project_ids(session, *, workspace_id: uuid.UUID, member) -> list[uuid.UUID] | None:
    """The requester's visible project id set; ``None`` means full workspace.

    Visible = ``visibility='public'`` projects ∪ projects covered by a
    ``project_members`` or ``member_project_access`` row (§3.1 R3, same
    caliber as the scope_key fingerprint). admin/owner → ``None`` (full).
    """
    if is_workspace_manager(member):
        return None
    public_ids = set(
        (
            await session.execute(
                select(Project.id).where(
                    Project.workspace_id == workspace_id,
                    Project.visibility == "public",
                    Project.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    pm_ids = set(
        (
            await session.execute(
                select(ProjectMember.project_id).where(
                    ProjectMember.workspace_id == workspace_id,
                    ProjectMember.member_id == member.id,
                )
            )
        )
        .scalars()
        .all()
    )
    mx_ids = set(
        (
            await session.execute(
                select(MemberProjectAccess.project_id).where(
                    MemberProjectAccess.workspace_id == workspace_id,
                    MemberProjectAccess.member_id == member.id,
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted(public_ids | pm_ids | mx_ids)


async def visible_agent_ids(session, *, workspace_id: uuid.UUID, member) -> list[uuid.UUID] | None:
    """Visible agent set: workspace-visible agents ∪ owned private agents.

    admin/owner → ``None`` (full workspace, §2.3.1 R4 cache-key caliber).
    """
    if is_workspace_manager(member):
        return None
    ids = (
        await session.execute(
            select(Agent.id).where(
                Agent.workspace_id == workspace_id,
                Agent.deleted_at.is_(None),
                or_(Agent.visibility == "workspace", Agent.owner_user_id == member.user_id),
            )
        )
    ).scalars().all()
    return sorted(ids)


async def compute_issue_scope_key(session, *, workspace_id: uuid.UUID, member) -> str:
    """scope_key for issue metrics: ``ws_admin`` | ``projects:<sha256>`` (§2.5)."""
    ids = await visible_project_ids(session, workspace_id=workspace_id, member=member)
    if ids is None:
        return "ws_admin"
    return f"projects:{hash_id_set(ids)}"


async def compute_exec_scope_key(session, *, workspace_id: uuid.UUID, member) -> str:
    """scope_key for execution metrics (§2.3.1 R4).

    admin/owner → ``ws_admin``; regular members →
    ``exec:p<sha256(visible projects)>:a<sha256(visible agents)>``.
    """
    project_ids = await visible_project_ids(session, workspace_id=workspace_id, member=member)
    if project_ids is None:
        return "ws_admin"
    agent_ids = await visible_agent_ids(session, workspace_id=workspace_id, member=member)
    return f"exec:p{hash_id_set(project_ids)}:a{hash_id_set(agent_ids or [])}"


async def analytics_exec_visible_to(
    session, *, execution_id: uuid.UUID, member_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    """Per-execution boolean form of VISIBLE_EXECUTIONS_CTE.

    Runs the SAME CTE text with the requester's three parameters and checks
    membership — semantically equivalent to the aggregate form line for line.
    """
    member = await session.get(Member, member_id)
    if member is None:
        return False
    sql = text(
        "WITH " + VISIBLE_EXECUTIONS_CTE + " SELECT 1 FROM visible_executions e"
        " WHERE e.id = :execution_id LIMIT 1"
    )
    params = {"ws": workspace_id, "execution_id": execution_id}
    params.update(requester_cte_params(member))
    row = await session.execute(sql, params)
    return row.first() is not None
