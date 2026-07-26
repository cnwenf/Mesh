"""Cross-project move — the two-step preview→confirm contract (issue.md §3.8).

Step 1 (``move-preview``) computes the mapped/cleared/kept field plan; step 2
(``move`` with ``confirm: true``) applies it in a SINGLE transaction:
optimistic-lock check → ``project_id`` change → status mapping → clear
project-private fields → version bump → activity trail → outbox
``issue.project_changed`` carrying the plan (README §6.6/§6.7, §9 T22).

Immutable numbering (README §6.3 / §9 T19): the move ONLY touches
``project_id`` — ``identifier_namespace_key``/``number``/``identifier`` are
never renumbered, so moving ``WEB-1`` into a project that already has
``APP-1`` violates nothing.

Status mapping: a project-PRIVATE current status maps to the target scope's
SAME-CATEGORY default (fallback: lowest position in that category, §3.8);
workspace-level statuses are visible everywhere and are KEPT. Project-private
milestones and project-bound cycles are cleared; workspace-level cycles stay.
Labels / custom-field values are owned by the label-property.md increment
(MES-32); until those tables exist there is nothing to clear — the
``label_module_pending`` / ``custom_field_module_pending`` skip markers keep
the contract honest.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.issue import Issue, IssueActivity
from mesh.db.models.member import Member
from mesh.db.models.project import Cycle, Milestone, Project
from mesh.errors import BusinessRuleError, ConflictError
from mesh.issue.service import (
    IssueService,
    _isoformat,
    _issue_channel,
    _workspace_issues_channel,
)
from mesh.issue.statuses import render_status, resolve_default_status
from mesh.outbox.service import emit_realtime

MOVE_NOT_CONFIRMED = "move requires confirmation"


def _jsonify_status(rendered: dict) -> dict:
    """Make a rendered status plain-JSON-safe (the move plan travels inside
    error ``details`` envelopes, which the §6.14 handler serializes without
    the FastAPI encoder)."""
    return {
        **rendered,
        "created_at": _isoformat(rendered.get("created_at")),
        "updated_at": _isoformat(rendered.get("updated_at")),
    }

KEPT_FIELDS = [
    "title",
    "description",
    "priority",
    "assignee_id",
    "reporter_id",
    "estimate",
    "estimate_unit",
    "due_date",
    "start_date",
    "identifier",
    "工作区级 labels",
    "工作区级自定义字段值",
]


class MoveService:
    """Cross-project issue moves (issue.md §3.8)."""

    def __init__(self, issue_service: IssueService) -> None:
        self._issues = issue_service

    async def _target_project(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        target_project_id: uuid.UUID | None,
    ) -> Project | None:
        if target_project_id is None:
            return None  # moving to the workspace inbox
        return await self._issues._projects._load_project(
            session, workspace_id=workspace_id, project_id=target_project_id
        )

    async def compute_plan(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        issue: Issue,
        target_project: Project | None,
    ) -> dict:
        """The mapped/cleared/kept plan shared by preview and move."""
        from mesh.db.models.issue import IssueStatus

        mapped_fields: list[dict] = []
        status = await session.scalar(
            select(IssueStatus).where(IssueStatus.id == issue.status_id)
        )
        if (
            status is not None
            and status.project_id is not None
            and status.project_id == issue.project_id
            and status.project_id != (target_project.id if target_project else None)
        ):
            new_status = await resolve_default_status(
                session,
                workspace_id=workspace_id,
                project_id=target_project.id if target_project is not None else None,
                category=status.category,
            )
            mapped_fields.append(
                {
                    "field": "status",
                    "from": _jsonify_status(render_status(status)),
                    "to": _jsonify_status(render_status(new_status)),
                    "reason": "项目私有 status → 目标项目同 category 默认 status",
                }
            )

        cleared_fields: list[dict] = []
        if issue.milestone_id is not None:
            milestone = await session.scalar(
                select(Milestone).where(Milestone.id == issue.milestone_id)
            )
            if milestone is not None:
                cleared_fields.append(
                    {
                        "field": "milestone_id",
                        "items": [{"id": str(milestone.id), "title": milestone.title}],
                        "reason": "项目私有里程碑",
                    }
                )
        if issue.cycle_id is not None:
            cycle = await session.scalar(select(Cycle).where(Cycle.id == issue.cycle_id))
            if cycle is not None and cycle.project_id is not None:
                cleared_fields.append(
                    {
                        "field": "cycle_id",
                        "items": [{"id": str(cycle.id), "name": cycle.name}],
                        "reason": "项目绑定的周期",
                    }
                )
        # Labels / custom fields clear once label-property.md (MES-32) lands.
        skipped_modules = [
            {"field": "labels", "reason": "label_module_pending"},
            {"field": "custom_field_values", "reason": "custom_field_module_pending"},
        ]

        return {
            "issue_id": str(issue.id),
            "identifier": issue.identifier,
            "from_project_id": str(issue.project_id) if issue.project_id is not None else None,
            "target_project_id": str(target_project.id) if target_project else None,
            "mapped_fields": mapped_fields,
            "cleared_fields": cleared_fields,
            "kept_fields": list(KEPT_FIELDS),
            "skipped_modules": skipped_modules,
        }

    async def preview(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        target_project_id: uuid.UUID | None,
    ) -> dict:
        factory = self._issues._factory
        async with factory() as session:
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id
            )
            await self._issues.assert_can_view_issue(session, viewer=viewer, issue=issue)
            target = await self._target_project(
                session, workspace_id=workspace_id, target_project_id=target_project_id
            )
            if target is not None:
                await self._issues._projects.assert_can_write(
                    session, viewer=viewer, project=target
                )
            return await self.compute_plan(
                session, workspace_id=workspace_id, issue=issue, target_project=target
            )

    async def move(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        target_project_id: uuid.UUID | None,
        confirm: bool,
        expected_version: int | None = None,
    ) -> dict:
        factory = self._issues._factory
        async with factory() as session:
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id
            )
            target = await self._target_project(
                session, workspace_id=workspace_id, target_project_id=target_project_id
            )
            if not confirm:
                plan = await self.compute_plan(
                    session, workspace_id=workspace_id, issue=issue, target_project=target
                )
                raise BusinessRuleError(
                    MOVE_NOT_CONFIRMED,
                    code="move_confirmation_required",
                    details={"preview": plan},
                )
        async with factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace_id)
            issue = await self._issues._load_issue(
                session, workspace_id=workspace_id, issue_id=issue_id, for_update=True
            )
            await self._issues.assert_can_write_issue(session, actor=actor, issue=issue)
            target = await self._target_project(
                session, workspace_id=workspace_id, target_project_id=target_project_id
            )
            if target is not None:
                await self._issues._projects.assert_can_write(
                    session, viewer=actor, project=target
                )
            if (
                expected_version is not None
                and issue.version != expected_version
            ):
                raise ConflictError(
                    "issue was modified concurrently",
                    code="conflict",
                    details={"id": str(issue.id), "current_version": issue.version},
                )
            if issue.project_id == (target.id if target is not None else None):
                # Already there — no-op (identical destination).
                return await self._issues.render_issue(session, issue)

            plan = await self.compute_plan(
                session, workspace_id=workspace_id, issue=issue, target_project=target
            )
            from_project_id = issue.project_id
            source_project = await self._issues._project_of(session, issue)

            # Single-transaction application (§3.8 step 2):
            issue.project_id = target.id if target is not None else None
            for mapped in plan["mapped_fields"]:
                if mapped["field"] == "status":
                    new_status = mapped["to"]
                    issue.status_id = uuid.UUID(new_status["id"])
                    issue.state_category = new_status["category"]
                    if new_status["category"] == "done" and issue.completed_at is None:
                        from mesh.issue.service import _now

                        issue.completed_at = _now(self._issues._clock)
                    elif new_status["category"] != "done" and issue.completed_at is not None:
                        issue.completed_at = None
            for cleared in plan["cleared_fields"]:
                if cleared["field"] == "milestone_id":
                    issue.milestone_id = None
                elif cleared["field"] == "cycle_id":
                    issue.cycle_id = None
            issue.version = issue.version + 1
            from mesh.issue.service import _now

            issue.updated_at = _now(self._issues._clock)
            session.add(
                IssueActivity(
                    workspace_id=workspace_id,
                    issue_id=issue.id,
                    actor_member_id=actor.id,
                    field="project_id",
                    old_value=str(from_project_id) if from_project_id is not None else None,
                    new_value=str(issue.project_id) if issue.project_id is not None else None,
                )
            )
            await session.flush()

            rendered = await self._issues.render_issue(session, issue)
            payload = {
                "id": str(issue.id),
                "from_project_id": plan["from_project_id"],
                "to_project_id": plan["target_project_id"],
                "mapped_fields": plan["mapped_fields"],
                "cleared_fields": plan["cleared_fields"],
                "version": issue.version,
                "updated_at": _isoformat(issue.updated_at),
            }
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(issue.id),
                event="issue.project_changed",
                data=payload,
            )
            target_public = target is None or target.visibility == "public"
            if target_public:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_workspace_issues_channel(workspace_id),
                    event="issue.project_changed",
                    data=payload,
                )
            # Source was public, destination is not: non-members' lists must
            # drop the card (same convergence frame project.md uses).
            source_public = source_project is None or source_project.visibility == "public"
            if source_public and not target_public:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_workspace_issues_channel(workspace_id),
                    event="issue.deleted",
                    data={"id": str(issue.id)},
                )
            return rendered


__all__ = ["MoveService"]
