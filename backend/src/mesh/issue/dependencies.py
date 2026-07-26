"""Issue dependencies — the directed graph relation (issue.md §1.2.4 / §2.2).

Separate from the parent tree: a weak "order" constraint, not composition.
Edges are stored NORMALIZED — ``blocked_by`` requests are inverted into a
``blocks`` edge (``A blocks B`` stored once, never both directions, §2.2
note); reads expand both directions and render from the requested issue's
perspective. Cycle prevention takes the workspace advisory lock BEFORE the
reachability walk — lock-then-check is what makes concurrent A→B / B→A
inserts reject exactly one side (README §9 T12).
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from mesh.db.constraints import violates as _violates
from mesh.db.models.issue import DEPENDENCY_TYPE_VALUES, Issue, IssueDependency
from mesh.db.models.member import Member
from mesh.errors import ConflictError, NotFoundError, ValidationError
from mesh.issue.graph import detect_dependency_cycle, lock_issue_graph
from mesh.issue.service import IssueService, _isoformat

DEPENDENCY_NOT_FOUND = "dependency not found"


def _render_edge(
    edge: IssueDependency,
    *,
    perspective: uuid.UUID,
    identifiers: dict[uuid.UUID, str] | None = None,
) -> dict:
    """Render an edge from ``perspective``'s point of view (§2.2 note).

    Stored ``blocks`` edges read as ``blocked_by`` from the other end;
    symmetric types (relates_to/duplicates) keep their type both ways.
    ``created_at`` is an RFC3339 string so the dict is both API- and
    outbox-JSONB-safe; ``depends_on_identifier`` carries the human-readable
    identifier so the UI shows ``WEB-12`` instead of a bare UUID (§4.2/§4.3).
    """
    ids = identifiers or {}
    if edge.issue_id == perspective:
        return {
            "id": str(edge.id),
            "issue_id": str(perspective),
            "depends_on_id": str(edge.depends_on_id),
            "depends_on_identifier": ids.get(edge.depends_on_id),
            "type": edge.type,
            "created_by": str(edge.created_by) if edge.created_by is not None else None,
            "created_at": _isoformat(edge.created_at),
        }
    inverted = "blocked_by" if edge.type == "blocks" else edge.type
    return {
        "id": str(edge.id),
        "issue_id": str(perspective),
        "depends_on_id": str(edge.issue_id),
        "depends_on_identifier": ids.get(edge.issue_id),
        "type": inverted,
        "created_by": str(edge.created_by) if edge.created_by is not None else None,
        "created_at": _isoformat(edge.created_at),
    }


class DependencyService:
    """Dependency edges over the issue graph (issue.md §3.1)."""

    def __init__(self, issue_service: IssueService) -> None:
        self._issues = issue_service

    async def list_dependencies(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
    ) -> list[dict]:
        factory = self._issues._factory
        async with factory() as session:
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id
            )
            await self._issues.assert_can_view_issue(session, viewer=viewer, issue=issue)
            edges = (
                (
                    await session.execute(
                        select(IssueDependency)
                        .where(
                            IssueDependency.workspace_id == workspace_id,
                            or_(
                                IssueDependency.issue_id == issue.id,
                                IssueDependency.depends_on_id == issue.id,
                            ),
                        )
                        .order_by(IssueDependency.created_at.asc(), IssueDependency.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            other_ids = {
                edge.depends_on_id if edge.issue_id == issue.id else edge.issue_id
                for edge in edges
            }
            identifiers: dict[uuid.UUID, str] = {}
            if other_ids:
                rows = (
                    await session.execute(
                        select(Issue.id, Issue.identifier).where(
                            Issue.workspace_id == workspace_id, Issue.id.in_(other_ids)
                        )
                    )
                ).all()
                identifiers = {row[0]: row[1] for row in rows}
            return [
                _render_edge(edge, perspective=issue.id, identifiers=identifiers)
                for edge in edges
            ]

    async def add_dependency(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        depends_on_id: uuid.UUID,
        dep_type: str = "relates_to",
    ) -> dict:
        if dep_type not in DEPENDENCY_TYPE_VALUES:
            raise ValidationError("invalid type", details={"type": dep_type})
        if depends_on_id == issue_id:
            raise ValidationError("an issue cannot depend on itself")
        factory = self._issues._factory
        async with factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            target = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=depends_on_id
            )
            await self._issues.assert_can_view_issue(session, viewer=actor, issue=target)

            # Normalize: store a single canonical edge per relation (§2.2).
            if dep_type == "blocked_by":
                stored_issue_id, stored_depends_on_id, stored_type = (
                    target.id, issue.id, "blocks",
                )
            else:
                stored_issue_id, stored_depends_on_id, stored_type = (
                    issue.id, target.id, dep_type,
                )

            # Serialize graph changes BEFORE the cycle check (issue.md §2.5
            # rule 4 / README §9 T12): without the lock, concurrent A→B and
            # B→A inserts both pass and form a cycle.
            await lock_issue_graph(session, workspace_id)
            if stored_type == "blocks":
                path = await detect_dependency_cycle(
                    session,
                    workspace_id=workspace_id,
                    issue_id=stored_issue_id,
                    depends_on_id=stored_depends_on_id,
                )
                if path is not None:
                    raise ConflictError(
                        "dependency would create a cycle",
                        code="circular_dependency",
                        details={"path": path},
                    )
            edge = IssueDependency(
                workspace_id=workspace_id,
                issue_id=stored_issue_id,
                depends_on_id=stored_depends_on_id,
                type=stored_type,
                created_by=actor.id,
            )
            session.add(edge)
            # Capture ids before the flush: a failed flush expires the ORM
            # instances and attribute access would re-issue SQL on the dead
            # transaction (same trap project.md templates noted).
            subject_id, target_id = issue.id, target.id
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_issue_dependencies_edge"):
                    raise ConflictError(
                        "dependency edge already exists",
                        code="dependency_exists",
                        details={
                            "issue_id": str(subject_id),
                            "depends_on_id": str(target_id),
                        },
                    ) from exc
                raise
            # Resolve the identifier on the creation path so the POST response
            # matches the GET listing (MES-45): without it the UI renders a
            # bare UUID fragment for the freshly added edge until a refetch.
            # ``target`` is the "other" issue from ``perspective=subject_id``
            # in both the plain and the inverted (blocked_by → blocks) cases.
            rendered = _render_edge(
                edge,
                perspective=subject_id,
                identifiers={target.id: target.identifier},
            )
            # Both detail channels + workspace list when visible (README §6.7).
            for channel_issue in (issue, target):
                project = await self._issues._project_of(session, channel_issue)
                await self._issues._emit_issue_event(
                    session,
                    issue=channel_issue,
                    event="dependency.changed",
                    data={
                        "issue_id": str(issue.id),
                        "depends_on_id": str(target.id),
                        "type": rendered["type"],
                        "action": "added",
                    },
                    project=project,
                )
            return rendered

    async def remove_dependency(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        dependency_id: uuid.UUID,
    ) -> dict:
        factory = self._issues._factory
        async with factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            edge = await session.scalar(
                select(IssueDependency).where(
                    IssueDependency.id == dependency_id,
                    IssueDependency.workspace_id == workspace_id,
                )
            )
            if edge is None or issue_id not in (edge.issue_id, edge.depends_on_id):
                raise NotFoundError(DEPENDENCY_NOT_FOUND)
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=edge.issue_id
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            snapshot = _render_edge(edge, perspective=issue_id)
            other_id = (
                edge.depends_on_id if edge.issue_id == issue.id else edge.issue_id
            )
            await session.delete(edge)
            await session.flush()
            for channel_issue_id in (issue.id, other_id):
                channel_issue = await self._issues._load_issue(
                    session, workspace_id=workspace_id, issue_id=channel_issue_id
                )
                project = await self._issues._project_of(session, channel_issue)
                await self._issues._emit_issue_event(
                    session,
                    issue=channel_issue,
                    event="dependency.changed",
                    data={**snapshot, "action": "removed"},
                    project=project,
                )
            return {"id": str(dependency_id), "deleted": True}


__all__ = ["DependencyService"]
