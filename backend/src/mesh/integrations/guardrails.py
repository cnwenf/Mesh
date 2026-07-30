"""Post-signature semantic inbound guardrails (integrations.md §2.10, MES-87).

Bound sessions are UNAUTHENTICATED parties relative to Mesh: unthrottled
inbound means one agent execution (paid compute) per message plus outbound
quota burn — a cost-amplification / integration-DoS surface. Every inbound
IM task message passes THREE counters AFTER signature verification and
binding match (orthogonal to msgId dedup), before queueing:

| guardrail                | key dimension            | default            |
| per-identity frequency   | full sender triple       | 20 / rolling min   |
| per-conversation freq.   | conversation_key         | 60 / rolling min   |
| per-conversation depth   | pending queue count (DB) | 50                 |

Redis rolling windows carry the TENANT dimension inside the key (the
sender triple / conversation key both embed provider:tenant). Over-limit:
NOT queued / executed / acked — the ledger row turns ``rejected`` with
``payload._mesh_reject_reason='rate_limited'`` and the REAL msgId occupies
the dedup slot (no retry storm on the same message); the bot answers with
a self-throttled rate-limit notice (≤1/minute per conversation — prevents
notice-reflection amplification). HTTP mode still answers 200 (non-2xx
would trigger platform retry amplification); Stream frames ACK normally.

This layer complements — does not replace — the pre-signature coarse
(integration, IP) anti-abuse layer (auth.md §3.6, inbound_routes.py).
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.integration import Integration, IntegrationMessageQueue
from mesh.integrations.connectors import VerifiedEnvelope
from mesh.outbox.service import emit_event

logger = logging.getLogger("mesh.integrations.guardrails")

# Defaults (integrations.md §2.10 table; settings-overridable).
DEFAULT_PER_IDENTITY_PER_MIN = 20
DEFAULT_PER_CONVERSATION_PER_MIN = 60
DEFAULT_MAX_PENDING_PER_CONVERSATION = 50

# Self-throttle for the rate-limit notice: ≤1 per conversation per minute.
_NOTICE_WINDOW_SECONDS = 60
DEFAULT_RATE_LIMIT_NOTICE = "⚠️ 消息过于频繁，已暂时限频，请稍后再试"

_WINDOW_SECONDS = 60

VERDICT_RATE_LIMITED = "rate_limited"


class InboundGuardrails:
    """The three §2.10 counters (Redis rolling windows + DB pending depth)."""

    def __init__(
        self,
        redis,
        *,
        per_identity_per_min: int = DEFAULT_PER_IDENTITY_PER_MIN,
        per_conversation_per_min: int = DEFAULT_PER_CONVERSATION_PER_MIN,
        max_pending_per_conversation: int = DEFAULT_MAX_PENDING_PER_CONVERSATION,
    ) -> None:
        self._redis = redis
        self.per_identity_per_min = per_identity_per_min
        self.per_conversation_per_min = per_conversation_per_min
        self.max_pending_per_conversation = max_pending_per_conversation

    async def _window_count(self, key: str, *, now: float) -> int:
        """Record one hit in the rolling window; return the hit count
        (INCLUDING this hit) inside the trailing 60s. Redis flakiness
        fails OPEN (returns 0 — admit) so a Redis blip cannot turn every
        callback into a 500 retry-amplifier; the pending-depth counter
        (pure DB) still enforces the hard leg."""
        redis_key = f"mesh:im:guard:{key}"
        try:
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(redis_key, 0, now - _WINDOW_SECONDS)
            pipe.zadd(redis_key, {f"{now}:{uuid.uuid4().hex}": now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, _WINDOW_SECONDS)
            _removed, _added, count, _ttl = await pipe.execute()
        except Exception:  # noqa: BLE001 — redis flakiness ⇒ fail open
            logger.warning(
                "inbound window counter unavailable (redis) — failing open for key=%s",
                redis_key,
            )
            return 0
        return int(count)

    async def _pending_depth(self, session: AsyncSession, conversation_key: str) -> int:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(IntegrationMessageQueue)
                    .where(
                        IntegrationMessageQueue.conversation_key == conversation_key,
                        IntegrationMessageQueue.state == "pending",
                    )
                )
            ).scalar_one()
        )

    async def check_rate_windows(
        self,
        *,
        sender_identity_key: str,
        conversation_key: str,
        now_epoch: float | None = None,
    ) -> str | None:
        """The two Redis rolling-window counters (identity 20/min global,
        conversation 60/min). Runs BEFORE the command plane (§3.7:975 —
        command handling is constrained by the §2.10 counters too).
        None = admit; ``'rate_limited'`` = reject (caller audits).

        Redis unavailable ⇒ the two WINDOW counters degrade explicitly
        (warn-once admit); the pending-DEPTH counter is pure DB and runs
        regardless — the §2.10 three-counter hard constraint keeps its
        DB-backed leg under a Redis outage."""
        if self._redis is None:
            logger.warning(
                "inbound window guardrails degraded (redis unavailable) — "
                "admitting; pending-depth counter still enforced"
            )
            return None
        moment = now_epoch if now_epoch is not None else time.time()

        identity_hits = await self._window_count(
            f"identity:{sender_identity_key}", now=moment
        )
        if identity_hits > self.per_identity_per_min:
            logger.warning(
                "inbound identity guardrail exceeded (%s hits/min, key=%s)",
                identity_hits,
                sender_identity_key,
            )
            return VERDICT_RATE_LIMITED

        conversation_hits = await self._window_count(
            f"conversation:{conversation_key}", now=moment
        )
        if conversation_hits > self.per_conversation_per_min:
            logger.warning(
                "inbound conversation guardrail exceeded (%s hits/min, conversation=%s)",
                conversation_hits,
                conversation_key,
            )
            return VERDICT_RATE_LIMITED

        return None

    async def check_pending_depth(
        self, session: AsyncSession, conversation_key: str
    ) -> str | None:
        """Pending-depth counter (DB count, hard cap 50). MUST run under the
        conversation's imq_seq advisory lock (caller holds it) so concurrent
        ingests cannot jointly exceed the §2.10 cap."""
        depth = await self._pending_depth(session, conversation_key)
        if depth >= self.max_pending_per_conversation:
            logger.warning(
                "inbound queue-depth guardrail exceeded (%s pending, conversation=%s)",
                depth,
                conversation_key,
            )
            return VERDICT_RATE_LIMITED
        return None

    async def maybe_emit_rate_limit_notice(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        integration: Integration,
        envelope: VerifiedEnvelope,
        conversation_key: str,
        now_epoch: float | None = None,
    ) -> bool:
        """Emit the ONE-PER-MINUTE rate-limit notice (im.send outbox).

        Returns True when a notice was scheduled. The per-conversation
        self-throttle (Redis SET NX EX 60) prevents notice-reflection:
        flooding a conversation must not amplify outbound quota burn via
        N notice messages.
        """
        if self._redis is None:
            return False  # notice self-throttle needs redis; skip notice
        moment = now_epoch if now_epoch is not None else time.time()
        notice_key = f"mesh:im:guard:notice:{conversation_key}"
        try:
            acquired = await self._redis.set(
                notice_key, "1", nx=True, ex=_NOTICE_WINDOW_SECONDS
            )
        except Exception:  # noqa: BLE001 — redis flakiness ⇒ skip notice
            logger.warning("rate-limit notice self-throttle unavailable (redis)")
            return False
        if not acquired:
            return False
        minute_bucket = int(moment // _NOTICE_WINDOW_SECONDS)
        idempotency_key = hashlib.sha256(
            f"{conversation_key}|rate-notice|{minute_bucket}".encode()
        ).hexdigest()
        template = str(
            (integration.config or {}).get("rate_limit_notice")
            or DEFAULT_RATE_LIMIT_NOTICE
        )
        await emit_event(
            session,
            workspace_id=workspace_id,
            event_type="im.send",
            payload={
                "kind": "rate_limit_notice",
                "integration_id": str(integration.id),
                "conversation_key": conversation_key,
                "external_ref": envelope.external_ref,
                "sender_key": envelope.sender_key,
                "template": template,
                "channel": envelope.channel,
            },
            idempotency_key=idempotency_key,
        )
        logger.error(
            "inbound rate-limit notice scheduled (integration=%s conversation=%s)",
            integration.id,
            conversation_key,
        )
        return True


__all__: list[Any] = [
    "DEFAULT_MAX_PENDING_PER_CONVERSATION",
    "DEFAULT_PER_CONVERSATION_PER_MIN",
    "DEFAULT_PER_IDENTITY_PER_MIN",
    "DEFAULT_RATE_LIMIT_NOTICE",
    "InboundGuardrails",
    "VERDICT_RATE_LIMITED",
]

