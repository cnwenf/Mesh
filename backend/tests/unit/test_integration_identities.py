"""External identity link/unlink tests (integrations.md §2.4.1 / §3.1 / §5.2).

Covers: code-delivery trust root (code lands in the external account's dev
outbox, never in the response), single-consume + expiry, duplicate mapping
409, owner-only unlink with NO admin bypass (executable reference
``external_identity_unlink_allowed``), audit rows.
"""

from __future__ import annotations

import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from mesh.db.models.audit import AuditLog
from mesh.db.models.integration import ExternalIdentity, Integration
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError
from mesh.integrations import identities as ids_mod
from tests.unit.integrations_support import seed_world

pytestmark = pytest.mark.unit


@pytest.fixture
async def redis_client():
    import os

    client = Redis.from_url(
        os.environ.get("MESH_TEST_REDIS_URL", "redis://127.0.0.1:6399/1"),
        decode_responses=True,
    )
    yield client
    await client.aclose()


async def _member(session_factory, member_id: uuid.UUID) -> Member:
    async with session_factory() as session:
        return await session.get(Member, member_id)


async def _integration(session_factory, integration_id: uuid.UUID) -> Integration:
    async with session_factory() as session:
        return await session.get(Integration, integration_id)


async def _run_link(session_factory, redis_client, world, *, external_user_key="U_CLICK"):
    member = await _member(session_factory, world["member"])
    integration = await _integration(session_factory, world["integ_slack"])
    delivery = ids_mod.RedisDevCodeDelivery(redis_client)
    async with session_factory() as session, session.begin():
        result = await ids_mod.start_link(
            session,
            redis=redis_client,
            delivery=delivery,
            workspace_id=world["ws"],
            member=member,
            provider="slack",
            integration=integration,
            external_user_key=external_user_key,
        )
    return result


# ---------------------------------------------------------------------------
# Link flow
# ---------------------------------------------------------------------------


async def test_link_confirm_creates_global_mapping(session_factory, redis_client):
    world = await seed_world(session_factory)
    result = await _run_link(session_factory, redis_client, world)
    assert "code" not in result, "code must never be returned in the response"
    # The code is delivered to the EXTERNAL account (dev outbox stands in
    # for the platform DM — the trust root).
    code = await redis_client.get(f"{ids_mod.DEV_OUTBOX_PREFIX}slack:T_TEST:U_CLICK")
    assert code is not None and len(code) == ids_mod.CODE_LENGTH
    member = await _member(session_factory, world["member"])
    async with session_factory() as session, session.begin():
        identity = await ids_mod.confirm_link(
            session,
            redis=redis_client,
            workspace_id=world["ws"],
            member=member,
            provider="slack",
            code=code,
        )
    assert identity["external_user_key"] == "U_CLICK"
    assert identity["user_id"] == str(world["user"])  # requester's own users.id
    assert identity["created_in_workspace_id"] == str(world["ws"])
    # Code is single-consumed.
    leftover = await redis_client.get(f"{ids_mod.CODE_PREFIX}{world['ws']}:{world['member']}:slack")
    assert leftover is None


async def test_confirm_wrong_code_rejected(session_factory, redis_client):
    world = await seed_world(session_factory)
    await _run_link(session_factory, redis_client, world)
    member = await _member(session_factory, world["member"])
    async with session_factory() as session, session.begin():
        with pytest.raises(BusinessRuleError):
            await ids_mod.confirm_link(
                session,
                redis=redis_client,
                workspace_id=world["ws"],
                member=member,
                provider="slack",
                code="000000",
            )


async def test_confirm_without_start_rejected(session_factory, redis_client):
    world = await seed_world(session_factory)
    member = await _member(session_factory, world["member"])
    async with session_factory() as session, session.begin():
        with pytest.raises(BusinessRuleError):
            await ids_mod.confirm_link(
                session,
                redis=redis_client,
                workspace_id=world["ws"],
                member=member,
                provider="slack",
                code="123456",
            )


async def test_duplicate_link_rejected_409(session_factory, redis_client):
    world = await seed_world(session_factory)
    # Pre-existing mapping for the external account.
    async with session_factory() as session, session.begin():
        session.add(
            ExternalIdentity(
                provider="slack",
                provider_tenant_key="T_TEST",
                external_user_key="U_TAKEN",
                user_id=world["user"],
                created_in_workspace_id=world["ws"],
            )
        )
    from mesh.errors import ConflictError

    with pytest.raises(ConflictError) as excinfo:
        await _run_link(session_factory, redis_client, world, external_user_key="U_TAKEN")
    assert excinfo.value.code == "identity_already_linked"


# ---------------------------------------------------------------------------
# Unlink authorization — owner only, NO admin bypass (R5 / T29⑪)
# ---------------------------------------------------------------------------


