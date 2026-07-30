"""L1 link-code issuance guards (integrations.md §3.1 / §2.10 / §5.6 T39-15c).

Covers the implementation-period L1 items on ``external-identities:link``:

* per-member AND per-target sliding-window rate limits on verification-code
  issuance (login-failure-counting paradigm; 429 ``rate_limited``);
* DingTalk staffId charset guard — ``x=<base64url>`` external-contact keys are
  rejected ('=' is outside the staffId charset), real staffIds accepted;
* the ``dingtalk`` provider whitelist addition.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import select

from mesh.db.models.integration import Integration
from mesh.db.models.member import Member
from mesh.errors import BusinessRuleError, RateLimitedError
from mesh.integrations import identities as ids_mod
from tests.unit.integrations_support import seed_world

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def redis_client():
    client = Redis.from_url(
        os.environ.get("MESH_TEST_REDIS_URL", "redis://127.0.0.1:6399/1"),
        decode_responses=True,
    )
    await client.flushdb()
    yield client
    await client.aclose()


async def _member(session_factory, member_id: uuid.UUID) -> Member:
    async with session_factory() as session:
        return await session.get(Member, member_id)


async def _integration(session_factory, integration_id: uuid.UUID) -> Integration:
    async with session_factory() as session:
        return await session.get(Integration, integration_id)


async def _make_dingtalk_integration(session_factory, world: dict) -> Integration:
    """A DingTalk integration row (kind allowed by the DB CHECK; the connector
    adapter is not required — ``_tenant_key_for`` falls back to config)."""
    async with session_factory() as session, session.begin():
        integration = Integration(
            id=uuid.uuid4(),
            workspace_id=world["ws"],
            kind="im_dingtalk",
            name="dingtalk-main",
            config={"corp_id": "dingTESTCORP", "app_key": "app-key-test"},
            created_by=world["member"],
        )
        session.add(integration)
    return integration


async def _run_link(
    session_factory,
    redis_client,
    world: dict,
    *,
    provider: str = "slack",
    integration: Integration | None = None,
    external_user_key: str = "U_X",
) -> dict:
    member = await _member(session_factory, world["member"])
    integ = integration or await _integration(session_factory, world["integ_slack"])
    delivery = ids_mod.RedisDevCodeDelivery(redis_client)
    async with session_factory() as session, session.begin():
        return await ids_mod.start_link(
            session,
            redis=redis_client,
            delivery=delivery,
            workspace_id=world["ws"],
            member=member,
            provider=provider,
            integration=integ,
            external_user_key=external_user_key,
        )


# ---------------------------------------------------------------------------
# Issuance rate limits (§3.1 L1)
# ---------------------------------------------------------------------------


async def test_member_rate_limit_trips_after_window(session_factory, redis_client):
    """Per-member window: 5 distinct-target issuances pass, the 6th → 429."""
    world = await seed_world(session_factory)
    for index in range(ids_mod.LINK_CODE_PER_MEMBER_PER_WINDOW):
        await _run_link(session_factory, redis_client, world, external_user_key=f"U_MEMBER_{index}")
    with pytest.raises(RateLimitedError) as excinfo:
        await _run_link(
            session_factory, redis_client, world, external_user_key="U_MEMBER_OVERFLOW"
        )
    assert excinfo.value.code == "rate_limited"
    assert excinfo.value.details == {
        "dimension": "member",
        "limit": ids_mod.LINK_CODE_PER_MEMBER_PER_WINDOW,
    }


async def test_target_rate_limit_trips_after_window(session_factory, redis_client):
    """Per-target window: 3 issuances for one account pass, the 4th → 429."""
    world = await seed_world(session_factory)
    for _ in range(ids_mod.LINK_CODE_PER_TARGET_PER_WINDOW):
        await _run_link(session_factory, redis_client, world, external_user_key="U_SAME_TARGET")
    with pytest.raises(RateLimitedError) as excinfo:
        await _run_link(session_factory, redis_client, world, external_user_key="U_SAME_TARGET")
    assert excinfo.value.code == "rate_limited"
    assert excinfo.value.details["dimension"] == "target"


async def test_rate_limit_windows_are_independent(session_factory, redis_client):
    """A target rejection does not consume the member budget for other targets:
    after tripping one target, a DIFFERENT target still issues (member < cap)."""
    world = await seed_world(session_factory)
    # Trip the per-target window for U_HOT (3 issuances).
    for _ in range(ids_mod.LINK_CODE_PER_TARGET_PER_WINDOW):
        await _run_link(session_factory, redis_client, world, external_user_key="U_HOT")
    with pytest.raises(RateLimitedError):
        await _run_link(session_factory, redis_client, world, external_user_key="U_HOT")
    # Member has issued 3 (< 5); a fresh target still succeeds.
    result = await _run_link(session_factory, redis_client, world, external_user_key="U_FRESH")
    assert result["external_user_key"] == "U_FRESH"


# ---------------------------------------------------------------------------
# DingTalk staffId charset guard + whitelist (§2.10 T39-15c)
# ---------------------------------------------------------------------------


async def test_dingtalk_staffid_guard_rejects_encoded_key(session_factory, redis_client):
    """'x=<base64url>' external-contact keys are NOT valid staffIds ('=' is
    outside the staffId charset) — rejected before any code is issued."""
    world = await seed_world(session_factory)
    dingtalk = await _make_dingtalk_integration(session_factory, world)
    with pytest.raises(BusinessRuleError) as excinfo:
        await _run_link(
            session_factory,
            redis_client,
            world,
            provider="dingtalk",
            integration=dingtalk,
            external_user_key="x=JEx3Q1B2Ml8xOiQ2R1lzbi16cmM1V1o3N3hjMnY0enN5WGZCdjFt",
        )
    assert excinfo.value.code == "invalid_request"
    # No code was delivered for the rejected key.
    delivered = await redis_client.get(
        f"{ids_mod.DEV_OUTBOX_PREFIX}dingtalk:dingTESTCORP:"
        "x=JEx3Q1B2Ml8xOiQ2R1lzbi16cmM1V1o3N3hjMnY0enN5WGZCdjFt"
    )
    assert delivered is None


async def test_dingtalk_staffid_guard_accepts_real_staffid(session_factory, redis_client):
    """A widest-caliber staffId passes the guard and a code is delivered."""
    world = await seed_world(session_factory)
    dingtalk = await _make_dingtalk_integration(session_factory, world)
    result = await _run_link(
        session_factory,
        redis_client,
        world,
        provider="dingtalk",
        integration=dingtalk,
        external_user_key="014728255240768602",
    )
    assert result["provider"] == "dingtalk"
    assert result["external_user_key"] == "014728255240768602"
    code = await redis_client.get(
        f"{ids_mod.DEV_OUTBOX_PREFIX}dingtalk:dingTESTCORP:014728255240768602"
    )
    assert code is not None and len(code) == ids_mod.CODE_LENGTH


async def test_dingtalk_added_to_provider_whitelist(session_factory, redis_client):
    """``dingtalk`` is an accepted provider; an unknown one is still rejected."""
    world = await seed_world(session_factory)
    dingtalk = await _make_dingtalk_integration(session_factory, world)
    # Accepted (whitelist addition) — guard + rate limit pass, code issued.
    ok = await _run_link(
        session_factory,
        redis_client,
        world,
        provider="dingtalk",
        integration=dingtalk,
        external_user_key="staffA._-1",
    )
    assert ok["provider"] == "dingtalk"
    # A provider outside the whitelist is rejected up front.
    with pytest.raises(BusinessRuleError) as excinfo:
        await _run_link(
            session_factory,
            redis_client,
            world,
            provider="telegram",
            integration=dingtalk,
            external_user_key="whatever",
        )
    assert excinfo.value.code == "invalid_request"


async def test_dingtalk_link_confirm_creates_mapping(session_factory, redis_client):
    """End-to-end: a DingTalk staffId link confirms into a global identity row
    keyed by the full triple (provider=dingtalk)."""
    world = await seed_world(session_factory)
    dingtalk = await _make_dingtalk_integration(session_factory, world)
    await _run_link(
        session_factory,
        redis_client,
        world,
        provider="dingtalk",
        integration=dingtalk,
        external_user_key="014728255240768602",
    )
    code = await redis_client.get(
        f"{ids_mod.DEV_OUTBOX_PREFIX}dingtalk:dingTESTCORP:014728255240768602"
    )
    member = await _member(session_factory, world["member"])
    async with session_factory() as session, session.begin():
        identity = await ids_mod.confirm_link(
            session,
            redis=redis_client,
            workspace_id=world["ws"],
            member=member,
            provider="dingtalk",
            code=code,
        )
    assert identity["provider"] == "dingtalk"
    assert identity["provider_tenant_key"] == "dingTESTCORP"
    assert identity["external_user_key"] == "014728255240768602"
    assert identity["user_id"] == str(world["user"])
    # The mapping row exists keyed by the full triple.
    async with session_factory() as session:
        row = await session.scalar(
            select(ids_mod.ExternalIdentity).where(
                ids_mod.ExternalIdentity.provider == "dingtalk",
                ids_mod.ExternalIdentity.external_user_key == "014728255240768602",
            )
        )
    assert row is not None and row.provider_tenant_key == "dingTESTCORP"
