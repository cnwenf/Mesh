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

from mesh.auth.rbac import assert_scope, role_satisfies
from mesh.db.models.issue import Issue
from mesh.db.models.view import View
from mesh.db.models.view_position import ViewIssuePosition, ViewQuickCreateRequest
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssuePatch
from mesh.issue.statuses import resolve_default_status, resolve_status_in_scope
from mesh.issue.triggers import apply_assign_triggers
from mesh.outbox.service import emit_realtime
from mesh.views.config import PRIORITY_KEYS, STATE_CATEGORY_KEYS, validate_group_axes
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
_SUPPORTED_GROUP_BY = frozenset({"state_category", "status", "assignee", "priority", "project"})
# Floating-point midpoint precision floor — below this a column re-ranks (kanban §4.3).
POSITION_EPSILON = 1e-6
IDEMPOTENCY_KEY_MAX_LENGTH = 255


def _view_channel(view_id: uuid.UUID) -> str:
    return f"view:{view_id}"


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValidationError(
            "invalid Idempotency-Key",
            details={"field": "Idempotency-Key"},
        )
    return normalized


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

    async def _load_view(self, session: AsyncSession, *, workspace_id: uuid.UUID, view_id: uuid.UUID) -> View:
        view = await session.scalar(select(View).where(View.id == view_id, View.workspace_id == workspace_id))
        if view is None:
            raise NotFoundError(_VIEW_NOT_FOUND)
        return view

    async def _load_view_for_update(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, view_id: uuid.UUID
    ) -> View:
        view = await session.scalar(
            select(View).where(View.id == view_id, View.workspace_id == workspace_id).with_for_update()
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
        to_sub_group_key: str | None = None,
        position: float,
        version: int | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Move a card to a primary column and, optionally, a swimlane.

        The lightweight preflight below exists only to select the mandatory
        cross-project confirmation path. Every mutating path reloads and locks
        the view/issue in its own transaction before applying anything.
        """
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id)
            await self._views.assert_can_read(session, viewer=actor, view=view)
            group_by = view.group_by or "state_category"
            sub_group_by = view.sub_group_by
            self._validate_axes_and_shape(
                view=view,
                group_by=group_by,
                sub_group_by=sub_group_by,
                sub_group_key=to_sub_group_key,
            )
            issue = await self._issues._load_issue(session, workspace_id=workspace_id, issue_id=issue_id)
            await self._issues.assert_can_view_issue(session, viewer=actor, issue=issue)
            target_sub_group_key = (
                group_key_for(sub_group_by, issue)
                if sub_group_by is not None and to_sub_group_key is None
                else to_sub_group_key
            )
            target_project_id = self._target_project_id(
                view=view,
                issue=issue,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=to_group_key,
                sub_group_key=target_sub_group_key,
                creating=False,
            )
            project_changed = target_project_id != issue.project_id

        if project_changed or (dry_run and "project" in {group_by, sub_group_by}):
            return await self._move_project_cell(
                actor=actor,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                to_group_key=to_group_key,
                to_sub_group_key=to_sub_group_key,
                position=position,
                version=version,
                confirm=confirm,
                dry_run=dry_run,
            )
        return await self._move_intra(
            actor=actor,
            workspace_id=workspace_id,
            view_id=view_id,
            issue_id=issue_id,
            to_group_key=to_group_key,
            to_sub_group_key=to_sub_group_key,
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
        to_group_key: str,
        to_sub_group_key: str | None,
        position: float,
        version: int | None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view_for_update(session, workspace_id=workspace_id, view_id=view_id)
            await self._views.assert_can_read(session, viewer=actor, view=view)
            if not role_satisfies(actor.role, "issue:write"):
                raise ForbiddenError("insufficient role for this action")
            assert_scope(actor, "issue:write")
            group_by = view.group_by or "state_category"
            sub_group_by = view.sub_group_by
            self._validate_axes_and_shape(
                view=view,
                group_by=group_by,
                sub_group_by=sub_group_by,
                sub_group_key=to_sub_group_key,
            )
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
            from_sub_group_key = group_key_for(sub_group_by, issue) if sub_group_by is not None else ""
            target_sub_group_key = from_sub_group_key if to_sub_group_key is None else to_sub_group_key
            target_project_id = self._target_project_id(
                view=view,
                issue=issue,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=to_group_key,
                sub_group_key=target_sub_group_key,
                creating=False,
            )
            if target_project_id != issue.project_id:
                # A concurrent issue/config change made the preflight stale.
                # Never fall through to a bare project_id write.
                raise ConflictError(
                    "move target changed; preview the cross-project move again",
                    code="conflict",
                    details={"id": str(issue.id), "current_version": issue.version},
                )

            wip, count, wip_exceeded = await self._enforce_wip_if_entering(
                session,
                actor=actor,
                workspace_id=workspace_id,
                view=view,
                group_by=group_by,
                from_group_key=from_group_key,
                to_group_key=to_group_key,
                exclude_issue_id=issue_id,
            )

            patch = await self._cell_patch(
                session,
                view=view,
                issue=issue,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=to_group_key,
                sub_group_key=target_sub_group_key,
                target_project_id=target_project_id,
            )
            rendered, changes = await self._issues.apply_changes_in_tx(
                session, actor=actor, issue=issue, patch=patch
            )
            final_group_key = group_key_for(group_by, issue)
            final_sub_group_key = group_key_for(sub_group_by, issue) if sub_group_by is not None else ""
            if final_group_key != to_group_key or (
                sub_group_by is not None and final_sub_group_key != target_sub_group_key
            ):
                raise BusinessRuleError(
                    "target values do not form the requested projection cell",
                    code="incompatible_projection_cell",
                    details={"group_key": to_group_key},
                )

            project = await self._issues._project_of(session, issue)
            moved_data: dict[str, Any] = {
                "id": str(issue.id),
                "from": {"group_key": from_group_key},
                "to": {"group_key": final_group_key},
                "position": position,
                "view_id": str(view_id),
            }
            if sub_group_by is not None:
                moved_data.update(
                    {
                        "from_sub_group": from_sub_group_key,
                        "to_sub_group": final_sub_group_key,
                    }
                )
            moved_event = await self._issues._emit_issue_event(
                session,
                issue=issue,
                event="issue.moved",
                data=moved_data,
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
            await self._upsert_position_tx(
                session,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                group_key=final_group_key,
                sub_group_key=final_sub_group_key,
                position=position,
            )
            await self._rerank_if_exhausted(
                session,
                view_id=view_id,
                group_key=final_group_key,
                sub_group_key=final_sub_group_key,
                position=position,
                moved_issue_id=issue_id,
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

    async def _move_project_cell(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        issue_id: uuid.UUID,
        to_group_key: str,
        to_sub_group_key: str | None,
        position: float,
        version: int | None,
        confirm: bool,
        dry_run: bool,
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            view = await self._load_view(session, workspace_id=workspace_id, view_id=view_id)
            await self._views.assert_can_read(session, viewer=actor, view=view)
            group_by = view.group_by or "state_category"
            sub_group_by = view.sub_group_by
            self._validate_axes_and_shape(
                view=view,
                group_by=group_by,
                sub_group_by=sub_group_by,
                sub_group_key=to_sub_group_key,
            )
            issue = await self._issues._load_issue(session, workspace_id=workspace_id, issue_id=issue_id)
            await self._issues.assert_can_view_issue(session, viewer=actor, issue=issue)
            target_sub_group_key = (
                group_key_for(sub_group_by, issue)
                if sub_group_by is not None and to_sub_group_key is None
                else to_sub_group_key
            )
            target_project_id = self._target_project_id(
                view=view,
                issue=issue,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=to_group_key,
                sub_group_key=target_sub_group_key,
                creating=False,
            )

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

        if version is None:
            raise ValidationError(
                "move requires the current version",
                details={"field": "version", "hint": "echo preview.version back"},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view_for_update(session, workspace_id=workspace_id, view_id=view_id)
            await self._views.assert_can_read(session, viewer=actor, view=view)
            group_by = view.group_by or "state_category"
            sub_group_by = view.sub_group_by
            self._validate_axes_and_shape(
                view=view,
                group_by=group_by,
                sub_group_by=sub_group_by,
                sub_group_key=to_sub_group_key,
            )
            before = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=before)
            from_group_key = group_key_for(group_by, before)
            from_sub_group_key = group_key_for(sub_group_by, before) if sub_group_by is not None else ""
            target_sub_group_key = from_sub_group_key if to_sub_group_key is None else to_sub_group_key
            target_project_id = self._target_project_id(
                view=view,
                issue=before,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=to_group_key,
                sub_group_key=target_sub_group_key,
                creating=False,
            )
            wip, count, wip_exceeded = await self._enforce_wip_if_entering(
                session,
                actor=actor,
                workspace_id=workspace_id,
                view=view,
                group_by=group_by,
                from_group_key=from_group_key,
                to_group_key=to_group_key,
                exclude_issue_id=issue_id,
            )
            rendered, plan = await self._moves.apply_confirmed_move_in_session(
                session,
                actor=actor,
                workspace_id=workspace_id,
                issue_id=issue_id,
                target_project_id=target_project_id,
                expected_version=version,
            )
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            patch = await self._cell_patch(
                session,
                view=view,
                issue=issue,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=to_group_key,
                sub_group_key=target_sub_group_key,
                target_project_id=target_project_id,
            )
            rendered, changes = await self._issues.apply_changes_in_tx(
                session, actor=actor, issue=issue, patch=patch
            )
            final_group_key = group_key_for(group_by, issue)
            final_sub_group_key = group_key_for(sub_group_by, issue) if sub_group_by is not None else ""
            if final_group_key != to_group_key or (
                sub_group_by is not None and final_sub_group_key != target_sub_group_key
            ):
                raise BusinessRuleError(
                    "target values do not form the requested projection cell",
                    code="incompatible_projection_cell",
                    details={"group_key": to_group_key},
                )
            await self._upsert_position_tx(
                session,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                group_key=final_group_key,
                sub_group_key=final_sub_group_key,
                position=position,
            )
            await self._rerank_if_exhausted(
                session,
                view_id=view_id,
                group_key=final_group_key,
                sub_group_key=final_sub_group_key,
                position=position,
                moved_issue_id=issue_id,
            )
            project = await self._issues._project_of(session, issue)
            moved_data: dict[str, Any] = {
                "id": str(issue.id),
                "from": {"group_key": from_group_key},
                "to": {"group_key": final_group_key},
                "position": position,
                "view_id": str(view_id),
            }
            if sub_group_by is not None:
                moved_data.update(
                    {
                        "from_sub_group": from_sub_group_key,
                        "to_sub_group": final_sub_group_key,
                    }
                )
            moved_event = await self._issues._emit_issue_event(
                session,
                issue=issue,
                event="issue.moved",
                data=moved_data,
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
        rendered["move_result"] = {
            "mapped_fields": plan.get("mapped_fields", []),
            "cleared_fields": plan.get("cleared_fields", []),
        }
        rendered["position"] = position
        return rendered

    # ------------------------------------------------------------------
    # quick-create (view-scoped, atomic)
    # ------------------------------------------------------------------

    async def quick_create(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view_id: uuid.UUID,
        title: str,
        group_key: str,
        sub_group_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        normalized_idempotency_key = _normalize_idempotency_key(idempotency_key)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view_for_update(session, workspace_id=workspace_id, view_id=view_id)
            await self._views.assert_can_read(session, viewer=actor, view=view)
            if not role_satisfies(actor.role, "issue:write"):
                raise ForbiddenError("insufficient role for this action")
            assert_scope(actor, "issue:write")
            if normalized_idempotency_key is not None:
                replay = await session.scalar(
                    select(ViewQuickCreateRequest).where(
                        ViewQuickCreateRequest.workspace_id == workspace_id,
                        ViewQuickCreateRequest.view_id == view_id,
                        ViewQuickCreateRequest.actor_member_id == actor.id,
                        ViewQuickCreateRequest.idempotency_key == normalized_idempotency_key,
                    )
                )
                if replay is not None:
                    issue = await self._issues._load_issue(
                        session,
                        workspace_id=workspace_id,
                        issue_id=replay.issue_id,
                    )
                    await self._issues.assert_can_view_issue(session, viewer=actor, issue=issue)
                    rendered = await self._issues.render_issue(session, issue)
                    stored_position = await session.scalar(
                        select(ViewIssuePosition.position).where(
                            ViewIssuePosition.view_id == view_id,
                            ViewIssuePosition.issue_id == issue.id,
                        )
                    )
                    rendered["position"] = (
                        float(stored_position) if stored_position is not None else float(issue.position)
                    )
                    return rendered
            group_by = view.group_by or "state_category"
            sub_group_by = view.sub_group_by
            self._validate_axes_and_shape(
                view=view,
                group_by=group_by,
                sub_group_by=sub_group_by,
                sub_group_key=sub_group_key,
                require_sub_group_key=True,
            )
            target_project_id = self._target_project_id(
                view=view,
                issue=None,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=group_key,
                sub_group_key=sub_group_key,
                creating=True,
            )
            body = await self._create_body_for_cell(
                session,
                view=view,
                title=title,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=group_key,
                sub_group_key=sub_group_key,
                target_project_id=target_project_id,
            )

            # Quick-create always adds a primary-column member, so it shares
            # the exact same advisory-lock namespace as drag WIP enforcement.
            await self._lock_wip_column(session, view_id=view_id, group_key=group_key)
            wip = (view.board_settings or {}).get("wip", {}).get(group_key)
            count = 0
            if wip is not None:
                count = await self._count_group(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    view=view,
                    group_by=group_by,
                    to_group_key=group_key,
                    exclude_issue_id=uuid.UUID(int=0),
                )
                if count >= wip["limit"] and wip["enforcement"] == "block":
                    raise BusinessRuleError(
                        "target column is at its WIP limit",
                        code="wip_limit_exceeded",
                        details={
                            "group_key": group_key,
                            "limit": wip["limit"],
                            "count": count,
                        },
                    )

            rendered = await self._issues.create_issue_in_session(
                session, actor=actor, workspace_id=workspace_id, body=body
            )
            issue_id = uuid.UUID(rendered["id"])
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            if not await self._candidate_matches_view(
                session,
                actor=actor,
                view=view,
                issue=issue,
                group_by=group_by,
                sub_group_by=sub_group_by,
                group_key=group_key,
                sub_group_key=sub_group_key,
            ):
                raise BusinessRuleError(
                    "created values do not match the locked view filters",
                    code="quick_create_filter_mismatch",
                    details={"unmet_filter_fields": self._filter_fields(view.filters)},
                )
            actual_sub_group_key = sub_group_key or ""
            await self._upsert_position_tx(
                session,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                group_key=group_key,
                sub_group_key=actual_sub_group_key,
                position=float(issue.position),
            )
            if wip is not None and count >= wip["limit"]:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_view_channel(view_id),
                    event="view.wip_exceeded",
                    data={
                        "view_id": str(view_id),
                        "group_key": group_key,
                        "limit": wip["limit"],
                        "count": count + 1,
                    },
                )
            if normalized_idempotency_key is not None:
                session.add(
                    ViewQuickCreateRequest(
                        workspace_id=workspace_id,
                        view_id=view_id,
                        actor_member_id=actor.id,
                        issue_id=issue_id,
                        idempotency_key=normalized_idempotency_key,
                    )
                )
            rendered["position"] = float(issue.position)
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
        sub_group_key: str | None = None,
        position: float,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            view = await self._load_view_for_update(session, workspace_id=workspace_id, view_id=view_id)
            await self._views.assert_can_read(session, viewer=actor, view=view)
            group_by = view.group_by or "state_category"
            sub_group_by = view.sub_group_by
            self._validate_axes_and_shape(
                view=view,
                group_by=group_by,
                sub_group_by=sub_group_by,
                sub_group_key=sub_group_key,
                require_sub_group_key=True,
            )
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            expected_group_key = group_key_for(group_by, issue)
            expected_sub_group_key = group_key_for(sub_group_by, issue) if sub_group_by is not None else ""
            if to_group_key != expected_group_key or (
                sub_group_by is not None and sub_group_key != expected_sub_group_key
            ):
                raise BusinessRuleError(
                    "reorder target must match the issue's current projection cell",
                    code="incompatible_projection_cell",
                    details={"group_key": to_group_key},
                )
            # Upsert first so the exhaustion check / rerank includes this card.
            await self._upsert_position_tx(
                session,
                workspace_id=workspace_id,
                view_id=view_id,
                issue_id=issue_id,
                group_key=to_group_key,
                sub_group_key=expected_sub_group_key,
                position=position,
            )
            await self._rerank_if_exhausted(
                session,
                view_id=view_id,
                group_key=to_group_key,
                sub_group_key=expected_sub_group_key,
                position=position,
                moved_issue_id=issue_id,
            )
            project = await self._issues._project_of(session, issue)
            moved_data: dict[str, Any] = {
                "id": str(issue.id),
                "from": {"group_key": to_group_key},
                "to": {"group_key": to_group_key},
                "position": position,
                "view_id": str(view_id),
            }
            if sub_group_by is not None:
                moved_data.update(
                    {
                        "from_sub_group": expected_sub_group_key,
                        "to_sub_group": expected_sub_group_key,
                    }
                )
            await self._issues._emit_issue_event(
                session,
                issue=issue,
                event="issue.moved",
                data=moved_data,
                project=project,
            )
        result = {"id": str(issue_id), "group_key": to_group_key, "position": position}
        if sub_group_by is not None:
            result["sub_group_key"] = expected_sub_group_key
        return result

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _validate_axes_and_shape(
        self,
        *,
        view: View,
        group_by: str,
        sub_group_by: str | None,
        sub_group_key: str | None,
        require_sub_group_key: bool = False,
    ) -> None:
        if view.layout != "board":
            raise ValidationError("cell commands require a board view", details={"layout": view.layout})
        validate_group_axes(view.group_by, sub_group_by)
        for field_name, axis in (("group_by", group_by), ("sub_group_by", sub_group_by)):
            if axis is not None and axis not in _SUPPORTED_GROUP_BY:
                raise ValidationError(
                    f"{field_name}=label/custom-field moves await the association increment",
                    code=PROJECTION_FIELD_PENDING,
                    details={field_name: axis},
                )
        if sub_group_by is None and sub_group_key is not None:
            raise ValidationError(
                "sub_group_key is not valid for a one-dimensional view",
                details={"field": "sub_group_key"},
            )
        if require_sub_group_key and sub_group_by is not None and sub_group_key is None:
            raise ValidationError(
                "sub_group_key is required for a swimlane cell",
                details={"field": "sub_group_key"},
            )

    @staticmethod
    def _parse_uuid_key(raw: str, *, field: str) -> uuid.UUID:
        try:
            return uuid.UUID(raw)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid {field}", details={field: str(raw)[:64]}) from exc

    def _target_project_id(
        self,
        *,
        view: View,
        issue: Issue | None,
        group_by: str,
        sub_group_by: str | None,
        group_key: str,
        sub_group_key: str | None,
        creating: bool,
    ) -> uuid.UUID | None:
        project_key: str | None = None
        if group_by == "project":
            project_key = group_key
        elif sub_group_by == "project":
            project_key = sub_group_key
        if project_key is not None:
            if project_key in _NONE_KEYS:
                return None
            return self._parse_uuid_key(project_key, field="project_key")
        if view.project_id is not None:
            return view.project_id
        if not creating and issue is not None:
            return issue.project_id
        return None

    @staticmethod
    def _axis_targets(
        *,
        group_by: str,
        sub_group_by: str | None,
        group_key: str,
        sub_group_key: str | None,
    ) -> dict[str, str]:
        targets = {group_by: group_key}
        if sub_group_by is not None and sub_group_key is not None:
            targets[sub_group_by] = sub_group_key
        return targets

    async def _resolved_status_id(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        target_project_id: uuid.UUID | None,
        targets: dict[str, str],
    ) -> uuid.UUID | None:
        category = targets.get("state_category")
        if category is not None and category not in STATE_CATEGORY_KEYS:
            raise ValidationError("invalid state category", details={"state_category": category})
        status_key = targets.get("status")
        if status_key is not None:
            status_id = self._parse_uuid_key(status_key, field="status_key")
            try:
                status = await resolve_status_in_scope(
                    session,
                    workspace_id=workspace_id,
                    project_id=target_project_id,
                    status_id=status_id,
                )
            except NotFoundError as exc:
                raise BusinessRuleError(
                    "status is not available in the target project",
                    code="incompatible_projection_cell",
                    details={"field": "status"},
                ) from exc
            if category is not None and status.category != category:
                raise BusinessRuleError(
                    "status and category do not form a projection cell",
                    code="incompatible_projection_cell",
                    details={"field": "status"},
                )
            return status.id
        if category is not None:
            status = await resolve_default_status(
                session,
                workspace_id=workspace_id,
                project_id=target_project_id,
                category=category,
            )
            return status.id
        return None

    async def _cell_patch(
        self,
        session: AsyncSession,
        *,
        view: View,
        issue: Issue,
        group_by: str,
        sub_group_by: str | None,
        group_key: str,
        sub_group_key: str | None,
        target_project_id: uuid.UUID | None,
    ) -> IssuePatch:
        if issue.project_id != target_project_id:
            raise ValidationError("project migration must complete before applying the cell")
        targets = self._axis_targets(
            group_by=group_by,
            sub_group_by=sub_group_by,
            group_key=group_key,
            sub_group_key=sub_group_key,
        )
        values: dict[str, Any] = {}
        status_id = await self._resolved_status_id(
            session,
            workspace_id=view.workspace_id,
            target_project_id=target_project_id,
            targets=targets,
        )
        if status_id is not None:
            values["status_id"] = status_id
        if "priority" in targets:
            priority = targets["priority"]
            if priority not in PRIORITY_KEYS:
                raise ValidationError("invalid priority", details={"priority": priority})
            values["priority"] = priority
        if "assignee" in targets:
            assignee_key = targets["assignee"]
            values["assignee_id"] = (
                None
                if assignee_key in _NONE_KEYS
                else self._parse_uuid_key(assignee_key, field="assignee_key")
            )
        return IssuePatch(**values)

    async def _create_body_for_cell(
        self,
        session: AsyncSession,
        *,
        view: View,
        title: str,
        group_by: str,
        sub_group_by: str | None,
        group_key: str,
        sub_group_key: str | None,
        target_project_id: uuid.UUID | None,
    ) -> CreateIssueRequest:
        targets = self._axis_targets(
            group_by=group_by,
            sub_group_by=sub_group_by,
            group_key=group_key,
            sub_group_key=sub_group_key,
        )
        status_id = await self._resolved_status_id(
            session,
            workspace_id=view.workspace_id,
            target_project_id=target_project_id,
            targets=targets,
        )
        priority = targets.get("priority", "none")
        if priority not in PRIORITY_KEYS:
            raise ValidationError("invalid priority", details={"priority": priority})
        assignee_id: str | None = None
        if "assignee" in targets and targets["assignee"] not in _NONE_KEYS:
            assignee_id = str(self._parse_uuid_key(targets["assignee"], field="assignee_key"))
        return CreateIssueRequest(
            title=title,
            project_id=str(target_project_id) if target_project_id is not None else None,
            status_id=str(status_id) if status_id is not None else None,
            priority=priority,
            assignee_id=assignee_id,
        )

    async def _candidate_matches_view(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        view: View,
        issue: Issue,
        group_by: str,
        sub_group_by: str | None,
        group_key: str,
        sub_group_key: str | None,
    ) -> bool:
        conditions = [
            Issue.id == issue.id,
            Issue.workspace_id == view.workspace_id,
            Issue.deleted_at.is_(None),
        ]
        visibility = self._issues._base_visibility_clause(actor, view.workspace_id)
        if visibility is not None:
            conditions.append(visibility)
        if view.project_id is not None:
            conditions.append(Issue.project_id == view.project_id)
        filters_clause = compile_view_filters(view.filters)
        if filters_clause is not None:
            conditions.append(filters_clause)
        matched = await session.scalar(select(Issue.id).where(*conditions))
        if matched is None or group_key_for(group_by, issue) != group_key:
            return False
        if sub_group_by is not None:
            return group_key_for(sub_group_by, issue) == sub_group_key
        return True

    @staticmethod
    def _filter_fields(filters: dict | None) -> list[str]:
        fields: set[str] = set()

        def walk(node: Any) -> None:
            if not isinstance(node, dict):
                return
            field = node.get("field")
            if isinstance(field, str):
                fields.add(field)
            for child in node.get("conditions", []):
                walk(child)

        walk(filters)
        return sorted(fields)

    async def _lock_wip_column(self, session: AsyncSession, *, view_id: uuid.UUID, group_key: str) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('wip:' || :view_id || ':' || :group_key))"),
            {"view_id": str(view_id), "group_key": group_key},
        )

    async def _enforce_wip_if_entering(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        view: View,
        group_by: str,
        from_group_key: str,
        to_group_key: str,
        exclude_issue_id: uuid.UUID,
    ) -> tuple[dict | None, int, bool]:
        wip = (view.board_settings or {}).get("wip", {}).get(to_group_key)
        if from_group_key == to_group_key:
            return wip, 0, False
        await self._lock_wip_column(session, view_id=view.id, group_key=to_group_key)
        if wip is None:
            return None, 0, False
        count = await self._count_group(
            session,
            actor=actor,
            workspace_id=workspace_id,
            view=view,
            group_by=group_by,
            to_group_key=to_group_key,
            exclude_issue_id=exclude_issue_id,
        )
        if count < wip["limit"]:
            return wip, count, False
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
        return wip, count, True

    def _group_predicate(self, group_by: str, to_group_key: str) -> Any:
        if group_by == "state_category":
            if to_group_key not in STATE_CATEGORY_KEYS:
                raise ValidationError("invalid state category", details={"state_category": to_group_key})
            return Issue.state_category == to_group_key
        if group_by == "status":
            return Issue.status_id == self._parse_uuid_key(to_group_key, field="status_key")
        if group_by == "priority":
            if to_group_key not in PRIORITY_KEYS:
                raise ValidationError("invalid priority", details={"priority": to_group_key})
            return Issue.priority == to_group_key
        if group_by == "assignee":
            if to_group_key in _NONE_KEYS:
                return Issue.assignee_id.is_(None)
            return Issue.assignee_id == self._parse_uuid_key(to_group_key, field="assignee_key")
        if group_by == "project":
            if to_group_key in _NONE_KEYS:
                return Issue.project_id.is_(None)
            return Issue.project_id == self._parse_uuid_key(to_group_key, field="project_key")
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
        return int(await session.scalar(select(func.count()).select_from(Issue).where(*conditions)))

    async def _rerank_if_exhausted(
        self,
        session: AsyncSession,
        *,
        view_id: uuid.UUID,
        group_key: str,
        sub_group_key: str,
        position: float,
        moved_issue_id: uuid.UUID,
    ) -> None:
        """Re-space one cell when float midpoint precision is exhausted.

        The moved row itself is excluded from collision detection; otherwise
        every insertion would compare equal to itself and spuriously rerank.
        Stale rows whose stored cell no longer matches issue fields are also
        ignored, preserving the §2.7 fail-closed ordering rule.
        """
        view = await session.scalar(select(View).where(View.id == view_id))
        if view is None:
            raise NotFoundError(_VIEW_NOT_FOUND)
        group_by = view.group_by or "state_category"
        sub_group_by = view.sub_group_by
        joined_rows = (
            await session.execute(
                select(ViewIssuePosition, Issue)
                .join(Issue, Issue.id == ViewIssuePosition.issue_id)
                .where(
                    ViewIssuePosition.view_id == view_id,
                    ViewIssuePosition.group_key == group_key,
                    ViewIssuePosition.sub_group_key == sub_group_key,
                    Issue.deleted_at.is_(None),
                )
            )
        ).all()
        valid_rows = [
            (row, issue)
            for row, issue in joined_rows
            if group_key_for(group_by, issue) == group_key
            and (sub_group_by is None or group_key_for(sub_group_by, issue) == sub_group_key)
        ]
        collides = any(
            row.issue_id != moved_issue_id and abs(float(row.position) - position) < POSITION_EPSILON
            for row, _issue in valid_rows
        )
        if not collides:
            return
        valid_rows.sort(key=lambda item: (item[0].position, item[0].id))
        for index, (row, _issue) in enumerate(valid_rows, start=1):
            row.position = float(index)
        await session.flush()
        for row, issue in valid_rows:
            project = await self._issues._project_of(session, issue)
            moved_data: dict[str, Any] = {
                "id": str(issue.id),
                "from": {"group_key": group_key},
                "to": {"group_key": group_key},
                "position": row.position,
                "view_id": str(view_id),
            }
            if sub_group_by is not None:
                moved_data.update(
                    {
                        "from_sub_group": sub_group_key,
                        "to_sub_group": sub_group_key,
                    }
                )
            await self._issues._emit_issue_event(
                session,
                issue=issue,
                event="issue.moved",
                data=moved_data,
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
        sub_group_key: str,
        position: float,
    ) -> None:
        stmt = pg_insert(ViewIssuePosition).values(
            workspace_id=workspace_id,
            view_id=view_id,
            issue_id=issue_id,
            group_key=group_key,
            sub_group_key=sub_group_key,
            position=position,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["view_id", "issue_id"],
            set_={
                "group_key": group_key,
                "sub_group_key": sub_group_key,
                "position": position,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)


__all__ = ["BoardMoveService"]
