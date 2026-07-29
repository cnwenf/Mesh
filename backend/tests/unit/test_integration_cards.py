"""Card callback auth-chain tests (integrations.md §3.2 / §5.2, HIGH-1/R4).

Full chain: signature → external_identities → users.id → workspace roster
JOIN → §6.10 permission (via decide_approval). Negative paths (unmapped /
no roster row / signature failure) must leave the approval unchanged and
write an audit row.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.audit import AuditLog
from mesh.db.models.integration import ExternalIdentity
from mesh.db.models.member import Member
from mesh.db.models.runtime import Approval, TaskExecution
from mesh.db.models.user import User
from mesh.integrations.cards import handle_card_callback
from tests.unit.integrations_support import NOW, TEST_SIGNING_SECRET, seed_world, slack_request

pytestmark = pytest.mark.unit


async def _make_pending_approval(session_factory, world, *, requester_member_id=None):
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        execution = TaskExecution(
            workspace_id=world["ws"],
            agent_id=world["agent"],
            trigger="integration",
            status="awaiting_approval",
            task_spec={"kind": "integration_event"},
        )
        session.add(execution)
        await session.flush()
        approval = Approval(
            workspace_id=world["ws"],
            subject_type="tool_call",
            subject_execution_id=execution.id,
            requested_by_member_id=requester_member_id or world["member"],
            action_summary={"action": "deploy", "capability": "exec:shell",
                            "permission": "confirm_required"},
            status="pending",
            expires_at=NOW + timedelta(hours=2),
        )
        session.add(approval)
    return approval


def _card_payload(approval_id: uuid.UUID, *, user_id: str = "U_CLICKER",
                  team_id: str = "T_TEST", decision: str = "approve") -> dict:
    return {
        "type": "block_actions",
        "team": {"id": team_id},
        "user": {"id": user_id},
        "actions": [{
            "action_id": "a1",
            "value": json.dumps({"approval_id": str(approval_id), "decision": decision}),
        }],
    }


async def _map_identity(session_factory, world, *, user_id=None, key="U_CLICKER"):
    async with session_factory() as session, session.begin():
        session.add(ExternalIdentity(
            provider="slack", provider_tenant_key="T_TEST",
            external_user_key=key, user_id=user_id or world["user"],
            created_in_workspace_id=world["ws"],
        ))


async def _call(session_factory, world, payload: dict, *, secret=None):
    body, headers = slack_request(
        secret or world["secrets"]["slack_signing_secret"], payload
    )
    async with session_factory() as session, session.begin():
        return await handle_card_callback(
            session, session_factory, kind="im_slack", raw_body=body,
            headers=headers, signing_secret=TEST_SIGNING_SECRET,
            now=NOW, tolerance=timedelta(seconds=300),
        )


async def test_mapped_member_approves_via_card(session_factory):
    world = await seed_world(session_factory)
    approval = await _make_pending_approval(session_factory, world)
    await _map_identity(session_factory, world)
    status, body = await _call(session_factory, world, _card_payload(approval.id))
    assert status == 200
    assert body["ok"] is True
    async with session_factory() as session:
        row = await session.get(Approval, approval.id)
        assert row.status == "approved"
        assert row.decision_comment == "via slack card callback"
        execution = await session.get(TaskExecution, row.subject_execution_id)
        assert execution.status == "queued", "approval resumes the execution (§6.10)"


async def test_unmapped_clicker_403_approval_unchanged(session_factory):
    world = await seed_world(session_factory)
    approval = await _make_pending_approval(session_factory, world)
    # No external_identities row for U_CLICKER.
    status, body = await _call(session_factory, world, _card_payload(approval.id))
    assert status == 403
    async with session_factory() as session:
        row = await session.get(Approval, approval.id)
        assert row.status == "pending", "denial must not change the approval"
        audits = (await session.execute(
            select(AuditLog).where(
                AuditLog.action == "integration.card_callback_denied"
            )
        )).scalars().all()
        assert len(audits) == 1
        assert audits[0].metadata_["reason"] == "identity_unmapped"


async def test_no_roster_row_in_workspace_403(session_factory):
    world = await seed_world(session_factory)
    approval = await _make_pending_approval(session_factory, world)
    # Identity maps to a user with NO member row in this workspace.
    async with session_factory() as session, session.begin():
        stranger = User(
            id=uuid.uuid4(), email=f"stranger-{uuid.uuid4().hex[:8]}@mesh.test",
            display_name="Stranger", password_hash="unused",
        )
        session.add(stranger)
        await session.flush()
    await _map_identity(session_factory, world, user_id=stranger.id)
    status, _ = await _call(session_factory, world, _card_payload(approval.id))
    assert status == 403
    async with session_factory() as session:
        assert (await session.get(Approval, approval.id)).status == "pending"


async def test_permission_denied_member_403(session_factory):
    world = await seed_world(session_factory)
    # A plain member (not admin, not the requester) with a mapping.
    async with session_factory() as session, session.begin():
        plain_user = User(
            id=uuid.uuid4(), email=f"plain-{uuid.uuid4().hex[:8]}@mesh.test",
            display_name="Plain", password_hash="unused",
        )
        session.add(plain_user)
        await session.flush()
        plain_member = Member(
            id=uuid.uuid4(), workspace_id=world["ws"], member_type="human",
            user_id=plain_user.id, role="member", status="active",
        )
        session.add(plain_member)
        session.add(ExternalIdentity(
            provider="slack", provider_tenant_key="T_TEST",
            external_user_key="U_CLICKER", user_id=plain_user.id,
            created_in_workspace_id=world["ws"],
        ))
    approval = await _make_pending_approval(session_factory, world)  # requested by admin member
    status, body = await _call(session_factory, world, _card_payload(approval.id))
    assert status == 403, "§6.10 permission row must be re-checked after the roster JOIN"
    async with session_factory() as session:
        assert (await session.get(Approval, approval.id)).status == "pending"


async def test_bad_signature_never_forwards(session_factory):
    world = await seed_world(session_factory)
    approval = await _make_pending_approval(session_factory, world)
    await _map_identity(session_factory, world)
    status, _ = await _call(
        session_factory, world, _card_payload(approval.id), secret="WRONG"
    )
    assert status == 401
    async with session_factory() as session:
        assert (await session.get(Approval, approval.id)).status == "pending"


async def test_repeat_click_idempotent(session_factory):
    world = await seed_world(session_factory)
    approval = await _make_pending_approval(session_factory, world)
    await _map_identity(session_factory, world)
    first = await _call(session_factory, world, _card_payload(approval.id))
    second = await _call(session_factory, world, _card_payload(approval.id))
    assert first[0] == 200 and second[0] == 200
    assert second[1]["approval"]["status"] == "approved", "repeat = no-op (§6.10)"


async def test_reject_decision(session_factory):
    world = await seed_world(session_factory)
    approval = await _make_pending_approval(session_factory, world)
    await _map_identity(session_factory, world)
    status, _ = await _call(
        session_factory, world, _card_payload(approval.id, decision="reject")
    )
    assert status == 200
    async with session_factory() as session:
        row = await session.get(Approval, approval.id)
        assert row.status == "rejected"
        execution = await session.get(TaskExecution, row.subject_execution_id)
        assert execution.status == "cancelled"
