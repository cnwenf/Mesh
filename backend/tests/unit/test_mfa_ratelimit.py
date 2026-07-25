"""H2: /auth/mfa/verify is rate limited per (IP, ticket) — TOTP brute-force bound."""

from __future__ import annotations

import httpx
import pytest

from mesh.api.app import create_app
from mesh.config import load_settings

pytestmark = pytest.mark.unit

MFA_VERIFY_LIMIT = 5


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url, redis_url=redis_url, auth_mode="dev", jwt_secret="mfa-rl-secret"
    )
    return create_app(settings)


async def test_mfa_verify_rate_limited_after_limit(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        last = None
        # The same (IP, ticket) tuple: first MFA_VERIFY_LIMIT pass the limiter
        # (then fail ticket validation with 401); the next is rate-limited (429).
        for _ in range(MFA_VERIFY_LIMIT + 1):
            last = await client.post(
                "/api/v1/auth/mfa/verify",
                json={"mfa_ticket": "ticket-x", "code": "000000"},
            )
    assert last is not None
    assert last.status_code == 429
    assert last.json()["error"]["code"] == "rate_limited"
    await app.state.redis.aclose()
    await app.state.engine.dispose()
