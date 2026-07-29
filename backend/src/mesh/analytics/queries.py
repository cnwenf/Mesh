"""Authoritative aggregation SQL builders (analytics.md §2.2/§2.3).

Two families:

- issue metrics (cycle time / velocity / throughput / burndown / workload-A)
  aggregate ``issues`` (+ ``issue_activity`` / ``cycles``) and are filtered to
  the requester's visible project set via a ``project_filter`` fragment the
  service layer derives (admin/owner → no fragment);
- execution metrics (workload-B, agent stats, retry rate, tokens) inline
  ``visibility.VISIBLE_EXECUTIONS_CTE`` **verbatim** — the R5 authoritative
  building block. No builder here aggregates ``task_executions`` by
  ``workspace_id + agent_id + window`` alone; T33 asserts the final values
  for four requester classes against exactly this SQL.
"""

from __future__ import annotations

from mesh.analytics.visibility import VISIBLE_EXECUTIONS_CTE

# ---------------------------------------------------------------------------
# Issue metrics
# ---------------------------------------------------------------------------

CYCLE_TIME_SQL = """
WITH first_start AS (
  SELECT a.issue_id, MIN(a.created_at) AS started_at
  FROM issue_activity a
  WHERE a.workspace_id = :ws
    AND a.field = 'state_category'
    AND (a.new_value #>> '{{}}') = :from_category
  GROUP BY a.issue_id
)
SELECT
  percentile_cont(0.5) WITHIN GROUP
    (ORDER BY EXTRACT(EPOCH FROM (i.completed_at - f.started_at))) AS p50_seconds,
  percentile_cont(0.9) WITHIN GROUP
    (ORDER BY EXTRACT(EPOCH FROM (i.completed_at - f.started_at))) AS p90_seconds,
  COUNT(*) AS sample_size
FROM issues i
JOIN first_start f ON f.issue_id = i.id
WHERE i.workspace_id = :ws
  AND i.deleted_at IS NULL
  AND i.state_category = 'done'
  AND i.completed_at IS NOT NULL
  AND i.completed_at >= :win_from AND i.completed_at < :win_to
  AND f.started_at < i.completed_at
  {project_filter}
"""

CYCLE_INSUFFICIENT_SQL = """
WITH first_start AS (
  SELECT a.issue_id, MIN(a.created_at) AS started_at
  FROM issue_activity a
  WHERE a.workspace_id = :ws
    AND a.field = 'state_category'
    AND (a.new_value #>> '{{}}') = :from_category
  GROUP BY a.issue_id
)
SELECT COUNT(*) AS insufficient
FROM issues i
LEFT JOIN first_start f ON f.issue_id = i.id
WHERE i.workspace_id = :ws
  AND i.deleted_at IS NULL
  AND i.state_category = 'done'
  AND i.completed_at IS NOT NULL
  AND i.completed_at >= :win_from AND i.completed_at < :win_to
  AND (f.started_at IS NULL OR f.started_at >= i.completed_at)
  {project_filter}
"""

VELOCITY_SQL = """
SELECT c.id AS cycle_id, c.name AS name, c.starts_at AS starts_at,
       c.ends_at AS ends_at, c.state AS state,
       COUNT(i.id) AS completed_issues,
       COALESCE(SUM(i.estimate), 0) AS completed_points,
       COALESCE(SUM(i.estimate) FILTER (WHERE i.estimate_unit = 'points'), 0)
         AS completed_points_unit,
       COALESCE(SUM(i.estimate) FILTER (WHERE i.estimate_unit = 'hours'), 0)
         AS completed_hours_unit
FROM cycles c
LEFT JOIN issues i
  ON i.cycle_id = c.id
  AND i.workspace_id = c.workspace_id
  AND i.deleted_at IS NULL
  AND i.state_category = 'done'
  AND i.completed_at >= (CAST(c.starts_at AS timestamp) AT TIME ZONE :display_tz)
  AND i.completed_at <  (CAST(c.ends_at + 1 AS timestamp) AT TIME ZONE :display_tz)
WHERE c.workspace_id = :ws
  {cycle_filter}
  {project_filter}
GROUP BY c.id, c.name, c.starts_at, c.ends_at, c.state
ORDER BY c.starts_at
"""

THROUGHPUT_SQL = """
SELECT bucket_local,
       (bucket_local AT TIME ZONE :calendar_tz) AS window_start_utc,
       ((bucket_local + CASE :granularity
             WHEN 'day' THEN INTERVAL '1 day'
             WHEN 'week' THEN INTERVAL '1 week'
             ELSE INTERVAL '1 month' END) AT TIME ZONE :calendar_tz)
         AS window_end_utc,
       COUNT(*) FILTER (WHERE kind = 'created')   AS created,
       COUNT(*) FILTER (WHERE kind = 'completed') AS completed
FROM (
  SELECT date_trunc(:granularity, i.created_at AT TIME ZONE :calendar_tz) AS bucket_local,
         'created' AS kind
    FROM issues i
   WHERE i.workspace_id = :ws AND i.deleted_at IS NULL
     AND i.created_at >= :win_from AND i.created_at < :win_to
     {project_filter}
  UNION ALL
  SELECT date_trunc(:granularity, i.completed_at AT TIME ZONE :calendar_tz) AS bucket_local,
         'completed' AS kind
    FROM issues i
   WHERE i.workspace_id = :ws AND i.deleted_at IS NULL
     AND i.state_category = 'done' AND i.completed_at IS NOT NULL
     AND i.completed_at >= :win_from AND i.completed_at < :win_to
     {project_filter}
) t
GROUP BY bucket_local
ORDER BY bucket_local
"""

