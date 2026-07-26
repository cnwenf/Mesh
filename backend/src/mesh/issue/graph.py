"""Graph-change serialization + cycle detection (issue.md §2.5 rules 3–4).

Both parent-tree and dependency-graph mutations take a workspace-level
``pg_advisory_xact_lock`` BEFORE the reachability check — the lock-then-check
order is what closes the concurrent-cycle window (two transactions inserting
A→B and B→A would both pass an unlocked check, README §9 T12). The lock
releases automatically at transaction end.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_issue_graph(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Serialize parent/dependency graph changes per workspace (§2.5)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('issue_dep_graph:' || :ws))"),
        {"ws": str(workspace_id)},
    )


async def detect_parent_cycle(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    new_parent_id: uuid.UUID,
) -> list[str] | None:
    """Walk UP from ``new_parent_id``; a hit on ``issue_id`` closes a cycle.

    Returns the cycle path (for error ``details.path``) or None. The caller
    MUST hold :func:`lock_issue_graph` first.
    """
    if new_parent_id == issue_id:
        return [str(issue_id), str(issue_id)]
    result = await session.execute(
        text(
            """
            WITH RECURSIVE ancestors(node, path) AS (
              SELECT CAST(:root AS uuid), ARRAY[CAST(:root AS uuid)]
              UNION ALL
              SELECT i.parent_id, a.path || i.parent_id
              FROM issues i
              JOIN ancestors a ON a.node = i.id
              WHERE i.workspace_id = :ws AND i.parent_id IS NOT NULL
                AND NOT i.parent_id = ANY(a.path)
            )
            SELECT path FROM ancestors WHERE node = :target LIMIT 1
            """
        ),
        {"root": new_parent_id, "ws": workspace_id, "target": issue_id},
    )
    row = result.first()
    if row is None:
        return None
    return [str(node) for node in row[0]]


async def detect_dependency_cycle(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    depends_on_id: uuid.UUID,
) -> list[str] | None:
    """Cycle check for a new ``blocks`` edge (issue.md §2.5 rule 4).

    Stored ``blocks`` edges point issue_id → depends_on_id ("issue_id blocks
    depends_on_id"). Adding P blocks Q closes a cycle when P is reachable
    from Q following existing blocks edges. Returns the path or None; the
    caller MUST hold :func:`lock_issue_graph` first.
    """
    if depends_on_id == issue_id:
        return [str(issue_id), str(issue_id)]
    result = await session.execute(
        text(
            """
            WITH RECURSIVE reach(node, path) AS (
              SELECT CAST(:root AS uuid), ARRAY[CAST(:root AS uuid)]
              UNION ALL
              SELECT d.depends_on_id, r.path || d.depends_on_id
              FROM issue_dependencies d
              JOIN reach r ON r.node = d.issue_id
              WHERE d.workspace_id = :ws AND d.type = 'blocks'
                AND NOT d.depends_on_id = ANY(r.path)
            )
            SELECT path FROM reach WHERE node = :target LIMIT 1
            """
        ),
        {"root": depends_on_id, "ws": workspace_id, "target": issue_id},
    )
    row = result.first()
    if row is None:
        return None
    return [str(node) for node in row[0]]


__all__ = ["detect_dependency_cycle", "detect_parent_cycle", "lock_issue_graph"]
