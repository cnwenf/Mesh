"""Bulk operations (issue.md §1.2.5 / §5.5).

Per-item SAVEPOINTs keep partial failures isolated: unauthorized or invalid
items land in ``errors`` (with ``issue_id`` + ``code`` + ``message``) while
the rest apply and commit in ONE transaction. Full success → HTTP 200 with
``failed=0``; any failure → 422 ``bulk_partial_failure`` carrying the counts.

A ``project_id`` change is a cross-project move and stays under the §3.8
two-step contract: without ``confirm: true`` the request is rejected with
``move_confirmation_required`` plus an aggregated preview; with confirmation
each item runs the same single-transaction move semantics as the explicit
move endpoint.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.member import Member
from mesh.errors import (
    BusinessRuleError,
    ForbiddenError,
    MeshError,
    NotFoundError,
    ValidationError,
)
from mesh.issue.move import MoveService, apply_move_plan, move_activity_rows
from mesh.issue.schemas import BulkRequest
from mesh.issue.service import (
    UNSET,
    IssuePatch,
    IssueService,
    _issue_channel,
    _now,
    _parse_uuid,
    _workspace_issues_channel,
)
from mesh.outbox.service import emit_realtime


class BulkService:
    """POST /issues/bulk (issue.md §3.1)."""

    def __init__(self, issue_service: IssueService, move_service: MoveService) -> None:
        self._issues = issue_service
        self._moves = move_service

    async def execute(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: BulkRequest,
    ) -> dict:
        has_project_change = body.changes is not None and body.changes.project_id is not None
        if has_project_change and not body.confirm:
            # §3.8 applies per issue: aggregate a preview and require confirm.
            previews = []
            factory = self._issues._factory
            async with factory() as session:
                from mesh.db.tenant import set_tenant_context

                await set_tenant_context(session, workspace_id)
                target = await self._moves._target_project(
                    session,
                    workspace_id=workspace_id,
                    target_project_id=_parse_uuid(
                        body.changes.project_id, field="project_id"
                    ),
                )
                if target is not None:
                    await self._issues._projects.assert_can_write(
                        session, viewer=actor, project=target
                    )
                for raw_id in body.issue_ids[:20]:
                    try:
                        issue = await self._issues._load_issue(
                            session, workspace_id=workspace_id, issue_id=uuid.UUID(raw_id)
                        )
                    except (ValueError, NotFoundError):
                        previews.append({"issue_id": raw_id, "error": "not_found"})
                        continue
                    # MES-46 H2: the preview carries each issue's field
                    # manifest, so every item is gated on the read permission
                    # exactly like GET /issues/{id} — invisible issues land
                    # as error markers (404→not_found for guests,
                    # 403→forbidden for members), NEVER as a plan.
                    try:
                        await self._issues.assert_can_view_issue(
                            session, viewer=actor, issue=issue
                        )
                    except ForbiddenError:
                        previews.append({"issue_id": raw_id, "error": "forbidden"})
                        continue
                    except NotFoundError:
                        previews.append({"issue_id": raw_id, "error": "not_found"})
                        continue
                    previews.append(
                        await self._moves.compute_plan(
                            session, workspace_id=workspace_id, issue=issue, target_project=target
                        )
                    )
            raise BusinessRuleError(
                "cross-project bulk move requires confirmation",
                code="move_confirmation_required",
                details={"previews": previews},
            )

        succeeded = 0
        errors: list[dict] = []
        factory = self._issues._factory
        async with factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            for raw_id in body.issue_ids:
                try:
                    issue_id = uuid.UUID(raw_id)
                except ValueError:
                    errors.append(
                        {"issue_id": raw_id, "code": "not_found", "message": "invalid issue id"}
                    )
                    continue
                try:
                    async with session.begin_nested():
                        await self._apply_one(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            issue_id=issue_id,
                            body=body,
                        )
                    succeeded += 1
                except MeshError as exc:
                    errors.append(
                        {"issue_id": str(issue_id), "code": exc.code, "message": exc.message}
                    )
        if errors:
            raise BusinessRuleError(
                "bulk operation partially failed",
                code="bulk_partial_failure",
                details={"succeeded": succeeded, "failed": len(errors), "errors": errors},
            )
        return {"succeeded": succeeded, "failed": 0, "errors": []}

    async def _apply_one(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        body: BulkRequest,
    ) -> None:
        issue = await self._issues._load_issue(
            session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
        )
        await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)

        if body.delete:
            project = await self._issues._project_of(session, issue)
            issue.deleted_at = _now(self._issues._clock)
            issue.updated_at = issue.deleted_at
            await session.flush()
            await self._issues._emit_issue_event(
                session,
                issue=issue,
                event="issue.deleted",
                data={"id": str(issue.id)},
                project=project,
            )
            return

        changes = body.changes
        if changes is None:
            raise ValidationError("either changes or delete is required")
        if changes.project_id is not None:
            target = await self._moves._target_project(
                session,
                workspace_id=workspace_id,
                target_project_id=_parse_uuid(changes.project_id, field="project_id"),
            )
            if target is not None:
                await self._issues._projects.assert_can_write(
                    session, viewer=actor, project=target
                )
            plan = await self._moves.compute_plan(
                session, workspace_id=workspace_id, issue=issue, target_project=target
            )
            from_project_id = issue.project_id
            source_project = await self._issues._project_of(session, issue)
            issue.project_id = target.id if target is not None else None
            now = _now(self._issues._clock)
            # Same single-transaction application semantics as the explicit
            # move (status mapping with completed_at sync, cleared private
            # fields — L3 parity) and the same §3.8 ⑥ audit trail with the
            # mapping/clearing manifest (M3/L2 parity).
            apply_move_plan(issue, plan, now=now)
            issue.version += 1
            issue.updated_at = now
            session.add_all(
                move_activity_rows(
                    workspace_id=workspace_id,
                    issue=issue,
                    actor=actor,
                    from_project_id=from_project_id,
                    plan=plan,
                )
            )
            await session.flush()
            payload = {
                "id": str(issue.id),
                "from_project_id": str(from_project_id)
                if from_project_id is not None
                else None,
                "to_project_id": plan["target_project_id"],
                "mapped_fields": plan["mapped_fields"],
                "cleared_fields": plan["cleared_fields"],
                "version": issue.version,
            }
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(issue.id),
                event="issue.project_changed",
                data=payload,
            )
            if target is None or target.visibility == "public":
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_workspace_issues_channel(workspace_id),
                    event="issue.project_changed",
                    data=payload,
                )
            source_public = source_project is None or source_project.visibility == "public"
            target_public = target is None or target.visibility == "public"
            if source_public and not target_public:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_workspace_issues_channel(workspace_id),
                    event="issue.deleted",
                    data={"id": str(issue.id)},
                )
            return

        patch = IssuePatch(
            status_id=_parse_uuid(changes.status_id, field="status_id")
            if changes.status_id
            else UNSET,
            priority=changes.priority if changes.priority is not None else UNSET,
            assignee_id=(
                _parse_uuid(changes.assignee_id, field="assignee_id")
                if changes.assignee_id
                else None
            )
            if changes.assignee_id is not None
            else UNSET,
            cycle_id=_parse_uuid(changes.cycle_id, field="cycle_id")
            if changes.cycle_id is not None
            else UNSET,
        )
        await self._issues.apply_changes_in_tx(session, actor=actor, issue=issue, patch=patch)


__all__ = ["BulkService"]