# Scope = issues CURRENTLY attributed to the cycle/milestone (current
# attribution caliber, §2.2.5 R3). ``pts`` is 1 for metric=count,
# COALESCE(estimate, 0) for metric=points — one shape for both metrics.
BURNDOWN_SQL = """
WITH scope AS (
  SELECT {pts_expr} AS pts, completed_at
    FROM issues
   WHERE workspace_id = :ws AND deleted_at IS NULL
     AND {scope_column} = :scope_id
),
total AS (SELECT COALESCE(SUM(pts), 0) AS v FROM scope)
SELECT CAST(days.d AS date) AS date,
       (SELECT v FROM total)
         - COALESCE(SUM(scope.pts) FILTER (
             WHERE scope.completed_at IS NOT NULL
               AND scope.completed_at
                   < (CAST((CAST(days.d AS date) + 1) AS timestamp)
                      AT TIME ZONE :display_tz)), 0)
         AS remaining,
       (SELECT v FROM total) AS total
FROM generate_series(CAST(:day_from AS timestamp), CAST(:day_to AS timestamp),
                     INTERVAL '1 day') AS days(d)
LEFT JOIN scope ON TRUE
GROUP BY days.d
ORDER BY days.d
"""

WORKLOAD_OPEN_SQL = """
SELECT i.assignee_id AS member_id, COUNT(*) AS open_issues
FROM issues i
WHERE i.workspace_id = :ws
  AND i.deleted_at IS NULL
  AND i.assignee_id IS NOT NULL
  AND i.state_category NOT IN ('done', 'cancelled')
  {project_filter}
GROUP BY i.assignee_id
"""

# ---------------------------------------------------------------------------
# Execution metrics — VISIBLE_EXECUTIONS_CTE inlined verbatim (R5)
# ---------------------------------------------------------------------------

WORKLOAD_INFLIGHT_SQL = (
    "WITH "
    + VISIBLE_EXECUTIONS_CTE
    + """
SELECT e.agent_id AS agent_id,
  COUNT(*) FILTER (WHERE e.status IN ('claimed','running','cancelling')) AS running,
  COUNT(*) FILTER (WHERE e.status = 'queued')                            AS queued,
  COUNT(*) FILTER (WHERE e.status = 'awaiting_approval')                 AS awaiting_approval
FROM visible_executions e
WHERE e.agent_id IS NOT NULL
  AND e.status IN ('queued','claimed','running','cancelling','awaiting_approval')
GROUP BY e.agent_id
"""
)

AGENT_STATS_SQL = (
    "WITH "
    + VISIBLE_EXECUTIONS_CTE
    + """
SELECT
  e.agent_id                                                              AS agent_id,
  COUNT(*)                                                                AS executions,
  COUNT(*) FILTER (WHERE e.status = 'completed')                          AS succeeded,
  COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout'))    AS terminal,
  COUNT(*) FILTER (WHERE e.status = 'cancelled')                          AS cancelled_count,
  ROUND(COUNT(*) FILTER (WHERE e.status = 'completed') * 1.0
        / NULLIF(COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout')), 0), 4)
        AS success_rate,
  ROUND(COUNT(*) FILTER (WHERE e.status = 'timeout') * 1.0
        / NULLIF(COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout')), 0), 4)
        AS timeout_rate,
  AVG(EXTRACT(EPOCH FROM (e.finished_at - e.queued_at)))
        FILTER (WHERE e.status IN ('completed','failed','timeout')
                AND e.finished_at IS NOT NULL)                            AS avg_duration_seconds
FROM visible_executions e
WHERE e.queued_at >= :win_from AND e.queued_at < :win_to {agent_filter}
GROUP BY e.agent_id
"""
)

RETRY_RATE_SQL = (
    "WITH "
    + VISIBLE_EXECUTIONS_CTE
    + """
SELECT agent_id AS agent_id,
       ROUND(COUNT(*) FILTER (WHERE n > 1) * 1.0 / NULLIF(COUNT(*), 0), 4) AS retry_rate
FROM (
  SELECT e.id AS id, e.agent_id AS agent_id, COUNT(att.id) AS n
  FROM visible_executions e
  LEFT JOIN execution_attempts att
    ON att.execution_id = e.id AND att.workspace_id = e.workspace_id
  WHERE e.queued_at >= :win_from AND e.queued_at < :win_to {agent_filter}
  GROUP BY e.id, e.agent_id
) r
GROUP BY agent_id
"""
)

