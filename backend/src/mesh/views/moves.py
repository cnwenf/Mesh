"""Board move commands — atomic drag + WIP (kanban.md §3.2/§4.3/§4.4).

``POST /views/{id}/moves`` is the ONLY write path that can enforce a view-level
WIP limit, because only it carries the ``view_id`` (a bare ``PATCH /issues/{id}``
cannot see the view). One drag = ONE transaction:

    optimistic-lock (``version``) → ``pg_advisory_xact_lock`` serializing the
    target column → count the target group under the view's filters → enforce
    WIP (``block`` → 422 ``wip_limit_exceeded``; ``warn`` → proceed + emit
    ``view.wip_exceeded``) → apply the grouping-field change (reusing the issue
    module's ``apply_changes_in_tx`` for status/assignee/priority, strict-mode,
    activity trail and ``issue.updated``) → upsert the per-view card position →
    broadcast ``issue.moved`` (with ``view_id``).

``group_by=project`` drags are the view-side entry to the cross-project two-step
contract (issue.md §3.8, README §9 T22): they delegate to ``MoveService``
(preview → ``move_confirmation_required`` → confirmed single-transaction move
emitting ``issue.project_changed``).

``POST /views/{id}/reorder`` is order-only: it upserts ``view_issue_positions``
and never touches issue fields (so a drag in one view cannot reorder another,
kanban §2.7).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mesh.db.models.issue import Issue
from mesh.db.models.view import View
from mesh.db.models.view_position import ViewIssuePosition
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from mesh.issue.service import IssuePatch
from mesh.issue.statuses import resolve_default_status
from mesh.issue.triggers import apply_assign_triggers
from mesh.outbox.service import emit_realtime
from mesh.views.projection import (
    PROJECTION_FIELD_PENDING,
    compile_view_filters,
    group_key_for,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from mesh.db.models.member import Member
    from mesh.issue.move import MoveService
    from mesh.issue.service import IssueService
    from mesh.views.service import ViewService

_VIEW_NOT_FOUND = "view not found"
_NONE_KEYS = frozenset({"__none__", "none", "no_project", "unassigned"})
_SUPPORTED_GROUP_BY = frozenset({"state_category", "status", "assignee", "priority"})
# Floating-point midpoint precision floor — below this a column re-ranks (kanban §4.3).
POSITION_EPSILON = 1e-6


def _view_channel(view_id: uuid.UUID) -> str:
    return f"view:{view_id}"


class BoardMoveService:
    """Atomic board moves + WIP + per-view ordering (kanban §3.2/§4.3/§4.4)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        issue_service: IssueService,
        move_service: MoveService,
        view_service: ViewService,
        *,
        clock: Any | None = None,
    ) -> None:
        self._factory = session_factory
        self._issues = issue_service
        self._moves = move_service
        self._views = view_service
        self._clock = clock

    async def _load_view(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, view_id: uuid.UUID
    ) -> View:
        view = await session.scalar(
            select(View).where(View.id == view_id, View.workspace_id == workspace_id)
        )
        if view is None:
            raise NotFoundError(_VIEW_NOT_FOUND)
        return view

    # ------------------------------------------------------------------
    # move
    # ------------------------------------------------------------------

    async def move(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        issue_id: uuid.UUID,
        to_group_key: str,
        position: float,
        version: int | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id)
            group_by = view.group_by or "state_category"

        if group_by == "project":
            return await self._move_project(
                actor=actor,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                to_group_key=to_group_key,
                position=position,
                version=version,
                confirm=confirm,
                dry_run=dry_run,
            )
        if group_by not in _SUPPORTED_GROUP_BY:
            raise ValidationError(
                "group_by=label/custom-field moves await the label-property association increment",
                code=PROJECTION_FIELD_PENDING,
                details={"group_by": group_by},
            )
        return await self._move_intra(
            actor=actor,
            workspace_id=workspace_id,
            view_id=view_id,
            issue_id=issue_id,
            group_by=group_by,
            to_group_key=to_group_key,
            position=position,
            version=version,
        )

    async def _move_intra(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        issue_id: uuid.UUID,
        group_by: str,
        to_group_key: str,
        position: float,
        version: int | None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id)
            await self._views.assert_can_read(session, viewer=actor, view=view)
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            if version is not None and issue.version != version:
                raise ConflictError(
                    "issue was modified concurrently",
                    code="conflict",
                    details={"id": str(issue.id), "current_version": issue.version},
                )

            from_group_key = group_key_for(group_by, issue)

            # (b) serialize the target column so concurrent WIP counts don't race.
            await session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('wip:' || :view_id || ':' || :group_key))"
                ),
                {"view_id": str(view_id), "group_key": to_group_key},
            )

            # (c)+(d) WIP: count target-group members under the view's filters.
            wip = (view.board_settings or {}).get("wip", {}).get(to_group_key)
            wip_exceeded = False
            count = 0
            if wip is not None:
                count = await self._count_group(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    view=view,
                    group_by=group_by,
                    to_group_key=to_group_key,
                    exclude_issue_id=issue_id,
                )
                if count >= wip["limit"]:
                    if wip["enforcement"] == "block":
                        raise BusinessRuleError(
                            "target column is at its WIP limit",
                            code="wip_limit_exceeded",
                            details={
                                "group_key": to_group_key,
                                "limit": wip["limit"],
                                "count": count,
                            },
                        )
                    wip_exceeded = True  # warn: allow + notify

            # (e) apply the grouping-field change (reuses the issue writer:
            # strict mode, activity trail, version bump, issue.updated).
            patch = await self._patch_for(session, view=view, group_by=group_by, to_group_key=to_group_key)
            rendered, changes = await self._issues.apply_changes_in_tx(
                session, actor=actor, issue=issue, patch=patch
            )
            # Broadcast issue.moved for every move — group change AND position-only
            # within the view — so collaborators never miss a reorder done via /moves
            # (kanban §3.5/§4.3; /reorder already emits likewise).
            project = await self._issues._project_of(session, issue)
            moved_event = await self._issues._emit_issue_event(
                session,
                issue=issue,
                event="issue.moved",
                data={
                    "id": str(issue.id),
                    "from": {"group_key": from_group_key},
                    "to": {"group_key": to_group_key},
                    "position": position,
                    "view_id": str(view_id),
                },
                project=project,
            )
            if "assignee_id" in changes:
                prev = changes.get("_prev_assignee")
                await apply_assign_triggers(
                    session,
                    workspace_id=workspace_id,
                    issue=issue,
                    previous_assignee_id=uuid.UUID(prev) if prev else None,
                    trigger_event_id=moved_event.id,
                )

            # Upsert FIRST so the exhaustion check sees the moved card's row and
            # the rerank (if triggered) re-spaces it too — otherwise the moved
            # card would keep a colliding position after a rerank (§4.3).
            await self._upsert_position_tx(
                session,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                group_key=to_group_key,
                position=position,
            )
            await self._rerank_if_exhausted(
                session, view_id=view_id, group_key=to_group_key, position=position
            )

            if wip_exceeded:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_view_channel(view_id),
                    event="view.wip_exceeded",
                    data={
                        "view_id": str(view_id),
                        "group_key": to_group_key,
                        "limit": wip["limit"],
                        "count": count + 1,
                    },
                )

        rendered["position"] = position
        return rendered

    async def _move_project(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        issue_id: uuid.UUID,
        to_group_key: str,
        position: float,
        version: int | None,
        confirm: bool,
        dry_run: bool,
    ) -> dict:
        target_project_id = None if to_group_key in _NONE_KEYS else uuid.UUID(to_group_key)

        if dry_run:
            return await self._moves.preview(
                viewer=actor,
                workspace_id=workspace_id,
                issue_id=issue_id,
                target_project_id=target_project_id,
            )
        if not confirm:
            # Raises 422 move_confirmation_required carrying details.preview.
            return await self._moves.move(
                actor=actor,
                workspace_id=workspace_id,
                issue_id=issue_id,
                target_project_id=target_project_id,
                confirm=False,
                expected_version=version,
            )

        # §3.8 step 2 (MES-48): a confirmed move must carry the current version.
        if version is None:
            raise ValidationError(
                "move requires the current version",
                details={"field": "version", "hint": "echo preview.version back"},
            )
        # kanban.md §3.2 single-transaction contract: the cross-project
        # migration AND the per-view ordering upsert share ONE transaction, so
        # the card's column position is committed atomically with project_id /
        # status mapping / clearing of project-private fields.
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            rendered, plan = await self._moves.apply_confirmed_move_in_session(
                session,
                actor=actor,
                workspace_id=workspace_id,
                issue_id=issue_id,
                target_project_id=target_project_id,
                expected_version=version,
            )
            await self._upsert_position_tx(
                session,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                group_key=to_group_key,
                position=position,
            )
        rendered["move_result"] = {
            "mapped_fields": plan.get("mapped_fields", []),
            "cleared_fields": plan.get("cleared_fields", []),
        }
        rendered["position"] = position
        return rendered

    # ------------------------------------------------------------------
    # reorder (order-only, no field change)
    # ------------------------------------------------------------------

    async def reorder(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        issue_id: uuid.UUID,
        to_group_key: str,
        position: float,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id)
            await self._views.assert_can_read(session, viewer=actor, view=view)
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            # Upsert first so the exhaustion check / rerank includes this card.
            await self._upsert_position_tx(
                session,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                group_key=to_group_key,
                position=position,
            )
            await self._rerank_if_exhausted(
                session, view_id=view_id, group_key=to_group_key, position=position
            )
            project = await self._issues._project_of(session, issue)
            await self._issues._emit_issue_event(
                session,
                issue=issue,
                event="issue.moved",
                data={
                    "id": str(issue.id),
                    "to": {"group_key": to_group_key},
                    "position": position,
                    "view_id": str(view_id),
                },
                project=project,
            )
        return {"id": str(issue_id), "group_key": to_group_key, "position": position}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _patch_for(
        self, session: AsyncSession, *, view: View, group_by: str, to_group_key: str
    ) -> IssuePatch:
        if group_by == "state_category":
            status = await resolve_default_status(
                session,
                workspace_id=view.workspace_id,
                project_id=view.project_id,
                category=to_group_key,
            )
            return IssuePatch(status_id=status.id)
        if group_by == "status":
            return IssuePatch(status_id=uuid.UUID(to_group_key))
        if group_by == "priority":
            return IssuePatch(priority=to_group_key)
        if group_by == "assignee":
            assignee = None if to_group_key in _NONE_KEYS else uuid.UUID(to_group_key)
            return IssuePatch(assignee_id=assignee)
        raise ValidationError("unsupported group_by for move", details={"group_by": group_by})

    def _group_predicate(self, group_by: str, to_group_key: str) -> Any:
        if group_by == "state_category":
            return Issue.state_category == to_group_key
        if group_by == "status":
            return Issue.status_id == uuid.UUID(to_group_key)
        if group_by == "priority":
            return Issue.priority == to_group_key
        if group_by == "assignee":
            if to_group_key in _NONE_KEYS:
                return Issue.assignee_id.is_(None)
            return Issue.assignee_id == uuid.UUID(to_group_key)
        raise ValidationError("unsupported group_by for WIP count", details={"group_by": group_by})

    async def _count_group(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view: View,
        group_by: str,
        to_group_key: str,
        exclude_issue_id: uuid.UUID,
    ) -> int:
        conditions = [Issue.workspace_id == workspace_id, Issue.deleted_at.is_(None)]
        visibility = self._issues._base_visibility_clause(actor, workspace_id)
        if visibility is not None:
            conditions.append(visibility)
        if view.project_id is not None:
            conditions.append(Issue.project_id == view.project_id)
        filters_clause = compile_view_filters(view.filters)
        if filters_clause is not None:
            conditions.append(filters_clause)
        conditions.append(self._group_predicate(group_by, to_group_key))
        conditions.append(Issue.id != exclude_issue_id)
        return int(
            await session.scalar(select(func.count()).select_from(Issue).where(*conditions))
        )

    async def _rerank_if_exhausted(
        self,
        session: AsyncSession,
        *,
        view_id: uuid.UUID,
        group_key: str,
        position: float,
    ) -> None:
        """Re-space a whole column when the float midpoint precision is exhausted.

        If the new position lands within ``POSITION_EPSILON`` of an existing
        neighbor, reassign integer positions (1.0, 2.0, …) across the column in
        current order and broadcast ``issue.moved`` for each card (kanban §4.3).
        """
        neighbors = (
            await session.execute(
                select(ViewIssuePosition.position).where(
                    ViewIssuePosition.view_id == view_id,
                    ViewIssuePosition.group_key == group_key,
                )
            )
        ).scalars().all()
        # A 0/1-row column cannot be precision-exhausted; reranking a lone row
        # would collapse its legitimate position to 1.0.
        if len(neighbors) < 2:
            return
        if not any(abs(existing - position) < POSITION_EPSILON for existing in neighbors):
            return
        rows = (
            await session.execute(
                select(ViewIssuePosition)
                .where(
                    ViewIssuePosition.view_id == view_id,
                    ViewIssuePosition.group_key == group_key,
                )
                .order_by(ViewIssuePosition.position.asc(), ViewIssuePosition.id.asc())
            )
        ).scalars().all()
        for index, row in enumerate(rows, start=1):
            row.position = float(index)
            await session.flush()
            issue = await self._issues._load_issue(
                session, workspace_id=row.workspace_id, issue_id=row.issue_id
            )
            project = await self._issues._project_of(session, issue)
            await self._issues._emit_issue_event(
                session,
                issue=issue,
                event="issue.moved",
                data={
                    "id": str(issue.id),
                    "to": {"group_key": group_key},
                    "position": row.position,
                    "view_id": str(view_id),
                },
                project=project,
            )

    async def _upsert_position_tx(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        issue_id: uuid.UUID,
        group_key: str,
        position: float,
    ) -> None:
        stmt = pg_insert(ViewIssuePosition).values(
            workspace_id=workspace_id,
            view_id=view_id,
            issue_id=issue_id,
            group_key=group_key,
            position=position,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["view_id", "issue_id"],
            set_={
                "group_key": group_key,
                "position": position,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)


__all__ = ["BoardMoveService"]
