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
label-property associations (MES-32): project-private labels and values of
project-scoped field definitions are cleared on the way out (they cannot
apply outside their project); workspace-level labels / values are KEPT.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.issue import Issue, IssueActivity
from mesh.db.models.label import (
    CustomFieldDef,
    IssueCustomFieldValue,
    IssueLabel,
    Label,
)
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
from mesh.labels.service import LabelService
from mesh.outbox.service import emit_realtime

MOVE_NOT_CONFIRMED = "move requires confirmation"


def apply_move_plan(issue: Issue, plan: dict, *, now: datetime) -> None:
    """Apply a §3.8 plan to the issue row (shared by move and bulk paths).

    Status mapping syncs ``completed_at`` exactly like a direct status
    change would: entering a done category stamps it, leaving one clears
    it. Cleared project-private fields go NULL.
    """
    for mapped in plan["mapped_fields"]:
        if mapped["field"] == "status":
            new_status = mapped["to"]
            issue.status_id = uuid.UUID(new_status["id"])
            issue.state_category = new_status["category"]
            if new_status["category"] == "done" and issue.completed_at is None:
                issue.completed_at = now
            elif new_status["category"] != "done" and issue.completed_at is not None:
                issue.completed_at = None
    for cleared in plan["cleared_fields"]:
        if cleared["field"] == "milestone_id":
            issue.milestone_id = None
        elif cleared["field"] == "cycle_id":
            issue.cycle_id = None


def move_activity_rows(
    *,
    workspace_id: uuid.UUID,
    issue: Issue,
    actor: Member,
    from_project_id: uuid.UUID | None,
    plan: dict,
) -> list[IssueActivity]:
    """The §3.8 ⑥ audit trail: the project change PLUS one row per mapped
    or cleared field, so the trail carries the mapping/clearing manifest."""
    rows = [
        IssueActivity(
            workspace_id=workspace_id,
            issue_id=issue.id,
            actor_member_id=actor.id,
            field="project_id",
            old_value=str(from_project_id) if from_project_id is not None else None,
            new_value=str(issue.project_id) if issue.project_id is not None else None,
        )
    ]
    for mapped in plan["mapped_fields"]:
        rows.append(
            IssueActivity(
                workspace_id=workspace_id,
                issue_id=issue.id,
                actor_member_id=actor.id,
                field=mapped["field"],
                old_value=mapped.get("from", {}).get("id"),
                new_value=mapped.get("to", {}).get("id"),
            )
        )
    for cleared in plan["cleared_fields"]:
        items = cleared.get("items") or []
        rows.append(
            IssueActivity(
                workspace_id=workspace_id,
                issue_id=issue.id,
                actor_member_id=actor.id,
                field=cleared["field"],
                old_value=items[0]["id"] if items else None,
                new_value=None,
            )
        )
    return rows


def redact_move_payload(payload: dict) -> dict:
    """A ``project_changed`` payload safe for channels non-source-members read.

    A private→public move reaches every workspace member, but the plan's
    source-side copies carry private-project readable metadata: the source
    status ``name`` inside ``mapped_fields[].from`` and the milestone /
    cycle titles inside ``cleared_fields[].items``. The redacted copy keeps
    the structural markers (field, reason, category, ``from_project_id``,
    ``to_project_id``) and the target-side ``to`` snapshot (the destination
    is public, so its statuses are readable by everyone anyway), and drops
    every source-owned readable value. The full manifest remains in the
    permission-gated ``issue_activity`` trail (§3.8 ⑥) — realtime consumers
    that need it refetch through the authorized read path.
    """
    redacted_mapped = []
    for entry in payload.get("mapped_fields") or []:
        item: dict = {"field": entry.get("field"), "reason": entry.get("reason")}
        if entry.get("to") is not None:
            item["to"] = entry["to"]
        source = entry.get("from")
        if isinstance(source, dict) and source.get("category") is not None:
            # category marker only — never the private status name/color
            item["from"] = {"category": source["category"]}
        redacted_mapped.append(item)
    redacted_cleared = [
        {"field": entry.get("field"), "reason": entry.get("reason")}
        for entry in payload.get("cleared_fields") or []
    ]
    return {**payload, "mapped_fields": redacted_mapped, "cleared_fields": redacted_cleared}


