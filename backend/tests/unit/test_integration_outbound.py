"""Outbound webhook subscription + delivery tests (integrations.md §3.4 / §5.3).

Covers: https-only + SSRF URL validation, HMAC header format, backoff
bounds, the delivery worker (success / retry / terminal failure / circuit
breaker) with an injected httpx transport and resolver (real outbox rows,
real DB ledger — only the external HTTP endpoint is simulated), dispatch
derivation + handler idempotency, manual retry + resume.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from mesh.db.models.integration import WebhookSubscription, WebhookSubscriptionDelivery
from mesh.db.models.outbox import OutboxEvent
from mesh.errors import BusinessRuleError, NotFoundError
from mesh.integrations import outbound as ob
from tests.unit.integrations_support import TEST_SIGNING_SECRET, seed_world

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
PUBLIC_IPS = ["93.184.216.34"]


def public_resolver(host: str, port: int) -> list[str]:
    return list(PUBLIC_IPS)


def private_resolver(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


class RecordingTransport(httpx.MockTransport):
    def __init__(self, status_code: int = 200):
        self.requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(status_code)

        super().__init__(handler)


def make_worker(session_factory, *, transport=None, resolver=None, **overrides):
    return ob.WebhookDeliveryWorker(
        session_factory,
        signing_secret=TEST_SIGNING_SECRET,
        max_attempts=overrides.get("max_attempts", 3),
        base_seconds=overrides.get("base_seconds", 30),
        max_seconds=overrides.get("max_seconds", 3600),
        timeout_seconds=5,
        break_threshold=overrides.get("break_threshold", 2),
        poll_interval=0.01,
        http_client_factory=(
            (lambda: httpx.AsyncClient(transport=transport)) if transport else None
        ),
        resolver=resolver or public_resolver,
        clock=lambda: NOW,
    )


async def make_subscription(session_factory, world, *, event_types=None, status="active"):
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        subscription, secret = await ob.create_subscription(
            session,
            workspace_id=world["ws"],
            creator_member_id=world["member"],
            url="https://hooks.example.com/mesh",
            event_types=event_types or [],
            signing_secret=TEST_SIGNING_SECRET,
            now=NOW,
        )
    return subscription, secret


async def make_delivery(session_factory, world, subscription, *, event_ref="evt-1"):
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        delivery = WebhookSubscriptionDelivery(
            workspace_id=world["ws"], subscription_id=subscription.id,
            event_ref=event_ref, state="pending",
        )
        session.add(delivery)
    return delivery


# ---------------------------------------------------------------------------
# URL validation (https-only + SSRF, §6.16)
# ---------------------------------------------------------------------------


def test_url_must_be_https():
    from mesh.errors import ValidationError

    with pytest.raises(ValidationError) as excinfo:
        ob.validate_subscription_url("http://hooks.example.com/x")
    assert excinfo.value.code == "invalid_url_scheme"


def test_url_private_host_blocked():
    for url in (
        "https://10.0.0.1/x",
        "https://192.168.1.10/x",
        "https://169.254.169.254/latest/meta-data",
        "https://127.0.0.1/x",
        "https://localhost/x",
    ):
        with pytest.raises(BusinessRuleError) as excinfo:
            ob.validate_subscription_url(url)
        assert excinfo.value.code == "ssrf_blocked"


def test_url_public_host_ok():
    ob.validate_subscription_url("https://hooks.example.com/mesh")


def test_resolved_private_address_blocked():
    with pytest.raises(BusinessRuleError) as excinfo:
        ob.assert_public_resolved("https://evil.example.com/x", resolver=private_resolver)
    assert excinfo.value.code == "ssrf_blocked"
    ob.assert_public_resolved("https://good.example.com/x", resolver=public_resolver)


# ---------------------------------------------------------------------------
# HMAC signature + backoff
# ---------------------------------------------------------------------------


def test_signature_header_format_recomputable():
    parts = ob.signature_headers("whsec_k", b"body", 1753790400)
    header = ob.format_signature_header(parts)
    assert header.startswith("t=1753790400,v1=")
    expected = hmac.new(b"whsec_k", b"1753790400.body", hashlib.sha256).hexdigest()
    assert header == f"t=1753790400,v1={expected}"


def test_backoff_bounds():
    delays = [
        ob.compute_next_retry(attempts, base_seconds=30, max_seconds=3600).total_seconds()
        for attempts in range(10)
    ]
    # Bounded by max * jitter(≤1.0); strictly increasing before the cap.
    assert all(d <= 3600 for d in delays)
    assert delays[0] <= 60  # base*2^1*1.0
    assert delays[1] >= 60 * 0.5  # base*2^2*0.5 lower bound


# ---------------------------------------------------------------------------
# Delivery worker
# ---------------------------------------------------------------------------


async def test_delivery_success_marks_sent_and_signs(session_factory):
    world = await seed_world(session_factory)
    subscription, secret = await make_subscription(session_factory, world)
    delivery = await make_delivery(session_factory, world, subscription)
    transport = RecordingTransport(200)
    worker = make_worker(session_factory, transport=transport)
    processed = await worker.run_once()
    assert processed == 1
    assert len(transport.requests) == 1
    request = transport.requests[0]
    # Receiver recomputes the HMAC with the shown-once secret (§5.3).
    header = request.headers["Mesh-Signature"]
    ts = header.split(",")[0][2:]
    presented = header.split("v1=")[1]
    body = request.content
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert presented == expected
    assert request.headers["Mesh-Delivery"] == str(delivery.id)
    assert request.headers["Mesh-Event"] == "evt-1"
    async with session_factory() as session:
        row = await session.get(WebhookSubscriptionDelivery, delivery.id)
        assert row.state == "sent"
        assert row.attempts == 1
        assert row.response_status == 200
        sub = await session.get(WebhookSubscription, subscription.id)
        assert sub.fail_count == 0


async def test_delivery_failure_retries_with_backoff_then_fails(session_factory):
    world = await seed_world(session_factory)
    subscription, _ = await make_subscription(session_factory, world)
    delivery = await make_delivery(session_factory, world, subscription)
    transport = RecordingTransport(500)
    worker = make_worker(session_factory, transport=transport, max_attempts=2)
    # Attempt 1 → scheduled retry.
    await worker.run_once()
    async with session_factory() as session:
        row = await session.get(WebhookSubscriptionDelivery, delivery.id)
        assert row.state == "pending"
        assert row.attempts == 1
        assert row.next_retry_at is not None
        assert row.response_status == 500
    # next_retry_at is in the future → not claimed again yet.
    assert await worker.run_once() == 0
    # Force the retry window open → attempt 2 → terminal failed.
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        row = await session.get(WebhookSubscriptionDelivery, delivery.id)
        row.next_retry_at = NOW - timedelta(seconds=1)
    await worker.run_once()
    async with session_factory() as session:
        row = await session.get(WebhookSubscriptionDelivery, delivery.id)
        assert row.state == "failed"
        assert row.attempts == 2
        sub = await session.get(WebhookSubscription, subscription.id)
        assert sub.fail_count == 1


async def test_circuit_breaker_trips_and_resume_clears(session_factory):
    world = await seed_world(session_factory)
    subscription, _ = await make_subscription(
        session_factory, world, event_types=[]
    )
    transport = RecordingTransport(500)
    worker = make_worker(
        session_factory, transport=transport, max_attempts=1, break_threshold=2
    )
    # Two distinct events → two failed deliveries → fail_count=2 → breaker.
    for i in range(2):
        await make_delivery(session_factory, world, subscription, event_ref=f"evt-{i}")
        await worker.run_once()
    async with session_factory() as session:
        sub = await session.get(WebhookSubscription, subscription.id)
        assert sub.status == "disabled"
        assert sub.fail_count == 2
    # Pending delivery under a breaker: left pending, not posted.
    await make_delivery(session_factory, world, subscription, event_ref="evt-late")
    before = len(transport.requests)
    await worker.run_once()
    assert len(transport.requests) == before, "breaker-open subscription must not post"
    # Resume clears fail_count + reactivates.
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        sub = await session.get(WebhookSubscription, subscription.id)
        await ob.resume_subscription(session, subscription=sub, now=NOW)
    async with session_factory() as session:
        sub = await session.get(WebhookSubscription, subscription.id)
        assert sub.status == "active" and sub.fail_count == 0


async def test_ssrf_at_delivery_fails_delivery(session_factory):
    world = await seed_world(session_factory)
    subscription, _ = await make_subscription(session_factory, world)
    delivery = await make_delivery(session_factory, world, subscription)
    transport = RecordingTransport(200)
    worker = make_worker(
        session_factory, transport=transport, resolver=private_resolver, max_attempts=1
    )
    await worker.run_once()
    assert transport.requests == [], "private-resolved target must never be POSTed"
    async with session_factory() as session:
        row = await session.get(WebhookSubscriptionDelivery, delivery.id)
        assert row.state == "failed"
        assert row.last_error == "ssrf_blocked"


# ---------------------------------------------------------------------------
# Dispatch derivation + handler idempotency
# ---------------------------------------------------------------------------


async def test_derivation_and_dispatch_creates_one_delivery(session_factory):
    world = await seed_world(session_factory)
    await make_subscription(
        session_factory, world, event_types=["issue.updated"]
    )
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        outbox_event = OutboxEvent(
            workspace_id=world["ws"],
            event_type="realtime.publish",
            payload={
                "channel": f"workspace:{world['ws']}:issues",
                "event": "issue.updated",
                "data": {"id": "x"},
            },
        )
        session.add(outbox_event)
        await session.flush()
        await ob.derive_dispatch_from_realtime(session, outbox_event)
    async with session_factory() as session:
        dispatch = (await session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == ob.WEBHOOK_DISPATCH_EVENT_TYPE
            )
        )).scalars().first()
        assert dispatch is not None
    async with session_factory() as session, session.begin():
        await ob.webhook_dispatch_handler(session, dispatch)
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        deliveries = (await session.execute(
            select(WebhookSubscriptionDelivery)
        )).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].state == "pending"
    # Redelivery (at-least-once) → UNIQUE(subscription_id, event_ref) no-op.
    async with session_factory() as session, session.begin():
        await ob.webhook_dispatch_handler(session, dispatch)
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        deliveries = (await session.execute(
            select(WebhookSubscriptionDelivery)
        )).scalars().all()
        assert len(deliveries) == 1


async def test_derivation_skips_non_matching_event_types(session_factory):
    world = await seed_world(session_factory)
    await make_subscription(session_factory, world, event_types=["issue.created"])
    async with session_factory() as session, session.begin():
        outbox_event = OutboxEvent(
            workspace_id=world["ws"],
            event_type="realtime.publish",
            payload={"channel": "c", "event": "comment.created", "data": {}},
        )
        session.add(outbox_event)
        await session.flush()
        await ob.derive_dispatch_from_realtime(session, outbox_event)
        dispatches = (await session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == ob.WEBHOOK_DISPATCH_EVENT_TYPE
            )
        )).scalars().all()
        assert dispatches == []


# ---------------------------------------------------------------------------
# Manual retry + secret rotation
# ---------------------------------------------------------------------------


async def test_retry_delivery_requeues_failed(session_factory):
    world = await seed_world(session_factory)
    subscription, _ = await make_subscription(session_factory, world)
    delivery = await make_delivery(session_factory, world, subscription)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        row = await session.get(WebhookSubscriptionDelivery, delivery.id)
        row.state = "failed"
        result = await ob.retry_delivery(
            session, workspace_id=world["ws"], subscription=subscription,
            delivery_id=delivery.id,
        )
        assert result.state == "pending"


async def test_retry_delivery_breaker_open_rejected(session_factory):
    world = await seed_world(session_factory)
    subscription, _ = await make_subscription(session_factory, world)
    delivery = await make_delivery(session_factory, world, subscription)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        sub = await session.get(WebhookSubscription, subscription.id)
        sub.status = "disabled"
        row = await session.get(WebhookSubscriptionDelivery, delivery.id)
        row.state = "failed"
        with pytest.raises(BusinessRuleError) as excinfo:
            await ob.retry_delivery(
                session, workspace_id=world["ws"], subscription=sub,
                delivery_id=delivery.id,
            )
        assert excinfo.value.code == "subscription_circuit_open"


async def test_rotate_subscription_secret(session_factory):
    world = await seed_world(session_factory)
    subscription, old_secret = await make_subscription(session_factory, world)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, world["ws"])
        sub = await session.get(WebhookSubscription, subscription.id)
        new_secret = await ob.rotate_subscription_secret(
            session, subscription=sub, signing_secret=TEST_SIGNING_SECRET, now=NOW
        )
        assert new_secret != old_secret
    # render never includes the secret.
    rendered = ob.render_subscription(sub)
    assert "secret" not in rendered
    assert rendered["has_secret"] is True


async def test_get_subscription_foreign_workspace_404(session_factory):
    world = await seed_world(session_factory)
    subscription, _ = await make_subscription(session_factory, world)
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await ob.get_subscription(
                session, workspace_id=uuid.uuid4(), subscription_id=subscription.id
            )
