"""Issue-metric service calibers (analytics.md §2.2.1–§2.2.5, §5.1–§5.2).

Fixed clock NOW = 2026-07-29T12:00Z; all expectations hand-computed from
the spec SQL so the service is pinned to §2 line-for-line.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mesh.analytics.service import AnalyticsService
from mesh.config import load_settings
from mesh.db.models.issue import Issue
from mesh.db.models.user import User
from mesh.errors import ForbiddenError, ValidationError
from tests.unit.analytics_support import activity_category, make_issue, seed_world

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
WIN_FROM = "2026-07-01T00:00:00Z"
WIN_TO = "2026-07-29T00:00:00Z"


def _settings(db_url: str, redis_url: str, **overrides):
    base = {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "analytics-service-test-signing-secret-000",
        "daemon_tls_required": False,
    }
    return load_settings(**(base | overrides))


def _service(session_factory, db_url, redis_url, **settings_overrides):
    return AnalyticsService(
        session_factory,
        _settings(db_url, redis_url, **settings_overrides),
        clock=lambda: NOW,
    )


async def _user(session, member) -> User:
    return await session.get(User, member.user_id)


async def _seed_cycle_time_world(session_factory, workspace_factory, member_factory):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session, session.begin():
        # 3 valid samples on the public project (durations 2d / 6d / 4d)
        samples = []
        for number, completed_day, started_day in ((10, 10, 8), (11, 12, 6), (12, 14, 10)):
            issue = make_issue(
                ws=world.ws, title=f"done {number}", status=world.status_done, number=number,
                project=world.pub, state_category="done",
                completed_at=datetime(2026, 7, completed_day, 12, 0, tzinfo=UTC),
            )
            session.add(issue)
            await session.flush()
            session.add(activity_category(
                issue, actor=world.admin, old="todo", new="in_progress",
                at=datetime(2026, 7, started_day, 12, 0, tzinfo=UTC),
            ))
            samples.append(issue)
        # no state trail → insufficient_data
        session.add(make_issue(
            ws=world.ws, title="no trail", status=world.status_done, number=13,
            project=world.pub, state_category="done",
            completed_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        ))
        # negative duration (trail after completion) → insufficient_data
        neg = make_issue(
            ws=world.ws, title="neg", status=world.status_done, number=14,
            project=world.pub, state_category="done",
            completed_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        )
        session.add(neg)
        await session.flush()
        session.add(activity_category(
            neg, actor=world.admin, old="todo", new="in_progress",
            at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        ))
        # done issue in the private project (invisible to m1, visible to admin)
        priv_issue = make_issue(
            ws=world.ws, title="priv done", status=world.status_done, number=15,
            project=world.priv, state_category="done",
            completed_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )
        session.add(priv_issue)
        await session.flush()
        session.add(activity_category(
            priv_issue, actor=world.admin, old="todo", new="in_progress",
            at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        ))
    return world


class TestCycleTime:
    async def test_p50_p90_and_insufficient(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await _seed_cycle_time_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.cycle_time(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        # durations: 2d=172800, 6d=518400, 4d=345600 → P50=345600
        assert data["sample_size"] == 3
        assert data["p50_seconds"] == 345600
        # P90 = 345600 + 0.8 * (518400 - 345600) = 483840
        assert data["p90_seconds"] == 483840
        assert data["meta"]["insufficient_data"] == 2  # 无留痕 + 负时长
        assert data["meta"]["display_timezone"] == "UTC"
        assert data["from_category"] == "in_progress"

    async def test_admin_sees_private_project_sample(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await _seed_cycle_time_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.admin)
        data = await service.cycle_time(
            actor=world.admin, user=user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert data["sample_size"] == 4  # 含 private 项目样本

    async def test_project_scoped(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await _seed_cycle_time_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.cycle_time(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            project_id=world.pub.id, win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert data["sample_size"] == 3
        assert data["project_id"] == str(world.pub.id)
        with pytest.raises(ForbiddenError) as exc:
            await service.cycle_time(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                project_id=world.priv.id, win_from=WIN_FROM, win_to=WIN_TO,
            )
        assert exc.value.code == "project_not_visible"

    async def test_cached_then_refresh(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await _seed_cycle_time_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        first = await service.cycle_time(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert first["meta"]["cached"] is False
        # 真源变化:新增一个 done 样本
        async with session_factory() as session, session.begin():
            issue = make_issue(
                ws=world.ws, title="late done", status=world.status_done, number=30,
                project=world.pub, state_category="done",
                completed_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
            )
            session.add(issue)
            await session.flush()
            session.add(activity_category(
                issue, actor=world.admin, old="todo", new="in_progress",
                at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
            ))
        # 未 refresh → 命中缓存旧值
        second = await service.cycle_time(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert second["meta"]["cached"] is True
        assert second["sample_size"] == 3
        # refresh=true → 重算与真源一致(§5.5)
        third = await service.cycle_time(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO, refresh=True,
        )
        assert third["meta"]["cached"] is False
        assert third["sample_size"] == 4

    async def test_invalid_inputs(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        with pytest.raises(ValidationError) as exc:
            await service.cycle_time(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                win_from=WIN_TO, win_to=WIN_FROM,
            )
        assert exc.value.code == "invalid_time_range"
        with pytest.raises(ValidationError) as exc:
            await service.cycle_time(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                from_category="warp",
            )
        assert exc.value.code == "validation_error"
        with pytest.raises(ValidationError) as exc:
            await service.cycle_time(
                actor=world.m1, user=user, workspace_id=world.ws.id, tz="Bad/Zone",
            )
        assert exc.value.code == "invalid_timezone"


class TestThroughput:
    async def _seed(self, session_factory, workspace_factory, member_factory):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        async with session_factory() as session, session.begin():
            # 上海当地 7/25 00:30(= UTC 7/24 16:30)创建 → 当地 7/25 桶
            session.add(make_issue(
                ws=world.ws, title="t1", status=world.status_todo, number=20,
                project=world.pub, created_at=datetime(2026, 7, 24, 16, 30, tzinfo=UTC),
            ))
            # 上海当地 7/24 23:59(= UTC 7/24 15:59)创建 → 当地 7/24 桶
            session.add(make_issue(
                ws=world.ws, title="t2", status=world.status_todo, number=21,
                project=world.pub, created_at=datetime(2026, 7, 24, 15, 59, tzinfo=UTC),
            ))
            done = make_issue(
                ws=world.ws, title="t3", status=world.status_done, number=22,
                project=world.pub, state_category="done",
                created_at=datetime(2026, 7, 20, 0, 0, tzinfo=UTC),
                completed_at=datetime(2026, 7, 24, 17, 0, tzinfo=UTC),  # 上海 7/25 01:00
            )
            session.add(done)
            # private project issue — m1 不可见
            session.add(make_issue(
                ws=world.ws, title="t4", status=world.status_todo, number=23,
                project=world.priv, created_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
            ))
        return world

    async def test_calendar_timezone_local_day_bucketing(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await self._seed(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.throughput(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from="2026-07-24T00:00:00Z", win_to="2026-07-26T00:00:00Z",
            calendar_timezone="Asia/Shanghai",
        )
        buckets = {b["label"]: b for b in data["series"]}
        # UTC+8 下当地 7/25 桶覆盖 UTC 7/24 16:00 – 7/25 16:00(本地自然日不跨桶)
        b25 = buckets["2026-07-25"]
        assert b25["window_start"] == "2026-07-24T16:00:00Z"
        assert b25["window_end"] == "2026-07-25T16:00:00Z"
        assert b25["created"] == 1  # t1
        assert b25["completed"] == 1  # t3
        assert b25["net"] == 0
        b24 = buckets["2026-07-24"]
        assert b24["window_start"] == "2026-07-23T16:00:00Z"
        assert b24["created"] == 1  # t2(priv 的 t4 对 m1 不可见)
        assert data["meta"]["calendar_timezone"] == "Asia/Shanghai"
        assert data["meta"]["net_window"] == 1  # created 2 - completed 1

    async def test_utc_bucketing_differs(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await self._seed(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.throughput(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from="2026-07-24T00:00:00Z", win_to="2026-07-26T00:00:00Z",
            calendar_timezone="UTC",
        )
        buckets = {b["label"]: b for b in data["series"]}
        # UTC 分桶:t1+t2+t3-completed 全落 7/24
        assert buckets["2026-07-24"]["created"] == 2
        assert buckets["2026-07-24"]["completed"] == 1

    async def test_week_and_month_granularity(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await self._seed(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        week = await service.throughput(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from="2026-07-20T00:00:00Z", win_to="2026-07-27T00:00:00Z",
            granularity="week", calendar_timezone="Asia/Shanghai",
        )
        labels = [b["label"] for b in week["series"]]
        assert labels == ["2026-07-20"]  # 当地周一起
        assert week["series"][0]["window_start"] == "2026-07-19T16:00:00Z"
        month = await service.throughput(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from="2026-07-20T00:00:00Z", win_to="2026-07-27T00:00:00Z",
            granularity="month",
        )
        assert [b["label"] for b in month["series"]] == ["2026-07"]

    async def test_multi_project_invisible_whole_403(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        with pytest.raises(ForbiddenError) as exc:
            await service.throughput(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                project_ids=[world.pub.id, world.priv.id],
            )
        assert exc.value.code == "project_not_visible"
        # m2(私有项目成员)可以
        data = await service.throughput(
            actor=world.m2, user=user, workspace_id=world.ws.id,
            project_ids=[world.pub.id, world.priv.id],
        )
        assert data["granularity"] == "day"

    async def test_admin_vs_member_cache_rows_separate(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await self._seed(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            m1_user = await _user(session, world.m1)
            admin_user = await _user(session, world.admin)
        admin_data = await service.throughput(
            actor=world.admin, user=admin_user, workspace_id=world.ws.id,
            win_from="2026-07-24T00:00:00Z", win_to="2026-07-26T00:00:00Z",
        )
        m1_data = await service.throughput(
            actor=world.m1, user=m1_user, workspace_id=world.ws.id,
            win_from="2026-07-24T00:00:00Z", win_to="2026-07-26T00:00:00Z",
        )
        # admin 含 priv 的 t4(created +1),m1 不含;两者缓存分行互不串读
        assert admin_data["meta"]["net_window"] == 2
        assert m1_data["meta"]["net_window"] == 1
        from sqlalchemy import select

        from mesh.db.models.analytics import AnalyticsSnapshot
        async with session_factory() as session:
            rows = (await session.execute(select(AnalyticsSnapshot).where(
                AnalyticsSnapshot.metric_key == "throughput"
            ))).scalars().all()
            scope_keys = {r.scope_key for r in rows}
            assert "ws_admin" in scope_keys
            assert any(k.startswith("projects:") for k in scope_keys)
            assert len(scope_keys) == 2


class TestVelocity:
    async def _seed(self, session_factory, workspace_factory, member_factory):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        async with session_factory() as session, session.begin():
            # cycle_pub 窗内完成:3 points + 2 hours
            session.add(make_issue(
                ws=world.ws, title="v1", status=world.status_done, number=40,
                project=world.pub, cycle=world.cycle_pub, state_category="done",
                estimate=3, estimate_unit="points",
                completed_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
            ))
            session.add(make_issue(
                ws=world.ws, title="v2", status=world.status_done, number=41,
                project=world.pub, cycle=world.cycle_pub, state_category="done",
                estimate=2, estimate_unit="hours",
                completed_at=datetime(2026, 7, 12, 23, 0, tzinfo=UTC),
            ))
            # 窗外完成 → 不计
            session.add(make_issue(
                ws=world.ws, title="v3", status=world.status_done, number=42,
                project=world.pub, cycle=world.cycle_pub, state_category="done",
                estimate=5, estimate_unit="points",
                completed_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
            ))
            # 未挂 cycle 的 done issue → 不计入任何周期
            session.add(make_issue(
                ws=world.ws, title="v4", status=world.status_done, number=43,
                project=world.pub, state_category="done",
                estimate=8, estimate_unit="points",
                completed_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
            ))
        return world

    async def test_velocity_counts_and_units(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await self._seed(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.velocity(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            project_id=world.pub.id, win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert len(data["cycles"]) == 1
        row = data["cycles"][0]
        assert row["cycle_id"] == str(world.cycle_pub.id)
        assert row["completed_issues"] == 2
        assert row["completed_points"] == 5
        assert row["completed_points_by_unit"] == {"points": 3, "hours": 2}
        assert data["meta"]["scope_caliber"] == "current_attribution"

    async def test_explicit_cycle_ids_private_403(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        with pytest.raises(ForbiddenError) as exc:
            await service.velocity(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                cycle_ids=[world.cycle_priv.id],
            )
        assert exc.value.code == "project_not_visible"
        # 项目成员 m2 可查
        async with session_factory() as session:
            m2_user = await _user(session, world.m2)
        data = await service.velocity(
            actor=world.m2, user=m2_user, workspace_id=world.ws.id,
            cycle_ids=[world.cycle_priv.id],
        )
        assert [c["cycle_id"] for c in data["cycles"]] == [str(world.cycle_priv.id)]

    async def test_current_attribution_recomputes_on_move(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await self._seed(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.admin)
        before = await service.velocity(
            actor=world.admin, user=user, workspace_id=world.ws.id,
            cycle_ids=[world.cycle_pub.id],
        )
        assert before["cycles"][0]["completed_issues"] == 2
        # 把 v1 移出 cycle(当前归属口径:历史 velocity 随之重算)
        async with session_factory() as session, session.begin():
            from sqlalchemy import select
            issue = (await session.execute(
                select(Issue).where(Issue.workspace_id == world.ws.id, Issue.number == 40)
            )).scalar_one()
            issue.cycle_id = None
        after = await service.velocity(
            actor=world.admin, user=user, workspace_id=world.ws.id,
            cycle_ids=[world.cycle_pub.id], refresh=True,
        )
        assert after["cycles"][0]["completed_issues"] == 1
        assert after["cycles"][0]["completed_points"] == 2

    async def test_cycle_ids_limit(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        import uuid as _uuid
        with pytest.raises(ValidationError) as exc:
            await service.velocity(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                cycle_ids=[_uuid.uuid4() for _ in range(21)],
            )
        assert exc.value.code == "filter_too_complex"


class TestBurndown:
    async def _seed(self, session_factory, workspace_factory, member_factory):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        async with session_factory() as session, session.begin():
            # scope: 3 pts(7/8 完成)+ 2 pts(未完成)+ 1 pt(7/11 完成)= total 6
            session.add(make_issue(
                ws=world.ws, title="b1", status=world.status_done, number=50,
                project=world.pub, cycle=world.cycle_pub, state_category="done",
                estimate=3, completed_at=datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
            ))
            session.add(make_issue(
                ws=world.ws, title="b2", status=world.status_todo, number=51,
                project=world.pub, cycle=world.cycle_pub, estimate=2,
            ))
            session.add(make_issue(
                ws=world.ws, title="b3", status=world.status_done, number=52,
                project=world.pub, cycle=world.cycle_pub, state_category="done",
                estimate=1, completed_at=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
            ))
        return world

    async def test_ideal_and_actual_lines(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await self._seed(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.burndown(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            cycle_id=world.cycle_pub.id, metric="points",
        )
        assert data["total"] == 6
        assert data["scope"] == {"type": "cycle", "id": str(world.cycle_pub.id)}
        assert data["meta"]["scope_caliber"] == "current_attribution"
        actual = {p["date"]: p["remaining"] for p in data["actual"]}
        # NOW=7/29 → 全部日皆过去(7/6..7/12)
        assert actual == {
            "2026-07-06": 6, "2026-07-07": 6, "2026-07-08": 3, "2026-07-09": 3,
            "2026-07-10": 3, "2026-07-11": 2, "2026-07-12": 2,
        }
        ideal = {p["date"]: p["remaining"] for p in data["ideal"]}
        assert ideal["2026-07-06"] == 6
        assert ideal["2026-07-12"] == 0
        assert ideal["2026-07-09"] == 3  # 6 * 3/6

    async def test_count_metric(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await self._seed(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.burndown(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            cycle_id=world.cycle_pub.id, metric="count",
        )
        assert data["total"] == 3
        actual = {p["date"]: p["remaining"] for p in data["actual"]}
        assert actual["2026-07-06"] == 3
        assert actual["2026-07-12"] == 1

    async def test_scope_errors(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        with pytest.raises(ValidationError) as exc:
            await service.burndown(actor=world.m1, user=user, workspace_id=world.ws.id)
        assert exc.value.code == "burndown_scope_required"
        with pytest.raises(ValidationError) as exc:
            await service.burndown(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                cycle_id=world.cycle_pub.id, milestone_id=world.milestone_pub.id,
            )
        assert exc.value.code == "burndown_scope_conflict"

    async def test_private_cycle_403(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        with pytest.raises(ForbiddenError) as exc:
            await service.burndown(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                cycle_id=world.cycle_priv.id,
            )
        assert exc.value.code == "project_not_visible"

    async def test_milestone_scope(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        # 显式 created_at 固定里程碑窗起点
        async with session_factory() as session, session.begin():
            from sqlalchemy import select

            from mesh.db.models.project import Milestone
            milestone = (await session.execute(
                select(Milestone).where(Milestone.id == world.milestone_pub.id)
            )).scalar_one()
            milestone.created_at = datetime(2026, 7, 1, tzinfo=UTC)
            session.add(make_issue(
                ws=world.ws, title="m1", status=world.status_done, number=60,
                project=world.pub, milestone=world.milestone_pub, state_category="done",
                estimate=4, completed_at=datetime(2026, 7, 5, 12, 0, tzinfo=UTC),
            ))
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.burndown(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            milestone_id=world.milestone_pub.id,
        )
        assert data["scope"]["type"] == "milestone"
        assert data["window"] == {"start": "2026-07-01", "end": "2026-07-20"}
        assert data["total"] == 4
        actual = {p["date"]: p["remaining"] for p in data["actual"]}
        assert actual["2026-07-04"] == 4
        assert actual["2026-07-05"] == 0

    async def test_milestone_without_target_date(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        async with session_factory() as session, session.begin():
            from sqlalchemy import select

            from mesh.db.models.project import Milestone
            milestone = (await session.execute(
                select(Milestone).where(Milestone.id == world.milestone_pub.id)
            )).scalar_one()
            milestone.target_date = None
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        with pytest.raises(ValidationError) as exc:
            await service.burndown(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                milestone_id=world.milestone_pub.id,
            )
        assert exc.value.code == "validation_error"


class TestStaleWhileRevalidate:
    async def test_stale_returns_old_and_refreshes_in_background(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await _seed_cycle_time_world(session_factory, workspace_factory, member_factory)
        clocks = [NOW]
        service = AnalyticsService(
            session_factory,
            _settings(db_url, redis_url, analytics_stale_while_revalidate=True),
            clock=lambda: clocks[0],
        )
        async with session_factory() as session:
            user = await _user(session, world.m1)
        first = await service.cycle_time(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert first["sample_size"] == 3
        # 时钟推进超过 TTL → 过期
        clocks[0] = NOW + timedelta(minutes=16)
        second = await service.cycle_time(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert second["meta"]["cached"] is True  # stale-while-revalidate 返回旧值
        assert second["sample_size"] == 3
        # 后台重算任务完成后缓存刷新
        import asyncio
        if service._bg_tasks:
            await asyncio.gather(*service._bg_tasks)
        clocks[0] = NOW + timedelta(minutes=16, seconds=1)
        third = await service.cycle_time(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert third["meta"]["cached"] is True
        assert third["sample_size"] == 3  # 真源未变,重算值一致
