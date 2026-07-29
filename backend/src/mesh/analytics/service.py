"""AnalyticsService — read-only aggregates over the source tables.

Six metric families (cycle time / velocity / throughput / workload /
burndown / agent run stats) plus project and workspace dashboards
(analytics.md §1–§4). Hard rules enforced here:

- read-only: source tables are never written; only ``analytics_snapshots``
  may be written (cache);
- visibility: issue metrics filter to the requester's visible project set;
  execution metrics aggregate through the §2.3.1 ``visible_executions``
  CTE; project-level access passes the ``project_members`` gate;
- cache keying: ``scope_key`` is derived from the requester's visibility
  fingerprint — cross-permission cache sharing is impossible (§2.5 R3/R4);
- caliber: velocity/burndown report ``scope_caliber='current_attribution'``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from mesh.analytics import exec_metrics, queries
from mesh.analytics.cache import fetch_snapshot, snapshot_is_fresh, upsert_snapshot
from mesh.analytics.scope import (
    MAX_CYCLE_IDS,
    MAX_PROJECT_IDS,
    assert_valid_timezone,
    hash_id_set,
    parse_time_window,
    resolve_display_timezone,
    single_project_scope_key,
    validate_from_category,
    validate_granularity,
    validate_member_type,
    validate_metric,
)
from mesh.analytics.visibility import (
    compute_exec_scope_key,
    is_workspace_manager,
    visible_project_ids,
)
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Cycle, Milestone, Project, ProjectMember
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

SCOPE_CALIBER = "current_attribution"
STATEMENT_TIMEOUT_MS = 8000
QUERY_CANCELED_SQLSTATE = "57014"
DEFAULT_WORKLOAD_LIMIT = 50
DASHBOARD_WORKLOAD_LIMIT = 10
_CURSOR_PAD = 1_000_000_000


class _IssueScope:
    __slots__ = ("visible_ids", "scope_key", "project_id")

    def __init__(self, visible_ids, scope_key: str, project_id=None):
        self.visible_ids = visible_ids
        self.scope_key = scope_key
        self.project_id = project_id


class AnalyticsService:
    def __init__(self, session_factory, settings, *, clock: Callable[[], datetime] | None = None):
        self._factory = session_factory
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._bg_tasks: set[asyncio.Task] = set()

    # -- infrastructure ----------------------------------------------------

    @asynccontextmanager
    async def _session(self, workspace_id: uuid.UUID):
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            yield session

    async def _guard_cost(self, session) -> None:
        await session.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))

    @staticmethod
    def _translate_cost_error(exc: Exception) -> None:
        if getattr(getattr(exc, "orig", None), "sqlstate", None) == QUERY_CANCELED_SQLSTATE:
            raise BusinessRuleError(
                "aggregate query cost exceeded; narrow the time window or dimensions",
                code="query_cost_exceeded",
            ) from exc

    async def _load_workspace(self, session, workspace_id: uuid.UUID) -> Workspace:
        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise NotFoundError("workspace not found")
        return workspace

    # -- visibility gates ----------------------------------------------------

    async def _load_project(self, session, workspace_id: uuid.UUID, project_id) -> Project:
        project = await session.get(Project, project_id)
        if project is None or project.workspace_id != workspace_id or project.deleted_at is not None:
            raise NotFoundError("project not found")
        return project

    async def _assert_project_visible(self, session, *, project: Project, actor: Member) -> None:
        if is_workspace_manager(actor) or project.visibility == "public":
            return
        row = (
            await session.execute(
                select(ProjectMember.id).where(
                    ProjectMember.workspace_id == project.workspace_id,
                    ProjectMember.project_id == project.id,
                    ProjectMember.member_id == actor.id,
                )
            )
        ).first()
        if row is None:
            row = (
                await session.execute(
                    select(MemberProjectAccess.id).where(
                        MemberProjectAccess.workspace_id == project.workspace_id,
                        MemberProjectAccess.project_id == project.id,
                        MemberProjectAccess.member_id == actor.id,
                    )
                )
            ).first()
        if row is None:
            raise ForbiddenError("project is private", code="project_not_visible")

    async def _issue_scope(
        self,
        session,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id=None,
        project_ids: list | None = None,
    ) -> _IssueScope:
        if project_id is not None:
            project = await self._load_project(session, workspace_id, project_id)
            await self._assert_project_visible(session, project=project, actor=actor)
            return _IssueScope(None, single_project_scope_key(project.id), project_id=project.id)
        if project_ids:
            if len(project_ids) > MAX_PROJECT_IDS:
                raise ValidationError(
                    "too many project ids",
                    details={"max": MAX_PROJECT_IDS},
                    code="filter_too_complex",
                )
            # 显式多项目聚合含不可见项目 → 整体 403(不部分返回,§3.1 R3 ②)
            for pid in project_ids:
                project = await self._load_project(session, workspace_id, pid)
                await self._assert_project_visible(session, project=project, actor=actor)
            ordered = sorted(project_ids)
            return _IssueScope(ordered, f"projects:{hash_id_set(ordered)}")
        if is_workspace_manager(actor):
            return _IssueScope(None, "ws_admin")
        ids = await visible_project_ids(session, workspace_id=workspace_id, member=actor)
        return _IssueScope(ids, f"projects:{hash_id_set(ids)}")

    # -- cache orchestration (§2.6) ------------------------------------------

    async def _cached_or_compute(
        self,
        session,
        *,
        workspace_id: uuid.UUID,
        metric_key: str,
        scope_key: str,
        dimensions: dict,
        window_start: datetime,
        window_end: datetime,
        refresh: bool,
        compute: Callable[[object], Awaitable[dict]],
    ) -> tuple[dict, bool]:
        """Return (value, cached). Recompute wins over any stale cache (§2.6).

        ``compute`` receives the session to aggregate on — the request
        session on the sync path, a fresh session on background refresh.
        """
        now = self._clock()
        ttl = self._settings.analytics_snapshot_ttl
        if not refresh:
            value, row = await fetch_snapshot(
                session,
                workspace_id=workspace_id,
                metric_key=metric_key,
                scope_key=scope_key,
                dimensions=dimensions,
                window_start=window_start,
                window_end=window_end,
            )
            if value is not None:
                if snapshot_is_fresh(row, ttl, now):
                    return value, True
                if self._settings.analytics_stale_while_revalidate:
                    self._schedule_background_refresh(
                        workspace_id=workspace_id,
                        metric_key=metric_key,
                        scope_key=scope_key,
                        dimensions=dimensions,
                        window_start=window_start,
                        window_end=window_end,
                        compute=compute,
                    )
                    return value, True
        value = await compute(session)
        await upsert_snapshot(
            session,
            workspace_id=workspace_id,
            metric_key=metric_key,
            scope_key=scope_key,
            dimensions=dimensions,
            window_start=window_start,
            window_end=window_end,
            value=value,
            now=now,
        )
        await session.commit()
        return value, False

    def _schedule_background_refresh(
        self, *, workspace_id, metric_key, scope_key, dimensions, window_start, window_end, compute
    ) -> None:
        async def _refresh() -> None:
            try:
                async with self._factory() as session:
                    await set_tenant_context(session, workspace_id)
                    value = await compute(session)
                    await upsert_snapshot(
                        session,
                        workspace_id=workspace_id,
                        metric_key=metric_key,
                        scope_key=scope_key,
                        dimensions=dimensions,
                        window_start=window_start,
                        window_end=window_end,
                        value=value,
                        now=self._clock(),
                    )
                    await session.commit()
            except Exception:  # noqa: BLE001 — cache refresh must never break queries
                logger.warning("analytics snapshot background refresh failed", exc_info=True)

        task = asyncio.get_event_loop().create_task(_refresh())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # -- issue metrics ---------------------------------------------------------

    async def cycle_time(
        self,
        *,
        actor: Member,
        user,
        workspace_id: uuid.UUID,
        project_id=None,
        win_from: str | None = None,
        win_to: str | None = None,
        from_category: str | None = None,
        tz: str | None = None,
        refresh: bool = False,
    ) -> dict:
        from_category = validate_from_category(from_category)
        start, end = parse_time_window(win_from, win_to, now=self._clock())
        async with self._session(workspace_id) as session:
            await self._guard_cost(session)
            workspace = await self._load_workspace(session, workspace_id)
            display_tz = resolve_display_timezone(user, workspace, tz)
            scope = await self._issue_scope(
                session, actor=actor, workspace_id=workspace_id, project_id=project_id
            )

            async def compute(s) -> dict:
                sql, extra = queries.build_cycle_time_sql(
                    visible_ids=scope.visible_ids, project_id=scope.project_id
                )
                params = {
                    "ws": workspace_id,
                    "from_category": from_category,
                    "win_from": start,
                    "win_to": end,
                } | extra
                try:
                    row = (await s.execute(text(sql), params)).mappings().one()
                    insufficient_sql, insufficient_extra = queries.build_cycle_insufficient_sql(
                        visible_ids=scope.visible_ids, project_id=scope.project_id
                    )
                    insufficient = (
                        await s.execute(text(insufficient_sql), params | insufficient_extra)
                    ).scalar_one()
                except Exception as exc:  # noqa: BLE001
                    self._translate_cost_error(exc)
                    raise
                return {
                    "project_id": str(project_id) if project_id else None,
                    "from_category": from_category,
                    "p50_seconds": _seconds(row["p50_seconds"]),
                    "p90_seconds": _seconds(row["p90_seconds"]),
                    "sample_size": int(row["sample_size"]),
                    "meta": {
                        "insufficient_data": int(insufficient),
                        "display_timezone": display_tz,
                    },
                }

            value, cached = await self._cached_or_compute(
                session,
                workspace_id=workspace_id,
                metric_key="cycle_time",
                scope_key=scope.scope_key,
                dimensions={
                    "project_id": str(project_id) if project_id else None,
                    "from_category": from_category,
                },
                window_start=start,
                window_end=end,
                refresh=refresh,
                compute=compute,
            )
        return _with_cached(value, cached)

    async def throughput(
        self,
        *,
        actor: Member,
        user,
        workspace_id: uuid.UUID,
        project_id=None,
        project_ids: list | None = None,
        win_from: str | None = None,
        win_to: str | None = None,
        granularity: str | None = None,
        tz: str | None = None,
        calendar_timezone: str | None = None,
        refresh: bool = False,
    ) -> dict:
        granularity = validate_granularity(granularity)
        start, end = parse_time_window(win_from, win_to, now=self._clock())
        async with self._session(workspace_id) as session:
            await self._guard_cost(session)
            workspace = await self._load_workspace(session, workspace_id)
            display_tz = resolve_display_timezone(user, workspace, tz)
            if calendar_timezone:
                assert_valid_timezone(calendar_timezone)
            cal_tz = calendar_timezone or display_tz
            scope = await self._issue_scope(
                session,
                actor=actor,
                workspace_id=workspace_id,
                project_id=project_id,
                project_ids=project_ids,
            )

            async def compute(s) -> dict:
                sql, extra = queries.build_throughput_sql(
                    visible_ids=scope.visible_ids, project_id=scope.project_id
                )
                params = {
                    "ws": workspace_id,
                    "win_from": start,
                    "win_to": end,
                    "granularity": granularity,
                    "calendar_tz": cal_tz,
                } | extra
                try:
                    rows = (await s.execute(text(sql), params)).mappings().all()
                except Exception as exc:  # noqa: BLE001
                    self._translate_cost_error(exc)
                    raise
                series = []
                total_created = total_completed = 0
                for row in rows:
                    created = int(row["created"])
                    completed = int(row["completed"])
                    total_created += created
                    total_completed += completed
                    series.append(
                        {
                            "label": _bucket_label(row["bucket_local"], granularity),
                            "bucket": _rfc3339(row["window_start_utc"]),
                            "window_start": _rfc3339(row["window_start_utc"]),
                            "window_end": _rfc3339(row["window_end_utc"]),
                            "created": created,
                            "completed": completed,
                            "net": created - completed,
                        }
                    )
                return {
                    "granularity": granularity,
                    "series": series,
                    "meta": {
                        "calendar_timezone": cal_tz,
                        "display_timezone": display_tz,
                        "net_window": total_created - total_completed,
                    },
                }

            value, cached = await self._cached_or_compute(
                session,
                workspace_id=workspace_id,
                metric_key="throughput",
                scope_key=scope.scope_key,
                dimensions={
                    "project_id": str(project_id) if project_id else None,
                    "project_ids": [str(p) for p in project_ids] if project_ids else None,
                    "granularity": granularity,
                    "calendar_timezone": cal_tz,
                },
                window_start=start,
                window_end=end,
                refresh=refresh,
                compute=compute,
            )
        return _with_cached(value, cached)

    async def velocity(
        self,
        *,
        actor: Member,
        user,
        workspace_id: uuid.UUID,
        project_id=None,
        cycle_ids: list | None = None,
        win_from: str | None = None,
        win_to: str | None = None,
        tz: str | None = None,
        refresh: bool = False,
    ) -> dict:
        if cycle_ids is not None and len(cycle_ids) > MAX_CYCLE_IDS:
            raise ValidationError(
                "too many cycle ids", details={"max": MAX_CYCLE_IDS}, code="filter_too_complex"
            )
        start, end = parse_time_window(win_from, win_to, now=self._clock())
        async with self._session(workspace_id) as session:
            await self._guard_cost(session)
            workspace = await self._load_workspace(session, workspace_id)
            display_tz = resolve_display_timezone(user, workspace, tz)
            scope = await self._issue_scope(
                session, actor=actor, workspace_id=workspace_id, project_id=project_id
            )
            if cycle_ids:
                # 直引 cycle 按归属项目可见性校验(§3.1 R3 / §5.6)
                cycles = (
                    await session.execute(
                        select(Cycle).where(Cycle.workspace_id == workspace_id, Cycle.id.in_(cycle_ids))
                    )
                ).scalars().all()
                if len(cycles) != len(set(cycle_ids)):
                    raise NotFoundError("cycle not found")
                for cycle in cycles:
                    if cycle.project_id is not None:
                        project = await self._load_project(session, workspace_id, cycle.project_id)
                        await self._assert_project_visible(session, project=project, actor=actor)

            async def compute(s) -> dict:
                sql, extra = queries.build_velocity_sql(
                    visible_ids=scope.visible_ids,
                    cycle_ids=cycle_ids,
                    project_id=scope.project_id,
                )
                params = {
                    "ws": workspace_id,
                    "display_tz": display_tz,
                    "win_from": start,
                    "win_to": end,
                } | extra
                try:
                    rows = (await s.execute(text(sql), params)).mappings().all()
                except Exception as exc:  # noqa: BLE001
                    self._translate_cost_error(exc)
                    raise
                return {
                    "cycles": [
                        {
                            "cycle_id": str(row["cycle_id"]),
                            "name": row["name"],
                            "starts_at": row["starts_at"].isoformat(),
                            "ends_at": row["ends_at"].isoformat(),
                            "state": row["state"],
                            "completed_issues": int(row["completed_issues"]),
                            "completed_points": _number(row["completed_points"]),
                            "completed_points_by_unit": {
                                "points": _number(row["completed_points_unit"]),
                                "hours": _number(row["completed_hours_unit"]),
                            },
                        }
                        for row in rows
                    ],
                    "meta": {
                        "display_timezone": display_tz,
                        "scope_caliber": SCOPE_CALIBER,
                    },
                }

            value, cached = await self._cached_or_compute(
                session,
                workspace_id=workspace_id,
                metric_key="velocity",
                scope_key=scope.scope_key,
                dimensions={
                    "project_id": str(project_id) if project_id else None,
                    "cycle_ids": sorted(str(c) for c in cycle_ids) if cycle_ids else None,
                    "tz": display_tz,
                },
                window_start=start,
                window_end=end,
                refresh=refresh,
                compute=compute,
            )
        return _with_cached(value, cached)

    async def burndown(
        self,
        *,
        actor: Member,
        user,
        workspace_id: uuid.UUID,
        cycle_id=None,
        milestone_id=None,
        metric: str | None = None,
        tz: str | None = None,
        refresh: bool = False,
    ) -> dict:
        if cycle_id is None and milestone_id is None:
            raise ValidationError(
                "cycle_id or milestone_id is required", code="burndown_scope_required"
            )
        if cycle_id is not None and milestone_id is not None:
            raise ValidationError(
                "provide exactly one of cycle_id or milestone_id",
                code="burndown_scope_conflict",
            )
        metric = validate_metric(metric)
        async with self._session(workspace_id) as session:
            await self._guard_cost(session)
            workspace = await self._load_workspace(session, workspace_id)
            display_tz = resolve_display_timezone(user, workspace, tz)
            if cycle_id is not None:
                cycle = await session.get(Cycle, cycle_id)
                if cycle is None or cycle.workspace_id != workspace_id:
                    raise NotFoundError("cycle not found")
                if cycle.project_id is not None:
                    project = await self._load_project(session, workspace_id, cycle.project_id)
                    await self._assert_project_visible(session, project=project, actor=actor)
                scope_type, scope_id, scope_column = "cycle", cycle.id, "cycle_id"
                day_from, day_to = cycle.starts_at, cycle.ends_at
            else:
                milestone = await session.get(Milestone, milestone_id)
                if milestone is None or milestone.workspace_id != workspace_id:
                    raise NotFoundError("milestone not found")
                project = await self._load_project(session, workspace_id, milestone.project_id)
                await self._assert_project_visible(session, project=project, actor=actor)
                if milestone.target_date is None:
                    raise ValidationError("milestone has no target date", code="validation_error")
                scope_type, scope_id, scope_column = "milestone", milestone.id, "milestone_id"
                day_from, day_to = milestone.created_at.date(), milestone.target_date
                if day_to < day_from:
                    day_from = day_to

            async def compute(s) -> dict:
                sql, extra = queries.build_burndown_sql(scope_column=scope_column, metric=metric)
                params = {
                    "ws": workspace_id,
                    "scope_id": scope_id,
                    "display_tz": display_tz,
                    "day_from": day_from,
                    "day_to": day_to,
                } | extra
                try:
                    rows = (await s.execute(text(sql), params)).mappings().all()
                except Exception as exc:  # noqa: BLE001
                    self._translate_cost_error(exc)
                    raise
                today = self._clock().astimezone(ZoneInfo(display_tz)).date()
                total_days = (day_to - day_from).days
                total = _number(rows[0]["total"]) if rows else 0
                actual = []
                ideal = []
                for row in rows:
                    d: date = row["date"]
                    if d < today:
                        actual.append({"date": d.isoformat(), "remaining": _number(row["remaining"])})
                    remaining_days = (day_to - d).days
                    ideal_value = total * remaining_days / total_days if total_days > 0 else 0
                    ideal.append({"date": d.isoformat(), "remaining": round(ideal_value, 4)})
                return {
                    "scope": {"type": scope_type, "id": str(scope_id)},
                    "window": {"start": day_from.isoformat(), "end": day_to.isoformat()},
                    "metric": metric,
                    "total": total,
                    "ideal": ideal,
                    "actual": actual,
                    "meta": {
                        "display_timezone": display_tz,
                        "scope_caliber": SCOPE_CALIBER,
                    },
                }

            window_start = datetime(day_from.year, day_from.month, day_from.day, tzinfo=UTC)
            window_end = datetime(day_to.year, day_to.month, day_to.day, 23, 59, 59, tzinfo=UTC)
            burndown_scope_key = await self._workspace_level_scope_key(
                session, actor=actor, workspace_id=workspace_id
            )
            value, cached = await self._cached_or_compute(
                session,
                workspace_id=workspace_id,
                metric_key="burndown",
                scope_key=burndown_scope_key,
                dimensions={
                    scope_type: str(scope_id),
                    "metric": metric,
                    "tz": display_tz,
                },
                window_start=window_start,
                window_end=window_end,
                refresh=refresh,
                compute=compute,
            )
        return _with_cached(value, cached)

    async def _workspace_level_scope_key(self, session, *, actor, workspace_id) -> str:
        """scope_key for window-scoped aggregates (burndown): ws_admin | projects:<hash>."""
        if is_workspace_manager(actor):
            return "ws_admin"
        ids = await visible_project_ids(session, workspace_id=workspace_id, member=actor)
        return f"projects:{hash_id_set(ids)}"

    # -- workload (never cached, §2.6) ------------------------------------------

    async def workload(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        project_id=None,
        member_type: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict:
        member_type = validate_member_type(member_type)
        page_limit = limit if limit is not None else DEFAULT_WORKLOAD_LIMIT
        if page_limit < 1:
            raise ValidationError("invalid limit", code="invalid_limit")
        from mesh.api.pagination import decode_cursor, encode_cursor

        async with self._session(workspace_id) as session:
            await self._guard_cost(session)
            scope = await self._issue_scope(
                session, actor=actor, workspace_id=workspace_id, project_id=project_id
            )
            try:
                open_counts = await exec_metrics.compute_workload_open(
                    session,
                    workspace_id=workspace_id,
                    visible_ids=scope.visible_ids,
                    project_id=scope.project_id,
                )
                inflight = await exec_metrics.compute_workload_inflight(
                    session, workspace_id=workspace_id, member=actor
                )
            except Exception as exc:  # noqa: BLE001
                self._translate_cost_error(exc)
                raise
            if inflight:
                agent_members = (
                    await session.execute(
                        select(Member).where(
                            Member.workspace_id == workspace_id,
                            Member.member_type == "agent",
                            Member.agent_id.in_(list(inflight.keys())),
                        )
                    )
                ).scalars().all()
            else:
                agent_members = []
            agent_member_by_member_id = {m.id: m for m in agent_members}
            member_ids = set(open_counts.keys()) | set(agent_member_by_member_id.keys())
            names = await exec_metrics.display_names_map(session, workspace_id, list(member_ids))

            rows: list[dict] = []
            for member_id in member_ids:
                display_name, mtype = names.get(member_id, ("unknown", "human"))
                if member_type is not None and mtype != member_type:
                    continue
                agent_member = agent_member_by_member_id.get(member_id)
                counts = inflight.get(agent_member.agent_id) if agent_member else None
                rows.append(
                    {
                        "member_id": str(member_id),
                        "display_name": display_name,
                        "member_type": mtype,
                        "open_issues": open_counts.get(member_id, 0),
                        "running": counts["running"] if counts else None,
                        "queued": counts["queued"] if counts else None,
                        "awaiting_approval": counts["awaiting_approval"] if counts else None,
                    }
                )
            rows.sort(
                key=lambda r: (
                    -r["open_issues"],
                    -(r["running"] if r["running"] is not None else -1),
                    r["member_id"],
                )
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                cursor_key = (str(position.sort_value), str(position.id))
                rows = [
                    r
                    for r in rows
                    if (_sort_key(r), r["member_id"]) > cursor_key
                ]
            page = rows[:page_limit]
            next_cursor = None
            if len(rows) > page_limit:
                last = page[-1]
                next_cursor = encode_cursor(_sort_key(last), uuid.UUID(last["member_id"]))
            return {"rows": page, "next_cursor": next_cursor}

    # -- agent stats -------------------------------------------------------------

    async def agent_stats(
        self,
        *,
        actor: Member,
        user,
        workspace_id: uuid.UUID,
        agent_id=None,
        win_from: str | None = None,
        win_to: str | None = None,
        refresh: bool = False,
    ) -> dict:
        start, end = parse_time_window(win_from, win_to, now=self._clock())
        async with self._session(workspace_id) as session:
            await self._guard_cost(session)
            workspace = await self._load_workspace(session, workspace_id)
            display_tz = resolve_display_timezone(user, workspace, None)
            if agent_id is not None:
                agent = await session.get(Agent, agent_id)
                if agent is None or agent.workspace_id != workspace_id or agent.deleted_at is not None:
                    raise NotFoundError("agent not found")
                if (
                    agent.visibility == "private"
                    and agent.owner_user_id != actor.user_id
                    and not is_workspace_manager(actor)
                ):
                    raise ForbiddenError("agent is private", code="agent_not_visible")
            scope_key = await compute_exec_scope_key(
                session, workspace_id=workspace_id, member=actor
            )

            async def compute(s) -> dict:
                try:
                    stats = await exec_metrics.compute_agent_stats_rows(
                        s,
                        workspace_id=workspace_id,
                        member=actor,
                        win_from=start,
                        win_to=end,
                        agent_id=agent_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._translate_cost_error(exc)
                    raise
                if stats:
                    member_rows = (
                        await s.execute(
                            select(Member).where(
                                Member.workspace_id == workspace_id,
                                Member.member_type == "agent",
                                Member.agent_id.in_(list(stats.keys())),
                            )
                        )
                    ).scalars().all()
                else:
                    member_rows = []
                names = await exec_metrics.display_names_map(
                    s, workspace_id, [m.id for m in member_rows]
                )
                name_by_agent = {}
                for member_row in member_rows:
                    display_name, mtype = names.get(member_row.id, ("unknown", "agent"))
                    name_by_agent[member_row.agent_id] = (display_name, mtype)
                agents = []
                for aid in sorted(stats.keys()):
                    row = stats[aid]
                    display_name, mtype = name_by_agent.get(aid, ("unknown", "agent"))
                    agents.append(
                        {"display_name": display_name, "member_type": mtype, **row}
                    )
                if agent_id is not None:
                    if not agents:
                        empty = {
                            "agent_id": str(agent_id),
                            "display_name": name_by_agent.get(agent_id, ("unknown", "agent"))[0],
                            "member_type": "agent",
                            "executions": 0,
                            "succeeded": 0,
                            "terminal": 0,
                            "cancelled_count": 0,
                            "success_rate": None,
                            "timeout_rate": None,
                            "avg_duration_seconds": None,
                            "retry_rate": None,
                            "tokens": {
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "total_tokens": 0,
                                "token_coverage": None,
                            },
                            "meta": {"token_note": exec_metrics.TOKEN_NOTE},
                        }
                        return {"single": empty}
                    return {"single": agents[0]}
                return {"agents": agents}

            value, cached = await self._cached_or_compute(
                session,
                workspace_id=workspace_id,
                metric_key="agent_stats",
                scope_key=scope_key,
                dimensions={"agent_id": str(agent_id)} if agent_id else {"mode": "all"},
                window_start=start,
                window_end=end,
                refresh=refresh,
                compute=compute,
            )
        payload = value["single"] if "single" in value else value
        out = _with_cached(payload, cached)
        out.setdefault("meta", {})["display_timezone"] = display_tz
        return out

    # -- dashboards ----------------------------------------------------------------

    async def project_dashboard(
        self,
        *,
        actor: Member,
        user,
        workspace_id: uuid.UUID,
        project_id,
        win_from: str | None = None,
        win_to: str | None = None,
        cycle_id=None,
        tz: str | None = None,
        refresh: bool = False,
    ) -> dict:
        async with self._session(workspace_id) as session:
            project = await self._load_project(session, workspace_id, project_id)
            await self._assert_project_visible(session, project=project, actor=actor)
        velocity_data = await self.velocity(
            actor=actor, user=user, workspace_id=workspace_id, project_id=project_id,
            win_from=win_from, win_to=win_to, tz=tz, refresh=refresh,
        )
        burndown_data = await self._project_dashboard_burndown(
            actor=actor, user=user, workspace_id=workspace_id, project_id=project_id,
            cycle_id=cycle_id, tz=tz, refresh=refresh,
        )
        cycle_data = await self.cycle_time(
            actor=actor, user=user, workspace_id=workspace_id, project_id=project_id,
            win_from=win_from, win_to=win_to, tz=tz, refresh=refresh,
        )
        return {
            "project_id": str(project_id),
            "velocity": velocity_data,
            "burndown": burndown_data,
            "cycle_time": cycle_data,
        }

    async def _project_dashboard_burndown(
        self, *, actor, user, workspace_id, project_id, cycle_id, tz, refresh
    ) -> dict | None:
        if cycle_id is None:
            async with self._session(workspace_id) as session:
                today = self._clock().date()
                cycle = (
                    await session.execute(
                        select(Cycle)
                        .where(
                            Cycle.workspace_id == workspace_id,
                            Cycle.project_id == project_id,
                            Cycle.state == "active",
                        )
                        .order_by(Cycle.starts_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if cycle is None:
                    cycle = (
                        await session.execute(
                            select(Cycle)
                            .where(
                                Cycle.workspace_id == workspace_id,
                                Cycle.project_id == project_id,
                                Cycle.starts_at <= today,
                                Cycle.ends_at >= today,
                            )
                            .order_by(Cycle.starts_at.desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                if cycle is None:
                    cycle = (
                        await session.execute(
                            select(Cycle)
                            .where(Cycle.workspace_id == workspace_id, Cycle.project_id == project_id)
                            .order_by(Cycle.starts_at.desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                if cycle is None:
                    return None
                cycle_id = cycle.id
        return await self.burndown(
            actor=actor, user=user, workspace_id=workspace_id, cycle_id=cycle_id, tz=tz,
            refresh=refresh,
        )

    async def workspace_dashboard(
        self,
        *,
        actor: Member,
        user,
        workspace_id: uuid.UUID,
        win_from: str | None = None,
        win_to: str | None = None,
        granularity: str | None = None,
        tz: str | None = None,
        calendar_timezone: str | None = None,
        refresh: bool = False,
    ) -> dict:
        throughput_data = await self.throughput(
            actor=actor, user=user, workspace_id=workspace_id,
            win_from=win_from, win_to=win_to, granularity=granularity,
            tz=tz, calendar_timezone=calendar_timezone, refresh=refresh,
        )
        workload_data = await self.workload(
            actor=actor, workspace_id=workspace_id, limit=DASHBOARD_WORKLOAD_LIMIT,
        )
        agent_data = await self.agent_stats(
            actor=actor, user=user, workspace_id=workspace_id,
            win_from=win_from, win_to=win_to, refresh=refresh,
        )
        filtered_note = not is_workspace_manager(actor)
        return {
            "throughput": throughput_data,
            "workload": {
                "data": workload_data["rows"],
                "next_cursor": workload_data["next_cursor"],
            },
            "agent_stats": agent_data,
            "meta": {"visibility_filtered": filtered_note},
        }


def _seconds(value) -> float | None:
    return round(float(value), 2) if value is not None else None


def _number(value):
    if value is None:
        return 0
    from decimal import Decimal

    if isinstance(value, Decimal):
        as_float = float(value)
        return int(as_float) if as_float == int(as_float) else as_float
    return value


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _bucket_label(bucket_local: datetime, granularity: str) -> str:
    if granularity == "month":
        return bucket_local.strftime("%Y-%m")
    return bucket_local.date().isoformat()


def _sort_key(row: dict) -> str:
    running = row["running"] if row["running"] is not None else -1
    return f"{_CURSOR_PAD - row['open_issues']:010d}:{_CURSOR_PAD - running:010d}"


def _with_cached(value: dict, cached: bool) -> dict:
    out = dict(value)
    meta = dict(out.get("meta", {}))
    meta["cached"] = cached
    out["meta"] = meta
    return out
