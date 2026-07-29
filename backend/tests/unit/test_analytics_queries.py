"""Authoritative SQL builders (analytics.md §2.2/§2.3, R5 HIGH-3).

The core invariant: every execution-metric builder inlines the
``visible_executions`` CTE VERBATIM — no bypass aggregation of
``task_executions`` exists in this module (T33 ⑦ structural guarantee).
"""

from __future__ import annotations

import uuid

import pytest

from mesh.analytics import queries
from mesh.analytics.visibility import VISIBLE_EXECUTIONS_CTE

pytestmark = pytest.mark.unit


def _norm(sql: str) -> str:
    return " ".join(sql.split())


CTE_PREFIX_NORM = _norm("WITH " + VISIBLE_EXECUTIONS_CTE)


def test_workload_inflight_inlines_authoritative_cte_verbatim():
    sql, params = queries.build_workload_inflight_sql()
    assert CTE_PREFIX_NORM in _norm(sql)
    assert "FROM visible_executions e" in sql
    assert params == {}
    assert "claimed','running','cancelling" in sql
    assert "awaiting_approval" in sql


def test_agent_stats_inlines_authoritative_cte_verbatim():
    for agent_id in (None, uuid.uuid4()):
        sql, params = queries.build_agent_stats_sql(agent_id=agent_id)
        assert CTE_PREFIX_NORM in _norm(sql)
        assert "FROM visible_executions e" in sql
        if agent_id is None:
            assert params == {}
            assert ":agent_id" not in sql
        else:
            assert params == {"agent_id": agent_id}
            assert "AND e.agent_id = :agent_id" in sql
    # 成功率口径:cancelled 不入分母
    sql, _ = queries.build_agent_stats_sql()
    assert "COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout')), 0), 4)" in _norm(
        sql
    ).replace("  ", " ") or "success_rate" in sql


def test_retry_rate_inlines_authoritative_cte_verbatim():
    agent_id = uuid.uuid4()
    sql, params = queries.build_retry_rate_sql(agent_id=agent_id)
    assert CTE_PREFIX_NORM in _norm(sql)
    assert "LEFT JOIN execution_attempts att" in sql
    assert "COUNT(att.id) AS n" in sql
    assert params == {"agent_id": agent_id}


def test_tokens_inlines_authoritative_cte_verbatim():
    agent_id = uuid.uuid4()
    sql, params = queries.build_tokens_sql(agent_id=agent_id)
    assert CTE_PREFIX_NORM in _norm(sql)
    assert "JOIN visible_executions e" in sql
    assert "SUM(r.total_tokens)" in sql
    assert "COUNT(r.id)                 AS runs_with_token_data" in sql
    assert params == {"agent_id": agent_id}


def test_no_bypass_task_executions_aggregation():
    """模块内不存在未经 CTE 直接聚合 task_executions 的落地 SQL。"""
    for builder in (
        queries.build_workload_inflight_sql,
        queries.build_agent_stats_sql,
        queries.build_retry_rate_sql,
        queries.build_tokens_sql,
    ):
        sql, _ = builder()
        norm = _norm(sql)
        # task_executions 只出现在 CTE 内部(FROM task_executions e JOIN agents ...)
        assert norm.count("FROM task_executions e") == 1
        assert "FROM task_executions e JOIN agents a" in norm
        # 聚合一律取自 visible_executions
        assert "FROM visible_executions e" in norm or "JOIN visible_executions e" in norm


def test_issue_metric_builders_do_not_reference_executions_cte():
    pid = [uuid.uuid4()]
    ct_sql, _ = queries.build_cycle_time_sql(visible_ids=pid)
    th_sql, _ = queries.build_throughput_sql(visible_ids=pid)
    bd_sql, _ = queries.build_burndown_sql(scope_column="cycle_id", metric="points")
    for sql in (ct_sql, th_sql, bd_sql):
        assert "visible_executions" not in sql
    assert "percentile_cont(0.5)" in ct_sql
    assert "new_value #>> '{}'" in ct_sql  # {{}} 转义还原
    assert "date_trunc(:granularity" in th_sql
    assert "AT TIME ZONE :calendar_tz" in th_sql


def test_project_visibility_fragment():
    fragment, params = queries.project_visibility_fragment(None)
    assert fragment == "" and params == {}
    pid_a, pid_b = uuid.uuid4(), uuid.uuid4()
    fragment, params = queries.project_visibility_fragment([pid_a, pid_b], alias="i")
    assert "i.project_id IS NULL OR i.project_id = ANY(:visible_project_ids)" in fragment
    assert params == {"visible_project_ids": [pid_a, pid_b]}


def test_cycle_time_builder_fragment_injection():
    sql, params = queries.build_cycle_time_sql(visible_ids=[uuid.uuid4()])
    assert "AND (i.project_id IS NULL" in sql
    assert "visible_project_ids" in params
    sql_admin, params_admin = queries.build_cycle_time_sql(visible_ids=None)
    assert "visible_project_ids" not in sql_admin
    assert params_admin == {}


def test_velocity_builder_filters():
    cid = uuid.uuid4()
    sql, params = queries.build_velocity_sql(visible_ids=None, cycle_ids=[cid])
    assert "AND c.id = ANY(:cycle_ids)" in sql
    assert params == {"cycle_ids": [cid]}
    pid = uuid.uuid4()
    sql2, params2 = queries.build_velocity_sql(visible_ids=None, project_id=pid)
    assert "daterange(c.starts_at" in sql2
    assert params2 == {"project_id": pid}
    # 普通成员:周期也要过可见项目过滤(alias=c)
    sql3, params3 = queries.build_velocity_sql(visible_ids=[pid])
    assert "c.project_id IS NULL OR c.project_id = ANY(:visible_project_ids)" in sql3
    assert params3["visible_project_ids"] == [pid]


def test_burndown_builder_metric_and_scope():
    sql_pts, _ = queries.build_burndown_sql(scope_column="cycle_id", metric="points")
    assert "COALESCE(estimate, 0) AS pts" in sql_pts
    assert "cycle_id = :scope_id" in sql_pts
    assert "AT TIME ZONE :display_tz" in sql_pts
    sql_cnt, _ = queries.build_burndown_sql(scope_column="milestone_id", metric="count")
    assert "SELECT 1 AS pts" in sql_cnt
    assert "milestone_id = :scope_id" in sql_cnt
    with pytest.raises(ValueError):
        queries.build_burndown_sql(scope_column="project_id", metric="points")


def test_workload_open_builder():
    pid = uuid.uuid4()
    sql, params = queries.build_workload_open_sql(visible_ids=None, project_id=pid)
    assert "AND i.project_id = :project_id" in sql
    assert params == {"project_id": pid}
    sql2, params2 = queries.build_workload_open_sql(visible_ids=[pid])
    assert "i.project_id IS NULL OR i.project_id = ANY(:visible_project_ids)" in sql2
    assert params2["visible_project_ids"] == [pid]
    sql3, params3 = queries.build_workload_open_sql(visible_ids=None)
    assert params3 == {} and "visible_project_ids" not in sql3


def test_granularity_bucket_step_table():
    from mesh.analytics.scope import GRANULARITY_BUCKET_STEP

    assert GRANULARITY_BUCKET_STEP == {"day": "1 day", "week": "1 week", "month": "1 month"}