async def test_unlink_allowed_only_for_owner(session_factory):
    world = await seed_world(session_factory)
    # Second user + admin member in the SAME workspace (not the owner).
    async with session_factory() as session, session.begin():
        other_user = User(
            id=uuid.uuid4(),
            email=f"other-{uuid.uuid4().hex[:8]}@mesh.test",
            display_name="Other Admin",
            password_hash="unused",
        )
        session.add(other_user)
        await session.flush()
        other_admin = Member(
            id=uuid.uuid4(),
            workspace_id=world["ws"],
            member_type="human",
            user_id=other_user.id,
            role="admin",
            status="active",
        )
        session.add(other_admin)
        await session.flush()
        identity = ExternalIdentity(
            provider="slack",
            provider_tenant_key="T_TEST",
            external_user_key="U_OWNER",
            user_id=world["user"],
            created_in_workspace_id=world["ws"],
        )
        session.add(identity)
    async with session_factory() as session:
        assert (
            await ids_mod.external_identity_unlink_allowed(
                session, identity_id=identity.id, member_id=world["member"]
            )
            is True
        ), "owner via their member row → allowed"
        assert (
            await ids_mod.external_identity_unlink_allowed(
                session, identity_id=identity.id, member_id=other_admin.id
            )
            is False
        ), "admin who is NOT the owner → denied (no bypass)"


async def test_unlink_by_other_user_forbidden(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        other_user = User(
            id=uuid.uuid4(),
            email=f"o2-{uuid.uuid4().hex[:8]}@mesh.test",
            display_name="O2",
            password_hash="unused",
        )
        session.add(other_user)
        await session.flush()
        other_admin = Member(
            id=uuid.uuid4(),
            workspace_id=world["ws"],
            member_type="human",
            user_id=other_user.id,
            role="owner",
            status="active",
        )
        session.add(other_admin)
        await session.flush()
        identity = ExternalIdentity(
            provider="slack",
            provider_tenant_key="T_TEST",
            external_user_key="U_MINE",
            user_id=world["user"],
            created_in_workspace_id=world["ws"],
        )
        session.add(identity)
    async with session_factory() as session, session.begin():
        with pytest.raises(ForbiddenError) as excinfo:
            await ids_mod.unlink_identity(
                session,
                workspace_id=world["ws"],
                member=other_admin,
                identity_id=identity.id,
            )
        assert excinfo.value.code == "identity_unlink_forbidden"
    # Mapping untouched.
    async with session_factory() as session:
        row = await session.get(ExternalIdentity, identity.id)
        assert row is not None


async def test_owner_unlink_succeeds_and_audits(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        identity = ExternalIdentity(
            provider="slack",
            provider_tenant_key="T_TEST",
            external_user_key="U_GONE",
            user_id=world["user"],
            created_in_workspace_id=world["ws"],
        )
        session.add(identity)
    member = await _member(session_factory, world["member"])
    async with session_factory() as session, session.begin():
        await ids_mod.unlink_identity(
            session,
            workspace_id=world["ws"],
            member=member,
            identity_id=identity.id,
        )
    async with session_factory() as session:
        assert await session.get(ExternalIdentity, identity.id) is None
        audits = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "external_identity.unlinked")))
            .scalars()
            .all()
        )
        assert len(audits) == 1


async def test_unlink_missing_identity_404(session_factory):
    world = await seed_world(session_factory)
    member = await _member(session_factory, world["member"])
    async with session_factory() as session, session.begin():
        with pytest.raises(NotFoundError):
            await ids_mod.unlink_identity(
                session,
                workspace_id=world["ws"],
                member=member,
                identity_id=uuid.uuid4(),
            )