async def clear_cleared_associations(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue: Issue,
    plan: dict,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Delete the plan's project-private labels / field values (label-property
    §3.8). ``apply_move_plan`` only touches issue columns; the association
    rows are deleted here, in the same transaction. Returns the cleared ids
    so callers can broadcast the §3.5 convergence frames."""
    cleared_label_ids: list[uuid.UUID] = []
    cleared_field_def_ids: list[uuid.UUID] = []
    for cleared in plan["cleared_fields"]:
        if cleared["field"] == "labels":
            cleared_label_ids = [uuid.UUID(item["id"]) for item in cleared["items"]]
        elif cleared["field"] == "custom_field_values":
            cleared_field_def_ids = [
                uuid.UUID(item["field_def_id"]) for item in cleared["items"]
            ]
    if cleared_label_ids:
        await session.execute(
            delete(IssueLabel).where(
                IssueLabel.workspace_id == workspace_id,
                IssueLabel.issue_id == issue.id,
                IssueLabel.label_id.in_(cleared_label_ids),
            )
        )
    if cleared_field_def_ids:
        await session.execute(
            delete(IssueCustomFieldValue).where(
                IssueCustomFieldValue.workspace_id == workspace_id,
                IssueCustomFieldValue.issue_id == issue.id,
                IssueCustomFieldValue.field_def_id.in_(cleared_field_def_ids),
            )
        )
    return cleared_label_ids, cleared_field_def_ids


async def emit_association_cleared_events(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue: Issue,
    plan: dict,
    cleared_label_ids: list[uuid.UUID],
    target_visible: bool,
) -> None:
    """§3.5 convergence frames after a move cleared associations:
    issue.labels_changed (surviving label set) and per-field
    issue.custom_field_changed (null value). Detail channel always,
    workspace list channel when the destination is visible."""
    if cleared_label_ids:
        remaining = list(
            (
                await session.execute(
                    select(Label)
                    .join(IssueLabel, IssueLabel.label_id == Label.id)
                    .where(
                        IssueLabel.workspace_id == workspace_id,
                        IssueLabel.issue_id == issue.id,
                    )
                    .order_by(IssueLabel.created_at.asc(), IssueLabel.label_id.asc())
                )
            ).scalars().all()
        )
        labels_data = {
            "issue_id": str(issue.id),
            "labels": [LabelService.render_label(label) for label in remaining],
        }
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=_issue_channel(issue.id),
            event="issue.labels_changed",
            data=labels_data,
        )
        if target_visible:
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_workspace_issues_channel(workspace_id),
                event="issue.labels_changed",
                data=labels_data,
            )
    for cleared in plan["cleared_fields"]:
        if cleared["field"] != "custom_field_values":
            continue
        for item in cleared["items"]:
            value_data = {
                "issue_id": str(issue.id),
                "field_def_id": item["field_def_id"],
                "field_key": item["field_key"],
                "value": None,
            }
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_issue_channel(issue.id),
                event="issue.custom_field_changed",
                data=value_data,
            )
            if target_visible:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_workspace_issues_channel(workspace_id),
                    event="issue.custom_field_changed",
                    data=value_data,
                )


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
            select(IssueStatus).where(
                IssueStatus.id == issue.status_id,
                IssueStatus.workspace_id == workspace_id,
            )
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
                select(Milestone).where(
                    Milestone.id == issue.milestone_id,
                    Milestone.workspace_id == workspace_id,
                )
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
            cycle = await session.scalar(
                select(Cycle).where(
                    Cycle.id == issue.cycle_id, Cycle.workspace_id == workspace_id
                )
            )
            if cycle is not None and cycle.project_id is not None:
                cleared_fields.append(
                    {
                        "field": "cycle_id",
                        "items": [{"id": str(cycle.id), "name": cycle.name}],
                        "reason": "项目绑定的周期",
                    }
                )
        # label-property association layer: project-private labels and values
        # of project-scoped field definitions cannot survive leaving their
        # project — preview them as cleared (workspace-level ones are kept).
        if issue.project_id is not None:
            private_labels = list(
                (
                    await session.execute(
                        select(Label)
                        .join(IssueLabel, IssueLabel.label_id == Label.id)
                        .where(
                            IssueLabel.workspace_id == workspace_id,
                            IssueLabel.issue_id == issue.id,
                            Label.project_id == issue.project_id,
                        )
                    )
                ).scalars().all()
            )
            if private_labels:
                cleared_fields.append(
                    {
                        "field": "labels",
                        "items": [
                            {"id": str(label.id), "name": label.name}
                            for label in private_labels
                        ],
                        "reason": "项目私有标签",
                    }
                )
            private_value_defs = list(
                (
                    await session.execute(
                        select(CustomFieldDef)
                        .join(
                            IssueCustomFieldValue,
                            IssueCustomFieldValue.field_def_id == CustomFieldDef.id,
                        )
                        .where(
                            IssueCustomFieldValue.workspace_id == workspace_id,
                            IssueCustomFieldValue.issue_id == issue.id,
                            CustomFieldDef.project_id == issue.project_id,
                        )
                    )
                ).scalars().all()
            )
            if private_value_defs:
                cleared_fields.append(
                    {
                        "field": "custom_field_values",
                        "items": [
                            # "id" 供 move_activity_rows 通用清单渲染;
                            # field_def_id / field_key 供关联层收敛事件。
                            {
                                "id": str(definition.id),
                                "field_def_id": str(definition.id),
                                "field_key": definition.field_key,
                            }
                            for definition in private_value_defs
                        ],
                        "reason": "项目私有字段值",
                    }
                )

        return {
            "issue_id": str(issue.id),
            "identifier": issue.identifier,
            "from_project_id": str(issue.project_id) if issue.project_id is not None else None,
            "target_project_id": str(target_project.id) if target_project else None,
            # §3.8 step 2 requires the current version — step 1 hands it over
            # so clients that start from the unconfirmed 422 can echo it back.
            "version": issue.version,
            "mapped_fields": mapped_fields,
            "cleared_fields": cleared_fields,
            "kept_fields": list(KEPT_FIELDS),
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
            issue = await self._issues._load_issue(session, workspace_id=workspace_id, issue_id=issue_id)
            await self._issues.assert_can_view_issue(session, viewer=viewer, issue=issue)
            target = await self._target_project(
                session, workspace_id=workspace_id, target_project_id=target_project_id
            )
            if target is not None:
                await self._issues._projects.assert_can_write(session, viewer=viewer, project=target)
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
        if confirm and expected_version is None:
            # §3.8 step 2: a confirmed move must carry the current version
            # (optimistic lock). The preview hands the version out. Defense
            # in depth under the schema boundary (MoveRequest enforces the
            # same rule → 422 move_version_required at the API edge); direct
            # service callers get the identical named-code 422.
            raise BusinessRuleError(
                "confirmed move requires the current version",
                code="move_version_required",
                details={"field": "version", "hint": "echo preview.version back"},
            )
        factory = self._issues._factory
        if not confirm:
            async with factory() as session:
                from mesh.db.tenant import set_tenant_context

                await set_tenant_context(session, workspace_id)
                issue = await self._issues._load_issue(session, workspace_id=workspace_id, issue_id=issue_id)
                # MES-46 H1: the 422 preview envelope carries the full field
                # manifest, so it is authorized exactly like the preview
                # endpoint and the confirming transaction — AND in the same
                # order: the source read gate runs BEFORE the target is even
                # resolved, so a caller holding an invisible issue UUID can
                # neither read the manifest nor probe project existence by
                # sweeping target_project_id (message-identical 404/403
                # regardless of the target). Confirmed moves skip straight
                # to the authorizing transaction below (no redundant load).
                await self._issues.assert_can_view_issue(session, viewer=actor, issue=issue)
                target = await self._target_project(
                    session, workspace_id=workspace_id, target_project_id=target_project_id
                )
                if target is not None:
                    await self._issues._projects.assert_can_write(session, viewer=actor, project=target)
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
            rendered, _plan = await self.apply_confirmed_move_in_session(
                session,
                actor=actor,
                workspace_id=workspace_id,
                issue_id=issue_id,
                target_project_id=target_project_id,
                expected_version=expected_version,
            )
            return rendered

    async def apply_confirmed_move_in_session(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        target_project_id: uuid.UUID | None,
        expected_version: int | None,
    ) -> tuple[dict, dict]:
        """Apply the confirmed cross-project migration inside the CALLER's
        transaction (caller owns ``begin`` + the tenant GUC, §3.8 step 2).

        Returns ``(rendered_issue, plan)``. Exposed so the kanban board move
        (kanban.md §3.2) can include the per-view ``view_issue_positions``
        upsert in the SAME transaction as the migration (the Spec's single-txn
        contract); the standalone REST confirm path (``move`` above) wraps this
        in its own transaction. ``plan`` is ``{}`` on the no-op
        (already-at-destination) case.
        """
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
        if expected_version is not None and issue.version != expected_version:
            raise ConflictError(
                "issue was modified concurrently",
                code="conflict",
                details={"id": str(issue.id), "current_version": issue.version},
            )
        if issue.project_id == (target.id if target is not None else None):
            # Already there — no-op (identical destination).
            return await self._issues.render_issue(session, issue), {}

        plan = await self.compute_plan(
            session, workspace_id=workspace_id, issue=issue, target_project=target
        )
        from_project_id = issue.project_id
        source_project = await self._issues._project_of(session, issue)

        # Single-transaction application (§3.8 step 2):
        issue.project_id = target.id if target is not None else None
        from mesh.issue.service import _now

        now = _now(self._issues._clock)
        apply_move_plan(issue, plan, now=now)
        # label-property §3.8: delete the plan's project-private labels /
        # field values in the same transaction (association rows are not
        # issue columns, so apply_move_plan does not cover them).
        cleared_label_ids, _cleared_field_def_ids = await clear_cleared_associations(
            session, workspace_id=workspace_id, issue=issue, plan=plan
        )
        issue.version = issue.version + 1
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

        # Association-layer convergence frames (label-property.md §3.5):
        # cleared labels / field values broadcast on the issue channels so
        # open details and lists drop the stale chips/values.
        target_visible = target is None or target.visibility == "public"
        await emit_association_cleared_events(
            session,
            workspace_id=workspace_id,
            issue=issue,
            plan=plan,
            cleared_label_ids=cleared_label_ids,
            target_visible=target_visible,
        )

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
        # Private source → the plan carries source-owned readable metadata
        # (status names, milestone/cycle titles). Once the issue is readable by
        # the whole workspace (the public-target broadcast below, or the issue
        # channel any member may join now that the card is public) that copy must
        # be redacted; the full manifest stays in the permission-gated trail
        # (MES-48 H1).
        source_private = source_project is not None and source_project.visibility != "public"
        broadcast_payload = redact_move_payload(payload) if source_private else payload
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=_issue_channel(issue.id),
            event="issue.project_changed",
            data=broadcast_payload,
        )
        target_public = target is None or target.visibility == "public"
        if target_public:
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_workspace_issues_channel(workspace_id),
                event="issue.project_changed",
                data=broadcast_payload,
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
        return rendered, plan


__all__ = ["MoveService", "apply_move_plan", "move_activity_rows", "redact_move_payload"]
