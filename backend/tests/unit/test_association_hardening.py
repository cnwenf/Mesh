"""Round-2 (验收打回) regression tests for the issue-association layer.

Pins the review findings so they cannot silently regress:

- B1: association writes advance ``issue.updated_at``/``version`` (§5.4) —
  stale ``If-Match`` writers get 409, no lost updates.
- B2: number precision validation accepts negatives within precision
  (``round(abs(x),10)`` sign bug regression).
- merge hardening: carrier budget (422 merge_too_large), count == live
  carriers (soft-deleted excluded), concurrent merges converge (race-safe
  ON CONFLICT DO NOTHING inserts), carriers' arbitration tokens advance.
- rigor caps: >50 DISTINCT ids/dicts hit the per-request cap (not the
  duplicate-id branch).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import mesh.labels.service as labels_service_module
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError, ConflictError, ValidationError
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.labels.association import FieldValueService, IssueLabelService
from mesh.labels.service import LabelService

pytestmark = pytest.mark.unit

BASE_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)

# Monotonic clock: every call advances 1s so consecutive bumps produce
# DISTINCT updated_at values (a constant clock would make the §5.4
# arbitration-token assertions indistinguishable).
_CLOCK_STATE = {"now": BASE_NOW}


def _clock() -> datetime:
    _CLOCK_STATE["now"] = _CLOCK_STATE["now"] + timedelta(seconds=1)
    return _CLOCK_STATE["now"]


async def _add_user(session_factory) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            display_name="Tester",
            password_hash="x",
            status="active",
        )
        session.add(user)
    return user.id


class Ctx:
    def __init__(self, session_factory, workspace, admin):
        self.sf = session_factory
        self.workspace = workspace
        self.admin = admin
        self.issues = IssueService(session_factory, clock=_clock)
        self.labels = LabelService(session_factory, clock=_clock)
        self.label_assoc = IssueLabelService(self.issues, clock=_clock)
        self.value_assoc = FieldValueService(self.issues, clock=_clock)


async def _setup(session_factory) -> Ctx:
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    user_id = await _add_user(session_factory)
    async with session_factory() as session, session.begin():
        admin = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user_id,
            role="admin",
            status="active",
            joined_at=BASE_NOW,
        )
        session.add(admin)
    return Ctx(session_factory, workspace, admin)


async def _create_issue(ctx: Ctx) -> uuid.UUID:
    created = await ctx.issues.create_issue(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        body=CreateIssueRequest(title="hardening"),
    )
    return uuid.UUID(created["id"])


async def _issue_row(ctx: Ctx, issue_id: uuid.UUID) -> Issue:
    async with ctx.sf() as session:
        return await session.scalar(select(Issue).where(Issue.id == issue_id))


async def _label(ctx: Ctx, name: str) -> uuid.UUID:
    created = await ctx.labels.create_label(
        actor=ctx.admin,
        workspace_id=ctx.workspace.id,
        name=name,
        color="#e5484d",
    )
    return uuid.UUID(created["id"])


async def _text_field(ctx: Ctx, key: str) -> uuid.UUID:
    created = await ctx.labels.create_field_def(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        name=key, field_key=key, field_type="text",
    )
    return uuid.UUID(created["id"])


# ---------------------------------------------------------------------------
# B1 — association writes advance the issue arbitration token (§5.4)
# ---------------------------------------------------------------------------


async def test_set_values_bumps_token_and_stale_if_match_conflicts(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    field_id = await _text_field(ctx, "note")
    before = await _issue_row(ctx, issue_id)
    token = before.updated_at.isoformat().replace("+00:00", "Z")

    await ctx.value_assoc.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[{"field_def_id": str(field_id), "value_text": "v1"}],
        if_match=token,
    )
    after = await _issue_row(ctx, issue_id)
    assert after.updated_at != before.updated_at  # token advanced
    assert after.version == before.version + 1

    # Second writer carrying the STALE token loses the race: 409, not a
    # silent overwrite.
    with pytest.raises(ConflictError) as excinfo:
        await ctx.value_assoc.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": str(field_id), "value_text": "v2"}],
            if_match=token,
        )
    assert excinfo.value.code == "conflict"
    # The value must still be v1 (the stale writer wrote nothing).
    async with ctx.sf() as session:
        from mesh.db.models.label import IssueCustomFieldValue

        row = await session.scalar(
            select(IssueCustomFieldValue).where(
                IssueCustomFieldValue.issue_id == issue_id
            )
        )
    assert row.value_text == "v1"


async def test_add_label_bumps_token_stale_field_put_conflicts(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    field_id = await _text_field(ctx, "note")
    token = (await _issue_row(ctx, issue_id)).updated_at.isoformat().replace(
        "+00:00", "Z"
    )
    label_id = await _label(ctx, "bug")
    await ctx.label_assoc.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=issue_id, label_id=label_id,
    )
    assert (await _issue_row(ctx, issue_id)).updated_at.isoformat().replace(
        "+00:00", "Z"
    ) != token
    with pytest.raises(ConflictError):
        await ctx.value_assoc.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": str(field_id), "value_text": "x"}],
            if_match=token,
        )


async def test_remove_and_replace_labels_bump_token(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    bug = await _label(ctx, "bug")
    ux = await _label(ctx, "ux")
    v0 = (await _issue_row(ctx, issue_id)).updated_at

    await ctx.label_assoc.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=issue_id, label_id=bug,
    )
    v1 = (await _issue_row(ctx, issue_id)).updated_at
    assert v1 != v0

    await ctx.label_assoc.replace_labels(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=issue_id, label_ids=[ux],
    )
    v2 = (await _issue_row(ctx, issue_id)).updated_at
    assert v2 != v1

    # A no-op replace (same set) must NOT advance the token (§6.9).
    await ctx.label_assoc.replace_labels(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=issue_id, label_ids=[ux],
    )
    assert (await _issue_row(ctx, issue_id)).updated_at == v2

    await ctx.label_assoc.remove_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=issue_id, label_id=ux,
    )
    v3 = (await _issue_row(ctx, issue_id)).updated_at
    assert v3 != v2

    # Idempotent remove (already gone) must NOT advance the token.
    await ctx.label_assoc.remove_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=issue_id, label_id=ux,
    )
    assert (await _issue_row(ctx, issue_id)).updated_at == v3


async def test_merge_bumps_carrier_tokens(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    src = await _label(ctx, "defect")
    tgt = await _label(ctx, "bug")
    await ctx.label_assoc.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=issue_id, label_id=src,
    )
    before = await _issue_row(ctx, issue_id)
    await ctx.labels.merge_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        source_label_id=src, target_label_id=tgt,
    )
    after = await _issue_row(ctx, issue_id)
    assert after.updated_at != before.updated_at
    assert after.version == before.version + 1


# ---------------------------------------------------------------------------
# B2 — negative numbers within precision are accepted
# ---------------------------------------------------------------------------


async def test_negative_number_within_precision_accepted(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    field = await ctx.labels.create_field_def(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        name="Delta", field_key="delta", field_type="number",
        config={"precision": 2, "min": -1000, "max": 1000},
    )
    listing = await ctx.value_assoc.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[{"field_def_id": field["id"], "value_number": -2.5}],
    )
    entry = next(e for e in listing if e["field_def"]["id"] == field["id"])
    assert entry["value"]["value_number"] == -2.5
    # Positive in precision still fine.
    await ctx.value_assoc.set_values(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
        values=[{"field_def_id": field["id"], "value_number": 2.5}],
    )


async def test_negative_number_out_of_precision_rejected(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    field = await ctx.labels.create_field_def(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        name="Delta", field_key="delta", field_type="number",
        config={"precision": 2},
    )
    with pytest.raises(BusinessRuleError) as excinfo:
        await ctx.value_assoc.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=issue_id,
            values=[{"field_def_id": field["id"], "value_number": -2.555}],
        )
    assert excinfo.value.code == "invalid_field_value"
    assert excinfo.value.details["reason"] == "number_precision_exceeded"


async def test_negative_default_within_precision_accepted(session_factory):
    """Definition-layer default validation shares the same fix."""
    ctx = await _setup(session_factory)
    field = await ctx.labels.create_field_def(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        name="Bias", field_key="bias", field_type="number",
        config={"precision": 2}, default_value=-1.25,
    )
    assert field["default_value"] == -1.25
    with pytest.raises(BusinessRuleError) as excinfo:
        await ctx.labels.create_field_def(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            name="Bias2", field_key="bias_two", field_type="number",
            config={"precision": 2}, default_value=-1.259,
        )
    assert excinfo.value.code == "invalid_field_config"


# ---------------------------------------------------------------------------
# merge hardening (review round 1 🟡)
# ---------------------------------------------------------------------------


async def test_merge_rejects_past_carrier_budget(session_factory, monkeypatch):
    ctx = await _setup(session_factory)
    src = await _label(ctx, "hot")
    tgt = await _label(ctx, "target")
    for _ in range(3):
        issue_id = await _create_issue(ctx)
        await ctx.label_assoc.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=issue_id, label_id=src,
        )
    monkeypatch.setattr(labels_service_module, "MERGE_MAX_CARRIERS", 2)
    with pytest.raises(BusinessRuleError) as excinfo:
        await ctx.labels.merge_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            source_label_id=src, target_label_id=tgt,
        )
    assert excinfo.value.code == "merge_too_large"
    assert excinfo.value.details["count"] == 3
    assert excinfo.value.details["budget"] == 2


async def test_merge_count_excludes_soft_deleted_carriers(session_factory):
    ctx = await _setup(session_factory)
    src = await _label(ctx, "defect")
    tgt = await _label(ctx, "bug")
    live = await _create_issue(ctx)
    dead = await _create_issue(ctx)
    for issue_id in (live, dead):
        await ctx.label_assoc.add_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=issue_id, label_id=src,
        )
    await ctx.issues.delete_issue(
        actor=ctx.admin, workspace_id=ctx.workspace.id, issue_id=dead,
    )
    result = await ctx.labels.merge_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        source_label_id=src, target_label_id=tgt,
    )
    # Only the live carrier counts (soft-deleted gets no link, no event).
    assert result["merged_issue_count"] == 1
    from mesh.db.models.label import IssueLabel

    async with ctx.sf() as session:
        live_links = (
            (
                await session.execute(
                    select(IssueLabel.label_id).where(IssueLabel.issue_id == live)
                )
            ).scalars().all()
        )
        dead_links = (
            (
                await session.execute(
                    select(IssueLabel.label_id).where(IssueLabel.issue_id == dead)
                )
            ).scalars().all()
        )
    assert [str(x) for x in live_links] == [str(tgt)]
    assert dead_links == []  # source link gone with the label, no target added


async def test_concurrent_merges_into_same_target_converge(session_factory):
    """Two merges racing on disjoint source locks but a shared target must
    converge: both succeed, the shared carrier ends with exactly one target
    link (ON CONFLICT DO NOTHING), no bare PK 500."""
    ctx = await _setup(session_factory)
    src1 = await _label(ctx, "defect")
    src2 = await _label(ctx, "dupe")
    tgt = await _label(ctx, "bug")
    shared = await _create_issue(ctx)
    only1 = await _create_issue(ctx)
    only2 = await _create_issue(ctx)
    await ctx.label_assoc.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=shared, label_id=src1,
    )
    await ctx.label_assoc.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=shared, label_id=src2,
    )
    await ctx.label_assoc.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=only1, label_id=src1,
    )
    await ctx.label_assoc.add_label(
        actor=ctx.admin, workspace_id=ctx.workspace.id,
        issue_id=only2, label_id=src2,
    )

    results = await asyncio.gather(
        ctx.labels.merge_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            source_label_id=src1, target_label_id=tgt,
        ),
        ctx.labels.merge_label(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            source_label_id=src2, target_label_id=tgt,
        ),
    )
    assert results[0]["merged_issue_count"] == 2  # shared + only1
    assert results[1]["merged_issue_count"] == 2  # shared + only2

    from mesh.db.models.label import IssueLabel

    async with ctx.sf() as session:
        shared_links = (
            (
                await session.execute(
                    select(IssueLabel.label_id).where(IssueLabel.issue_id == shared)
                )
            ).scalars().all()
        )
    assert [str(x) for x in shared_links] == [str(tgt)]  # exactly one, deduped


# ---------------------------------------------------------------------------
# rigor: per-request caps must trip on DISTINCT inputs (review round 1 P3)
# ---------------------------------------------------------------------------


async def test_replace_labels_cap_on_distinct_ids(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    distinct = [uuid.uuid4() for _ in range(51)]
    with pytest.raises(ValidationError) as excinfo:
        await ctx.label_assoc.replace_labels(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=issue_id, label_ids=distinct,
        )
    assert excinfo.value.details["count"] == 51


async def test_set_values_cap_on_distinct_fields(session_factory):
    ctx = await _setup(session_factory)
    issue_id = await _create_issue(ctx)
    values = [
        {"field_def_id": str(uuid.uuid4()), "value_text": "x"} for _ in range(51)
    ]
    with pytest.raises(ValidationError) as excinfo:
        await ctx.value_assoc.set_values(
            actor=ctx.admin, workspace_id=ctx.workspace.id,
            issue_id=issue_id, values=values,
        )
    assert excinfo.value.details["count"] == 51