async def test_list_own_identities_filters_by_user(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        other_user = User(
            id=uuid.uuid4(),
            email=f"o3-{uuid.uuid4().hex[:8]}@mesh.test",
            display_name="O3",
            password_hash="unused",
        )
        session.add(other_user)
        await session.flush()
        session.add(
            ExternalIdentity(
                provider="slack",
                provider_tenant_key="T_TEST",
                external_user_key="U_MINE_A",
                user_id=world["user"],
            )
        )
        session.add(
            ExternalIdentity(
                provider="github",
                provider_tenant_key="",
                external_user_key="octocat",
                user_id=other_user.id,
            )
        )
    member = await _member(session_factory, world["member"])
    async with session_factory() as session:
        rows = await ids_mod.list_own_identities(session, member=member)
    assert [r.external_user_key for r in rows] == ["U_MINE_A"]


# ---------------------------------------------------------------------------
# Verification-code brute-force cap (security M-1)
# ---------------------------------------------------------------------------
#
# A 6-digit code over a 600s TTL must not be guessable: after
# MAX_CODE_ATTEMPTS mismatches the code is DESTROYED (the write rate limit
# alone would still allow ~1200 tries per window). A failed attempt also
# must not extend the code's lifetime.


def _wrong_code(real: str) -> str:
    """A 6-digit code guaranteed unequal to ``real``."""
    return str((int(real) + 1) % 10**ids_mod.CODE_LENGTH).zfill(ids_mod.CODE_LENGTH)


async def _confirm_expect_mismatch(session_factory, redis_client, world, member, code):
    async with session_factory() as session, session.begin():
        with pytest.raises(BusinessRuleError) as excinfo:
            await ids_mod.confirm_link(
                session,
                redis=redis_client,
                workspace_id=world["ws"],
                member=member,
                provider="slack",
                code=code,
            )
    return excinfo.value


async def test_confirm_link_failure_budget_destroys_code(session_factory, redis_client):
    # Arrange — a delivered code and its guaranteed-wrong counterpart.
    world = await seed_world(session_factory)
    await _run_link(session_factory, redis_client, world)
    real = await redis_client.get(f"{ids_mod.DEV_OUTBOX_PREFIX}slack:T_TEST:U_CLICK")
    wrong = _wrong_code(real)
    member = await _member(session_factory, world["member"])
    # Act — exhaust the MAX_CODE_ATTEMPTS budget with wrong codes.
    for _ in range(ids_mod.MAX_CODE_ATTEMPTS):
        exc = await _confirm_expect_mismatch(session_factory, redis_client, world, member, wrong)
        assert exc.code == "invalid_request"
    # Assert — the code is destroyed: even the REAL code is now refused...
    async with session_factory() as session, session.begin():
        with pytest.raises(BusinessRuleError) as excinfo:
            await ids_mod.confirm_link(
                session,
                redis=redis_client,
                workspace_id=world["ws"],
                member=member,
                provider="slack",
                code=real,
            )
    assert excinfo.value.code == "invalid_request"
    # ...and the code record is gone from Redis.
    leftover = await redis_client.get(f"{ids_mod.CODE_PREFIX}{world['ws']}:{world['member']}:slack")
    assert leftover is None


async def test_confirm_link_succeeds_within_failure_budget(session_factory, redis_client):
    # Arrange
    world = await seed_world(session_factory)
    await _run_link(session_factory, redis_client, world)
    real = await redis_client.get(f"{ids_mod.DEV_OUTBOX_PREFIX}slack:T_TEST:U_CLICK")
    wrong = _wrong_code(real)
    member = await _member(session_factory, world["member"])
    # Act — MAX_CODE_ATTEMPTS - 1 wrong tries, THEN the real code.
    for _ in range(ids_mod.MAX_CODE_ATTEMPTS - 1):
        await _confirm_expect_mismatch(session_factory, redis_client, world, member, wrong)
    async with session_factory() as session, session.begin():
        identity = await ids_mod.confirm_link(
            session,
            redis=redis_client,
            workspace_id=world["ws"],
            member=member,
            provider="slack",
            code=real,
        )
    # Assert — the link succeeds; the cap is a budget, not a lockout.
    assert identity["external_user_key"] == "U_CLICK"


async def test_confirm_link_restart_resets_failure_counter(session_factory, redis_client):
    # Arrange — destroy one code via the failure budget.
    world = await seed_world(session_factory)
    await _run_link(session_factory, redis_client, world)
    real = await redis_client.get(f"{ids_mod.DEV_OUTBOX_PREFIX}slack:T_TEST:U_CLICK")
    member = await _member(session_factory, world["member"])
    for _ in range(ids_mod.MAX_CODE_ATTEMPTS):
        await _confirm_expect_mismatch(session_factory, redis_client, world, member, _wrong_code(real))
    # Act — a fresh start_link issues a new code with a fresh budget.
    await _run_link(session_factory, redis_client, world)
    fresh = await redis_client.get(f"{ids_mod.DEV_OUTBOX_PREFIX}slack:T_TEST:U_CLICK")
    async with session_factory() as session, session.begin():
        identity = await ids_mod.confirm_link(
            session,
            redis=redis_client,
            workspace_id=world["ws"],
            member=member,
            provider="slack",
            code=fresh,
        )
    # Assert
    assert identity["external_user_key"] == "U_CLICK"


async def test_confirm_link_failure_does_not_extend_ttl(session_factory, redis_client):
    # Arrange — a failed attempt must not extend the code's lifetime.
    world = await seed_world(session_factory)
    await _run_link(session_factory, redis_client, world)
    real = await redis_client.get(f"{ids_mod.DEV_OUTBOX_PREFIX}slack:T_TEST:U_CLICK")
    member = await _member(session_factory, world["member"])
    key = f"{ids_mod.CODE_PREFIX}{world['ws']}:{world['member']}:slack"
    ttl_before = await redis_client.ttl(key)
    # Act
    await _confirm_expect_mismatch(session_factory, redis_client, world, member, _wrong_code(real))
    # Assert — TTL only ever shrinks (no window extension via failures).
    ttl_after = await redis_client.ttl(key)
    assert 0 < ttl_after <= ttl_before
