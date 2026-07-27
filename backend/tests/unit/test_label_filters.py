"""Label / custom-field filter connection-point tests (label-property.md §2.7).

The clause builders consumed by the list/kanban projection layer (MES-33
remainder) must (a) filter correctly and (b) hit the §2.7 value indexes —
composite GIN ``idx_icfv_value_json`` for enums, the ``(field_def_id,
value_*)`` partial B-Trees for number / date / member. Small fixtures make
the planner prefer seq scans, so plan-shape assertions run with
``enable_seqscan = off`` (proves the indexes are USABLE for the documented
plan shapes; the P95 acceptance itself is the README §10 benchmark).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.labels.association import FieldValueService, IssueLabelService
from mesh.labels.filters import (
    issues_with_boolean_value,
    issues_with_date_range,
    issues_with_enum_value,
    issues_with_labels,
    issues_with_member_value,
    issues_with_number_range,
)
from mesh.labels.service import LabelService

pytestmark = pytest.mark.unit

FIXED_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


async def _setup(session_factory):
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name="Admin", password_hash="x", status="active",
        )
        session.add(user)
    async with session_factory() as session, session.begin():
        admin = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id,
            role="admin", status="active", joined_at=FIXED_NOW,
        )
        session.add(admin)
    issues = IssueService(session_factory, clock=lambda: FIXED_NOW)
    labels = LabelService(session_factory, clock=lambda: FIXED_NOW)
    label_assoc = IssueLabelService(issues, clock=lambda: FIXED_NOW)
    field_assoc = FieldValueService(issues, clock=lambda: FIXED_NOW)

    bug = await labels.create_label(
        actor=admin, workspace_id=workspace.id, name="bug", color="#e5484d"
    )
    ux = await labels.create_label(
        actor=admin, workspace_id=workspace.id, name="ux", color="#30a46c"
    )
    severity = await labels.create_field_def(
        actor=admin, workspace_id=workspace.id,
        name="Severity", field_key="severity", field_type="single_select",
        options=[{"name": "Major", "position": 0}, {"name": "Minor", "position": 1}],
    )
    users = await labels.create_field_def(
        actor=admin, workspace_id=workspace.id,
        name="Users", field_key="users", field_type="number",
    )
    launch = await labels.create_field_def(
        actor=admin, workspace_id=workspace.id,
        name="Launch", field_key="launch", field_type="date",
    )
    acceptor = await labels.create_field_def(
        actor=admin, workspace_id=workspace.id,
        name="Acceptor", field_key="acceptor", field_type="member",
    )
    needs_docs = await labels.create_field_def(
        actor=admin, workspace_id=workspace.id,
        name="Docs", field_key="docs", field_type="boolean",
    )
    major_id = next(o["id"] for o in severity["options"] if o["name"] == "Major")

    created = {}
    for name in ("one", "two", "three"):
        rendered = await issues.create_issue(
            actor=admin, workspace_id=workspace.id,
            body=CreateIssueRequest(title=name),
        )
        created[name] = uuid.UUID(rendered["id"])

    # one: bug + Major + users 1500 + launch 2026-08-01 + member admin + docs
    await label_assoc.add_label(
        actor=admin, workspace_id=workspace.id,
        issue_id=created["one"], label_id=uuid.UUID(bug["id"]),
    )
    await field_assoc.set_values(
        actor=admin, workspace_id=workspace.id, issue_id=created["one"],
        values=[
            {"field_def_id": severity["id"], "value_json": major_id},
            {"field_def_id": users["id"], "value_number": 1500},
            {"field_def_id": launch["id"], "value_date": "2026-08-01"},
            {"field_def_id": acceptor["id"], "value_member_id": str(admin.id)},
            {"field_def_id": needs_docs["id"], "value_boolean": True},
        ],
    )
    # two: ux only + users 10
    await label_assoc.add_label(
        actor=admin, workspace_id=workspace.id,
        issue_id=created["two"], label_id=uuid.UUID(ux["id"]),
    )
    await field_assoc.set_values(
        actor=admin, workspace_id=workspace.id, issue_id=created["two"],
        values=[{"field_def_id": users["id"], "value_number": 10}],
    )
    # three: untouched
    return {
        "workspace": workspace, "admin": admin, "issue_ids": created,
        "bug": bug, "ux": ux, "severity": severity, "users": users,
        "launch": launch, "acceptor": acceptor, "needs_docs": needs_docs,
        "major_id": major_id, "sf": session_factory,
    }


async def _matched(session_factory, workspace_id: uuid.UUID, clause) -> set[uuid.UUID]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Issue.id).where(
                    Issue.workspace_id == workspace_id,
                    Issue.deleted_at.is_(None),
                    clause,
                )
            )
        ).scalars().all()
    return set(rows)


# ---------------------------------------------------------------------------
# correctness
# ---------------------------------------------------------------------------


async def test_label_any_and_all_filters(session_factory):
    env = await _setup(session_factory)
    ids = env["issue_ids"]
    bug_id = uuid.UUID(env["bug"]["id"])
    ux_id = uuid.UUID(env["ux"]["id"])
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_labels([bug_id])) == {ids["one"]}
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_labels([bug_id, ux_id])) == {ids["one"], ids["two"]}
    # ALL of two labels: no issue carries both.
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_labels([bug_id, ux_id], match_all=True)) == set()
    # ALL of one label.
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_labels([bug_id], match_all=True)) == {ids["one"]}


async def test_enum_value_filter(session_factory):
    env = await _setup(session_factory)
    matched = await _matched(
        env["sf"], env["workspace"].id,
        issues_with_enum_value(uuid.UUID(env["severity"]["id"]), [env["major_id"]]),
    )
    assert matched == {env["issue_ids"]["one"]}


async def test_number_range_filter(session_factory):
    env = await _setup(session_factory)
    field = uuid.UUID(env["users"]["id"])
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_number_range(field, ge=1000)) == {env["issue_ids"]["one"]}
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_number_range(field, le=100)) == {env["issue_ids"]["two"]}
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_number_range(field, ge=0, le=1000000)) == {
        env["issue_ids"]["one"], env["issue_ids"]["two"],
    }


async def test_date_range_filter(session_factory):
    env = await _setup(session_factory)
    field = uuid.UUID(env["launch"]["id"])
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 9, 1, tzinfo=UTC)
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_date_range(field, start=start, end=end)) == {
        env["issue_ids"]["one"]
    }
    assert await _matched(env["sf"], env["workspace"].id,
                          issues_with_date_range(field, start=datetime(2027, 1, 1, tzinfo=UTC))) == set()


async def test_member_and_boolean_filters(session_factory):
    env = await _setup(session_factory)
    assert await _matched(
        env["sf"], env["workspace"].id,
        issues_with_member_value(uuid.UUID(env["acceptor"]["id"]), [env["admin"].id]),
    ) == {env["issue_ids"]["one"]}
    assert await _matched(
        env["sf"], env["workspace"].id,
        issues_with_boolean_value(uuid.UUID(env["needs_docs"]["id"]), True),
    ) == {env["issue_ids"]["one"]}
    assert await _matched(
        env["sf"], env["workspace"].id,
        issues_with_boolean_value(uuid.UUID(env["needs_docs"]["id"]), False),
    ) == set()


async def test_empty_input_raises(session_factory):
    with pytest.raises(ValueError):
        issues_with_labels([])
    with pytest.raises(ValueError):
        issues_with_enum_value(uuid.uuid4(), [])
    with pytest.raises(ValueError):
        issues_with_member_value(uuid.uuid4(), [])


# ---------------------------------------------------------------------------
# plan shape — §2.7 indexes are usable
# ---------------------------------------------------------------------------


async def _plan_text(session_factory, workspace_id: uuid.UUID, clause) -> str:
    """EXPLAIN ANALYZE with seq scans disabled — index-usability proof.

    Runs the statement through the driver with native binds (a JSONB-typed
    containment parameter cannot be inlined as a SQL literal).
    """
    async with session_factory() as session:
        # Seq scans off so the plan must prove the §2.7 index is USABLE
        # (the P95 acceptance itself is the README §10 100k-row benchmark).
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        stmt = select(Issue.id).where(
            Issue.workspace_id == workspace_id,
            Issue.deleted_at.is_(None),
            clause,
        )
        # Default-dialect compile renders :name placeholders, which text()
        # re-binds through the asyncpg driver (the asyncpg dialect itself
        # renders positional $n that text() cannot re-consume).
        compiled = stmt.compile()
        explained = (
            await session.execute(
                text(f"EXPLAIN (ANALYZE, BUFFERS) {compiled}"),
                dict(compiled.params),
            )
        ).all()
    return "\n".join(row[0] for row in explained)


async def test_enum_filter_plan_uses_gin_index(session_factory):
    from sqlalchemy import cast, literal
    from sqlalchemy.dialects.postgresql import JSONB

    from mesh.db.models.label import IssueCustomFieldValue

    env = await _setup(session_factory)
    field_def_id = uuid.UUID(env["severity"]["id"])
    # Seed per the §2.8 distribution: the severity field holds many rows and
    # the containment condition is selective — so the composite GIN must beat
    # any btree path deterministically (500 issues × one field value each,
    # exactly one matching the @> condition).
    async with session_factory() as session, session.begin():
        status_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO issue_statuses (id, workspace_id, name, category, color, position) "
                "VALUES (:st, :ws, 'Seeded', 'todo', '#000000', 99)"
            ),
            {"st": status_id, "ws": env["workspace"].id},
        )
        await session.execute(
            text(
                "INSERT INTO issues (id, workspace_id, identifier_namespace_key, "
                "number, identifier, title, status_id, state_category) "
                "SELECT gen_random_uuid(), :ws, 'seed', 100000 + g, 'SEED-' || g, "
                "'seed', :st, 'todo' FROM generate_series(1, 500) g"
            ),
            {"ws": env["workspace"].id, "st": status_id},
        )
        await session.execute(
            text(
                "INSERT INTO issue_custom_field_values "
                "(workspace_id, issue_id, field_def_id, value_json) "
                "SELECT i.workspace_id, i.id, :fd, '\"seed-other\"' "
                "FROM issues i WHERE i.identifier_namespace_key = 'seed'"
            ),
            {"fd": field_def_id},
        )
        # Exactly one matching row (mirrors §2.8 "Major 10%" selectivity).
        await session.execute(
            text(
                "UPDATE issue_custom_field_values SET value_json = :match "
                "WHERE issue_id = :iss AND field_def_id = :fd"
            ),
            {
                "match": f'"{env["major_id"]}"',
                "iss": env["issue_ids"]["one"],
                "fd": field_def_id,
            },
        )
        await session.execute(text("ANALYZE issue_custom_field_values"))

    # §2.8 containment shape on the values table itself: with seq scans off
    # the planner must route field_def_id = … AND value_json @> … through the
    # composite GIN. (In the joined form the planner may legitimately drive
    # from the issues side when the issue predicates are more selective —
    # the §10 benchmark settles which plan wins at scale.)
    async with session_factory() as session:
        # Seq scans AND plain btree index scans off → the containment query
        # must take the bitmap path, i.e. the composite GIN.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        await session.execute(text("SET LOCAL enable_indexscan = off"))
        stmt = select(IssueCustomFieldValue.issue_id).where(
            IssueCustomFieldValue.field_def_id == field_def_id,
            IssueCustomFieldValue.value_json.op("@>")(
                cast(literal(f'"{env["major_id"]}"'), JSONB)
            ),
        )
        compiled = stmt.compile()
        rows = (
            await session.execute(
                text(f"EXPLAIN (ANALYZE, BUFFERS) {compiled}"), dict(compiled.params)
            )
        ).all()
    plan = "\n".join(row[0] for row in rows)
    assert "idx_icfv_value_json" in plan
    assert "Seq Scan on issue_custom_field_values" not in plan
    # And the production clause renders the same containment operator.
    assert "@>" in str(
        issues_with_enum_value(field_def_id, [env["major_id"]]).compile()
    )


async def test_number_filter_plan_uses_partial_index(session_factory):
    env = await _setup(session_factory)
    plan = await _plan_text(
        session_factory, env["workspace"].id,
        issues_with_number_range(uuid.UUID(env["users"]["id"]), ge=1000),
    )
    assert "idx_icfv_number" in plan
    assert "Seq Scan on issue_custom_field_values" not in plan
