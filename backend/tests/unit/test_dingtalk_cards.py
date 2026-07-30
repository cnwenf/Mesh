"""DingTalk interactive card tests (integrations.md §3.10 / §4.4).

Callback auth chain runs against the REAL external_identities → members →
approvals stack; card push runs against the scripted DingTalk transport.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from mesh.db.models.integration import ExternalIdentity, Integration
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification, NotificationDelivery
from mesh.db.models.runtime import Approval, TaskExecution
from mesh.db.models.user import User
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


def _card_payload(approval_id, *, decision: str = "approve", user_id: str = STAFF_ID) -> dict:
    return {
        "outTrackId": derive_out_track_id(approval_id),
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
    approved = lifecycle_response(
        "approved", approver_name="张三", decided_at="2026-07-30T12:00:00+00:00"
    )
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
    assert (
        verify_callback_signature(app_secret=APP_SECRET, timestamp="123", sign=None, now=NOW)
        == "missing"
    )


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
    clicker = extract_dingtalk_clicker(
        {"userId": "", "senderId": sender_id, "corpId": CORP_ID}, world_obj
    )
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


async def _adapter_client(transport: ScriptedDingTalkTransport):
    import redis.asyncio as aioredis

    from mesh.integrations.dingtalk_api import DingTalkClient, DingTalkTokenManager

    redis_client = aioredis.from_url("redis://127.0.0.1:6390/3", decode_responses=True)
    manager = DingTalkTokenManager(
        redis_client,
        http_client=make_client(transport),
        integration_id=uuid.uuid4(),
        app_key="k",
        app_secret="s",
        jitter=lambda: 0,
    )
    return DingTalkClient(manager, http_client=make_client(transport), robot_code="robot-1")


async def test_push_approval_card_wire_format(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(transport)
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
    assert body["outTrackId"] == derive_out_track_id(approval.id)
    assert body["openSpaceId"] == "dtv1.card//IM_GROUP.cid6EUvB2O8qVF2RYQtHTKEsg=="
    assert body["callbackType"] == "STREAM"
    assert body["cardData"]["cardParamMap"]["title"].startswith("Mesh 审批请求")


async def test_push_approval_card_direct_space(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(transport)
    outcome = await push_approval_card(
        client,
        approval=approval,
        target=_target(world, conversation_type=CONVERSATION_DIRECT, sender_key=STAFF_ID),
    )
    assert outcome.sent
    (sent,) = transport.calls_for("/v1.0/card/instances/createAndDeliver")
    assert sent.body["openSpaceId"] == f"dtv1.card//IM_ROBOT.{STAFF_ID}"


async def test_push_approval_card_rejects_action_card_template(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(transport)
    with pytest.raises(AssertionError):
        await push_approval_card(
            client, approval=approval, target=_target(world),
            card_template_id="sampleActionCard6",
        )
    assert transport.calls_for("/v1.0/card/instances/createAndDeliver") == []


async def test_push_approval_card_direct_external_contact_fails(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(transport)
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


async def test_update_card_is_idempotent_by_out_track_id(session_factory):
    transport = ScriptedDingTalkTransport()
    client = await _adapter_client(transport)
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
            detail_url_base="https://mesh.example.com",
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
    status, body = await _run_callback(
        session_factory, world, _card_payload(approval.id, decision="reject")
    )
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
            id=uuid.uuid4(), email="outsider@mesh.test",
            display_name="Outsider", password_hash="x",
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
            id=uuid.uuid4(), email="plain@mesh.test",
            display_name="Plain", password_hash="x",
        )
        session.add(plain_user)
        await session.flush()
        session.add(Member(
            id=uuid.uuid4(), workspace_id=world["ws"], member_type="human",
            user_id=plain_user.id, role="member", status="active",
        ))
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


async def test_callback_expired_approval(session_factory):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world, status="expired")
    await _map_identity(session_factory, world)
    status, body = await _run_callback(session_factory, world, _card_payload(approval.id))
    assert status == 200
    params = body["cardData"]["cardParamMap"]
    assert "过期" in params["status_text"]
    assert params["detail_url"].endswith(f"/approvals/{approval.id}")
    assert await _approval_status(session_factory, approval.id) == "expired"


async def test_callback_malformed_payload_400(session_factory):
    world = await _seed(session_factory)
    status, _ = await _run_callback(session_factory, world, {"corpId": CORP_ID})
    assert status == 400


# ---------------------------------------------------------------------------
# IMSendRelay 'card' kind end-to-end (outbox event → createAndDeliver)
# ---------------------------------------------------------------------------


async def test_relay_card_kind_pushes_and_marks_delivery_sent(session_factory, redis_client):
    world = await _seed(session_factory)
    approval = await _make_approval(session_factory, world)
    transport = ScriptedDingTalkTransport()
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=world["ws"], recipient_id=world["member"],
            type="review_requested", priority="critical",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=world["ws"], notification_id=notification.id,
            channel="im", provider="dingtalk",
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
                "detail_url_base": "https://mesh.example.com",
            },
            idempotency_key=f"card:{approval.id}",
        )
    relay = IMSendRelay(
        session_factory,
        redis=redis_client,
        signing_secret=TEST_SIGNING_SECRET,
        api_base="http://dingtalk.fake",
        http_client=make_client(transport),
        card_pusher=push_card_from_event,
    )
    assert await relay.run_once() == 1
    (sent,) = transport.calls_for("/v1.0/card/instances/createAndDeliver")
    assert sent.body["outTrackId"] == derive_out_track_id(approval.id)
    assert sent.body["callbackType"] == "STREAM"
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "sent"


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
        card_pusher=push_card_from_event,
    )
    await relay.run_once()
    (sent,) = transport.calls_for("/v1.0/card/instances/createAndDeliver")
    assert sent.body["callbackType"] == "HTTP"
