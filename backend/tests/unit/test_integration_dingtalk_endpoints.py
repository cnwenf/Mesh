"""test-send / stream-status endpoint tests (integrations.md §3.9 / §3.5).

Diagnostics split: test-send goes through the OpenAPI adapter and never
reports the receive-channel state; stream-status is the ONLY place 503
``stream_channel_unavailable`` appears.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.db.models.integration import Integration
from mesh.db.models.outbox import OutboxEvent
from mesh.errors import BusinessRuleError, ServiceUnavailableError, UpstreamError
from mesh.integrations.service import IntegrationService
from tests.unit.integrations_dingtalk_support import (
    ScriptedDingTalkTransport,
    make_client,
)
from tests.unit.integrations_support import TEST_SIGNING_SECRET
from tests.unit.test_dingtalk_cards import _seed


def _service(session_factory) -> IntegrationService:
    return IntegrationService(session_factory, TEST_SIGNING_SECRET)


async def test_test_send_success(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport()
    result = await _service(session_factory).test_send(
        workspace_id=world["ws"],
        integration_id=world["integ_dingtalk"],
        conversation_ref="cid6EUvB2O8qVF2RYQtHTKEsg==",
        redis=redis_client,
        api_base="http://dingtalk.fake",
        http_client=make_client(transport),
    )
    assert result["status"] == "sent"
    (sent,) = transport.group_sends()
    assert sent.body["openConversationId"] == "cid6EUvB2O8qVF2RYQtHTKEsg=="


async def test_test_send_succeeds_while_stream_channel_down(session_factory, redis_client):
    """Diagnostics split (§3.9): a down receive channel must NOT fail the
    outbound test — and the response never mentions it."""
    world = await _seed(session_factory)
    from mesh.db.models.integration import Integration

    async with session_factory() as session, session.begin():
        integration = await session.get(Integration, world["integ_dingtalk"])
        integration.stream_state = {"state": "down"}
    transport = ScriptedDingTalkTransport()
    result = await _service(session_factory).test_send(
        workspace_id=world["ws"],
        integration_id=world["integ_dingtalk"],
        conversation_ref="cidX",
        redis=redis_client,
        api_base="http://dingtalk.fake",
        http_client=make_client(transport),
    )
    assert result["status"] == "sent"
    assert "stream_channel_unavailable" not in str(result)


async def test_test_send_upstream_failure_is_502_not_503(session_factory, redis_client):
    world = await _seed(session_factory)
    transport = ScriptedDingTalkTransport(send_status=500, send_body={"code": "boom"})
    with pytest.raises(UpstreamError) as excinfo:
        await _service(session_factory).test_send(
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            conversation_ref="cidX",
            redis=redis_client,
            api_base="http://dingtalk.fake",
            http_client=make_client(transport),
        )
    assert excinfo.value.status_code == 502
    assert excinfo.value.code == "upstream_error"
    assert excinfo.value.details["reason"] == "upstream_error"


async def test_test_send_disabled_integration_is_502(session_factory, redis_client):
    world = await _seed(session_factory)
    from mesh.db.models.integration import Integration

    async with session_factory() as session, session.begin():
        integration = await session.get(Integration, world["integ_dingtalk"])
        integration.status = "disabled"
    with pytest.raises(UpstreamError) as excinfo:
        await _service(session_factory).test_send(
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            conversation_ref="cidX",
            redis=redis_client,
            http_client=make_client(ScriptedDingTalkTransport()),
        )
    assert excinfo.value.details["reason"] == "integration_unavailable"


async def test_test_send_non_dingtalk_integration_rejected(session_factory, redis_client):
    world = await _seed(session_factory)
    with pytest.raises(BusinessRuleError):
        await _service(session_factory).test_send(
            workspace_id=world["ws"],
            integration_id=world["integ_feishu"],
            conversation_ref="cidX",
            redis=redis_client,
            http_client=make_client(ScriptedDingTalkTransport()),
        )


async def test_test_send_direct_external_contact_fails_502(session_factory, redis_client):
    world = await _seed(session_factory)
    from mesh.integrations.im_outbound import encode_external_contact_key

    transport = ScriptedDingTalkTransport()
    with pytest.raises(UpstreamError) as excinfo:
        await _service(session_factory).test_send(
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            conversation_ref="directconv",
            conversation_type="direct",
            user_key=encode_external_contact_key("$:LWCP_v1:$xyz"),
            redis=redis_client,
            api_base="http://dingtalk.fake",
            http_client=make_client(transport),
        )
    assert excinfo.value.details["reason"] == "no_staff_id"
    assert transport.direct_sends() == []


# ---------------------------------------------------------------------------
# stream-status
# ---------------------------------------------------------------------------


async def _set_stream_state(session_factory, world, state: dict | None = None, status: str | None = None):
    from mesh.db.models.integration import Integration

    async with session_factory() as session, session.begin():
        integration = await session.get(Integration, world["integ_dingtalk"])
        if state is not None:
            integration.stream_state = state
        if status is not None:
            integration.status = status


async def test_stream_status_connected(session_factory):
    world = await _seed(session_factory)
    await _set_stream_state(
        session_factory,
        world,
        state={"state": "connected", "last_frame_at": "2026-07-30T11:00:00+00:00"},
    )
    body = await _service(session_factory).get_stream_status(
        workspace_id=world["ws"], integration_id=world["integ_dingtalk"]
    )
    assert body["state"] == "connected"
    assert body["last_frame_at"] == "2026-07-30T11:00:00+00:00"


async def test_stream_status_down_is_503_stream_channel_unavailable(session_factory):
    world = await _seed(session_factory)
    await _set_stream_state(session_factory, world, state={"state": "down", "backoff_seconds": 64})
    with pytest.raises(ServiceUnavailableError) as excinfo:
        await _service(session_factory).get_stream_status(
            workspace_id=world["ws"], integration_id=world["integ_dingtalk"]
        )
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "stream_channel_unavailable"
    assert excinfo.value.details["state"] == "down"
    assert excinfo.value.details["backoff_seconds"] == 64


async def test_stream_status_no_state_defaults_down_503(session_factory):
    world = await _seed(session_factory)
    with pytest.raises(ServiceUnavailableError):
        await _service(session_factory).get_stream_status(
            workspace_id=world["ws"], integration_id=world["integ_dingtalk"]
        )


async def test_stream_status_disabled_integration(session_factory):
    world = await _seed(session_factory)
    await _set_stream_state(session_factory, world, status="disabled")
    body = await _service(session_factory).get_stream_status(
        workspace_id=world["ws"], integration_id=world["integ_dingtalk"]
    )
    assert body["state"] == "disabled"


async def test_stream_status_non_dingtalk_rejected(session_factory):
    world = await _seed(session_factory)
    with pytest.raises(BusinessRuleError):
        await _service(session_factory).get_stream_status(
            workspace_id=world["ws"], integration_id=world["integ_slack"]
        )


async def test_stream_status_missing_integration_404(session_factory):
    from mesh.errors import NotFoundError

    world = await _seed(session_factory)
    with pytest.raises(NotFoundError):
        await _service(session_factory).get_stream_status(
            workspace_id=world["ws"], integration_id=uuid.uuid4()
        )


# ---------------------------------------------------------------------------
# explicit Stream reconnect
# ---------------------------------------------------------------------------


async def test_request_stream_reconnect_persists_cross_worker_signal_and_broadcasts(
    session_factory,
):
    """The API writes a durable marker; it must not rely on process-local state."""
    world = await _seed(session_factory)
    requested_at = datetime(2026, 7, 31, 9, 30, tzinfo=UTC)

    result = await _service(session_factory).request_stream_reconnect(
        workspace_id=world["ws"],
        integration_id=world["integ_dingtalk"],
        now=requested_at,
    )

    assert result == {"accepted": True}
    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_dingtalk"])
        realtime = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")))
            .scalars()
            .all()
        )

    state = integration.stream_state
    assert state["state"] == "reconnecting"
    assert state["backoff_seconds"] == 0
    assert state["last_attempt_at"] == requested_at.isoformat()
    assert uuid.UUID(state["reconnect_request_id"])
    assert any(
        event.payload["data"]["subject"] == "stream_channel"
        and event.payload["data"]["stream_state"] == "reconnecting"
        for event in realtime
    )


async def test_repeated_stream_reconnect_gets_a_new_durable_marker(session_factory):
    world = await _seed(session_factory)
    service = _service(session_factory)

    await service.request_stream_reconnect(workspace_id=world["ws"], integration_id=world["integ_dingtalk"])
    async with session_factory() as session:
        first = dict((await session.get(Integration, world["integ_dingtalk"])).stream_state)

    await service.request_stream_reconnect(workspace_id=world["ws"], integration_id=world["integ_dingtalk"])
    async with session_factory() as session:
        second = dict((await session.get(Integration, world["integ_dingtalk"])).stream_state)

    assert second["reconnect_request_id"] != first["reconnect_request_id"]


@pytest.mark.parametrize("receive_mode", ["http", ""])
async def test_request_stream_reconnect_rejects_non_stream_mode(session_factory, receive_mode):
    world = await _seed(session_factory, receive_mode=receive_mode)

    with pytest.raises(BusinessRuleError) as excinfo:
        await _service(session_factory).request_stream_reconnect(
            workspace_id=world["ws"], integration_id=world["integ_dingtalk"]
        )

    assert excinfo.value.code == "invalid_request"


async def test_request_stream_reconnect_rejects_disabled_integration(session_factory):
    world = await _seed(session_factory)
    await _set_stream_state(session_factory, world, status="disabled")

    with pytest.raises(BusinessRuleError) as excinfo:
        await _service(session_factory).request_stream_reconnect(
            workspace_id=world["ws"], integration_id=world["integ_dingtalk"]
        )

    assert excinfo.value.code == "invalid_request"


async def test_request_stream_reconnect_rejects_other_kind_and_missing(session_factory):
    world = await _seed(session_factory)
    service = _service(session_factory)

    with pytest.raises(BusinessRuleError):
        await service.request_stream_reconnect(workspace_id=world["ws"], integration_id=world["integ_slack"])
    from mesh.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await service.request_stream_reconnect(workspace_id=world["ws"], integration_id=uuid.uuid4())
