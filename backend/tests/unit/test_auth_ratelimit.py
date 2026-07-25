"""Unit tests for rate limiting + login lockout (auth.md §3.6/§5.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mesh.auth.ratelimit import RateLimiter, assert_not_locked_out
from mesh.db.models.user import LoginAttempt
from mesh.errors import LockedError, RateLimitedError


class TestRateLimiter:
    async def test_allows_under_limit(self, redis_client):
        limiter = RateLimiter(redis_client)
        remaining, reset = await limiter.check("k", limit=3, window_seconds=60, now=1000.0)
        assert remaining == 2
        assert reset == 60

    async def test_blocks_over_limit_with_retry_after(self, redis_client):
        limiter = RateLimiter(redis_client)
        for i in range(3):
            await limiter.check("k", limit=3, window_seconds=60, now=1000.0 + i)
        with pytest.raises(RateLimitedError) as exc:
            await limiter.check("k", limit=3, window_seconds=60, now=1003.0)
        assert exc.value.status_code == 429
        assert exc.value.retry_after == 60
        assert exc.value.headers == {"Retry-After": "60"}

    async def test_window_slides_and_frees_capacity(self, redis_client):
        limiter = RateLimiter(redis_client)
        for i in range(3):
            await limiter.check("k", limit=3, window_seconds=60, now=1000.0 + i)
        # 61s later the old hits have slid out of the window.
        remaining, _ = await limiter.check("k", limit=3, window_seconds=60, now=1062.0)
        assert remaining == 2

    async def test_distinct_keys_are_independent(self, redis_client):
        limiter = RateLimiter(redis_client)
        for i in range(3):
            await limiter.check("a", limit=3, window_seconds=60, now=1000.0 + i)
        # Key "b" is unaffected by "a" hitting its limit.
        remaining, _ = await limiter.check("b", limit=3, window_seconds=60, now=1000.0)
        assert remaining == 2


async def _seed_failures(db_session, *, email, ip, count, at):
    for _ in range(count):
        db_session.add(
            LoginAttempt(email=email, ip_address=ip, succeeded=False, created_at=at)
        )
    await db_session.commit()


class TestLoginLockout:
    async def test_locks_out_at_threshold(self, db_session):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        await _seed_failures(db_session, email="li@corp.com", ip="10.0.0.1", count=5, at=now)
        with pytest.raises(LockedError) as exc:
            await assert_not_locked_out(
                db_session,
                email="li@corp.com",
                ip_address="10.0.0.1",
                max_failures=5,
                lock_seconds=900,
                now_epoch=now.timestamp(),
            )
        assert exc.value.code == "account_locked"

    async def test_under_threshold_not_locked(self, db_session):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        await _seed_failures(db_session, email="li@corp.com", ip="10.0.0.1", count=4, at=now)
        await assert_not_locked_out(
            db_session,
            email="li@corp.com",
            ip_address="10.0.0.1",
            max_failures=5,
            lock_seconds=900,
            now_epoch=now.timestamp(),
        )  # no raise

    async def test_different_ip_not_locked(self, db_session):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        await _seed_failures(db_session, email="li@corp.com", ip="10.0.0.1", count=5, at=now)
        # Same email, different IP — not locked (avoids lockout DoS).
        await assert_not_locked_out(
            db_session,
            email="li@corp.com",
            ip_address="10.0.0.2",
            max_failures=5,
            lock_seconds=900,
            now_epoch=now.timestamp(),
        )

    async def test_email_case_insensitive(self, db_session):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        await _seed_failures(db_session, email="li@corp.com", ip="10.0.0.1", count=5, at=now)
        with pytest.raises(LockedError):
            await assert_not_locked_out(
                db_session,
                email="LI@CORP.COM",
                ip_address="10.0.0.1",
                max_failures=5,
                lock_seconds=900,
                now_epoch=now.timestamp(),
            )

    async def test_old_failures_outside_window_ignored(self, db_session):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        old = now - timedelta(minutes=30)
        await _seed_failures(db_session, email="li@corp.com", ip="10.0.0.1", count=5, at=old)
        await assert_not_locked_out(
            db_session,
            email="li@corp.com",
            ip_address="10.0.0.1",
            max_failures=5,
            lock_seconds=900,
            now_epoch=now.timestamp(),
        )  # no raise — failures are outside the 15-min window
