"""Execution-metric assembly shared by endpoints and dashboards.

Everything here aggregates through the ``visible_executions`` CTE
(analytics.md §2.3.1 R5) — workload-B, agent stats main/retry/token queries
are the verbatim §2.2.4/§2.3 SQL built in ``queries.py``. Results are keyed
by agent id and merged into member-dimension rows (roster unification,
README §6.1) with a server-computed ``member_type`` snapshot and
``display_name``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, text

from mesh.analytics import queries
from mesh.analytics.visibility import requester_cte_params
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.member.display import resolve_display_name

TOKEN_NOTE = "tokens cover autopilot-triggered executions only"

def _to_float(value) -> float | None:
    return float(value) if value is not None else None


async def display_names_map(
    session, workspace_id, member_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    """member_id → (display_name, member_type) via the roster JOINs (§6.1)."""
    if not member_ids:
        return {}
    members = (
        await session.execute(
            select(Member).where(Member.workspace_id == workspace_id, Member.id.in_(member_ids))
        )
    ).scalars().all()
    user_ids = [m.user_id for m in members if m.user_id is not None]
    agent_ids = [m.agent_id for m in members if m.agent_id is not None]
    users = {
        u.id: u
        for u in (
            await session.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
    } if user_ids else {}
    agents = {
        a.id: a
        for a in (
            await session.execute(
                select(Agent).where(Agent.workspace_id == workspace_id, Agent.id.in_(agent_ids))
            )
        ).scalars().all()
    } if agent_ids else {}
    out: dict[uuid.UUID, tuple[str, str]] = {}
    for member in members:
        user = users.get(member.user_id) if member.user_id else None
        agent = agents.get(member.agent_id) if member.agent_id else None
        name = resolve_display_name(
            member=member, user=user, agent_name=agent.name if agent else None
        )
        out[member.id] = (name, member.member_type)
    return out


async def compute_agent_stats_rows(
    session,
    *,
    workspace_id: uuid.UUID,
    member,
    win_from: datetime,
    win_to: datetime,
    agent_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, dict]:
    """Per-agent stats for every visible agent (or one agent).

    The three authoritative queries (main / retry / tokens) share the CTE
    and the requester bind parameters; private-project executions and
    invisible private-agent executions are filtered inside the CTE.
    """
    params: dict = {"ws": workspace_id, "win_from": win_from, "win_to": win_to}
    params.update(requester_cte_params(member))

    stats_sql, stats_extra = queries.build_agent_stats_sql(agent_id=agent_id)
    retry_sql, retry_extra = queries.build_retry_rate_sql(agent_id=agent_id)
    tokens_sql, tokens_extra = queries.build_tokens_sql(agent_id=agent_id)

    main_rows = (
        await session.execute(text(stats_sql), params | stats_extra)
    ).mappings().all()
    retry_rows = (
        await session.execute(text(retry_sql), params | retry_extra)
    ).mappings().all()
    token_rows = (
        await session.execute(text(tokens_sql), params | tokens_extra)
    ).mappings().all()

    retry_by_agent = {row["agent_id"]: _to_float(row["retry_rate"]) for row in retry_rows}
    tokens_by_agent = {row["agent_id"]: row for row in token_rows}

    out: dict[uuid.UUID, dict] = {}
    for row in main_rows:
        aid = row["agent_id"]
        executions = int(row["executions"])
        token_row = tokens_by_agent.get(aid)
        runs_with_data = int(token_row["runs_with_token_data"]) if token_row else 0
        prompt = int(token_row["prompt_tokens"] or 0) if token_row else 0
        completion = int(token_row["completion_tokens"] or 0) if token_row else 0
        total = int(token_row["total_tokens"] or 0) if token_row else 0
        out[aid] = {
            "agent_id": str(aid),
            "executions": executions,
            "succeeded": int(row["succeeded"]),
            "terminal": int(row["terminal"]),
            "cancelled_count": int(row["cancelled_count"]),
            "success_rate": _to_float(row["success_rate"]),
            "timeout_rate": _to_float(row["timeout_rate"]),
            "avg_duration_seconds": _to_float(row["avg_duration_seconds"]),
            "retry_rate": retry_by_agent.get(aid, 0.0 if executions else None),
            "tokens": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "token_coverage": round(runs_with_data / executions, 4) if executions else None,
            },
            "meta": {"token_note": TOKEN_NOTE},
        }
    return out


async def compute_workload_inflight(
    session, *, workspace_id: uuid.UUID, member
) -> dict[uuid.UUID, dict]:
    """agent_id → in-flight execution counts (workload-B, §2.2.4 via CTE)."""
    sql, _extra = queries.build_workload_inflight_sql()
    params: dict = {"ws": workspace_id}
    params.update(requester_cte_params(member))
    rows = (await session.execute(text(sql), params)).mappings().all()
    return {
        row["agent_id"]: {
            "running": int(row["running"]),
            "queued": int(row["queued"]),
            "awaiting_approval": int(row["awaiting_approval"]),
        }
        for row in rows
    }


async def compute_workload_open(
    session,
    *,
    workspace_id: uuid.UUID,
    visible_ids,
    project_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, int]:
    """member_id → open issue count (workload-A, §2.2.4)."""
    sql, extra = queries.build_workload_open_sql(visible_ids=visible_ids, project_id=project_id)
    rows = (
        await session.execute(text(sql), {"ws": workspace_id} | extra)
    ).mappings().all()
    return {row["member_id"]: int(row["open_issues"]) for row in rows}
