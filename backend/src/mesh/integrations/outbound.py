"""Outbound developer webhooks (integrations.md §2.5/§2.6 / §3.4 / §5.3).

Subscriptions filter domain events and receive HTTPS POSTs signed with
``Mesh-Signature: t=<ts>,v1=HMAC_SHA256(secret, "<ts>.<body>")`` plus
``Mesh-Event`` / ``Mesh-Delivery`` headers. Delivery is outbox-driven
(README §6.6 — the business transaction NEVER posts external URLs):

    domain event → outbox realtime.publish (existing producers)
      → relay derives ``webhook.dispatch`` (one per event, deduped)
      → ``webhook.dispatch`` handler inserts per-subscription deliveries
        (``UNIQUE(subscription_id, event_ref)`` = §6.5 idempotency)
      → WebhookDeliveryWorker posts with exponential backoff + jitter;
        consecutive failures trip the subscription-level circuit breaker
        (``disabled`` + alert; manual ``resume`` clears ``fail_count``).

SSRF (README §6.16): https-only at creation (400 ``invalid_url_scheme``);
resolved addresses re-checked at delivery time (private / loopback /
link-local / metadata → delivery failed ``ssrf_blocked``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.integration import WebhookSubscription, WebhookSubscriptionDelivery
from mesh.db.models.outbox import OutboxEvent
from mesh.errors import BusinessRuleError, NotFoundError
from mesh.outbox.service import emit_event, emit_realtime
from mesh.runtime.checkout import is_forbidden_host
from mesh.runtime.credentials import decrypt_credential_value, encrypt_credential_value

logger = logging.getLogger("mesh.integrations.outbound")

WEBHOOK_DISPATCH_EVENT_TYPE = "webhook.dispatch"

# §2.6 workspace-level delivery constants (defaults; config-overridable).
DEFAULT_RETRY_MAX_ATTEMPTS = 8
DEFAULT_RETRY_BASE_SECONDS = 30
DEFAULT_RETRY_MAX_SECONDS = 3600
DEFAULT_DELIVERY_TIMEOUT_SECONDS = 10
DEFAULT_CIRCUIT_BREAK_THRESHOLD = 20
JITTER_MIN = 0.5
JITTER_MAX = 1.0


# ---------------------------------------------------------------------------
# URL validation (https-only + SSRF, README §6.16)
# ---------------------------------------------------------------------------


def validate_subscription_url(url: str) -> None:
    """Creation-time guard: https scheme + non-private hostname."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https":
        raise BusinessRuleError(
            "webhook url must use https",
            code="invalid_url_scheme",
            details={"scheme": parsed.scheme},
        )
    host = parsed.hostname or ""
    if not host or is_forbidden_host(host):
        raise BusinessRuleError(
            "webhook url target is forbidden",
            code="ssrf_blocked",
            details={"host": host},
        )


