"""DingTalk interactive card tests (integrations.md §3.10 / §4.4).

Callback auth chain runs against the REAL external_identities → members →
approvals stack; card push runs against the scripted DingTalk transport.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import hmac
import json
import types
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.audit import AuditLog
from mesh.db.models.integration import ExternalIdentity, Integration
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification, NotificationDelivery
from mesh.db.models.runtime import Approval, TaskExecution
from mesh.db.models.user import User
from mesh.integrations.cards import handle_card_callback
from mesh.integrations.dingtalk_cards import (
    CARD_STATE_EXPIRED,
    CARD_STATE_FORBIDDEN,
    CARD_STATE_LOADING,
    DEFAULT_CARD_TEMPLATE_ID,
    DINGTALK_CALLBACK_TOLERANCE,
    assert_not_action_card,
    build_approval_card_param_map,
    derive_out_track_id,
    extract_dingtalk_action,
    extract_dingtalk_clicker,
    handle_dingtalk_card_callback,
    lifecycle_response,
    open_space_id,
    parse_out_track_context,
    parse_out_track_id,
    push_approval_card,
    push_card_from_event,
    verify_callback_signature,
)
from mesh.integrations.im_outbound import (
    CONVERSATION_DIRECT,
    CONVERSATION_GROUP,
    IM_SEND_EVENT_TYPE,
    ConversationTarget,
    IMSendRelay,
    encode_external_contact_key,
)
from tests.unit.integrations_dingtalk_support import (
    ScriptedDingTalkTransport,
    make_client,
)
from tests.unit.integrations_support import TEST_SIGNING_SECRET, encrypt, seed_world
from tests.unit.test_ack import CONVERSATION_KEY

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
STAFF_ID = "014728255240768602"
CORP_ID = "dingcorpTEST"
APP_SECRET = "ding-app-secret-plaintext"


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


async def _seed(session_factory, *, receive_mode: str = "stream") -> dict:
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        integration = Integration(
            id=uuid.uuid4(),
            workspace_id=world["ws"],
            kind="im_dingtalk",
            name="dingtalk-cards",
            config={
                "app_key": "dingappkey",
                "corp_id": CORP_ID,
                "robot_code": "robot-1",
                "receive_mode": receive_mode,
            },
            secret_ref=encrypt(APP_SECRET),
            created_by=world["member"],
        )
        session.add(integration)
    world["integ_dingtalk"] = integration.id
    world["integration_obj"] = integration
    return world


async def _make_approval(session_factory, world, *, status: str = "pending") -> Approval:
    async with session_factory() as session, session.begin():
        execution = TaskExecution(
            workspace_id=world["ws"],
            agent_id=world["agent"],
            trigger="integration",
            status="awaiting_approval",
            task_spec={},
        )
        session.add(execution)
        await session.flush()
        approval = Approval(
            workspace_id=world["ws"],
            subject_type="tool_call",
            subject_execution_id=execution.id,
            requested_by_member_id=world["member"],
            action_summary={
                "action": "删除过期数据",
                "capability": "database",
                "permission": "write",
                "impact_scope": "orders 表",
                "estimated_cost": "~1200 tokens",
                "tools_summary": "purge_orders()",
            },
            status=status,
            expires_at=NOW + timedelta(hours=1),
        )
        session.add(approval)
        await session.flush()
    return approval


async def _map_identity(
    session_factory, world, *, external_user_key: str = STAFF_ID, user_id=None
) -> ExternalIdentity:
    async with session_factory() as session, session.begin():
        identity = ExternalIdentity(
            provider="dingtalk",
            provider_tenant_key=CORP_ID,
            external_user_key=external_user_key,
            user_id=user_id or world["user"],
            created_in_workspace_id=world["ws"],
        )
        session.add(identity)
    return identity


def _card_payload(
    approval_id,
    *,
    decision: str = "approve",
    user_id: str = STAFF_ID,
    integration_id: uuid.UUID | None = None,
) -> dict:
    return {
        "outTrackId": derive_out_track_id(approval_id, integration_id),
        "corpId": CORP_ID,
        "userId": user_id,
        "userIdType": "staffId",
        "content": {
            "cardPrivateData": {
                "actionIds": [decision],
                "params": {"approval_id": str(approval_id), "decision": decision},
            }
        },
    }


def _sign(app_secret: str, ts_ms: int) -> str:
    material = f"{ts_ms}\n{app_secret}".encode()
    return base64.b64encode(hmac.new(app_secret.encode(), material, hashlib.sha256).digest()).decode()


def _target(world, *, conversation_type: str = CONVERSATION_GROUP, sender_key: str = ""):
    return ConversationTarget(
        workspace_id=world["ws"],
        integration_id=world["integ_dingtalk"],
        provider_tenant_key=CORP_ID,
        external_ref="cid6EUvB2O8qVF2RYQtHTKEsg==",
        conversation_type=conversation_type,
        sender_key=sender_key,
    )


# ---------------------------------------------------------------------------
# Derivation + builders (§3.10 / §4.4)
# ---------------------------------------------------------------------------


def test_out_track_id_roundtrip():
    approval_id = uuid.uuid4()
    track = derive_out_track_id(approval_id)
    assert track.startswith("mesh-appr-")
    assert parse_out_track_id(track) == approval_id
    assert parse_out_track_id("garbage") is None
    assert parse_out_track_id("mesh-appr-nothex") is None
    integration_id = uuid.uuid4()
    anchored = derive_out_track_id(approval_id, integration_id)
    assert len(anchored) <= 64
    assert parse_out_track_context(anchored) == (approval_id, integration_id)
    assert parse_out_track_id(anchored) == approval_id


def test_open_space_id_formats():
    assert (
        open_space_id(conversation_type=CONVERSATION_GROUP, open_conversation_id="cid==")
        == "dtv1.card//IM_GROUP.cid=="
    )
    assert (
        open_space_id(conversation_type=CONVERSATION_DIRECT, sender_staff_id="staff1")
        == "dtv1.card//IM_ROBOT.staff1"
    )
    with pytest.raises(ValueError):
        open_space_id(conversation_type=CONVERSATION_GROUP)
    with pytest.raises(ValueError):
        open_space_id(conversation_type=CONVERSATION_DIRECT)


def test_action_card_forbidden_for_approval_cards():
    for key in ("sampleActionCard", "sampleActionCard6", "sampleActionCard2"):
        with pytest.raises(AssertionError):
            assert_not_action_card(key)
    assert_not_action_card(DEFAULT_CARD_TEMPLATE_ID)  # no raise


def test_card_param_map_maps_action_summary():
    class _FakeApproval:
        action_summary = {
            "action": "删除过期数据",
            "capability": "database",
            "permission": "write",
            "impact_scope": "orders 表",
            "estimated_cost": "~1200 tokens",
            "tools_summary": "purge_orders()",
        }
        expires_at = NOW + timedelta(hours=1)

    params = build_approval_card_param_map(
        _FakeApproval(), agent_name="值班 Agent", detail_url="https://mesh/x"
    )
    assert params["title"] == "Mesh 审批请求 · 删除过期数据"
    assert params["agent_name"] == "值班 Agent"
    assert params["action"] == "删除过期数据"
    assert params["capability"] == "database"
    assert params["permission"] == "write"
    assert params["impact_scope"] == "orders 表"
    assert params["estimated_cost"] == "~1200 tokens"
    assert "purge_orders()" in params["resume_hint"]
    assert params["buttons_disabled"] == "false"
    assert params["detail_url"] == "https://mesh/x"


def test_lifecycle_loading_is_clicker_private():
    body = lifecycle_response(CARD_STATE_LOADING, user_id="staff1")
    assert body["cardUpdateOptions"]["updatePrivateDataByKey"] is True
    assert body["userPrivateData"]["staff1"]["cardParamMap"]["status_text"] == "处理中…"
    assert "cardData" not in body  # public data untouched while loading


def test_lifecycle_terminal_states():
    approved = lifecycle_response("approved", approver_name="张三", decided_at="2026-07-30T12:00:00+00:00")
    assert "已批准" in approved["cardData"]["cardParamMap"]["status_text"]
    assert approved["cardData"]["cardParamMap"]["buttons_disabled"] == "true"
    rejected = lifecycle_response("rejected", approver_name="张三", decided_at="t")
    assert "已拒绝" in rejected["cardData"]["cardParamMap"]["status_text"]
    forbidden = lifecycle_response(CARD_STATE_FORBIDDEN)
    assert "无权限" in forbidden["cardData"]["cardParamMap"]["status_text"]
    expired = lifecycle_response(CARD_STATE_EXPIRED, detail_url="https://mesh/a")
    assert "过期" in expired["cardData"]["cardParamMap"]["status_text"]
    assert expired["cardData"]["cardParamMap"]["detail_url"] == "https://mesh/a"
    failed = lifecycle_response("failed", detail_url="https://mesh/a")
    assert "处理失败" in failed["cardData"]["cardParamMap"]["status_text"]


# ---------------------------------------------------------------------------
# Callback signature (§3.2 DingTalk row)
# ---------------------------------------------------------------------------


def test_callback_signature_valid():
    ts_ms = int(NOW.timestamp() * 1000)
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, timestamp=str(ts_ms), sign=_sign(APP_SECRET, ts_ms), now=NOW
        )
        == "valid"
    )


def test_callback_signature_wrong_secret():
    ts_ms = int(NOW.timestamp() * 1000)
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET,
            timestamp=str(ts_ms),
            sign=_sign("other-secret", ts_ms),
            now=NOW,
        )
        == "invalid"
    )


def test_callback_signature_missing():
    assert verify_callback_signature(app_secret=APP_SECRET, timestamp="123", sign=None, now=NOW) == "missing"


def test_callback_signature_timestamp_window_official_3600s():
    assert DINGTALK_CALLBACK_TOLERANCE == timedelta(seconds=3600)
    # 59 minutes ago — inside the official window → accepted
    ts_in = int((NOW - timedelta(minutes=59)).timestamp() * 1000)
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, timestamp=str(ts_in), sign=_sign(APP_SECRET, ts_in), now=NOW
        )
        == "valid"
    )
    # 61 minutes ago — outside → rejected
    ts_out = int((NOW - timedelta(minutes=61)).timestamp() * 1000)
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, timestamp=str(ts_out), sign=_sign(APP_SECRET, ts_out), now=NOW
        )
        == "invalid"
    )


# ---------------------------------------------------------------------------
# Clicker / action extraction
# ---------------------------------------------------------------------------


def test_clicker_staff_id_normalization():
    world_obj = type("I", (), {"config": {"corp_id": CORP_ID}})()
    clicker = extract_dingtalk_clicker({"userId": STAFF_ID, "corpId": CORP_ID}, world_obj)
    assert clicker == ("dingtalk", CORP_ID, STAFF_ID)


def test_clicker_external_contact_fallback():
    world_obj = type("I", (), {"config": {"corp_id": CORP_ID}})()
    sender_id = "$:LWCP_v1:$6GYsn+zrv5WZ77xc2v4zsyXfBv1MhAv9"
    clicker = extract_dingtalk_clicker({"userId": "", "senderId": sender_id, "corpId": CORP_ID}, world_obj)
    assert clicker == ("dingtalk", CORP_ID, encode_external_contact_key(sender_id))
    assert ":" not in clicker[2]


def test_clicker_tenant_from_config_when_payload_missing():
    world_obj = type("I", (), {"config": {"corp_id": CORP_ID}})()
    clicker = extract_dingtalk_clicker({"userId": STAFF_ID}, world_obj)
    assert clicker[1] == CORP_ID


def test_action_extraction_from_private_data_params():
    approval_id = uuid.uuid4()
    payload = _card_payload(approval_id, decision="reject")
    assert extract_dingtalk_action(payload) == (approval_id, False)
    payload_approve = _card_payload(approval_id, decision="approve")
    assert extract_dingtalk_action(payload_approve) == (approval_id, True)


def test_action_extraction_malformed():
    assert extract_dingtalk_action({"content": {"cardPrivateData": {"params": {}}}}) is None
    assert extract_dingtalk_action({}) is None


# ---------------------------------------------------------------------------
# Card push (createAndDeliver wire format)
# ---------------------------------------------------------------------------


async def _adapter_client(redis_client, transport: ScriptedDingTalkTransport):
    """Build a DingTalkClient on the ENV-DRIVEN redis fixture (CI parity —
    never hardcode a redis URL; the test redis port differs per env)."""
    from mesh.integrations.dingtalk_api import DingTalkClient, DingTalkTokenManager

    manager = DingTalkTokenManager(
        redis_client,
        http_client=make_client(transport),
        integration_id=uuid.uuid4(),
        app_key="k",
        app_secret="s",
        jitter=lambda: 0,
    )
    return DingTalkClient(manager, http_client=make_client(transport), robot_code="robot-1")


async def test_push_approval_card_wire_format(session_factory, redis_client):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(redis_client, transport)
    outcome = await push_approval_card(
        client,
        approval=approval,
        target=_target(world),
        agent_name="值班 Agent",
        detail_url="https://mesh/approvals/x",
    )
    assert outcome.sent
    (sent,) = transport.calls_for("/v1.0/card/instances/createAndDeliver")
    body = sent.body
    assert body["cardTemplateId"] == DEFAULT_CARD_TEMPLATE_ID
    assert body["outTrackId"] == derive_out_track_id(approval.id, world["integ_dingtalk"])
    assert body["openSpaceId"] == "dtv1.card//IM_GROUP.cid6EUvB2O8qVF2RYQtHTKEsg=="
    assert body["callbackType"] == "STREAM"
    assert body["cardData"]["cardParamMap"]["title"].startswith("Mesh 审批请求")


async def test_push_approval_card_direct_space(session_factory, redis_client):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(redis_client, transport)
    outcome = await push_approval_card(
        client,
        approval=approval,
        target=_target(world, conversation_type=CONVERSATION_DIRECT, sender_key=STAFF_ID),
    )
    assert outcome.sent
    (sent,) = transport.calls_for("/v1.0/card/instances/createAndDeliver")
    assert sent.body["openSpaceId"] == f"dtv1.card//IM_ROBOT.{STAFF_ID}"


async def test_push_approval_card_rejects_action_card_template(session_factory, redis_client):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(redis_client, transport)
    with pytest.raises(AssertionError):
        await push_approval_card(
            client,
            approval=approval,
            target=_target(world),
            card_template_id="sampleActionCard6",
        )
    assert transport.calls_for("/v1.0/card/instances/createAndDeliver") == []


async def test_push_approval_card_direct_external_contact_fails(session_factory, redis_client):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(redis_client, transport)
    outcome = await push_approval_card(
        client,
        approval=approval,
        target=_target(
            world,
            conversation_type=CONVERSATION_DIRECT,
            sender_key=encode_external_contact_key("$:LWCP_v1:$abc"),
        ),
    )
    assert outcome.reason == "no_staff_id"


async def test_update_card_is_idempotent_by_out_track_id(session_factory, redis_client):
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(redis_client, transport)
    approval_id = uuid.uuid4()
    body = {
        "outTrackId": derive_out_track_id(approval_id),
        "cardData": {"cardParamMap": {"status_text": "x"}},
    }
    await client.update_card(body)
    await client.update_card(body)
    updates = transport.calls_for("/v1.0/card/instances")
    assert len(updates) == 2
    assert all(u.body["outTrackId"] == derive_out_track_id(approval_id) for u in updates)


# ---------------------------------------------------------------------------
# Callback pipeline (auth chain + lifecycle + idempotency)
# ---------------------------------------------------------------------------


async def _run_callback(session_factory, world, payload):
    async with session_factory() as session, session.begin():
        return await handle_dingtalk_card_callback(
            session,
            session_factory,
            integration=world["integration_obj"],
            payload=payload,
            now=NOW,
            app_base_url="https://mesh.example.com",
        )


async def _approval_status(session_factory, approval_id):
    async with session_factory() as session:
        row = await session.get(Approval, approval_id)
        return row.status


async def test_callback_approve_forwards_and_writes_back_terminal_card(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    status, body = await _run_callback(session_factory, world, _card_payload(approval.id))
    assert status == 200
    assert await _approval_status(session_factory, approval.id) == "approved"
    card_params = body["cardData"]["cardParamMap"]
    assert "已批准" in card_params["status_text"]
    assert card_params["buttons_disabled"] == "true"
    # decision comment records the IM surface
    async with session_factory() as session:
        row = await session.get(Approval, approval.id)
        assert row.decision_comment == "via dingtalk card callback"


async def test_callback_reject(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    status, body = await _run_callback(session_factory, world, _card_payload(approval.id, decision="reject"))
    assert status == 200
    assert await _approval_status(session_factory, approval.id) == "rejected"
    assert "已拒绝" in body["cardData"]["cardParamMap"]["status_text"]


async def test_callback_unmapped_identity_403_approval_unchanged(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    # NO identity mapping
    status, body = await _run_callback(session_factory, world, _card_payload(approval.id))
    assert status == 403
    assert await _approval_status(session_factory, approval.id) == "pending"
    assert "无权限" in body["cardData"]["cardParamMap"]["status_text"]


async def test_callback_no_roster_row_403(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    # Identity mapped to a user WITHOUT a membership in this workspace
    async with session_factory() as session, session.begin():
        outsider = User(
            id=uuid.uuid4(),
            email="outsider@mesh.test",
            display_name="Outsider",
            password_hash="x",
        )
        session.add(outsider)
        await session.flush()
    await _map_identity(session_factory, world, user_id=outsider.id)
    status, _ = await _run_callback(session_factory, world, _card_payload(approval.id))
    assert status == 403
    assert await _approval_status(session_factory, approval.id) == "pending"


async def test_callback_no_permission_403_approval_unchanged(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    # A plain member (not admin/owner, not the agent's owner)
    async with session_factory() as session, session.begin():
        plain_user = User(
            id=uuid.uuid4(),
            email="plain@mesh.test",
            display_name="Plain",
            password_hash="x",
        )
        session.add(plain_user)
        await session.flush()
        session.add(
            Member(
                id=uuid.uuid4(),
                workspace_id=world["ws"],
                member_type="human",
                user_id=plain_user.id,
                role="member",
                status="active",
            )
        )
    await _map_identity(session_factory, world, user_id=plain_user.id)
    status, body = await _run_callback(session_factory, world, _card_payload(approval.id))
    assert status == 403
    assert await _approval_status(session_factory, approval.id) == "pending"
    assert "无权限" in body["cardData"]["cardParamMap"]["status_text"]


async def test_callback_repeat_click_idempotent(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    status1, _ = await _run_callback(session_factory, world, _card_payload(approval.id))
    assert status1 == 200
    # Second click (even with the opposite decision) is a no-op
    status2, body2 = await _run_callback(
        session_factory, world, _card_payload(approval.id, decision="reject")
    )
    assert status2 == 200
    assert await _approval_status(session_factory, approval.id) == "approved"
    assert "已批准" in body2["cardData"]["cardParamMap"]["status_text"]


async def test_concurrent_opposite_clicks_both_render_persisted_truth(
    session_factory,
):
    """Both callbacks may observe pending before the row-lock winner commits;
    each lifecycle response must nevertheless render decide_approval's final
    status, never its own stale request intent."""
    from unittest.mock import patch

    from mesh.runtime.approvals import decide_approval as real_decide_approval

    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    both_ready = asyncio.Event()
    arrivals = 0

    async def synchronized_decide(*args, **kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=2)
        return await real_decide_approval(*args, **kwargs)

    with patch(
        "mesh.integrations.dingtalk_cards.decide_approval",
        side_effect=synchronized_decide,
    ):
        responses = await asyncio.gather(
            _run_callback(session_factory, world, _card_payload(approval.id)),
            _run_callback(
                session_factory,
                world,
                _card_payload(approval.id, decision="reject"),
            ),
        )

    final_status = await _approval_status(session_factory, approval.id)
    expected_text = "已批准" if final_status == "approved" else "已拒绝"
    assert final_status in {"approved", "rejected"}
    assert all(status == 200 for status, _body in responses)
    assert all(
        expected_text in body["cardData"]["cardParamMap"]["status_text"] for _status, body in responses
    )


async def test_http_callback_selects_the_valid_app_among_same_corp_candidates(
    session_factory,
):
    world = await _seed(session_factory, receive_mode="http")
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    second_secret = "second-app-secret"
    async with session_factory() as session, session.begin():
        session.add(
            Integration(
                id=uuid.uuid4(),
                workspace_id=world["ws"],
                kind="im_dingtalk",
                name="dingtalk-cards-second-app",
                config={
                    "app_key": "ding-second-app",
                    "corp_id": CORP_ID,
                    "robot_code": "robot-2",
                    "receive_mode": "http",
                },
                secret_ref=encrypt(second_secret),
                created_by=world["member"],
            )
        )
    payload = _card_payload(approval.id)
    raw_body = json.dumps(payload).encode()
    ts_ms = int(NOW.timestamp() * 1000)

    async with session_factory() as session, session.begin():
        status, body = await handle_card_callback(
            session,
            session_factory,
            kind="im_dingtalk",
            raw_body=raw_body,
            headers={"timestamp": str(ts_ms), "sign": _sign(second_secret, ts_ms)},
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
            app_base_url="https://mesh.example.com",
        )

    assert status == 200
    assert "已批准" in body["cardData"]["cardParamMap"]["status_text"]
    assert await _approval_status(session_factory, approval.id) == "approved"


async def test_http_card_callback_routes_to_immutable_source_sibling(session_factory):
    world = await _seed(session_factory, receive_mode="http")
    active_approval = await _make_approval(session_factory, world)
    disabled_approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    disabled_id = uuid.UUID(int=1)
    async with session_factory() as session, session.begin():
        session.add(
            Integration(
                id=disabled_id,
                workspace_id=world["ws"],
                kind="im_dingtalk",
                name="dingtalk-disabled-sibling",
                status="disabled",
                config={
                    "app_key": "ding-app-key",
                    "corp_id": CORP_ID,
                    "robot_code": "robot-disabled",
                    "receive_mode": "http",
                },
                secret_ref=encrypt(APP_SECRET),
                created_by=world["member"],
            )
        )

    async def invoke(approval_id, source_id):
        payload = _card_payload(approval_id, integration_id=source_id)
        raw_body = json.dumps(payload).encode()
        ts_ms = int(NOW.timestamp() * 1000)
        async with session_factory() as session, session.begin():
            return await handle_card_callback(
                session,
                session_factory,
                kind="im_dingtalk",
                raw_body=raw_body,
                headers={"timestamp": str(ts_ms), "sign": _sign(APP_SECRET, ts_ms)},
                signing_secret=TEST_SIGNING_SECRET,
                now=NOW,
                tolerance=timedelta(seconds=300),
                app_base_url="https://mesh.example.com",
            )

    active_status, _ = await invoke(active_approval.id, world["integ_dingtalk"])
    disabled_status, disabled_body = await invoke(disabled_approval.id, disabled_id)

    assert active_status == 200
    assert await _approval_status(session_factory, active_approval.id) == "approved"
    assert disabled_status == 401
    assert disabled_body["error"]["code"] == "integration_disabled"
    assert await _approval_status(session_factory, disabled_approval.id) == "pending"


async def test_http_callback_rejects_out_track_action_approval_mismatch(
    session_factory,
):
    world = await _seed(session_factory, receive_mode="http")
    card_approval = await _make_approval(session_factory, world)
    injected_approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    payload = _card_payload(injected_approval.id)
    payload["outTrackId"] = derive_out_track_id(card_approval.id)
    raw_body = json.dumps(payload).encode()
    ts_ms = int(NOW.timestamp() * 1000)

    async with session_factory() as session, session.begin():
        status, body = await handle_card_callback(
            session,
            session_factory,
            kind="im_dingtalk",
            raw_body=raw_body,
            headers={"timestamp": str(ts_ms), "sign": _sign(APP_SECRET, ts_ms)},
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
        )

    assert status == 400
    assert body["error"]["code"] == "invalid_request"
    assert await _approval_status(session_factory, card_approval.id) == "pending"
    assert await _approval_status(session_factory, injected_approval.id) == "pending"


async def test_http_callback_does_not_admit_stream_mode_integration(session_factory):
    world = await _seed(session_factory, receive_mode="stream")
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    payload = _card_payload(approval.id)
    raw_body = json.dumps(payload).encode()
    ts_ms = int(NOW.timestamp() * 1000)

    async with session_factory() as session, session.begin():
        status, body = await handle_card_callback(
            session,
            session_factory,
            kind="im_dingtalk",
            raw_body=raw_body,
            headers={"timestamp": str(ts_ms), "sign": _sign(APP_SECRET, ts_ms)},
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
            app_base_url="https://mesh.example.com",
        )

    assert status == 401
    assert body["error"]["code"] == "invalid_signature"
    assert await _approval_status(session_factory, approval.id) == "pending"


async def test_http_callback_disabled_integration_is_audited_and_cannot_decide(
    session_factory,
):
    world = await _seed(session_factory, receive_mode="http")
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    async with session_factory() as session, session.begin():
        integration = await session.get(Integration, world["integ_dingtalk"])
        integration.status = "disabled"

    payload = _card_payload(approval.id)
    raw_body = json.dumps(payload).encode()
    ts_ms = int(NOW.timestamp() * 1000)
    async with session_factory() as session, session.begin():
        status, body = await handle_card_callback(
            session,
            session_factory,
            kind="im_dingtalk",
            raw_body=raw_body,
            headers={"timestamp": str(ts_ms), "sign": _sign(APP_SECRET, ts_ms)},
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
            app_base_url="https://mesh.example.com",
        )

    assert status == 401
    assert body["error"]["code"] == "integration_disabled"
    assert await _approval_status(session_factory, approval.id) == "pending"
    async with session_factory() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "integration.card_callback_denied")
            )
        ).scalar_one()
    assert audit.metadata_["reason"] == "integration_disabled"


async def test_http_callback_expired_card_uses_deployment_ui_deep_link(
    session_factory,
):
    world = await _seed(session_factory, receive_mode="http")
    approval = await _make_approval(session_factory, world, status="expired")
    await _map_identity(session_factory, world)
    payload = _card_payload(approval.id)
    raw_body = json.dumps(payload).encode()
    ts_ms = int(NOW.timestamp() * 1000)

    async with session_factory() as session, session.begin():
        status, body = await handle_card_callback(
            session,
            session_factory,
            kind="im_dingtalk",
            raw_body=raw_body,
            headers={"timestamp": str(ts_ms), "sign": _sign(APP_SECRET, ts_ms)},
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=timedelta(seconds=300),
            app_base_url="https://mesh.example.com/root/",
        )

    assert status == 200
    assert body["cardData"]["cardParamMap"]["detail_url"] == (
        f"https://mesh.example.com/root/w/intg-{world['ws'].hex[:10]}/approvals?approval_id={approval.id}"
    )


async def test_callback_expired_approval(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world, status="expired")
    await _map_identity(session_factory, world)
    status, body = await _run_callback(session_factory, world, _card_payload(approval.id))
    assert status == 200
    params = body["cardData"]["cardParamMap"]
    assert "过期" in params["status_text"]
    assert params["detail_url"] == (
        f"https://mesh.example.com/w/intg-{world['ws'].hex[:10]}/approvals?approval_id={approval.id}"
    )
    assert await _approval_status(session_factory, approval.id) == "expired"


async def test_callback_malformed_payload_400(session_factory):
    world = await _seed(session_factory)
    status, _ = await _run_callback(session_factory, world, {"corpId": CORP_ID})
    assert status == 400


async def test_callback_without_app_base_url_fails_closed_and_audits(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)

    async with session_factory() as session, session.begin():
        status, body = await handle_dingtalk_card_callback(
            session,
            session_factory,
            integration=world["integration_obj"],
            payload=_card_payload(approval.id),
            now=NOW,
        )

    assert status == 500
    assert body["error"]["code"] == "integration_misconfigured"
    assert await _approval_status(session_factory, approval.id) == "pending"
    async with session_factory() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "integration.card_callback_denied")
            )
        ).scalar_one()
    assert audit.metadata_["reason"] == "app_base_url_missing"


# ---------------------------------------------------------------------------
# IMSendRelay 'card' kind end-to-end (outbox event → createAndDeliver)
# ---------------------------------------------------------------------------


async def test_relay_card_kind_pushes_and_marks_delivery_sent(session_factory, redis_client):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"],
            recipient_id=world["member"],
            type="review_requested",
            priority="critical",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=world["ws"],
            notification_id=notification.id,
            channel="im",
            provider="dingtalk",
            destination_key=f"dingtalk:bind:card:{approval.id}",
            external_target=json.dumps({"card": True}),
            state="pending",
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    from mesh.outbox.service import emit_event

    async with session_factory() as session, session.begin():
        await emit_event(
            session,
            workspace_id=world["ws"],
            event_type=IM_SEND_EVENT_TYPE,
            payload={
                "kind": "card",
                "workspace_id": str(world["ws"]),
                "integration_id": str(world["integ_dingtalk"]),
                "conversation_key": CONVERSATION_KEY,
                "conversation_type": "group",
                "approval_id": str(approval.id),
                "delivery_id": str(delivery_id),
            },
            idempotency_key=f"card:{approval.id}",
        )
    relay = IMSendRelay(
        session_factory,
        redis=redis_client,
        signing_secret=TEST_SIGNING_SECRET,
        api_base="http://dingtalk.fake",
        http_client=make_client(transport),
        card_pusher=functools.partial(push_card_from_event, app_base_url="https://mesh.example.com/root/"),
    )
    assert await relay.run_once() == 1
    (sent,) = transport.calls_for("/v1.0/card/instances/createAndDeliver")
    assert sent.body["outTrackId"] == derive_out_track_id(approval.id, world["integ_dingtalk"])
    assert sent.body["callbackType"] == "STREAM"
    assert sent.body["cardData"]["cardParamMap"]["detail_url"] == (
        f"https://mesh.example.com/root/w/intg-{world['ws'].hex[:10]}/approvals?approval_id={approval.id}"
    )
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "sent"


@pytest.mark.parametrize("app_base_url", ["", "mesh.example.com", "/mesh", "ftp://mesh.example.com"])
async def test_push_card_without_valid_app_base_url_never_calls_dingtalk(
    session_factory, redis_client, app_base_url
):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(redis_client, transport)

    async with session_factory() as session:
        outcome = await push_card_from_event(
            session,
            types.SimpleNamespace(client=client),
            {
                "workspace_id": str(world["ws"]),
                "integration_id": str(world["integ_dingtalk"]),
                "approval_id": str(approval.id),
                "conversation_key": CONVERSATION_KEY,
                "conversation_type": "group",
            },
            app_base_url=app_base_url,
        )

    assert outcome.reason == "invalid_configuration"
    assert transport.calls_for("/v1.0/card/instances/createAndDeliver") == []


async def test_relay_card_kind_http_mode_uses_http_callback_type(session_factory, redis_client):
    world = await _seed(session_factory, receive_mode="http")
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    from mesh.outbox.service import emit_event

    async with session_factory() as session, session.begin():
        await emit_event(
            session,
            workspace_id=world["ws"],
            event_type=IM_SEND_EVENT_TYPE,
            payload={
                "kind": "card",
                "workspace_id": str(world["ws"]),
                "integration_id": str(world["integ_dingtalk"]),
                "conversation_key": CONVERSATION_KEY,
                "conversation_type": "group",
                "approval_id": str(approval.id),
            },
            idempotency_key=f"card-http:{approval.id}",
        )
    relay = IMSendRelay(
        session_factory,
        redis=redis_client,
        signing_secret=TEST_SIGNING_SECRET,
        api_base="http://dingtalk.fake",
        http_client=make_client(transport),
        card_pusher=functools.partial(
            push_card_from_event,
            app_base_url="https://mesh.example.com",
        ),
    )
    await relay.run_once()
    (sent,) = transport.calls_for("/v1.0/card/instances/createAndDeliver")
    assert sent.body["callbackType"] == "HTTP"


# ---------------------------------------------------------------------------
# D — handler-level: callback forwarding internal error → §4.4 failed state
# ---------------------------------------------------------------------------


async def test_callback_forwarding_internal_error_failed_card_approval_unchanged(
    session_factory, monkeypatch
):
    """Handler-level (not renderer-level) coverage of the forwarding-failure
    branch: decide_approval raises a non-Forbidden/NotFound MeshError →
    500 + 「处理失败」card writeback with the [回 Mesh 处理] deep link,
    approval state UNCHANGED."""
    from mesh.errors import BusinessRuleError

    async def _forwarding_boom(*args, **kwargs):
        raise BusinessRuleError("forwarding exploded", code="approval_forward_failed")

    monkeypatch.setattr("mesh.integrations.dingtalk_cards.decide_approval", _forwarding_boom)
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    status, body = await _run_callback(session_factory, world, _card_payload(approval.id))
    assert status == 500
    card_params = body["cardData"]["cardParamMap"]
    assert "处理失败" in card_params["status_text"]
    assert card_params["buttons_disabled"] == "true"
    assert card_params["detail_url"] == (
        f"https://mesh.example.com/w/intg-{world['ws'].hex[:10]}/approvals?approval_id={approval.id}"
    )
    assert card_params["fallback_label"] == "回 Mesh 处理"
    # the failed writeback must not decide the approval
    assert await _approval_status(session_factory, approval.id) == "pending"
