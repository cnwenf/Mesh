"""Synchronous unit tests for the search pure builders (no async plumbing).

These cover the row construction, capacity merging, pagination and SQL
fragment logic directly — the async service methods are thin glue over
these functions.
"""

from __future__ import annotations

import uuid

import pytest

from mesh.search.cursor import decode_cursor
from mesh.search.service import (
    CursorBoundary,
    Row,
    apply_capacities,
    build_chat_rows,
    build_issue_rows,
    build_member_rows,
    build_pin_row,
    build_project_rows,
    build_view_rows,
    issue_visibility_clause,
    keyset_clause,
    match_clause,
    merge_capacities,
    order_limit,
    paginate_rows,
    validate_search_params,
    visible_projects_subquery,
)

pytestmark = pytest.mark.unit


class _Viewer:
    """Minimal Member stand-in for the role-driven fragment builders."""

    def __init__(self, role: str):
        self.role = role


def _issue_row(**over) -> dict:
    base = {
        "id": uuid.uuid4(),
        "identifier": "WEB-1",
        "title": "登录页",
        "state_category": "todo",
        "status_id": uuid.uuid4(),
        "status_name": "Todo",
        "project_id": None,
        "project_name": None,
        "score_bucket": 6,
        "title_len": 3,
        "title_lex": "登录页",
    }
    base.update(over)
    return base


def test_build_issue_rows_shape():
    rows = build_issue_rows([_issue_row()])
    assert len(rows) == 1
    row = rows[0]
    assert row.type == "issue"
    assert row.sort_key[0] == -6
    assert row.payload["identifier"] == "WEB-1"
    assert row.payload["project_id"] is None


def test_build_pin_row_none_and_hit():
    assert build_pin_row(None) is None
    pin = build_pin_row(_issue_row(title="WEB 精确"))
    assert pin is not None
    assert pin.score_bucket == 95
    assert pin.sort_key < build_issue_rows([_issue_row()])[0].sort_key


def test_build_member_rows_type_filter():
    row = {
        "id": uuid.uuid4(),
        "member_type": "agent",
        "role": "member",
        "title": "bot",
        "agent_visibility": "workspace",
        "lifecycle_status": "active",
        "score_bucket": 3,
        "title_len": 3,
        "title_lex": "bot",
    }
    human = dict(row, member_type="human", id=uuid.uuid4())
    only_members = build_member_rows([row, human], frozenset({"member"}))
    assert [r.type for r in only_members] == ["member"]
    assert only_members[0].payload["agent_id"] is None
    only_agents = build_member_rows([row, human], frozenset({"agent"}))
    assert [r.type for r in only_agents] == ["agent"]
    assert only_agents[0].payload["agent_id"] == row["id"]
    both = build_member_rows([row, human], frozenset({"member", "agent"}))
    assert {r.type for r in both} == {"member", "agent"}


def test_build_project_view_chat_rows():
    pid = uuid.uuid4()
    projects = build_project_rows(
        [
            {
                "id": pid,
                "title": "proj",
                "key": "WEB",
                "visibility": "private",
                "score_bucket": 6,
                "title_len": 4,
                "title_lex": "proj",
            }
        ]
    )
    assert projects[0].payload == {"row_id": pid, "visibility": "private", "key": "WEB"}

    views = build_view_rows(
        [
            {
                "id": uuid.uuid4(),
                "title": "v",
                "visibility": "shared",
                "project_id": None,
                "project_name": None,
                "score_bucket": 5,
                "title_len": 1,
                "title_lex": "v",
            }
        ]
    )
    assert views[0].payload["project_id"] is None

    agent_id = uuid.uuid4()
    chats = build_chat_rows(
        [
            {
                "id": uuid.uuid4(),
                "title": "c",
                "agent_id": agent_id,
                "agent_name": "bot",
                "score_bucket": 5,
                "title_len": 1,
                "title_lex": "c",
            }
        ]
    )
    assert chats[0].payload["agent_name"] == "bot"


def test_merge_capacities():
    m1, m2, a1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    capacities = merge_capacities(
        {m1: a1, m2: None},
        [{"agent_id": a1, "running": 2, "queued": 1}],
        [{"agent_id": a1, "pending": 3}],
    )
    assert capacities[m1] == {"running": 2, "queued": 1, "awaiting_approval": 3}
    assert capacities[m2] == {"running": 0, "queued": 0, "awaiting_approval": 0}
    # No executions at all → zeros.
    zeroed = merge_capacities({m1: a1}, [], [])
    assert zeroed[m1] == {"running": 0, "queued": 0, "awaiting_approval": 0}


def test_apply_capacities_default_for_unknown():
    row = Row(
        sort_key=(-1, 1, "x", "agent", "r"),
        type="agent",
        id=uuid.uuid4(),
        title="x",
        score_bucket=1,
        title_len=1,
        title_lex="x",
        payload={},
    )
    apply_capacities([row], {})
    assert row.payload["capacity"] == {"running": 0, "queued": 0, "awaiting_approval": 0}


