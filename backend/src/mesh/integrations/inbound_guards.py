"""Post-signature semantic inbound guards (integrations.md §2.10 入站频率护栏).

Unauthenticated external members of a bound conversation are an untrusted
party: every inbound message costs one full agent execution (paid compute)
plus outbound quota, so unlimited inflow is a cost-amplification / DoS
surface. Each inbound IM message passes three counters BEFORE it is matched
and enqueued (orthogonal to msgId dedupe):

* per-identity frequency  — 20 / rolling minute (tenant-dimensioned key)
* per-conversation frequency — 60 / rolling minute
* per-conversation pending depth — 50 (DB count, re-checked authoritatively
  under the ``imq_seq`` advisory lock inside ``enqueue_message``)

Over-limit handling: NOT enqueued, NOT executed, NOT acked — the caller
persists an ``integration_events`` row with ``process_status='rejected'`` and
``payload._mesh_reject_reason='rate_limited'`` (the real msgId occupies the
dedupe key so a retry storm cannot re-occupy it), the bot sends a one-shot
self-rate-limit notice (≤1/min per conversation — notice-reflection guard)
and an alert is raised; HTTP callback mode still returns 200 (non-2xx would
trigger platform re-push amplification).

The sliding windows reuse the auth.md §3.6 Redis ZSET pattern
(:class:`mesh.auth.ratelimit.RateLimiter`); failures of the guard
infrastructure fail OPEN rather than dropping legitimate traffic is
explicitly NOT the policy — a Redis outage raises through and the ingest
transaction rolls back (the platform re-pushes, dedupe keeps it safe).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.auth.ratelimit import RateLimiter
from mesh.db.models.integration import IntegrationMessageQueue
from mesh.errors import RateLimitedError

logger = logging.getLogger(__name__)

_GUARD_WINDOW_SECONDS = 60
_HINT_TTL_SECONDS = 60


class InboundGuardRejected(Exception):
    """Raised when a semantic inbound guard trips (§2.10).

    ``reason`` drives the ``_mesh_reject_reason`` audit marker. The caller
    converts this into a ``rejected`` audit row + optional one-shot bot
    notice; it is never rendered as an HTTP error envelope (the platform
    contract requires a bare 200).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"inbound guard rejected: {reason}")
        self.reason = reason


async def check_inbound_guards(
    redis,
    session: AsyncSession,
    *,
    settings,
    provider: str,
    tenant_key: str,
    user_key: str,
    conversation_key: str,
) -> None:
    """Run the three guards; raise :class:`InboundGuardRejected` over limit.

    Order: identity window, conversation window, pending depth. The depth
    count here is a fast-path pre-check; ``enqueue_message`` re-checks it
    under the conversation advisory lock (authoritative gate).
    """
    limiter = RateLimiter(redis)
    try:
        await limiter.check(
            f"im-guard:identity:{provider}:{tenant_key}:{user_key}",
            limit=settings.im_inbound_per_identity_per_min,
            window_seconds=_GUARD_WINDOW_SECONDS,
        )
    except RateLimitedError:
        raise InboundGuardRejected("identity_rate") from None
    try:
        await limiter.check(
            f"im-guard:conv:{conversation_key}",
            limit=settings.im_inbound_per_conversation_per_min,
            window_seconds=_GUARD_WINDOW_SECONDS,
        )
    except RateLimitedError:
        raise InboundGuardRejected("conversation_rate") from None

    depth = await session.scalar(
        select(func.count())
        .select_from(IntegrationMessageQueue)
        .where(
            IntegrationMessageQueue.conversation_key == conversation_key,
            IntegrationMessageQueue.state == "pending",
        )
    )
    if (depth or 0) >= settings.im_queue_max_pending_per_conversation:
        raise InboundGuardRejected("queue_depth")


async def rate_limit_hint_allowed(redis, *, conversation_key: str) -> bool:
    """One self-rate-limit bot notice per conversation per minute (§2.10).

    ``SET … NX EX 60`` semantics: True only for the caller that set the flag
    (prevents notice-reflection floods when an attacker keeps tripping the
    guards).
    """
    was_set = await redis.set(
        f"mesh:im-guard:hint:{conversation_key}",
        "1",
        nx=True,
        ex=_HINT_TTL_SECONDS,
    )
    return bool(was_set)


__all__ = [
    "InboundGuardRejected",
    "check_inbound_guards",
    "rate_limit_hint_allowed",
]
