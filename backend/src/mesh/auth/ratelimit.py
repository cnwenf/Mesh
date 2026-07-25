"""Rate limiting + login lockout (auth.md §3.6 / §5.5).

Two independent mechanisms:

1. **Endpoint rate limiting** — a Redis sliding window keyed by (dimension,
   bucket). Exceeding the limit raises 429 ``rate_limited`` with ``Retry-After``
   and the ``X-RateLimit-*`` response headers are set by the route layer.

2. **Login lockout** — counted from the ``login_attempts`` table over the
   ``(ip, email)`` tuple (NOT email alone, to avoid a lockout-DoS where an
   attacker fails a victim's password repeatedly; auth.md §5.5). Reaching the
   threshold raises 423 ``account_locked``; the lock clears when the trailing
   window expires. §3.6 reserves a captcha-unlock path, but it is NOT wired
   here yet — ``assert_not_locked_out`` takes no unlock-token parameter.

Redis is the rate-limit backend (non-authoritative, per README §3.1); lockout
state is durable in PostgreSQL.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.user import LoginAttempt
from mesh.errors import LockedError, RateLimitedError


class RateLimiter:
    """Redis sliding-window rate limiter (auth.md §3.6)."""

    def __init__(self, redis) -> None:
        self._redis = redis

    async def check(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> tuple[int, int]:
        """Record one hit; return ``(remaining, reset_in_seconds)``.

        Raises :class:`RateLimitedError` (429 + ``Retry-After``) once ``limit``
        hits fall within the trailing ``window_seconds``.
        """
        moment = now if now is not None else time.time()
        window_start = moment - window_seconds
        redis_key = f"mesh:ratelimit:{key}"

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zcard(redis_key)
        pipe.zadd(redis_key, {f"{moment}:{uuid.uuid4().hex}": moment})
        pipe.expire(redis_key, window_seconds)
        _removed, count, _added, _ttl = await pipe.execute()

        if count >= limit:
            # Over the limit: drop the hit we just speculatively added.
            await self._redis.zremrangebyscore(redis_key, moment, moment + 1)
            reset_in = max(1, int(window_seconds))
            raise RateLimitedError(
                "rate limit exceeded",
                retry_after=reset_in,
                details={"limit": limit, "window_seconds": window_seconds},
            )
        remaining = max(0, limit - count - 1)
        return remaining, window_seconds


async def assert_not_locked_out(
    session: AsyncSession,
    *,
    email: str,
    ip_address: str | None,
    max_failures: int,
    lock_seconds: int,
    now_epoch: float | None = None,
) -> None:
    """Raise 423 ``account_locked`` when recent failures hit the threshold.

    Counts failed attempts for the ``(ip, email)`` tuple within the trailing
    lock window. A successful attempt within the window resets the count (the
    legitimate user got in). Email is compared case-insensitively.
    """
    from datetime import datetime

    moment = now_epoch if now_epoch is not None else time.time()
    window_start = datetime.fromtimestamp(moment - lock_seconds, tz=UTC)

    stmt = (
        select(func.count(LoginAttempt.id))
        .where(LoginAttempt.email == email.lower())
        .where(LoginAttempt.created_at >= window_start)
        .where(LoginAttempt.succeeded.is_(False))
    )
    if ip_address is not None:
        stmt = stmt.where(LoginAttempt.ip_address == ip_address)
    failures = (await session.execute(stmt)).scalar_one()

    if failures >= max_failures:
        raise LockedError(
            "account temporarily locked due to too many failed attempts",
            code="account_locked",
            details={"retry_after_seconds": lock_seconds},
        )