def test_paginate_rows_cursor_roundtrip():
    def mk(bucket: int, lex: str, rtype: str = "issue") -> Row:
        rid = uuid.uuid4()
        return Row(
            sort_key=(-bucket, len(lex), lex, rtype, str(rid)),
            type=rtype,
            id=rid,
            title=lex,
            score_bucket=bucket,
            title_len=len(lex),
            title_lex=lex,
            payload={},
        )

    rows = [mk(6, f"t{i:02d}") for i in range(5)]
    page, cursor = paginate_rows(rows, limit=3, fingerprint="fp", secret=b"s")
    assert len(page) == 3
    assert cursor is not None
    decoded_fp, factors = decode_cursor(b"s", cursor)
    assert decoded_fp == "fp"
    assert factors[3] == "issue"
    assert factors[2] == page[-1].title_lex
    assert factors[4] == str(page[-1].id)

    # No overflow → no cursor.
    page2, cursor2 = paginate_rows(rows[:2], limit=3, fingerprint="fp", secret=b"s")
    assert len(page2) == 2
    assert cursor2 is None


def test_keyset_clause_all_branches():
    assert keyset_clause("issue", None) == "TRUE"
    cursor = CursorBoundary(
        score_bucket=6, title_len=4, title_lex="mmm", result_type="member", row_id=uuid.uuid4()
    )
    # entity type < cursor type → strictly-before-tuple form
    issue = keyset_clause("issue", cursor)
    assert '(title_lex COLLATE "C") > (CAST(:k_tlx AS TEXT) COLLATE "C"))' in issue
    assert "id > :k_id" not in issue
    # H2 — every title_lex comparison is COLLATE "C" (code-point order).
    assert issue.count('COLLATE "C"') >= 2
    # entity type == cursor type → id tiebreak present
    member = keyset_clause("member", cursor)
    assert "id > :k_id" in member
    assert '(title_lex COLLATE "C") = (CAST(:k_tlx AS TEXT) COLLATE "C")' in member
    # entity type > cursor type → inclusive title_lex
    assert '(title_lex COLLATE "C") >= (CAST(:k_tlx AS TEXT) COLLATE "C"))' in keyset_clause(
        "view", cursor
    )


def test_order_limit_collates_code_point():
    order = order_limit(21)
    assert '(title_lex COLLATE "C") ASC' in order
    assert "LIMIT 21" in order


def test_match_and_order_fragments():
    prefix = match_clause("prefix", "E")
    assert "public.mesh_search_norm(:nq)" in prefix
    assert "replace(replace(replace" in prefix
    assert "|| '%'" in prefix
    trigram = match_clause("trigram", "E")
    assert "LIKE '%' ||" in trigram and "% public.mesh_search_norm(:nq)" in trigram
    assert "ORDER BY score_bucket DESC" in order_limit(20)
    assert "LIMIT 20" in order_limit(20)


def test_visibility_fragments_by_role():
    admin, guest, member = _Viewer("owner"), _Viewer("guest"), _Viewer("member")
    assert issue_visibility_clause(admin) == "TRUE"
    assert "member_project_access" in issue_visibility_clause(guest)
    assert "assignee_id" in issue_visibility_clause(guest)
    member_clause = issue_visibility_clause(member)
    assert "project_id IS NULL" in member_clause
    assert "visibility = 'public'" in member_clause

    assert "deleted_at IS NULL" in visible_projects_subquery(admin)
    assert "member_project_access" in visible_projects_subquery(guest)
    assert "lead_member_id" in visible_projects_subquery(member)


def test_validate_search_params_sync():
    ws = uuid.uuid4()
    assert validate_search_params(
        q="", types=None, limit=None, cursor_raw=None, workspace_id=ws, secret=b"s"
    ) is None
    params = validate_search_params(
        q="  José ", types="issue, view", limit=10, cursor_raw=None, workspace_id=ws, secret=b"s"
    )
    assert params is not None
    assert params.q == "José"
    assert params.normalized == "jose"
    assert params.types == frozenset({"issue", "view"})
    assert params.limit == 10
    assert params.cursor is None

    from mesh.errors import ValidationError

    with pytest.raises(ValidationError):
        validate_search_params(
            q="x" * 121, types=None, limit=None, cursor_raw=None, workspace_id=ws, secret=b"s"
        )
    with pytest.raises(ValidationError):
        validate_search_params(
            q="x", types="bogus", limit=None, cursor_raw=None, workspace_id=ws, secret=b"s"
        )
    with pytest.raises(ValidationError):
        validate_search_params(
            q="x", types=" , ", limit=None, cursor_raw=None, workspace_id=ws, secret=b"s"
        )
    with pytest.raises(ValidationError):
        validate_search_params(
            q="x", types=None, limit=99, cursor_raw=None, workspace_id=ws, secret=b"s"
        )
    with pytest.raises(ValidationError):
        validate_search_params(
            q="x", types=None, limit=0, cursor_raw=None, workspace_id=ws, secret=b"s"
        )
    with pytest.raises(ValidationError):
        validate_search_params(
            q="x", types=None, limit=None, cursor_raw="junk", workspace_id=ws, secret=b"s"
        )