TOKENS_SQL = (
    "WITH "
    + VISIBLE_EXECUTIONS_CTE
    + """
SELECT e.agent_id                  AS agent_id,
       SUM(r.prompt_tokens)        AS prompt_tokens,
       SUM(r.completion_tokens)    AS completion_tokens,
       SUM(r.total_tokens)         AS total_tokens,
       COUNT(r.id)                 AS runs_with_token_data
FROM autopilot_runs r
JOIN visible_executions e
  ON e.id = r.execution_id AND e.workspace_id = r.workspace_id
WHERE r.started_at >= :win_from AND r.started_at < :win_to {agent_filter}
GROUP BY e.agent_id
"""
)


# ---------------------------------------------------------------------------
# Builders — each returns (sql, extra_params)
# ---------------------------------------------------------------------------


def project_visibility_fragment(visible_ids, *, alias: str = "i", project_id=None) -> tuple[str, dict]:
    """Issue-metric visibility fragment (§3.1 R3).

    ``project_id`` → single-project filter (the endpoint already passed the
    project visibility gate). ``visible_ids is None`` → admin/owner, no
    fragment. Otherwise keep inbox issues (``project_id IS NULL``,
    workspace-level visible) plus the requester's visible project set.
    """
    if project_id is not None:
        return f"AND {alias}.project_id = :project_id", {"project_id": project_id}
    if visible_ids is None:
        return "", {}
    return (
        f"AND ({alias}.project_id IS NULL OR {alias}.project_id = ANY(:visible_project_ids))",
        {"visible_project_ids": list(visible_ids)},
    )


def build_cycle_time_sql(*, visible_ids, project_id=None) -> tuple[str, dict]:
    fragment, params = project_visibility_fragment(visible_ids, project_id=project_id)
    return CYCLE_TIME_SQL.format(project_filter=fragment), params


def build_cycle_insufficient_sql(*, visible_ids, project_id=None) -> tuple[str, dict]:
    fragment, params = project_visibility_fragment(visible_ids, project_id=project_id)
    return CYCLE_INSUFFICIENT_SQL.format(project_filter=fragment), params


def build_velocity_sql(*, visible_ids, cycle_ids=None, project_id=None) -> tuple[str, dict]:
    params: dict = {}
    if cycle_ids:
        cycle_filter = "AND c.id = ANY(:cycle_ids)"
        params["cycle_ids"] = list(cycle_ids)
    else:
        cycle_filter = (
            "AND daterange(c.starts_at, c.ends_at, '[]') "
            "&& daterange(CAST(:win_from AS date), CAST(:win_to AS date), '[]')"
        )
        if project_id is not None:
            cycle_filter += " AND c.project_id = :project_id"
            params["project_id"] = project_id
    fragment, frag_params = project_visibility_fragment(visible_ids, alias="c")
    params.update(frag_params)
    return VELOCITY_SQL.format(cycle_filter=cycle_filter, project_filter=fragment), params


def build_throughput_sql(*, visible_ids, project_id=None) -> tuple[str, dict]:
    fragment, params = project_visibility_fragment(visible_ids, project_id=project_id)
    return THROUGHPUT_SQL.format(project_filter=fragment), params


def build_burndown_sql(*, scope_column: str, metric: str) -> tuple[str, dict]:
    if scope_column not in ("cycle_id", "milestone_id"):
        raise ValueError("scope_column must be cycle_id or milestone_id")
    pts_expr = "1" if metric == "count" else "COALESCE(estimate, 0)"
    return BURNDOWN_SQL.format(scope_column=scope_column, pts_expr=pts_expr), {}


def build_workload_open_sql(*, visible_ids, project_id=None) -> tuple[str, dict]:
    params: dict = {}
    if project_id is not None:
        fragment = "AND i.project_id = :project_id"
        params["project_id"] = project_id
    else:
        fragment, params = project_visibility_fragment(visible_ids)
    return WORKLOAD_OPEN_SQL.format(project_filter=fragment), params


def build_workload_inflight_sql() -> tuple[str, dict]:
    return WORKLOAD_INFLIGHT_SQL, {}


def build_agent_stats_sql(*, agent_id=None) -> tuple[str, dict]:
    if agent_id is not None:
        return AGENT_STATS_SQL.format(agent_filter="AND e.agent_id = :agent_id"), {
            "agent_id": agent_id
        }
    return AGENT_STATS_SQL.format(agent_filter=""), {}


def build_retry_rate_sql(*, agent_id=None) -> tuple[str, dict]:
    if agent_id is not None:
        return RETRY_RATE_SQL.format(agent_filter="AND e.agent_id = :agent_id"), {
            "agent_id": agent_id
        }
    return RETRY_RATE_SQL.format(agent_filter=""), {}


def build_tokens_sql(*, agent_id=None) -> tuple[str, dict]:
    if agent_id is not None:
        return TOKENS_SQL.format(agent_filter="AND e.agent_id = :agent_id"), {"agent_id": agent_id}
    return TOKENS_SQL.format(agent_filter=""), {}