def assert_public_resolved(url: str, resolver=None) -> None:
    """Delivery-time guard: every resolved address must be public.

    DNS rebinding / hostname indirection cannot smuggle a private target
    past the creation-time check. ``resolver`` is injectable for tests.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise BusinessRuleError("webhook url must use https", code="ssrf_blocked")
    host = parsed.hostname or ""
    if is_forbidden_host(host):
        raise BusinessRuleError("webhook url target is forbidden", code="ssrf_blocked")
    resolve = resolver or _resolve_host
    try:
        addresses = resolve(host, parsed.port or 443)
    except OSError as exc:
        raise BusinessRuleError(
            "webhook url does not resolve", code="ssrf_blocked",
            details={"host": host},
        ) from exc
    for address in addresses:
        if is_forbidden_host(address):
            raise BusinessRuleError(
                "webhook url resolves to a forbidden address",
                code="ssrf_blocked",
                details={"host": host},
            )


def _resolve_host(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------


async def create_subscription(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    creator_member_id: uuid.UUID,
    url: str,
    event_types: list[str] | None = None,
    integration_id: uuid.UUID | None = None,
    signing_secret: str,
    now: datetime | None = None,
) -> tuple[WebhookSubscription, str]:
    """Create a subscription; returns (row, plaintext_secret shown ONCE)."""
    validate_subscription_url(url)
    secret_plaintext = f"whsec_{uuid.uuid4().hex}{uuid.uuid4().hex[:16]}"
    moment = now or datetime.now(UTC)
    subscription = WebhookSubscription(
        workspace_id=workspace_id,
        integration_id=integration_id,
        url=url,
        secret_ref=encrypt_credential_value(secret_plaintext, signing_secret),
        event_types=[str(e) for e in (event_types or [])],
        status="active",
        fail_count=0,
        created_by=creator_member_id,
        created_at=moment,
        updated_at=moment,
    )
    session.add(subscription)
    await session.flush()
    await _emit_subscription_updated(session, subscription, now=moment)
    return subscription, secret_plaintext


async def get_subscription(
    session: AsyncSession, *, workspace_id: uuid.UUID, subscription_id: uuid.UUID
) -> WebhookSubscription:
    subscription = await session.scalar(
        select(WebhookSubscription).where(
            WebhookSubscription.id == subscription_id,
            WebhookSubscription.workspace_id == workspace_id,
        )
    )
    if subscription is None:
        raise NotFoundError("webhook subscription not found")
    return subscription


async def update_subscription(
    session: AsyncSession,
    *,
    subscription: WebhookSubscription,
    url: str | None = None,
    event_types: list[str] | None = None,
    status: str | None = None,
    now: datetime,
) -> WebhookSubscription:
    if url is not None:
        validate_subscription_url(url)
        subscription.url = url
    if event_types is not None:
        subscription.event_types = [str(e) for e in event_types]
    if status is not None:
        if status not in ("active", "paused", "disabled"):
            raise BusinessRuleError("invalid status", code="invalid_request")
        subscription.status = status
        if status == "active":
            subscription.fail_count = 0
    subscription.updated_at = now
    await session.flush()
    await _emit_subscription_updated(session, subscription, now=now)
    return subscription


async def resume_subscription(
    session: AsyncSession, *, subscription: WebhookSubscription, now: datetime
) -> WebhookSubscription:
    """Clear the circuit breaker / pause (fail_count reset, §3.1 resume)."""
    subscription.status = "active"
    subscription.fail_count = 0
    subscription.updated_at = now
    await session.flush()
    await _emit_subscription_updated(session, subscription, now=now)
    return subscription


async def rotate_subscription_secret(
    session: AsyncSession,
    *,
    subscription: WebhookSubscription,
    signing_secret: str,
    now: datetime,
) -> str:
    """Rotate the HMAC secret; returns the new plaintext shown ONCE."""
    secret_plaintext = f"whsec_{uuid.uuid4().hex}{uuid.uuid4().hex[:16]}"
    subscription.secret_ref = encrypt_credential_value(secret_plaintext, signing_secret)
    subscription.updated_at = now
    await session.flush()
    return secret_plaintext


async def delete_subscription(
    session: AsyncSession, *, subscription: WebhookSubscription, now: datetime
) -> None:
    workspace_id, subscription_id = subscription.workspace_id, subscription.id
    await session.delete(subscription)  # deliveries cascade
    await session.flush()
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"workspace:{workspace_id}:integrations",
        event="integration.updated",
        data={"subject": "subscription", "subscription_id": str(subscription_id), "status": "deleted"},
        idempotency_key=f"subscription:{subscription_id}:deleted:{int(now.timestamp() * 1000)}",
    )


async def _emit_subscription_updated(
    session: AsyncSession, subscription: WebhookSubscription, *, now: datetime
) -> None:
    await emit_realtime(
        session,
        workspace_id=subscription.workspace_id,
        channel=f"workspace:{subscription.workspace_id}:integrations",
        event="integration.updated",
        data={
            "subject": "subscription",
            "subscription_id": str(subscription.id),
            "status": subscription.status,
            "fail_count": subscription.fail_count,
        },
        idempotency_key=(
            f"subscription:{subscription.id}:updated:"
            f"{subscription.status}:{subscription.fail_count}:{int(now.timestamp() * 1000)}"
        ),
    )


def render_subscription(subscription: WebhookSubscription) -> dict[str, Any]:
    """List/detail rendering — the secret is NEVER echoed (§6.16)."""
    return {
        "id": str(subscription.id),
        "integration_id": str(subscription.integration_id) if subscription.integration_id else None,
        "url": subscription.url,
        "event_types": list(subscription.event_types or []),
        "status": subscription.status,
        "fail_count": subscription.fail_count,
        "has_secret": bool(subscription.secret_ref),
        "created_by": str(subscription.created_by),
        "created_at": subscription.created_at.isoformat() if subscription.created_at else None,
        "updated_at": subscription.updated_at.isoformat() if subscription.updated_at else None,
    }


def render_delivery(delivery: WebhookSubscriptionDelivery) -> dict[str, Any]:
    return {
        "id": str(delivery.id),
        "subscription_id": str(delivery.subscription_id),
        "event_ref": delivery.event_ref,
        "state": delivery.state,
        "attempts": delivery.attempts,
        "next_retry_at": delivery.next_retry_at.isoformat() if delivery.next_retry_at else None,
        "response_status": delivery.response_status,
        "last_error": delivery.last_error,
        "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
    }


# ---------------------------------------------------------------------------
# Outbox derivation + dispatch handler
# ---------------------------------------------------------------------------


def _event_matches(subscription: WebhookSubscription, event_type: str) -> bool:
    allowed = list(subscription.event_types or [])
    return not allowed or event_type in allowed


async def derive_dispatch_from_realtime(session: AsyncSession, event: OutboxEvent) -> None:
    """Relay-side derivation: realtime.publish → webhook.dispatch (deduped).

    Runs in the relay transaction; at-least-once redelivery is absorbed by
    the outbox idempotency key. No dispatch row when nothing subscribes.
    """
    payload = event.payload or {}
    event_type = str(payload.get("event") or "")
    if not event_type:
        return
    matching = await session.scalar(
        select(func.count())
        .select_from(WebhookSubscription)
        .where(
            WebhookSubscription.workspace_id == event.workspace_id,
            WebhookSubscription.status == "active",
            or_(
                func.array_length(WebhookSubscription.event_types, 1).is_(None),
                WebhookSubscription.event_types == [],
                WebhookSubscription.event_types.any(event_type),
            ),
        )
    )
    if not matching:
        return
    await emit_event(
        session,
        workspace_id=event.workspace_id,
        event_type=WEBHOOK_DISPATCH_EVENT_TYPE,
        payload={
            "event_type": event_type,
            "data": payload.get("data") or {},
            "source_event_ref": str(event.id),
        },
        idempotency_key=f"webhook-dispatch:{event.id}",
    )


async def webhook_dispatch_handler(
    session: AsyncSession, event: OutboxEvent
) -> list[tuple[str, dict]] | None:
    """Create one pending delivery per matching ACTIVE subscription.

    ``UNIQUE(subscription_id, event_ref)`` makes outbox redelivery a no-op
    (§6.5: at-least-once → exactly one ledger row per source event).
    """
    from mesh.db.tenant import set_tenant_context

    await set_tenant_context(session, event.workspace_id)
    payload = event.payload or {}
    event_type = str(payload.get("event_type") or "")
    source_event_ref = str(payload.get("source_event_ref") or event.id)
    subscriptions = (await session.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.workspace_id == event.workspace_id,
            WebhookSubscription.status == "active",
        )
    )).scalars().all()
    for subscription in subscriptions:
        if not _event_matches(subscription, event_type):
            continue
        delivery = WebhookSubscriptionDelivery(
            workspace_id=event.workspace_id,
            subscription_id=subscription.id,
            event_ref=source_event_ref,
            state="pending",
        )
        session.add(delivery)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            continue  # duplicate dequeue — the ledger row already exists
    return None


# ---------------------------------------------------------------------------
# Delivery worker (HMAC + retry/backoff + circuit breaker)
# ---------------------------------------------------------------------------


def signature_headers(secret: str, body: bytes, timestamp: int) -> dict[str, str]:
    """``Mesh-Signature: t=<ts>,v1=HMAC_SHA256(secret, "<ts>.<body>")``."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return {"t": str(timestamp), "v1": mac.hexdigest()}


