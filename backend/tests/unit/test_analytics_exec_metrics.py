"""Execution-metric service calibers + visibility matrix (analytics.md §2.2.4/§2.3/§2.3.1).

Four-requester matrix on the SAME authoritative aggregate SQL (R5/T33):
- m1 (plain): private-project executions AND private-agent executions dropped;
- m2 (private-project member): includes the private-project executions;
- m3 (private-agent owner): includes own private agent, still excludes the
  invisible private project;
- admin: full workspace aggregate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mesh.analytics.service import AnalyticsService
from mesh.config import load_settings
from mesh.db.models.runtime import ExecutionAttempt
from mesh.db.models.user import User
from mesh.errors import ForbiddenError, NotFoundError
from tests.unit.analytics_support import TS, make_execution, seed_world

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
WIN_FROM = "2026-07-01T00:00:00Z"
WIN_TO = "2026-07-29T00:00:00Z"


def _service(session_factory, db_url, redis_url, **overrides):
    base = {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "analytics-exec-test-signing-secret-00000",
        "daemon_tls_required": False,
    }
    return AnalyticsService(
        session_factory, load_settings(**(base | overrides)), clock=lambda: NOW
    )


async def _user(session, member) -> User:
    return await session.get(User, member.user_id)


class TestAgentStatsMatrix:
    async def test_four_requester_final_values(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        """同一权威聚合 SQL 对四类请求者的最终统计值(R5/T33 ⑦ 单元层)。"""
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            users = {
                name: await _user(session, member)
                for name, member in (
                    ("admin", world.admin),
                    ("m1", world.m1),
                    ("m2", world.m2),
                    ("m3", world.m3),
                )
            }

        # WA stats per requester (window covers TS=2026-07-10)
        stats = {}
        for name, member in (
            ("admin", world.admin),
            ("m1", world.m1),
            ("m2", world.m2),
            ("m3", world.m3),
        ):
            stats[name] = await service.agent_stats(
                actor=member, user=users[name], workspace_id=world.ws.id,
                agent_id=world.wa.id, win_from=WIN_FROM, win_to=WIN_TO,
            )

        # admin:全量——priv/pub/running/queued/approval = 5 执行,2 成功
        admin = stats["admin"]
        assert admin["executions"] == 5
        assert admin["succeeded"] == 2
        assert admin["terminal"] == 2
        assert admin["success_rate"] == 1.0
        assert admin["timeout_rate"] == 0.0
        assert admin["retry_rate"] == 0.0
        assert admin["tokens"]["total_tokens"] == 150
        assert admin["tokens"]["token_coverage"] == 0.2  # 1/5

        # m1:剔除 priv 项目执行 → 4 执行,1 成功;token 覆盖 1/4
        m1 = stats["m1"]
        assert m1["executions"] == 4
        assert m1["succeeded"] == 1
        assert m1["tokens"]["total_tokens"] == 150  # pub 执行的 token 仍可见
        assert m1["tokens"]["token_coverage"] == 0.25

        # m2:私有项目成员 → 与 admin 同(含 priv 执行)
        assert stats["m2"]["executions"] == 5
        assert stats["m2"]["succeeded"] == 2
        assert stats["m2"]["tokens"]["token_coverage"] == 0.2

        # m3:private agent owner,但非 priv 项目成员 → WA 执行仍剔除 priv → 4
        assert stats["m3"]["executions"] == 4
        assert stats["m3"]["succeeded"] == 1

    async def test_private_agent_stats_visibility(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            m1_user = await _user(session, world.m1)
            m3_user = await _user(session, world.m3)
            admin_user = await _user(session, world.admin)
        # 普通成员 → 403 agent_not_visible(不泄露统计存在性)
        with pytest.raises(ForbiddenError) as exc:
            await service.agent_stats(
                actor=world.m1, user=m1_user, workspace_id=world.ws.id,
                agent_id=world.pa.id, win_from=WIN_FROM, win_to=WIN_TO,
            )
        assert exc.value.code == "agent_not_visible"
        # owner 可见
        owner_stats = await service.agent_stats(
            actor=world.m3, user=m3_user, workspace_id=world.ws.id,
            agent_id=world.pa.id, win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert owner_stats["executions"] == 1  # 仅无 issue 的 manual 执行
        assert owner_stats["succeeded"] == 1
        assert owner_stats["tokens"]["token_coverage"] == 0.0
        # admin 可见
        admin_stats = await service.agent_stats(
            actor=world.admin, user=admin_user, workspace_id=world.ws.id,
            agent_id=world.pa.id, win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert admin_stats["executions"] == 1

    async def test_multi_agent_mode_filters_private_agent(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            m1_user = await _user(session, world.m1)
            admin_user = await _user(session, world.admin)
        m1_all = await service.agent_stats(
            actor=world.m1, user=m1_user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        m1_agent_ids = {a["agent_id"] for a in m1_all["agents"]}
        assert str(world.wa.id) in m1_agent_ids
        assert str(world.pa.id) not in m1_agent_ids  # private agent 不呈现
        admin_all = await service.agent_stats(
            actor=world.admin, user=admin_user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        admin_agent_ids = {a["agent_id"] for a in admin_all["agents"]}
        assert {str(world.wa.id), str(world.pa.id)} <= admin_agent_ids
        # display_name 快照存在
        wa_row = next(a for a in admin_all["agents"] if a["agent_id"] == str(world.wa.id))
        assert wa_row["display_name"] == "WA"
        assert wa_row["member_type"] == "agent"

    async def test_retry_rate_derived_from_attempts(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        # 追加一个 2 次 attempt 的执行(retry_count = 2-1 > 0)
        async with session_factory() as session, session.begin():
            retried = make_execution(ws=world.ws, agent=world.wa, issue=None,
                                     trigger="manual", status="failed",
                                     finished_at=TS)
            session.add(retried)
            await session.flush()
            session.add(ExecutionAttempt(workspace_id=world.ws.id,
                                         execution_id=retried.id, attempt_number=1,
                                         status="failed", started_at=TS, finished_at=TS))
            session.add(ExecutionAttempt(workspace_id=world.ws.id,
                                         execution_id=retried.id, attempt_number=2,
                                         status="failed", started_at=TS, finished_at=TS))
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.admin)
        data = await service.agent_stats(
            actor=world.admin, user=user, workspace_id=world.ws.id,
            agent_id=world.wa.id, win_from=WIN_FROM, win_to=WIN_TO,
        )
        # WA 执行:5 + 1 retried = 6;其中 1 个含重试 → 1/6
        assert data["executions"] == 6
        assert data["retry_rate"] == pytest.approx(1 / 6, abs=1e-4)
        # success_rate:completed=2,terminal=2+1 failed=3 → 2/3(cancelled 不入分母)
        assert data["success_rate"] == pytest.approx(2 / 3, abs=1e-4)

    async def test_agent_not_found(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        import uuid

        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        with pytest.raises(NotFoundError):
            await service.agent_stats(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                agent_id=uuid.uuid4(),
            )

    async def test_agent_without_executions_returns_zero_row(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        # 新建一个无任何执行的 agent
        from mesh.db.models.agent import Agent
        from mesh.db.models.member import Member

        async with session_factory() as session, session.begin():
            quiet = Agent(workspace_id=world.ws.id, name="Quiet",
                          owner_user_id=world.admin.user_id, visibility="workspace")
            session.add(quiet)
            await session.flush()
            session.add(Member(workspace_id=world.ws.id, member_type="agent",
                               agent_id=quiet.id, role="member", status="active"))
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        data = await service.agent_stats(
            actor=world.m1, user=user, workspace_id=world.ws.id, agent_id=quiet.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert data["executions"] == 0
        assert data["success_rate"] is None
        assert data["tokens"]["total_tokens"] == 0


class TestWorkload:
    async def _seed_pa_inflight(self, session_factory, world):
        async with session_factory() as session, session.begin():
            session.add(make_execution(ws=world.ws, agent=world.pa, issue=None,
                                       trigger="manual", status="running",
                                       finished_at=None))

    async def test_rows_unified_and_sorted(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        data = await service.workload(actor=world.admin, workspace_id=world.ws.id)
        rows = data["rows"]
        # m1 有 2 个 open issue(priv+pub,admin 全量) → 排首位
        assert rows[0]["member_id"] == str(world.m1.id)
        assert rows[0]["open_issues"] == 2
        assert rows[0]["member_type"] == "human"
        assert rows[0]["running"] is None  # 人类行 executions 字段为 null
        # WA 行:open 0,running 1 / queued 1 / awaiting_approval 1
        wa_row = next(r for r in rows if r["member_id"] == str(world.wa_member.id))
        assert wa_row["open_issues"] == 0
        assert wa_row["running"] == 1
        assert wa_row["queued"] == 1
        assert wa_row["awaiting_approval"] == 1
        assert wa_row["display_name"] == "WA"

    async def test_private_project_and_agent_filtering(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        await self._seed_pa_inflight(session_factory, world)
        service = _service(session_factory, db_url, redis_url)
        # m1:priv 项目 issue 剔除 → open 1;PA 在途执行不呈现
        m1 = await service.workload(actor=world.m1, workspace_id=world.ws.id)
        m1_rows = {r["member_id"]: r for r in m1["rows"]}
        assert m1_rows[str(world.m1.id)]["open_issues"] == 1
        assert str(world.pa_member.id) not in m1_rows  # private agent 执行不可见
        # m2:priv 可见 → open 2
        m2 = await service.workload(actor=world.m2, workspace_id=world.ws.id)
        m2_rows = {r["member_id"]: r for r in m2["rows"]}
        assert m2_rows[str(world.m1.id)]["open_issues"] == 2
        # m3:可见自家 PA 的在途执行
        m3 = await service.workload(actor=world.m3, workspace_id=world.ws.id)
        m3_rows = {r["member_id"]: r for r in m3["rows"]}
        assert m3_rows[str(world.pa_member.id)]["running"] == 1
        # admin:全量
        admin = await service.workload(actor=world.admin, workspace_id=world.ws.id)
        admin_rows = {r["member_id"]: r for r in admin["rows"]}
        assert admin_rows[str(world.pa_member.id)]["running"] == 1
        assert admin_rows[str(world.m1.id)]["open_issues"] == 2

    async def test_member_type_filter_and_pagination(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        agents_only = await service.workload(
            actor=world.admin, workspace_id=world.ws.id, member_type="agent"
        )
        assert all(r["member_type"] == "agent" for r in agents_only["rows"])
        assert agents_only["rows"], "WA 行应在"
        # 分页:limit=1 → 首页 m1(open 最多),游标翻页取到 WA
        page1 = await service.workload(actor=world.admin, workspace_id=world.ws.id, limit=1)
        assert len(page1["rows"]) == 1
        assert page1["next_cursor"]
        page2 = await service.workload(
            actor=world.admin, workspace_id=world.ws.id, limit=1,
            cursor=page1["next_cursor"],
        )
        assert len(page2["rows"]) == 1
        assert page2["rows"][0]["member_id"] != page1["rows"][0]["member_id"]
        # 翻到底 next_cursor=None
        page3 = await service.workload(
            actor=world.admin, workspace_id=world.ws.id, limit=10,
            cursor=page2["next_cursor"] if page2["next_cursor"] else None,
        )
        assert page3["rows"]


class TestDashboards:
    async def test_workspace_dashboard_visibility_note(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            m1_user = await _user(session, world.m1)
            admin_user = await _user(session, world.admin)
        m1_dash = await service.workspace_dashboard(
            actor=world.m1, user=m1_user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert m1_dash["meta"]["visibility_filtered"] is True
        agent_ids = {a["agent_id"] for a in m1_dash["agent_stats"]["agents"]}
        assert str(world.pa.id) not in agent_ids
        wa_row = next(a for a in m1_dash["agent_stats"]["agents"]
                      if a["agent_id"] == str(world.wa.id))
        assert wa_row["executions"] == 4  # 剔除 priv 执行后
        assert m1_dash["throughput"]["granularity"] == "day"
        assert m1_dash["workload"]["data"]
        admin_dash = await service.workspace_dashboard(
            actor=world.admin, user=admin_user, workspace_id=world.ws.id,
            win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert admin_dash["meta"]["visibility_filtered"] is False
        admin_ids = {a["agent_id"] for a in admin_dash["agent_stats"]["agents"]}
        assert str(world.pa.id) in admin_ids

    async def test_project_dashboard_composition(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        # pub 周期内放一个 done issue(供 velocity/burndown/cycle time 取数)
        from tests.unit.analytics_support import activity_category, make_issue

        async with session_factory() as session, session.begin():
            issue = make_issue(
                ws=world.ws, title="dash done", status=world.status_done, number=70,
                project=world.pub, cycle=world.cycle_pub, state_category="done",
                estimate=3, estimate_unit="points",
                completed_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
            )
            session.add(issue)
            await session.flush()
            session.add(activity_category(issue, actor=world.admin, old="todo",
                                          new="in_progress",
                                          at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC)))
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        dash = await service.project_dashboard(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            project_id=world.pub.id, win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert dash["project_id"] == str(world.pub.id)
        assert dash["velocity"]["cycles"][0]["completed_issues"] == 1
        assert dash["burndown"]["total"] == 3
        assert dash["cycle_time"]["sample_size"] == 1
        # 私有项目 → 403
        with pytest.raises(ForbiddenError):
            await service.project_dashboard(
                actor=world.m1, user=user, workspace_id=world.ws.id,
                project_id=world.priv.id,
            )

    async def test_project_dashboard_no_cycle_burndown_null(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        world = await seed_world(session_factory, workspace_factory, member_factory)
        # 删除 pub 的 cycle → burndown 为 null
        from sqlalchemy import delete

        from mesh.db.models.project import Cycle

        async with session_factory() as session, session.begin():
            await session.execute(
                delete(Cycle).where(Cycle.workspace_id == world.ws.id)
            )
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.m1)
        dash = await service.project_dashboard(
            actor=world.m1, user=user, workspace_id=world.ws.id,
            project_id=world.pub.id, win_from=WIN_FROM, win_to=WIN_TO,
        )
        assert dash["burndown"] is None


class TestReadOnly:
    async def test_queries_never_write_source_tables(
        self, session_factory, workspace_factory, member_factory, db_url, redis_url
    ):
        """§5.3:含 refresh=true 的查询不写真源表,仅 analytics_snapshots。"""
        world = await seed_world(session_factory, workspace_factory, member_factory)
        service = _service(session_factory, db_url, redis_url)
        async with session_factory() as session:
            user = await _user(session, world.admin)
        from sqlalchemy import text

        async def fingerprint():
            async with session_factory() as s:
                out = {}
                # issue_activity is insert-only (no updated_at column)
                tables = {
                    "issues": "updated_at",
                    "task_executions": "updated_at",
                    "execution_attempts": "updated_at",
                    "autopilot_runs": "updated_at",
                    "issue_activity": "created_at",
                }
                for table, stamp in tables.items():
                    count = (await s.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
                    max_stamp = (await s.execute(
                        text(f"SELECT COALESCE(MAX({stamp}), 'epoch'::timestamptz) FROM {table}")
                    )).scalar_one()
                    out[table] = (count, str(max_stamp))
                return out

        before = await fingerprint()
        await service.cycle_time(actor=world.admin, user=user, workspace_id=world.ws.id,
                                 win_from=WIN_FROM, win_to=WIN_TO, refresh=True)
        await service.throughput(actor=world.admin, user=user, workspace_id=world.ws.id,
                                 win_from=WIN_FROM, win_to=WIN_TO, refresh=True)
        await service.velocity(actor=world.admin, user=user, workspace_id=world.ws.id,
                               win_from=WIN_FROM, win_to=WIN_TO, refresh=True)
        await service.burndown(actor=world.admin, user=user, workspace_id=world.ws.id,
                               cycle_id=world.cycle_pub.id, refresh=True)
        await service.agent_stats(actor=world.admin, user=user, workspace_id=world.ws.id,
                                  win_from=WIN_FROM, win_to=WIN_TO, refresh=True)
        await service.workload(actor=world.admin, workspace_id=world.ws.id)
        await service.workspace_dashboard(actor=world.admin, user=user,
                                          workspace_id=world.ws.id,
                                          win_from=WIN_FROM, win_to=WIN_TO, refresh=True)
        after = await fingerprint()
        assert before == after
        # 且缓存表确有写入
        from sqlalchemy import select

        from mesh.db.models.analytics import AnalyticsSnapshot

        async with session_factory() as session:
            snaps = (await session.execute(select(AnalyticsSnapshot))).scalars().all()
            assert len(snaps) >= 5
