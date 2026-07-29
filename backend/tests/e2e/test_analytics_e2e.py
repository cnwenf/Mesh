"""Analytics REAL end-to-end tests (analytics.md §5, T33 seven items).

Real uvicorn API subprocess (RLS app role) + real PostgreSQL — no mocks on
any contract path. T33 matrix: the SAME authoritative aggregate SQL
(visible_executions CTE, §2.3.1 R5) asserted on FINAL STAT VALUES for four
requester classes over HTTP — plain member / project member /
private-agent owner / admin — plus cross-permission cache isolation,
whole-403 negatives, calendar-timezone bucketing incl. DST, read-only
audit and current-attribution caliber.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import select, text

from mesh.db.models.agent import Agent
from mesh.db.models.autopilot import Autopilot, AutopilotRun
from mesh.db.models.issue import Issue, IssueActivity, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.project import Cycle, Project, ProjectMember
from mesh.db.models.runtime import ExecutionAttempt, TaskExecution
from mesh.db.models.user import User

pytestmark = pytest.mark.e2e

TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
WIN_Q = "from=2026-07-01T00:00:00Z&to=2026-07-29T00:00:00Z"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_login(client: httpx.AsyncClient, name: str) -> tuple[str, str]:
    email = f"{name}-{uuid.uuid4().hex[:8]}@x.io"
    register = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "S3cure-Analytics-12345!", "display_name": name},
    )
    assert register.status_code in (200, 201), register.text
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "S3cure-Analytics-12345!"}
    )
    assert login.status_code == 200, login.text
    data = login.json()["data"]
    return data["access_token"], email


async def _user_id(session_factory, email: str) -> uuid.UUID:
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        return user.id


async def _seed_world(api_client, session_factory) -> dict:
    """Four users via API + full visibility world via ORM (deterministic)."""
    tokens, emails = {}, {}
    for name in ("admin", "m1", "m2", "m3"):
        tokens[name], emails[name] = await _register_login(api_client, name)

    ws_resp = await api_client.post(
        "/api/v1/workspaces",
        json={"name": "AN E2E", "slug": f"an-e2e-{uuid.uuid4().hex[:8]}"},
        headers=_auth(tokens["admin"]),
    )
    assert ws_resp.status_code == 201, ws_resp.text
    ws = ws_resp.json()["data"]
    ws_id = uuid.UUID(ws["id"])

    async with session_factory() as session, session.begin():
        admin_user = (
            await session.execute(select(User).where(User.email == emails["admin"]))
        ).scalar_one()
        admin_member = (
            await session.execute(
                select(Member).where(
                    Member.workspace_id == ws_id, Member.user_id == admin_user.id
                )
            )
        ).scalar_one()
        members = {"admin": admin_member}
        for name in ("m1", "m2", "m3"):
            user = (
                await session.execute(select(User).where(User.email == emails[name]))
            ).scalar_one()
            member = Member(
                workspace_id=ws_id, user_id=user.id, member_type="human",
                role="member", status="active",
            )
            session.add(member)
            members[name] = member
        await session.flush()

        pub = Project(workspace_id=ws_id, name="Pub", key=f"PUB{uuid.uuid4().hex[:4].upper()}",
                      visibility="public")
        priv = Project(workspace_id=ws_id, name="Priv",
                       key=f"PRV{uuid.uuid4().hex[:4].upper()}", visibility="private")
        session.add_all([pub, priv])
        await session.flush()
        session.add(ProjectMember(workspace_id=ws_id, project_id=priv.id,
                                  member_id=members["m2"].id, role="member"))

        wa = Agent(workspace_id=ws_id, name="WA", owner_user_id=admin_user.id,
                   visibility="workspace")
        pa = Agent(workspace_id=ws_id, name="PA",
                   owner_user_id=members["m3"].user_id, visibility="private")
        session.add_all([wa, pa])
        await session.flush()
        session.add(Member(workspace_id=ws_id, member_type="agent", agent_id=wa.id,
                           role="member", status="active"))
        session.add(Member(workspace_id=ws_id, member_type="agent", agent_id=pa.id,
                           role="member", status="active"))

        todo_status = (
            await session.execute(
                select(IssueStatus).where(
                    IssueStatus.workspace_id == ws_id, IssueStatus.category == "todo"
                )
            )
        ).scalars().first()
        done_status = (
            await session.execute(
                select(IssueStatus).where(
                    IssueStatus.workspace_id == ws_id, IssueStatus.category == "done"
                )
            )
        ).scalars().first()

        def issue(number, *, project, status, category, assignee=None, cycle=None,
                  estimate=None, completed_at=None, created_at=None, title="i"):
            kwargs = {}
            if created_at is not None:
                kwargs["created_at"] = created_at
            return Issue(
                workspace_id=ws_id, title=f"{title}{number}",
                identifier_namespace_key=project.key, number=number,
                identifier=f"{project.key}-{number}", status_id=status.id,
                state_category=category, project_id=project.id,
                cycle_id=cycle.id if cycle else None,
                assignee_id=assignee.id if assignee else None,
                estimate=estimate, completed_at=completed_at, **kwargs,
            )

        issue_priv = issue(1, project=priv, status=todo_status, category="todo",
                           assignee=members["m1"], title="priv-")
        issue_pub = issue(2, project=pub, status=todo_status, category="todo",
                          assignee=members["m1"], title="pub-")
        # 供吞吐量时区/DST 测试的 issue(显式 created_at)
        issue_tz = issue(3, project=pub, status=todo_status, category="todo",
                         created_at=datetime(2026, 7, 24, 16, 30, tzinfo=UTC), title="tz-")
        issue_dst_a = issue(4, project=pub, status=todo_status, category="todo",
                            created_at=datetime(2026, 3, 7, 12, 0, tzinfo=UTC), title="dsta-")
        issue_dst_b = issue(5, project=pub, status=todo_status, category="todo",
                            created_at=datetime(2026, 3, 8, 12, 0, tzinfo=UTC), title="dstb-")
        # velocity/burndown 用 done issue(挂 pub 周期)
        cycle_pub = Cycle(workspace_id=ws_id, name="C-pub", project_id=pub.id,
                          starts_at=date(2026, 7, 6), ends_at=date(2026, 7, 12))
        cycle_priv = Cycle(workspace_id=ws_id, name="C-priv", project_id=priv.id,
                           starts_at=date(2026, 7, 6), ends_at=date(2026, 7, 12))
        session.add_all([issue_priv, issue_pub, issue_tz, issue_dst_a, issue_dst_b,
                         cycle_pub, cycle_priv])
        await session.flush()  # cycle ids must exist before issue references them
        issue_done = issue(6, project=pub, status=done_status, category="done",
                           cycle=cycle_pub, estimate=3,
                           completed_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
                           title="done-")
        session.add(issue_done)
        await session.flush()
        session.add(IssueActivity(
            workspace_id=ws_id, issue_id=issue_done.id,
            actor_member_id=admin_member.id, field="state_category",
            old_value="todo", new_value="in_progress",
            created_at=datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
        ))

        # 执行矩阵(T33):WA 5 条 / PA 2 条
        exec_wa_priv = TaskExecution(workspace_id=ws_id, agent_id=wa.id,
                                     issue_id=issue_priv.id, trigger="assign",
                                     status="completed", queued_at=TS, finished_at=TS)
        exec_wa_pub = TaskExecution(workspace_id=ws_id, agent_id=wa.id,
                                    issue_id=issue_pub.id, trigger="assign",
                                    status="completed", queued_at=TS, finished_at=TS)
        exec_wa_running = TaskExecution(workspace_id=ws_id, agent_id=wa.id,
                                        issue_id=issue_pub.id, trigger="assign",
                                        status="running", queued_at=TS)
        exec_wa_queued = TaskExecution(workspace_id=ws_id, agent_id=wa.id,
                                       issue_id=None, trigger="manual",
                                       status="queued", queued_at=TS)
        exec_pa_manual = TaskExecution(workspace_id=ws_id, agent_id=pa.id,
                                       issue_id=None, trigger="manual",
                                       status="completed", queued_at=TS, finished_at=TS)
        exec_wa_approval = TaskExecution(workspace_id=ws_id, agent_id=wa.id,
                                         issue_id=None, trigger="manual",
                                         status="awaiting_approval", queued_at=TS)
        exec_pa_running = TaskExecution(workspace_id=ws_id, agent_id=pa.id,
                                        issue_id=None, trigger="manual",
                                        status="running", queued_at=TS)
        session.add_all([exec_wa_priv, exec_wa_pub, exec_wa_running, exec_wa_queued,
                         exec_wa_approval, exec_pa_manual, exec_pa_running])
        await session.flush()
        # priv 执行重试 1 次(2 attempts)→ retry 派生
        session.add(ExecutionAttempt(workspace_id=ws_id, execution_id=exec_wa_priv.id,
                                     attempt_number=1, status="failed",
                                     started_at=TS, finished_at=TS))
        session.add(ExecutionAttempt(workspace_id=ws_id, execution_id=exec_wa_priv.id,
                                     attempt_number=2, status="completed",
                                     started_at=TS, finished_at=TS))
        session.add(ExecutionAttempt(workspace_id=ws_id, execution_id=exec_wa_pub.id,
                                     attempt_number=1, status="completed",
                                     started_at=TS, finished_at=TS))
        # token 唯一真源 autopilot_runs(仅 autopilot 触发执行有 token)
        autopilot = Autopilot(workspace_id=ws_id, name="AP",
                              trigger_type="issue_created", created_by=admin_member.id)
        session.add(autopilot)
        await session.flush()
        session.add(AutopilotRun(workspace_id=ws_id, autopilot_id=autopilot.id,
                                 trigger_type="issue_created",
                                 execution_id=exec_wa_pub.id, status="succeeded",
                                 started_at=TS, prompt_tokens=100, completion_tokens=50))

    return {
        "ws_id": str(ws_id),
        "tokens": tokens,
        "pub": pub.id,
        "priv": priv.id,
        "wa": wa.id,
        "pa": pa.id,
        "cycle_pub": cycle_pub.id,
        "cycle_priv": cycle_priv.id,
    }


class TestT33FourRequesterMatrix:
    """⑦ 同一权威聚合 SQL 对四类请求者断言最终统计值(R5 HIGH-3)。"""

    async def test_agent_stats_final_values_per_requester(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws, wa = world["ws_id"], world["wa"]
        url = f"/api/v1/workspaces/{ws}/analytics/agents/stats?agent_id={wa}&{WIN_Q}"

        got = {}
        for name in ("admin", "m1", "m2", "m3"):
            resp = await api_client.get(url, headers=_auth(world["tokens"][name]))
            assert resp.status_code == 200, resp.text
            got[name] = resp.json()["data"]

        # admin:全量 5 执行 / 2 成功;retry 1/5;token 150 覆盖 0.2
        assert got["admin"]["executions"] == 5
        assert got["admin"]["succeeded"] == 2
        assert got["admin"]["success_rate"] == 1.0
        assert got["admin"]["retry_rate"] == 0.2
        assert got["admin"]["tokens"]["total_tokens"] == 150
        assert got["admin"]["tokens"]["token_coverage"] == 0.2

        # m1(普通成员):priv 执行全部剔除 → 4/1;retry 0;token 覆盖 1/4
        assert got["m1"]["executions"] == 4
        assert got["m1"]["succeeded"] == 1
        assert got["m1"]["retry_rate"] == 0.0
        assert got["m1"]["tokens"]["total_tokens"] == 150
        assert got["m1"]["tokens"]["token_coverage"] == 0.25

        # m2(私有项目成员):含 priv 执行,与 admin 一致
        assert got["m2"]["executions"] == 5
        assert got["m2"]["succeeded"] == 2
        assert got["m2"]["retry_rate"] == 0.2

        # m3(private agent owner,非 priv 项目成员):WA 仍剔除 priv → 4/1
        assert got["m3"]["executions"] == 4
        assert got["m3"]["succeeded"] == 1

    async def test_workload_inflight_counts_per_requester(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws = world["ws_id"]
        url = f"/api/v1/workspaces/{ws}/analytics/workload"

        def rows_for(resp_json, member_id=None):
            return {r["member_id"]: r for r in resp_json["data"]}

        resp_admin = await api_client.get(url, headers=_auth(world["tokens"]["admin"]))
        resp_m1 = await api_client.get(url, headers=_auth(world["tokens"]["m1"]))
        resp_m3 = await api_client.get(url, headers=_auth(world["tokens"]["m3"]))
        assert resp_admin.status_code == 200
        admin_rows = rows_for(resp_admin.json())
        m1_rows = rows_for(resp_m1.json())
        m3_rows = rows_for(resp_m3.json())

        # WA 在途 running 1 / queued 1 / approval 0(无人需审批)——全体可见
        # (running 挂 pub issue;queued 无 issue → 无项目侧信道)
        wa_member_admin = [r for r in admin_rows.values() if r["display_name"] == "WA"][0]
        assert wa_member_admin["running"] == 1
        assert wa_member_admin["queued"] == 1
        # PA 在途执行(1 running):m1 剔除、m3 与 admin 可见(堵执行计数侧信道)
        assert not any(r["display_name"] == "PA" for r in m1_rows.values())
        assert any(r["display_name"] == "PA" for r in m3_rows.values())
        pa_row_admin = [r for r in admin_rows.values() if r["display_name"] == "PA"][0]
        assert pa_row_admin["running"] == 1
        # open issue:priv issue 指派 m1——m1 不可见 priv → open 1;admin → 2
        m1_self = [r for r in m1_rows.values() if r["member_type"] == "human"][0]
        assert m1_self["open_issues"] == 1
        admin_humans = [r for r in admin_rows.values() if r["open_issues"] == 2]
        assert admin_humans, "admin 应见 priv+pub 两条 open issue"

    async def test_workspace_dashboard_agent_section_per_requester(
        self, api_client, session_factory
    ):
        world = await _seed_world(api_client, session_factory)
        ws = world["ws_id"]
        url = f"/api/v1/workspaces/{ws}/dashboards/workspace?{WIN_Q}"
        resp_m1 = await api_client.get(url, headers=_auth(world["tokens"]["m1"]))
        resp_admin = await api_client.get(url, headers=_auth(world["tokens"]["admin"]))
        assert resp_m1.status_code == 200 and resp_admin.status_code == 200
        m1_dash = resp_m1.json()["data"]
        admin_dash = resp_admin.json()["data"]
        m1_agents = {a["agent_id"]: a for a in m1_dash["agent_stats"]["agents"]}
        admin_agents = {a["agent_id"]: a for a in admin_dash["agent_stats"]["agents"]}
        # m1:PA 不呈现、WA 剔除 priv 执行;admin:全量
        assert str(world["pa"]) not in m1_agents
        assert m1_agents[str(world["wa"])]["executions"] == 4
        assert str(world["pa"]) in admin_agents
        assert admin_agents[str(world["wa"])]["executions"] == 5
        assert m1_dash["meta"]["visibility_filtered"] is True
        assert admin_dash["meta"]["visibility_filtered"] is False


class TestT33Negatives:
    """①② 可见性负向:private agent 403、整体 403、cycle 归属 403。"""

    async def test_private_agent_stats_403(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws, pa = world["ws_id"], world["pa"]
        resp = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/agents/stats?agent_id={pa}&{WIN_Q}",
            headers=_auth(world["tokens"]["m1"]),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "agent_not_visible"
        # owner 可见
        resp_owner = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/agents/stats?agent_id={pa}&{WIN_Q}",
            headers=_auth(world["tokens"]["m3"]),
        )
        assert resp_owner.status_code == 200
        assert resp_owner.json()["data"]["executions"] == 2  # manual completed + running

    async def test_multi_project_invisible_whole_403(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws = world["ws_id"]
        resp = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/throughput"
            f"?project_ids={world['pub']},{world['priv']}&{WIN_Q}",
            headers=_auth(world["tokens"]["m1"]),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "project_not_visible"

    async def test_private_cycle_and_project_gates(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws = world["ws_id"]
        h = _auth(world["tokens"]["m1"])
        resp_cycle = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/velocity?cycle_ids={world['cycle_priv']}",
            headers=h,
        )
        assert resp_cycle.status_code == 403
        assert resp_cycle.json()["error"]["code"] == "project_not_visible"
        resp_proj = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/cycle-time?project_id={world['priv']}&{WIN_Q}",
            headers=h,
        )
        assert resp_proj.status_code == 403
        assert resp_proj.json()["error"]["code"] == "project_not_visible"


class TestT33CacheIsolation:
    """⑤⑥ scope_key 缓存分行:跨权限绝不共享,变更自然失效。"""

    async def test_ws_admin_never_served_to_member(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws, wa = world["ws_id"], world["wa"]
        url = f"/api/v1/workspaces/{ws}/analytics/agents/stats?agent_id={wa}&{WIN_Q}"
        # admin 先查 → 写 ws_admin 快照
        admin_resp = await api_client.get(url, headers=_auth(world["tokens"]["admin"]))
        assert admin_resp.json()["data"]["executions"] == 5
        from mesh.db.models.analytics import AnalyticsSnapshot

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(AnalyticsSnapshot).where(
                        AnalyticsSnapshot.metric_key == "agent_stats"
                    )
                )
            ).scalars().all()
            assert any(r.scope_key == "ws_admin" for r in rows)
            assert len(rows) == 1
        # m1 查同一端点 → 不得命中 ws_admin 行(值必为剔除后 4),且另写 exec: 行
        m1_resp = await api_client.get(url, headers=_auth(world["tokens"]["m1"]))
        assert m1_resp.json()["data"]["executions"] == 4
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(AnalyticsSnapshot).where(
                        AnalyticsSnapshot.metric_key == "agent_stats"
                    )
                )
            ).scalars().all()
            scope_keys = {r.scope_key for r in rows}
            assert "ws_admin" in scope_keys
            assert any(k.startswith("exec:p") for k in scope_keys)
            assert len(scope_keys) == 2  # 物理分行

    async def test_refresh_recomputes_and_matches_direct_aggregate(
        self, api_client, session_factory
    ):
        """⑤ 源表变化 + refresh=true 重算与直接聚合逐值一致(§5.5)。"""
        world = await _seed_world(api_client, session_factory)
        ws, wa = world["ws_id"], world["wa"]
        url = f"/api/v1/workspaces/{ws}/analytics/agents/stats?agent_id={wa}&{WIN_Q}"
        h_admin = _auth(world["tokens"]["admin"])
        first = await api_client.get(url, headers=h_admin)
        assert first.json()["data"]["executions"] == 5
        # 新增一条 WA 执行(真源变化)
        async with session_factory() as session, session.begin():
            session.add(TaskExecution(
                workspace_id=uuid.UUID(ws), agent_id=world["wa"], issue_id=None,
                trigger="manual", status="failed", queued_at=TS, finished_at=TS,
            ))
        # 未 refresh → 命中缓存旧值
        cached = await api_client.get(url, headers=h_admin)
        assert cached.json()["data"]["executions"] == 5
        assert cached.json()["data"]["meta"]["cached"] is True
        # refresh=true → 重算 == 直接聚合(6 执行)
        fresh = await api_client.get(f"{url}&refresh=true", headers=h_admin)
        assert fresh.json()["data"]["executions"] == 6
        assert fresh.json()["data"]["meta"]["cached"] is False


class TestT33BucketingAndCaliber:
    """③④ calendar_timezone 本地日历分桶(含跨 DST)+ 当前归属口径。"""

    async def test_utc8_local_day_not_split(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws = world["ws_id"]
        resp = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/throughput"
            "?from=2026-07-24T00:00:00Z&to=2026-07-26T00:00:00Z"
            "&calendar_timezone=Asia/Shanghai",
            headers=_auth(world["tokens"]["m1"]),
        )
        assert resp.status_code == 200
        buckets = {b["label"]: b for b in resp.json()["data"]["series"]}
        # issue_tz created 2026-07-24T16:30Z = 上海当地 7/25 00:30 → 当地 7/25 桶
        assert "2026-07-25" in buckets
        assert buckets["2026-07-25"]["window_start"] == "2026-07-24T16:00:00Z"
        assert buckets["2026-07-25"]["window_end"] == "2026-07-25T16:00:00Z"
        assert buckets["2026-07-25"]["created"] == 1
        assert resp.json()["data"]["meta"]["calendar_timezone"] == "Asia/Shanghai"

    async def test_dst_spring_forward_day_alignment(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws = world["ws_id"]
        resp = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/throughput"
            "?from=2026-03-07T00:00:00Z&to=2026-03-10T00:00:00Z"
            "&calendar_timezone=America/New_York",
            headers=_auth(world["tokens"]["m1"]),
        )
        assert resp.status_code == 200
        buckets = resp.json()["data"]["series"]
        labels = [b["label"] for b in buckets]
        # 无重复日、无缺失日
        assert labels == ["2026-03-07", "2026-03-08"]
        by_label = {b["label"]: b for b in buckets}
        # 春进日(3/8)前 UTC-5,当日 00:00 = 05:00Z;次日 00:00 = 04:00Z(UTC-4,23h 日)
        assert by_label["2026-03-07"]["window_start"] == "2026-03-07T05:00:00Z"
        assert by_label["2026-03-08"]["window_start"] == "2026-03-08T05:00:00Z"
        assert by_label["2026-03-08"]["window_end"] == "2026-03-09T04:00:00Z"
        # 两条 issue 各落其当地日桶
        assert by_label["2026-03-07"]["created"] == 1
        assert by_label["2026-03-08"]["created"] == 1

    async def test_velocity_and_burndown_current_attribution(
        self, api_client, session_factory
    ):
        world = await _seed_world(api_client, session_factory)
        ws = world["ws_id"]
        h = _auth(world["tokens"]["admin"])
        vel = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/velocity?cycle_ids={world['cycle_pub']}",
            headers=h,
        )
        assert vel.status_code == 200
        vel_data = vel.json()["data"]
        assert vel_data["meta"]["scope_caliber"] == "current_attribution"
        assert vel_data["cycles"][0]["completed_issues"] == 1
        bd = await api_client.get(
            f"/api/v1/workspaces/{ws}/analytics/burndown?cycle_id={world['cycle_pub']}",
            headers=h,
        )
        assert bd.status_code == 200
        bd_data = bd.json()["data"]
        assert bd_data["meta"]["scope_caliber"] == "current_attribution"
        assert bd_data["total"] == 3
        assert bd_data["actual"]  # 全部为过去日(NOW 远晚于周期窗)


class TestT33ReadOnly:
    """§5.3 只读审计:任何端点(含 refresh)不写真源表。"""

    async def test_no_source_table_writes(self, api_client, session_factory):
        world = await _seed_world(api_client, session_factory)
        ws = world["ws_id"]
        h = _auth(world["tokens"]["admin"])

        async def fingerprint() -> dict:
            async with session_factory() as session:
                out = {}
                tables = {
                    "issues": "updated_at",
                    "task_executions": "updated_at",
                    "execution_attempts": "updated_at",
                    "autopilot_runs": "updated_at",
                    "issue_activity": "created_at",
                }
                for table, stamp in tables.items():
                    count = (
                        await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    ).scalar_one()
                    max_stamp = (
                        await session.execute(
                            text(f"SELECT COALESCE(MAX({stamp}), 'epoch'::timestamptz) FROM {table}")
                        )
                    ).scalar_one()
                    out[table] = (count, str(max_stamp))
                return out

        before = await fingerprint()
        for path in (
            f"/analytics/cycle-time?{WIN_Q}",
            f"/analytics/throughput?{WIN_Q}",
            f"/analytics/velocity?{WIN_Q}",
            "/analytics/workload",
            f"/analytics/burndown?cycle_id={world['cycle_pub']}",
            f"/analytics/agents/stats?{WIN_Q}&refresh=true",
            f"/dashboards/workspace?{WIN_Q}&refresh=true",
            f"/dashboards/project/{world['pub']}?{WIN_Q}",
        ):
            resp = await api_client.get(f"/api/v1/workspaces/{ws}{path}", headers=h)
            assert resp.status_code == 200, path
        after = await fingerprint()
        assert before == after
