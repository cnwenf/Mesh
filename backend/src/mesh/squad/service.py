"""Squad service — CRUD, membership, messages, activity (squad.md §3.1–§3.5).

Stateless orchestrator over the squad tables. Every public method owns its
transaction and sets the tenant GUC first (RLS, README §6.2). Human/agent is
resolved by JOINing ``members.member_type`` and surfaced in responses as a
computed snapshot — it is NEVER stored on squad rows (README §6.1).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.constraints import violates
from mesh.db.models.member import Member
from mesh.db.models.squad import (
    Squad,
    SquadActivity,
    SquadMember,
    SquadMessage,
    SquadTask,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from mesh.outbox.service import emit_realtime
from mesh.squad.common import SQUAD_CHANNEL, load_member_snapshot, now_utc, record_squad_activity
from mesh.validation import LIKE_ESCAPE_CHAR, escape_like

# Max member snapshots embedded in a squad rendering (list member_preview, §3.1).
MEMBER_PREVIEW_LIMIT = 8

# Export sections are capped so a pathological squad cannot produce an
# unbounded archive document (far above realistic orchestration volume).
EXPORT_SECTION_LIMIT = 5000

# squad_activity.action vocabulary (squad.md §2.8).
ACTIVITY_ACTIONS = frozenset(
    {
        "squad_created",
        "squad_updated",
        "squad_archived",
        "squad_restored",
        "member_added",
        "member_removed",
        "role_changed",
        "task_received",
        "decompose_started",
        "plan_submitted",
        "plan_approved",
        "plan_rejected",
        "task_decomposed",
        "task_dispatched",
        "task_started",
        "task_blocked",
        "task_finished",
        "task_failed",
        "task_cancelled",
        "task_aggregated",
        "message_sent",
        # Leader trigger → evaluation closed loop (§5.1): the orchestrator run
        # ends in action / no_action / failed, recorded on the timeline.
        "leader_evaluated",
    }
)


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return now_utc(clock)


class SquadService:
    """Squad lifecycle, membership, messages and activity timeline."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock

    # -- internal helpers -----------------------------------------------------

    async def _load_squad(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, squad_id: uuid.UUID, for_update=False
    ) -> Squad:
        stmt = select(Squad).where(
            Squad.workspace_id == workspace_id,
            Squad.id == squad_id,
            Squad.deleted_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        squad = (await session.execute(stmt)).scalar_one_or_none()
        if squad is None:
            raise NotFoundError("squad not found")
        return squad

    async def _active_member_rows(
        self, session: AsyncSession, *, squad_id: uuid.UUID
    ) -> list[SquadMember]:
        return list(
            (
                await session.execute(
                    select(SquadMember)
                    .where(SquadMember.squad_id == squad_id, SquadMember.left_at.is_(None))
                    .order_by(SquadMember.joined_at)
                )
            ).scalars()
        )

    async def _assert_is_member(
        self, session: AsyncSession, *, squad_id: uuid.UUID, member_id: uuid.UUID
    ) -> SquadMember:
        row = await session.scalar(
            select(SquadMember).where(
                SquadMember.squad_id == squad_id,
                SquadMember.member_id == member_id,
                SquadMember.left_at.is_(None),
            )
        )
        if row is None:
            raise ForbiddenError("not a member of this squad")
        return row

    async def record_activity(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        squad_id: uuid.UUID,
        action: str,
        actor_id: uuid.UUID | None,
        task_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> None:
        """Append one timeline row and broadcast ``squad_activity.created``."""
        await record_squad_activity(
            session,
            workspace_id=workspace_id,
            squad_id=squad_id,
            action=action,
            actor_id=actor_id,
            task_id=task_id,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
        )

    async def render_squad(self, session: AsyncSession, squad: Squad) -> dict:
        members = await self._active_member_rows(session, squad_id=squad.id)
        leaders = []
        preview = []
        for row in members:
            snap = await load_member_snapshot(
                session, workspace_id=squad.workspace_id, member_id=row.member_id
            )
            if snap is None:
                continue
            if row.role == "leader":
                leaders.append(snap)
            if len(preview) < MEMBER_PREVIEW_LIMIT:
                preview.append({**snap, "role": row.role})
        active_tasks = await session.scalar(
            select(func.count())
            .select_from(SquadTask)
            .where(
                SquadTask.workspace_id == squad.workspace_id,
                SquadTask.squad_id == squad.id,
                SquadTask.status.not_in(["done", "failed", "cancelled"]),
            )
        )
        leader_snap = await load_member_snapshot(
            session, workspace_id=squad.workspace_id, member_id=squad.primary_leader_id
        )
        return {
            "id": str(squad.id),
            "workspace_id": str(squad.workspace_id),
            "name": squad.name,
            "description": squad.description,
            "instructions": squad.instructions,
            "avatar_url": squad.avatar_url,
            "kind": squad.kind,
            "status": squad.status,
            "leader_mode": squad.leader_mode,
            "primary_leader_id": str(squad.primary_leader_id) if squad.primary_leader_id else None,
            "primary_leader": leader_snap,
            "require_plan_approval": squad.require_plan_approval,
            "max_decompose_depth": squad.max_decompose_depth,
            "member_count": len(members),
            "member_preview": preview,
            "active_task_count": int(active_tasks or 0),
            "leaders": leaders,
            "archived_at": squad.archived_at.isoformat() if squad.archived_at else None,
            "created_at": squad.created_at.isoformat(),
            "updated_at": squad.updated_at.isoformat(),
        }

    # -- CRUD -----------------------------------------------------------------

    async def create_squad(self, *, actor: Member, workspace_id: uuid.UUID, body) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = _now(self._clock)
            leader_members = [m for m in body.members if m.role == "leader"]
            primary_leader_id = (
                uuid.UUID(leader_members[0].member_id) if leader_members else None
            )
            squad = Squad(
                workspace_id=workspace_id,
                name=body.name.strip(),
                description=body.description,
                instructions=body.instructions,
                avatar_url=body.avatar_url,
                kind=body.kind,
                leader_mode=body.leader_mode,
                require_plan_approval=body.require_plan_approval,
                max_decompose_depth=body.max_decompose_depth,
                primary_leader_id=primary_leader_id,
                creator_id=actor.id,
                created_at=now,
                updated_at=now,
            )
            session.add(squad)
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_squads_name"):
                    raise ConflictError("squad name already taken", code="squad_name_taken") from exc
                raise
            # Validate + add initial members; each must be an active roster member.
            for m in body.members:
                await self._add_member_tx(
                    session,
                    workspace_id=workspace_id,
                    squad=squad,
                    member_id=uuid.UUID(m.member_id),
                    role=m.role,
                    added_by_id=actor.id,
                    now=now,
                )
            # Reconcile primary leader with the actual leader set.
            await self._reconcile_primary_leader(session, squad=squad, now=now)
            await self.record_activity(
                session,
                workspace_id=workspace_id,
                squad_id=squad.id,
                action="squad_created",
                actor_id=actor.id,
                target_type="squad",
                target_id=squad.id,
                payload={"name": squad.name},
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=SQUAD_CHANNEL.format(squad_id=squad.id),
                event="squad.updated",
                data={"squad_id": str(squad.id)},
                idempotency_key=f"squad:{squad.id}:created",
            )
            return await self.render_squad(session, squad)

    async def _add_member_tx(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        squad: Squad,
        member_id: uuid.UUID,
        role: str,
        added_by_id: uuid.UUID | None,
        now: datetime,
    ) -> SquadMember:
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.id == member_id,
                Member.status == "active",
            )
        )
        if member is None:
            raise BusinessRuleError(
                "member not found in workspace roster", code="assignee_not_member"
            )
        row = SquadMember(
            workspace_id=workspace_id,
            squad_id=squad.id,
            member_id=member_id,
            role=role,
            joined_at=now,
            added_by_id=added_by_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as exc:
            if violates(exc, "uq_squad_member_active"):
                raise ConflictError("already a member", code="conflict") from exc
            raise
        await self.record_activity(
            session,
            workspace_id=workspace_id,
            squad_id=squad.id,
            action="member_added",
            actor_id=added_by_id,
            target_type="member",
            target_id=member_id,
            payload={"role": role},
        )
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=SQUAD_CHANNEL.format(squad_id=squad.id),
            event="squad_member.changed",
            data={"squad_id": str(squad.id), "member_id": str(member_id), "role": role},
            idempotency_key=f"squad-member:{squad.id}:{member_id}:added:{now.isoformat()}",
        )
        return row

    async def _reconcile_primary_leader(
        self, session: AsyncSession, *, squad: Squad, now: datetime
    ) -> None:
        """Ensure ``primary_leader_id`` points at an active leader member.

        A rotation here goes through the SAME propagation path as an explicit
        PATCH (§2.5 / B11): every leader change — however triggered — updates
        all active assignment rows + their issue assignees, broadcasts
        ``squad_assignment.changed`` and unblocks ``leader_lost`` roots.
        """
        leaders = (
            await session.execute(
                select(SquadMember.member_id).where(
                    SquadMember.squad_id == squad.id,
                    SquadMember.role == "leader",
                    SquadMember.left_at.is_(None),
                )
            )
        ).scalars().all()
        if not leaders:
            return
        if squad.primary_leader_id not in leaders:
            from mesh.squad.tasks import change_primary_leader_tx

            await change_primary_leader_tx(
                session,
                workspace_id=squad.workspace_id,
                squad=squad,
                new_leader_id=leaders[0],
                actor_id=None,  # system-driven rotation
                now=now,
            )

    async def list_squads(
        self,
        *,
        workspace_id: uuid.UUID,
        status: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        limit: int = 30,
        cursor: str | None = None,
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(Squad)
                .where(Squad.workspace_id == workspace_id, Squad.deleted_at.is_(None))
                .order_by(Squad.created_at.desc(), Squad.id.desc())
            )
            if status:
                stmt = stmt.where(Squad.status == status)
            if kind:
                stmt = stmt.where(Squad.kind == kind)
            if q:
                # Escaped so user-supplied wildcards match literally — same
                # fix class as the roster search hardening (MES-57 L5): a raw
                # ``q=%`` would otherwise match every squad.
                pattern = f"%{escape_like(q.strip())}%"
                stmt = stmt.where(Squad.name.ilike(pattern, escape=LIKE_ESCAPE_CHAR))
            if cursor:
                from mesh.api.pagination import decode_cursor

                pos = decode_cursor(cursor)
                stmt = stmt.where(
                    tuple_(Squad.created_at, Squad.id) < (pos.sort_value, pos.id)
                )
            squads = list((await session.execute(stmt.limit(limit + 1))).scalars())
            next_cursor = None
            if len(squads) > limit:
                squads = squads[:limit]
                last = squads[-1]
                from mesh.api.pagination import encode_cursor

                next_cursor = encode_cursor(last.created_at, last.id)
            return {
                "data": [await self.render_squad(session, s) for s in squads],
                "next_cursor": next_cursor,
            }

    async def get_squad(self, *, workspace_id: uuid.UUID, squad_id: uuid.UUID) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            squad = await self._load_squad(session, workspace_id=workspace_id, squad_id=squad_id)
            return await self.render_squad(session, squad)

    async def export_markdown(self, *, workspace_id: uuid.UUID, squad_id: uuid.UUID) -> str:
        """Archive export (squad.md §4.5 parity L486): tasks + messages +
        timeline rendered as one markdown document.

        Read-only; sections are chronologically ordered and capped so a
        pathological squad cannot produce an unbounded document (the cap is
        far above realistic orchestration volume).
        """
        from mesh.db.models.issue import Issue
        from mesh.squad.export import render_squad_export

        limit = EXPORT_SECTION_LIMIT
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            squad = await self._load_squad(
                session, workspace_id=workspace_id, squad_id=squad_id
            )
            rendered_squad = await self.render_squad(session, squad)

            task_rows = list(
                (
                    await session.execute(
                        select(SquadTask)
                        .where(
                            SquadTask.workspace_id == workspace_id,
                            SquadTask.squad_id == squad_id,
                        )
                        .order_by(SquadTask.created_at.asc())
                        .limit(limit)
                    )
                ).scalars()
            )
            identifiers = dict(
                (
                    await session.execute(
                        select(Issue.id, Issue.identifier).where(
                            Issue.workspace_id == workspace_id,
                            Issue.id.in_([t.issue_id for t in task_rows] or [uuid.uuid4()]),
                        )
                    )
                ).all()
            ) if task_rows else {}
            tasks = []
            for task in task_rows:
                assignee = (
                    await load_member_snapshot(
                        session, workspace_id=workspace_id, member_id=task.assignee_id
                    )
                    if task.assignee_id
                    else None
                )
                tasks.append(
                    {
                        "title": task.title_snapshot,
                        "status": task.status,
                        "issue_id": str(task.issue_id),
                        "issue_identifier": identifiers.get(task.issue_id),
                        "assignee": assignee,
                        "created_at": task.created_at.isoformat(),
                        "started_at": task.started_at.isoformat() if task.started_at else None,
                        "finished_at": task.finished_at.isoformat()
                        if task.finished_at
                        else None,
                        "failure_reason": task.failure_reason,
                        "result_summary": task.result_summary,
                    }
                )

            message_rows = list(
                (
                    await session.execute(
                        select(SquadMessage)
                        .where(
                            SquadMessage.workspace_id == workspace_id,
                            SquadMessage.squad_id == squad_id,
                        )
                        .order_by(SquadMessage.created_at.asc())
                        .limit(limit)
                    )
                ).scalars()
            )
            messages = [await self._render_message(session, m) for m in message_rows]

            activity_rows = list(
                (
                    await session.execute(
                        select(SquadActivity)
                        .where(
                            SquadActivity.workspace_id == workspace_id,
                            SquadActivity.squad_id == squad_id,
                        )
                        .order_by(SquadActivity.created_at.asc())
                        .limit(limit)
                    )
                ).scalars()
            )
            activity = []
            for row in activity_rows:
                actor = (
                    await load_member_snapshot(
                        session, workspace_id=workspace_id, member_id=row.actor_id
                    )
                    if row.actor_id
                    else None
                )
                activity.append(
                    {
                        "created_at": row.created_at.isoformat(),
                        "actor": actor,
                        "action": row.action,
                        "task_id": str(row.task_id) if row.task_id else None,
                        "target_id": str(row.target_id) if row.target_id else None,
                    }
                )

            return render_squad_export(
                squad=rendered_squad,
                tasks=tasks,
                messages=messages,
                activity=activity,
                exported_at=_now(self._clock).isoformat(),
            )

    async def get_issue_assignment(
        self, *, workspace_id: uuid.UUID, issue_id: uuid.UUID
    ) -> dict | None:
        """The ACTIVE squad assignment carrying ``issue_id`` (§2.5), or None.

        Powers the issue header's single-responsibility presentation
        (§4.3-2: leader avatar + squad badge)."""
        from mesh.db.models.squad import IssueSquadAssignment

        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            row = await session.scalar(
                select(IssueSquadAssignment).where(
                    IssueSquadAssignment.workspace_id == workspace_id,
                    IssueSquadAssignment.issue_id == issue_id,
                    IssueSquadAssignment.status == "active",
                )
            )
            if row is None:
                return None
            squad_name = await session.scalar(
                select(Squad.name).where(
                    Squad.workspace_id == workspace_id, Squad.id == row.squad_id
                )
            )
            leader = await load_member_snapshot(
                session, workspace_id=workspace_id, member_id=row.leader_member_id
            )
            return {
                "assignment_id": str(row.id),
                "squad_id": str(row.squad_id),
                "squad_name": squad_name,
                "issue_id": str(row.issue_id),
                "root_task_id": str(row.root_task_id) if row.root_task_id else None,
                "leader": leader,
                "assigned_at": row.assigned_at.isoformat(),
            }

    async def update_squad(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, body
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            squad = await self._load_squad(
                session, workspace_id=workspace_id, squad_id=squad_id, for_update=True
            )
            now = _now(self._clock)
            changes = []
            for field in (
                "name",
                "description",
                "instructions",
                "avatar_url",
                "kind",
                "leader_mode",
                "require_plan_approval",
                "max_decompose_depth",
            ):
                value = getattr(body, field)
                if value is not None and getattr(squad, field) != value:
                    setattr(squad, field, value.strip() if field == "name" else value)
                    changes.append(field)
            new_leader = getattr(body, "primary_leader_id", None)
            if new_leader is not None:
                from mesh.squad.tasks import change_primary_leader_tx

                await change_primary_leader_tx(
                    session,
                    workspace_id=workspace_id,
                    squad=squad,
                    new_leader_id=uuid.UUID(new_leader),
                    actor_id=actor.id,
                    now=now,
                )
                changes.append("primary_leader_id")
            if not changes:
                return await self.render_squad(session, squad)
            squad.updated_at = now
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_squads_name"):
                    raise ConflictError("squad name already taken", code="squad_name_taken") from exc
                raise
            await self.record_activity(
                session,
                workspace_id=workspace_id,
                squad_id=squad.id,
                action="squad_updated",
                actor_id=actor.id,
                target_type="squad",
                target_id=squad.id,
                payload={"changes": changes},
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=SQUAD_CHANNEL.format(squad_id=squad.id),
                event="squad.updated",
                data={"squad_id": str(squad.id)},
                idempotency_key=f"squad:{squad.id}:updated:{now.isoformat()}",
            )
            return await self.render_squad(session, squad)

    async def _set_status(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, archive: bool
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            squad = await self._load_squad(
                session, workspace_id=workspace_id, squad_id=squad_id, for_update=True
            )
            now = _now(self._clock)
            if archive:
                # Guard: cannot archive with running tasks (squad.md §3.3 conflict).
                running = await session.scalar(
                    select(func.count())
                    .select_from(SquadTask)
                    .where(
                        SquadTask.workspace_id == workspace_id,
                        SquadTask.squad_id == squad.id,
                        SquadTask.status.in_(["in_progress", "dispatching", "decomposing"]),
                    )
                )
                if running:
                    raise ConflictError(
                        "squad has running tasks", code="conflict"
                    )
                squad.status = "archived"
                squad.archived_at = now
                squad.archived_by_id = actor.id
                action, event = "squad_archived", "squad.archived"
            else:
                squad.status = "active"
                squad.archived_at = None
                squad.archived_by_id = None
                action, event = "squad_restored", "squad.updated"
            squad.updated_at = now
            await self.record_activity(
                session,
                workspace_id=workspace_id,
                squad_id=squad.id,
                action=action,
                actor_id=actor.id,
                target_type="squad",
                target_id=squad.id,
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=SQUAD_CHANNEL.format(squad_id=squad.id),
                event=event,
                data={"squad_id": str(squad.id)},
                idempotency_key=f"squad:{squad.id}:{action}:{now.isoformat()}",
            )
            return await self.render_squad(session, squad)

    async def archive_squad(self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID) -> dict:
        return await self._set_status(
            actor=actor, workspace_id=workspace_id, squad_id=squad_id, archive=True
        )

    async def restore_squad(self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID) -> dict:
        return await self._set_status(
            actor=actor, workspace_id=workspace_id, squad_id=squad_id, archive=False
        )

    # -- membership -----------------------------------------------------------

    async def list_members(self, *, workspace_id: uuid.UUID, squad_id: uuid.UUID) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            await self._load_squad(session, workspace_id=workspace_id, squad_id=squad_id)
            rows = await self._active_member_rows(session, squad_id=squad_id)
            data = []
            for row in rows:
                snap = await load_member_snapshot(
                    session, workspace_id=workspace_id, member_id=row.member_id
                )
                if snap is None:
                    continue
                data.append(
                    {
                        "id": str(row.id),
                        "member_id": snap["member_id"],
                        "member_type": snap["member_type"],
                        "name": snap["name"],
                        "role": row.role,
                        "joined_at": row.joined_at.isoformat(),
                    }
                )
            return {"data": data, "next_cursor": None}

    async def add_members(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, body
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            squad = await self._load_squad(
                session, workspace_id=workspace_id, squad_id=squad_id, for_update=True
            )
            now = _now(self._clock)
            for m in body.members:
                await self._add_member_tx(
                    session,
                    workspace_id=workspace_id,
                    squad=squad,
                    member_id=uuid.UUID(m.member_id),
                    role=m.role,
                    added_by_id=actor.id,
                    now=now,
                )
            await self._reconcile_primary_leader(session, squad=squad, now=now)
            squad.updated_at = now
            return await self.render_squad(session, squad)

    async def change_role(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, member_id: uuid.UUID, role: str
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            squad = await self._load_squad(
                session, workspace_id=workspace_id, squad_id=squad_id, for_update=True
            )
            now = _now(self._clock)
            row = await session.scalar(
                select(SquadMember)
                .where(
                    SquadMember.squad_id == squad.id,
                    SquadMember.member_id == member_id,
                    SquadMember.left_at.is_(None),
                )
                .with_for_update()
            )
            if row is None:
                raise NotFoundError("member not found")
            old_role = row.role
            if old_role == role:
                return await self.render_squad(session, squad)
            row.role = role
            row.updated_at = now
            # Flush so the leader-count guard sees the change (autoflush is off).
            await session.flush()
            # Removing the last leader is forbidden (squad.md §5.1 no_leader).
            await self._assert_has_leader(session, squad=squad)
            await self._reconcile_primary_leader(session, squad=squad, now=now)
            squad.updated_at = now
            await self.record_activity(
                session,
                workspace_id=workspace_id,
                squad_id=squad.id,
                action="role_changed",
                actor_id=actor.id,
                target_type="member",
                target_id=member_id,
                payload={"from": old_role, "to": role},
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=SQUAD_CHANNEL.format(squad_id=squad.id),
                event="squad_member.changed",
                data={"squad_id": str(squad.id), "member_id": str(member_id), "role": role},
                idempotency_key=f"squad-member:{squad.id}:{member_id}:role:{now.isoformat()}",
            )
            return await self.render_squad(session, squad)

    async def remove_member(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, member_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            squad = await self._load_squad(
                session, workspace_id=workspace_id, squad_id=squad_id, for_update=True
            )
            # §5.3 anti-privilege-escalation: an agent cannot edit its own squad's
            # membership. A human actor is required for membership mutation.
            if actor.member_type != "human":
                raise ForbiddenError("agents cannot modify squad membership")
            now = _now(self._clock)
            row = await session.scalar(
                select(SquadMember)
                .where(
                    SquadMember.squad_id == squad.id,
                    SquadMember.member_id == member_id,
                    SquadMember.left_at.is_(None),
                )
                .with_for_update()
            )
            if row is None:
                raise NotFoundError("member not found")
            # Guard: cannot remove a member holding an in_progress subtask.
            active = await session.scalar(
                select(func.count())
                .select_from(SquadTask)
                .where(
                    SquadTask.workspace_id == workspace_id,
                    SquadTask.squad_id == squad.id,
                    SquadTask.assignee_id == member_id,
                    SquadTask.status == "in_progress",
                )
            )
            if active:
                raise BusinessRuleError(
                    "member has an in-progress task", code="member_has_active_task"
                )
            row.left_at = now
            row.updated_at = now
            squad.updated_at = now
            # Flush so the leader-departure query sees the membership removal.
            await session.flush()
            await self.record_activity(
                session,
                workspace_id=workspace_id,
                squad_id=squad.id,
                action="member_removed",
                actor_id=actor.id,
                target_type="member",
                target_id=member_id,
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=SQUAD_CHANNEL.format(squad_id=squad.id),
                event="squad_member.changed",
                data={"squad_id": str(squad.id), "member_id": str(member_id), "role": None},
                idempotency_key=f"squad-member:{squad.id}:{member_id}:removed:{now.isoformat()}",
            )
            # Leader departure protocol (squad.md §2.5): rotate the primary leader
            # if others remain; otherwise keep the assignment but block its root
            # (failure_reason='leader_lost') and notify — NOT a hard error, so the
            # squad can accept a replacement leader later.
            from mesh.squad.tasks import handle_leader_departure_tx

            await handle_leader_departure_tx(
                session,
                workspace_id=workspace_id,
                squad=squad,
                departed_member_id=member_id,
                actor_id=actor.id,
                now=now,
            )
            return await self.render_squad(session, squad)

    async def _assert_has_leader(self, session: AsyncSession, *, squad: Squad) -> None:
        count = await session.scalar(
            select(func.count())
            .select_from(SquadMember)
            .where(
                SquadMember.squad_id == squad.id,
                SquadMember.role == "leader",
                SquadMember.left_at.is_(None),
            )
        )
        if not count:
            raise BusinessRuleError("squad must keep at least one leader", code="no_leader")

    # -- messages -------------------------------------------------------------

    async def list_messages(
        self,
        *,
        workspace_id: uuid.UUID,
        squad_id: uuid.UUID,
        task_id: uuid.UUID | None = None,
        kind: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            await self._load_squad(session, workspace_id=workspace_id, squad_id=squad_id)
            stmt = (
                select(SquadMessage)
                .where(
                    SquadMessage.workspace_id == workspace_id,
                    SquadMessage.squad_id == squad_id,
                    SquadMessage.deleted_at.is_(None),
                )
                .order_by(SquadMessage.created_at.desc(), SquadMessage.id.desc())
            )
            if task_id is not None:
                stmt = stmt.where(SquadMessage.task_id == task_id)
            if kind:
                stmt = stmt.where(SquadMessage.kind == kind)
            if cursor:
                from mesh.api.pagination import decode_cursor

                pos = decode_cursor(cursor)
                stmt = stmt.where(
                    tuple_(SquadMessage.created_at, SquadMessage.id) < (pos.sort_value, pos.id)
                )
            rows = list((await session.execute(stmt.limit(limit + 1))).scalars())
            next_cursor = None
            if len(rows) > limit:
                rows = rows[:limit]
                from mesh.api.pagination import encode_cursor

                next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
            return {
                "data": [await self._render_message(session, m) for m in rows],
                "next_cursor": next_cursor,
            }

    async def _render_message(self, session: AsyncSession, m: SquadMessage) -> dict:
        sender = await load_member_snapshot(
            session, workspace_id=m.workspace_id, member_id=m.sender_id
        )
        recipient = await load_member_snapshot(
            session, workspace_id=m.workspace_id, member_id=m.recipient_id
        )
        return {
            "id": str(m.id),
            "squad_id": str(m.squad_id),
            "task_id": str(m.task_id) if m.task_id else None,
            "sender": sender,
            "recipient": recipient,
            "kind": m.kind,
            "body_markdown": m.body_markdown,
            "body_html": m.body_html,
            "pinned": m.pinned,
            "attachment_ids": list(m.attachment_ids or []),
            "created_at": m.created_at.isoformat(),
        }

    async def send_message(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, body
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            squad = await self._load_squad(
                session, workspace_id=workspace_id, squad_id=squad_id, for_update=True
            )
            # Non-system messages require an active membership.
            if body.kind != "system":
                await self._assert_is_member(
                    session, squad_id=squad.id, member_id=actor.id
                )
            now = _now(self._clock)
            recipient_id = uuid.UUID(body.recipient.member_id) if body.recipient else None
            task_id = uuid.UUID(body.task_id) if body.task_id else None
            message = SquadMessage(
                workspace_id=workspace_id,
                squad_id=squad.id,
                task_id=task_id,
                sender_id=None if body.kind == "system" else actor.id,
                recipient_id=recipient_id,
                kind=body.kind,
                body_markdown=body.body_markdown,
                body_text=body.body_markdown,
                pinned=body.pinned,
                attachment_ids=list(body.attachment_ids or []),
                created_at=now,
                updated_at=now,
            )
            session.add(message)
            await session.flush()
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=SQUAD_CHANNEL.format(squad_id=squad.id),
                event="squad_message.created",
                data={
                    "squad_id": str(squad.id),
                    "message_id": str(message.id),
                    "kind": message.kind,
                    "task_id": str(task_id) if task_id else None,
                },
                idempotency_key=f"squad-message:{message.id}",
            )
            rendered = await self._render_message(session, message)
            # §4.5: an instruction from a leader to an AGENT member triggers a run.
            # Loop suppression: only leader→agent instructions trigger, never the
            # reverse (member→leader reports do not re-wake the sender).
            if body.kind == "instruction" and recipient_id is not None:
                from mesh.squad.tasks import maybe_trigger_instruction_run

                await maybe_trigger_instruction_run(
                    session,
                    workspace_id=workspace_id,
                    squad=squad,
                    message=message,
                    sender=actor,
                    recipient_id=recipient_id,
                    task_id=task_id,
                )
            return rendered

    # -- activity -------------------------------------------------------------

    async def list_activity(
        self,
        *,
        workspace_id: uuid.UUID,
        squad_id: uuid.UUID,
        task_id: uuid.UUID | None = None,
        action: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            await self._load_squad(session, workspace_id=workspace_id, squad_id=squad_id)
            stmt = (
                select(SquadActivity)
                .where(
                    SquadActivity.workspace_id == workspace_id,
                    SquadActivity.squad_id == squad_id,
                )
                .order_by(SquadActivity.created_at.desc(), SquadActivity.id.desc())
            )
            if task_id is not None:
                stmt = stmt.where(SquadActivity.task_id == task_id)
            if action:
                stmt = stmt.where(SquadActivity.action == action)
            if cursor:
                from mesh.api.pagination import decode_cursor

                pos = decode_cursor(cursor)
                stmt = stmt.where(
                    tuple_(SquadActivity.created_at, SquadActivity.id) < (pos.sort_value, pos.id)
                )
            rows = list((await session.execute(stmt.limit(limit + 1))).scalars())
            next_cursor = None
            if len(rows) > limit:
                rows = rows[:limit]
                from mesh.api.pagination import encode_cursor

                next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
            data = []
            for row in rows:
                actor = await load_member_snapshot(
                    session, workspace_id=workspace_id, member_id=row.actor_id
                )
                data.append(
                    {
                        "id": str(row.id),
                        "task_id": str(row.task_id) if row.task_id else None,
                        "actor_kind": row.actor_kind,
                        "actor": actor,
                        "action": row.action,
                        "target_type": row.target_type,
                        "target_id": str(row.target_id) if row.target_id else None,
                        "payload": row.payload,
                        "created_at": row.created_at.isoformat(),
                    }
                )
            return {"data": data, "next_cursor": next_cursor}


__all__ = ["SquadService", "load_member_snapshot", "SQUAD_CHANNEL", "ACTIVITY_ACTIONS"]