def format_signature_header(parts: dict[str, str]) -> str:
    return f"t={parts['t']},v1={parts['v1']}"


def compute_next_retry(attempts: int, *, base_seconds: int, max_seconds: int) -> timedelta:
    """Exponential backoff with jitter: min(base*2^n, max) * U(0.5, 1.0)."""
    delay = min(base_seconds * (2 ** max(attempts, 0)), max_seconds)
    return timedelta(seconds=delay * random.uniform(JITTER_MIN, JITTER_MAX))


class WebhookDeliveryWorker:
    """Claims pending deliveries and POSTs them (outbox consumer, §6.6)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        signing_secret: str,
        max_attempts: int = DEFAULT_RETRY_MAX_ATTEMPTS,
        base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
        max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
        timeout_seconds: int = DEFAULT_DELIVERY_TIMEOUT_SECONDS,
        break_threshold: int = DEFAULT_CIRCUIT_BREAK_THRESHOLD,
        poll_interval: float = 1.0,
        batch_size: int = 50,
        http_client_factory=None,
        resolver=None,
        clock=None,
    ) -> None:
        self._session_factory = session_factory
        self._signing_secret = signing_secret
        self._max_attempts = max_attempts
        self._base_seconds = base_seconds
        self._max_seconds = max_seconds
        self._timeout_seconds = timeout_seconds
        self._break_threshold = break_threshold
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._http_client_factory = http_client_factory
        self._resolver = resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    def _client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(
            timeout=self._timeout_seconds, follow_redirects=False
        )

    async def claim_due(self, session: AsyncSession) -> list[WebhookSubscriptionDelivery]:
        now = self._clock()
        stmt = (
            select(WebhookSubscriptionDelivery)
            .where(
                WebhookSubscriptionDelivery.state == "pending",
                or_(
                    WebhookSubscriptionDelivery.next_retry_at.is_(None),
                    WebhookSubscriptionDelivery.next_retry_at <= now,
                ),
            )
            .order_by(WebhookSubscriptionDelivery.created_at.asc())
            .limit(self._batch_size)
            .with_for_update(skip_locked=True)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def deliver_one(
        self, session: AsyncSession, delivery: WebhookSubscriptionDelivery
    ) -> None:
        subscription = await session.get(WebhookSubscription, delivery.subscription_id)
        if subscription is None or subscription.status != "active":
            return  # deleted / paused / breaker-open: leave pending until resume
        secret = decrypt_credential_value(subscription.secret_ref, self._signing_secret)
        body = json.dumps(
            {"event_ref": delivery.event_ref, "delivery_id": str(delivery.id)},
            separators=(",", ":"),
        ).encode()
        now = self._clock()
        # SSRF re-check on the RESOLVED address (defense in depth, §6.16).
        try:
            assert_public_resolved(subscription.url, resolver=self._resolver)
        except BusinessRuleError as exc:
            delivery.attempts += 1
            delivery.response_status = None
            delivery.last_error = exc.code
            await self._register_failure(session, subscription, delivery, now=now)
            return
        timestamp = int(now.timestamp())
        headers = {
            "Mesh-Signature": format_signature_header(signature_headers(secret, body, timestamp)),
            "Mesh-Event": delivery.event_ref,
            "Mesh-Delivery": str(delivery.id),
            "Content-Type": "application/json",
        }
        try:
            async with self._client() as client:
                response = await client.post(subscription.url, content=body, headers=headers)
            status_code = response.status_code
            ok = 200 <= status_code < 300
            error_text = None if ok else f"http_{status_code}"
        except (httpx.HTTPError, OSError) as exc:
            status_code = None
            ok = False
            error_text = type(exc).__name__
        delivery.attempts += 1
        delivery.response_status = status_code
        delivery.last_error = error_text
        if ok:
            delivery.state = "sent"
            delivery.next_retry_at = None
            subscription.fail_count = 0
        else:
            await self._register_failure(session, subscription, delivery, now=now)
        subscription.updated_at = now
        await session.flush()

    async def _register_failure(
        self,
        session: AsyncSession,
        subscription: WebhookSubscription,
        delivery: WebhookSubscriptionDelivery,
        *,
        now: datetime,
    ) -> None:
        if delivery.attempts >= self._max_attempts:
            delivery.state = "failed"
            delivery.next_retry_at = None
            subscription.fail_count += 1
            if subscription.fail_count >= self._break_threshold:
                subscription.status = "disabled"  # circuit breaker (§3.4)
                logger.error(
                    "webhook subscription %s circuit breaker OPEN after %d "
                    "consecutive failures",
                    subscription.id, subscription.fail_count,
                )
                await emit_realtime(
                    session,
                    workspace_id=subscription.workspace_id,
                    channel=f"workspace:{subscription.workspace_id}:integrations",
                    event="integration.updated",
                    data={
                        "subject": "subscription",
                        "subscription_id": str(subscription.id),
                        "status": "disabled",
                        "fail_count": subscription.fail_count,
                        "reason": "circuit_break",
                    },
                    idempotency_key=(
                        f"subscription:{subscription.id}:breaker:{int(now.timestamp() * 1000)}"
                    ),
                )
        else:
            delivery.next_retry_at = now + compute_next_retry(
                delivery.attempts, base_seconds=self._base_seconds, max_seconds=self._max_seconds
            )

    async def run_once(self) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                deliveries = await self.claim_due(session)
                for delivery in deliveries:
                    await self.deliver_one(session, delivery)
        return len(deliveries)

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:  # noqa: BLE001 — worker must survive a bad batch
                logger.exception("webhook delivery batch failed")
                processed = 0
            if processed == 0:
                if stop is not None:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                else:
                    await asyncio.sleep(self._poll_interval)


# ---------------------------------------------------------------------------
# Manual retry (§3.1)
# ---------------------------------------------------------------------------


async def retry_delivery(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    subscription: WebhookSubscription,
    delivery_id: uuid.UUID,
) -> WebhookSubscriptionDelivery:
    delivery = await session.scalar(
        select(WebhookSubscriptionDelivery).where(
            WebhookSubscriptionDelivery.id == delivery_id,
            WebhookSubscriptionDelivery.workspace_id == workspace_id,
            WebhookSubscriptionDelivery.subscription_id == subscription.id,
        )
    )
    if delivery is None:
        raise NotFoundError("delivery not found")
    if subscription.status == "disabled":
        raise BusinessRuleError(
            "subscription circuit breaker is open; resume first",
            code="subscription_circuit_open",
        )
    if delivery.state != "failed":
        raise BusinessRuleError(
            "only failed deliveries can be retried",
            code="invalid_request",
            details={"state": delivery.state},
        )
    delivery.state = "pending"
    delivery.next_retry_at = None
    await session.flush()
    return delivery


__all__ = [
    "DEFAULT_CIRCUIT_BREAK_THRESHOLD",
    "DEFAULT_RETRY_BASE_SECONDS",
    "DEFAULT_RETRY_MAX_ATTEMPTS",
    "DEFAULT_RETRY_MAX_SECONDS",
    "WEBHOOK_DISPATCH_EVENT_TYPE",
    "WebhookDeliveryWorker",
    "assert_public_resolved",
    "compute_next_retry",
    "create_subscription",
    "delete_subscription",
    "derive_dispatch_from_realtime",
    "format_signature_header",
    "get_subscription",
    "render_delivery",
    "render_subscription",
    "resume_subscription",
    "retry_delivery",
    "rotate_subscription_secret",
    "signature_headers",
    "update_subscription",
    "validate_subscription_url",
    "webhook_dispatch_handler",
]
