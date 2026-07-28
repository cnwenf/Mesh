"""autopilot.webhook — HMAC verify → dedup → audit → route (§2.5 / §3.2 / §5.3)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.autopilot import webhook as webhook_mod
from mesh.autopilot.webhook import (
    REJECTED_KEY_PREFIX,
    generate_credential_pair,
    hash_token,
    verify_signature,
)
from mesh.db.models.autopilot import AutopilotRun, WebhookEvent
from mesh.db.models.member import Member
from tests.unit.autopilot_support import TEST_SIGNING_SECRET, make_rule
from tests.unit.runtime_support import seed_world

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
TOLERANCE = timedelta(seconds=300)


def sign(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _headers(secret: str, body: bytes, *, ts: int | None = None, event_id: str = "evt_1") -> dict:
    timestamp = ts if ts is not None else int(NOW.timestamp())
    return {
        "x-signature": sign(secret, body, timestamp),
        "x-event-type": "alert.triggered",
        "x-event-id": event_id,
        "content-type": "application/json",
        "authorization": "Bearer must-not-be-stored",
    }


async def _member(session_factory, world) -> Member:
    async with session_factory() as session:
        return await session.scalar(select(Member).where(Member.id == world["member_id"]))


# ---------------------------------------------------------------------------
# Pure signature verification
# ---------------------------------------------------------------------------


def test_verify_signature_valid() -> None:
    body = b'{"alert": 1}'
    header = sign("s3cret", body, int(NOW.timestamp()))
    assert verify_signature("s3cret", body, header, now=NOW, tolerance=TOLERANCE) == "valid"


def test_verify_signature_missing() -> None:
    assert verify_signature("s3cret", b"{}", None, now=NOW, tolerance=TOLERANCE) == "missing"


def test_verify_signature_invalid_and_replay() -> None:
    body = b"{}"
    bad = f"t={int(NOW.timestamp())},v1={'0' * 64}"
    assert verify_signature("s3cret", body, bad, now=NOW, tolerance=TOLERANCE) == "invalid"
    # timestamp outside the tolerance window → replay protection rejects
    old = int((NOW - timedelta(seconds=600)).timestamp())
    stale = sign("s3cret", body, old)
    assert verify_signature("s3cret", body, stale, now=NOW, tolerance=TOLERANCE) == "invalid"
    # malformed header shapes
    assert verify_signature("s", body, "garbage", now=NOW, tolerance=TOLERANCE) == "invalid"
    assert verify_signature("s", body, "t=notanumber,v1=abc", now=NOW, tolerance=TOLERANCE) == "invalid"


def test_token_hash_deterministic() -> None:
    token, _secret = generate_credential_pair()
    assert hash_token(token) == hash_token(token)
    assert token.startswith("whk_")


# ---------------------------------------------------------------------------
# Secret lifecycle
# ---------------------------------------------------------------------------


async def test_create_secret_shows_plaintext_once(session_factory) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    async with session_factory() as session, session.begin():
        created = await webhook_mod.create_secret(
            session,
            workspace_id=world["ws_id"],
            member=member,
            signing_secret=TEST_SIGNING_SECRET,
        )
    assert created["token"].startswith("whk_")
    assert created["secret"].startswith("whs_")
    # stored row: hashed token + ciphertext secret — no plaintext anywhere
    async with session_factory() as session:
        rows = (
            (await session.execute(select(webhook_mod.WebhookSecret))).scalars().all()
        )
    row = rows[0]
    assert row.token_hash == hash_token(created["token"])
    assert created["secret"] not in row.encrypted_secret
    public = webhook_mod.public_secret_row(row)
    assert "token" not in public and "secret" not in public and "encrypted_secret" not in public


async def test_rotate_secret_replaces_pair_in_place(session_factory) -> None:
    from mesh.errors import NotFoundError

    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    async with session_factory() as session, session.begin():
        first = await webhook_mod.create_secret(
            session, workspace_id=world["ws_id"], member=member,
            signing_secret=TEST_SIGNING_SECRET, label="prod",
        )
    async with session_factory() as session, session.begin():
        second = await webhook_mod.rotate_secret(
            session,
            workspace_id=world["ws_id"],
            secret_id=uuid.UUID(first["id"]),
            member=member,
            signing_secret=TEST_SIGNING_SECRET,
        )
    # same row id + label, fresh credential pair
    assert second["id"] == first["id"]
    assert second["label"] == "prod"
    assert second["token"] != first["token"]
    async with session_factory() as session:
        rows = (await session.execute(select(webhook_mod.WebhookSecret))).scalars().all()
        assert len(rows) == 1
        # old token no longer resolves; new token does
        assert rows[0].token_hash == hash_token(second["token"])
        assert await webhook_mod.lookup_secret_by_token(session, first["token"]) is None
        assert await webhook_mod.lookup_secret_by_token(session, second["token"]) is not None
    # rotating a missing secret → 404
    with pytest.raises(NotFoundError):
        async with session_factory() as session, session.begin():
            await webhook_mod.rotate_secret(
                session, workspace_id=world["ws_id"], secret_id=uuid.uuid4(),
                member=member, signing_secret=TEST_SIGNING_SECRET,
            )


# ---------------------------------------------------------------------------
# Inbound pipeline
# ---------------------------------------------------------------------------


async def _inbound(session_factory, world, token, body, headers):
    async with session_factory() as session, session.begin():
        return await webhook_mod.process_inbound(
            session,
            token=token,
            raw_body=body,
            headers=headers,
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
            tolerance=TOLERANCE,
        )


async def test_inbound_unknown_token_401(session_factory) -> None:
    world = await seed_world(session_factory)
    status, body = await _inbound(session_factory, world, "whk_unknown", b"{}", {})
    assert status == 401
    assert body["error"]["code"] == "invalid_signature"


async def test_inbound_invalid_signature_rejected_and_never_dispatched(session_factory) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    async with session_factory() as session, session.begin():
        created = await webhook_mod.create_secret(
            session, workspace_id=world["ws_id"], member=member,
            signing_secret=TEST_SIGNING_SECRET,
        )
    await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        trigger_type="webhook_received",
        trigger_config={"secret_id": created["id"]},
    )
    body = b'{"alert": {"severity": "critical"}}'
    headers = _headers("wrong-secret", body)
    status, response = await _inbound(session_factory, world, created["token"], body, headers)
    assert status == 401
    assert response["error"]["code"] == "invalid_signature"
    # audit row exists, rejected, in the SEPARATE idempotency namespace; no run
    async with session_factory() as session:
        events = (await session.execute(select(WebhookEvent))).scalars().all()
        runs = (await session.execute(select(AutopilotRun))).scalars().all()
    assert len(events) == 1
    assert events[0].process_status == "rejected"
    assert events[0].signature_status == "invalid"
    assert events[0].idempotency_key.startswith(REJECTED_KEY_PREFIX)
    # sensitive headers are NOT persisted
    assert "authorization" not in (events[0].headers or {})
    assert runs == []


async def test_inbound_rejected_cannot_preoccupy_legit_dedup_key(session_factory) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    async with session_factory() as session, session.begin():
        created = await webhook_mod.create_secret(
            session, workspace_id=world["ws_id"], member=member,
            signing_secret=TEST_SIGNING_SECRET,
        )
    await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        trigger_type="webhook_received",
        trigger_config={"secret_id": created["id"]},
    )
    body = b'{"x": 1}'
    # forgery with event id evt_42 + bad signature → rejected namespace
    await _inbound(
        session_factory, world, created["token"], body,
        {**_headers("bad", body, event_id="evt_42")},
    )
    # legit signed event with the SAME event id still dispatches
    status, response = await _inbound(
        session_factory, world, created["token"], body,
        _headers(created["secret"], body, event_id="evt_42"),
    )
    assert status == 200
    assert response["process_status"] == "dispatched"
    assert response["run_id"] is not None


async def test_inbound_valid_dispatches_and_dedupes(session_factory) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    async with session_factory() as session, session.begin():
        created = await webhook_mod.create_secret(
            session, workspace_id=world["ws_id"], member=member,
            signing_secret=TEST_SIGNING_SECRET,
        )
    await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        trigger_type="webhook_received",
        trigger_config={"secret_id": created["id"]},
    )
    body = b'{"alert": {"severity": "critical"}}'
    headers = _headers(created["secret"], body)
    status, first = await _inbound(session_factory, world, created["token"], body, headers)
    assert status == 200
    assert first["received"] is True
    assert first["process_status"] == "dispatched"
    assert first["run_id"]
    # redelivery of the same event id → deduped, no second run
    status2, second = await _inbound(session_factory, world, created["token"], body, headers)
    assert status2 == 200
    assert second["process_status"] == "deduped"
    assert second["run_id"] is None
    async with session_factory() as session:
        runs = (await session.execute(select(AutopilotRun))).scalars().all()
    assert len(runs) == 1
    # snapshot nests the payload under the untrusted "webhook" root
    assert runs[0].trigger_snapshot["webhook"]["payload"]["alert"]["severity"] == "critical"


async def test_inbound_event_type_filter_and_payload_match(session_factory) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    async with session_factory() as session, session.begin():
        created = await webhook_mod.create_secret(
            session, workspace_id=world["ws_id"], member=member,
            signing_secret=TEST_SIGNING_SECRET,
        )
    # rule only wants deploy.* events with severity critical
    await make_rule(
        session_factory,
        world["ws_id"],
        created_by=world["member_id"],
        trigger_type="webhook_received",
        trigger_config={
            "secret_id": created["id"],
            "event_types": ["deploy.finished"],
            "payload_match": [{"path": "severity", "op": "in", "value": ["critical"]}],
        },
    )
    body = b'{"severity": "low"}'
    headers = _headers(created["secret"], body)
    headers["x-event-type"] = "deploy.finished"
    status, response = await _inbound(session_factory, world, created["token"], body, headers)
    assert status == 200
    # payload_match is part of routing: severity=low matches no rule → received
    assert response["process_status"] == "received"
    assert response["run_id"] is None

    body2 = b'{"severity": "critical"}'
    headers2 = _headers(created["secret"], body2, event_id="evt_2")
    headers2["x-event-type"] = "deploy.finished"
    status2, response2 = await _inbound(session_factory, world, created["token"], body2, headers2)
    assert response2["process_status"] == "dispatched"

    # wrong event type → received but unmatched
    body3 = b'{"severity": "critical"}'
    headers3 = _headers(created["secret"], body3, event_id="evt_3")
    headers3["x-event-type"] = "other.event"
    _status3, response3 = await _inbound(session_factory, world, created["token"], body3, headers3)
    assert response3["process_status"] == "received"

    # routing matches but the guardrail gate denies (concurrency_limit=1 and
    # the dispatched run is still in flight) → "matched", no new run
    body4 = b'{"severity": "critical"}'
    headers4 = _headers(created["secret"], body4, event_id="evt_4")
    headers4["x-event-type"] = "deploy.finished"
    _status4, response4 = await _inbound(session_factory, world, created["token"], body4, headers4)
    assert response4["process_status"] == "matched"
    assert response4["run_id"] is None


async def test_inbound_malformed_json_still_audited(session_factory) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    async with session_factory() as session, session.begin():
        created = await webhook_mod.create_secret(
            session, workspace_id=world["ws_id"], member=member,
            signing_secret=TEST_SIGNING_SECRET,
        )
    body = b"\xff\xfe not json"
    headers = _headers(created["secret"], body)
    status, response = await _inbound(session_factory, world, created["token"], body, headers)
    assert status == 200
    assert response["process_status"] == "received"
    async with session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert "raw" in event.payload


async def test_rule_requires_configured_secret(session_factory) -> None:
    """§3.2 red line: webhook trigger rules need a valid secret at creation."""
    from mesh.autopilot.service import AutopilotService
    from mesh.errors import BusinessRuleError

    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    service = AutopilotService(session_factory, TEST_SIGNING_SECRET)
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.create_rule(
            workspace_id=world["ws_id"],
            creator=member,
            payload={
                "name": "webhook-rule-no-secret",
                "trigger_type": "webhook_received",
                "trigger_config": {},
                "action_config": [{"type": "send_notification", "message": "hi"}],
            },
        )
    assert excinfo.value.code == "webhook_secret_required"


async def test_inbound_json_array_body_wrapped(session_factory) -> None:
    world = await seed_world(session_factory)
    member = await _member(session_factory, world)
    async with session_factory() as session, session.begin():
        created = await webhook_mod.create_secret(
            session, workspace_id=world["ws_id"], member=member,
            signing_secret=TEST_SIGNING_SECRET,
        )
    body = json.dumps([1, 2, 3]).encode()
    headers = _headers(created["secret"], body)
    status, _response = await _inbound(session_factory, world, created["token"], body, headers)
    assert status == 200
    async with session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert event.payload == {"value": [1, 2, 3]}
