"""External identity linking (integrations.md §2.4.1 / §3.1, HIGH-1/R4/R5).

``external_identities`` maps an external platform account onto the Mesh
GLOBAL login identity ``users.id`` (not a workspace-scoped member). Trust
root: a mapping is created ONLY through an authenticated flow — a one-time
verification code delivered to the claimed external account's private
message (whoever reads that DM owns the account), or an OAuth round-trip
whose returned platform identity the server checks. Implicit creation from
card callbacks / inbound events is forbidden (identity forgery).

Unlink authorization is owner-only: ``external_identity_unlink_allowed``
compares ONLY ``users.id`` (line-for-line equivalent of the executable
reference SQL in migration 0028 / schema_r2_validation.sql) — workspace
admin/owner roles do NOT constitute authorization (no bypass, T29⑪).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets as pysecrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.auth.audit import write_audit
from mesh.db.models.integration import ExternalIdentity, Integration
from mesh.db.models.member import Member
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
)

logger = logging.getLogger("mesh.integrations.identities")

CODE_TTL_SECONDS = 600  # §3.1: 10-minute TTL
CODE_PREFIX = "mesh:identity-code:"
DEV_OUTBOX_PREFIX = "mesh:identity-dev-outbox:"
CODE_LENGTH = 6
# Brute-force cap (security M-1): a 6-digit code must not be guessable
# within its TTL. After this many mismatches the code is DESTROYED — the
# write rate limit alone would still allow ~1000+ guesses per window, and
# a success would map someone else's unlinked external account onto the
# attacker's users.id (the card-callback chain resolves clickers by it).
MAX_CODE_ATTEMPTS = 5

# L1 (integrations.md §3.1): verification-code ISSUANCE rate limits. Each
# :link call delivers a code to a claimed external account; without a cap an
# authenticated member could spam/enumerate codes against any staffId in a
# connected tenant. Two independent sliding windows mirror the login-failure
# counting paradigm (auth.md §3.6): per member (issuer) AND per target
# external account. Both are checked before any code is created/delivered.
LINK_CODE_WINDOW_SECONDS = 600  # 10-minute sliding window
LINK_CODE_PER_MEMBER_PER_WINDOW = 5
LINK_CODE_PER_TARGET_PER_WINDOW = 3
LINK_CODE_MEMBER_PREFIX = "mesh:identity-link:member:"
LINK_CODE_TARGET_PREFIX = "mesh:identity-link:target:"

# DingTalk staffId charset (integrations.md §2.10 — widest official caliber).
# Encoded external-contact keys are 'x=<base64url>' and carry '=' as their
# second character, which is OUTSIDE this set; a staffId field therefore can
# never be an encoded key, and 'x=…' is rejected here (T39-15c).
STAFF_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class CodeDelivery(Protocol):
    """Deliver a verification code to the external account's private message.

    Production adapters send through the platform API (feishu/slack DM);
    the dev adapter records into a Redis outbox tests can read. Delivery
    failures raise — the link never proceeds without a delivered code.
    """

    async def deliver(self, *, provider: str, tenant_key: str, external_user_key: str, code: str) -> None: ...


class RedisDevCodeDelivery:
    """Dev/test delivery: the code lands in a Redis dev-outbox key.

    The code is NEVER returned in an API response — tests read the outbox
    the same way a human would read their DMs on the external platform.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def deliver(self, *, provider: str, tenant_key: str, external_user_key: str, code: str) -> None:
        key = f"{DEV_OUTBOX_PREFIX}{provider}:{tenant_key}:{external_user_key}"
        await self._redis.set(key, code, ex=CODE_TTL_SECONDS)
        logger.info(
            "identity verification code delivered (dev outbox): %s:%s:%s",
            provider,
            tenant_key,
            external_user_key,
        )


def _code_key(workspace_id: uuid.UUID, member_id: uuid.UUID, provider: str) -> str:
    return f"{CODE_PREFIX}{workspace_id}:{member_id}:{provider}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Executable reference — owner-only unlink (R5, T29⑪)
# ---------------------------------------------------------------------------


async def external_identity_unlink_allowed(
    session: AsyncSession, *, identity_id: uuid.UUID, member_id: uuid.UUID
) -> bool:
    """True iff the member resolves to the mapping's owning ``users.id``.

    Role columns deliberately do NOT participate — workspace admin/owner is
    not an authorization (no bypass). Line-for-line equivalent of the SQL
    reference function ``external_identity_unlink_allowed(uuid, uuid)``.
    """
    row = (
        await session.execute(
            select(ExternalIdentity.id)
            .join(Member, Member.id == member_id)
            .where(
                ExternalIdentity.id == identity_id,
                Member.user_id == ExternalIdentity.user_id,
                Member.status == "active",
            )
        )
    ).first()
    return row is not None


# ---------------------------------------------------------------------------
# L1 issuance rate limit (integrations.md §3.1)
# ---------------------------------------------------------------------------


async def _sliding_window_count(redis: Redis, key: str, window_start: float) -> int:
    """Count hits in the trailing window (ZSET sliding-window paradigm)."""
    await redis.zremrangebyscore(key, 0, window_start)
    return int(await redis.zcard(key))


async def check_link_rate_limits(
    redis: Redis,
    *,
    member_id: uuid.UUID,
    provider: str,
    tenant_key: str,
    external_user_key: str,
    now: float | None = None,
) -> None:
    """Raise 429 ``rate_limited`` once per-member OR per-target caps are hit.

    Mirrors the auth sliding-window paradigm (auth.md §3.6). Both windows are
    read BEFORE either is recorded so a target rejection does not inflate the
    member window (and vice versa).
    """
    moment = now if now is not None else time.time()
    window_start = moment - LINK_CODE_WINDOW_SECONDS
    member_key = f"{LINK_CODE_MEMBER_PREFIX}{member_id}"
    target_key = f"{LINK_CODE_TARGET_PREFIX}{provider}:{tenant_key}:{external_user_key}"
    member_count = await _sliding_window_count(redis, member_key, window_start)
    if member_count >= LINK_CODE_PER_MEMBER_PER_WINDOW:
        raise RateLimitedError(
            "too many verification codes requested",
            retry_after=LINK_CODE_WINDOW_SECONDS,
            details={"dimension": "member", "limit": LINK_CODE_PER_MEMBER_PER_WINDOW},
        )
    target_count = await _sliding_window_count(redis, target_key, window_start)
    if target_count >= LINK_CODE_PER_TARGET_PER_WINDOW:
        raise RateLimitedError(
            "too many verification codes requested for this account",
            retry_after=LINK_CODE_WINDOW_SECONDS,
            details={"dimension": "target", "limit": LINK_CODE_PER_TARGET_PER_WINDOW},
        )
    pipe = redis.pipeline()
    pipe.zadd(member_key, {f"{moment}:{uuid.uuid4().hex}": moment})
    pipe.expire(member_key, LINK_CODE_WINDOW_SECONDS)
    pipe.zadd(target_key, {f"{moment}:{uuid.uuid4().hex}": moment})
    pipe.expire(target_key, LINK_CODE_WINDOW_SECONDS)
    await pipe.execute()


# ---------------------------------------------------------------------------
# Link flow
# ---------------------------------------------------------------------------


async def start_link(
    session: AsyncSession,
    *,
    redis: Redis,
    delivery: CodeDelivery,
    workspace_id: uuid.UUID,
    member: Member,
    provider: str,
    integration: Integration,
    external_user_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Generate + deliver a one-time code to the claimed external account.

    The link target is FIXED to the requester's own ``users.id`` — the
    endpoint accepts no parameter pointing at another user (HIGH-1).
    """
    if provider not in ("feishu", "slack", "github", "gitlab", "dingtalk"):
        raise BusinessRuleError("unsupported provider", code="invalid_request")
    if not external_user_key:
        raise BusinessRuleError("external_user_key is required", code="invalid_request")
    # L1 (§3.1 / §2.10 T39-15c): a DingTalk staffId must match the widest
    # official charset. Encoded external-contact keys ('x=<base64url>') carry
    # '=' outside this set, so they are rejected here — a staffId field can
    # never impersonate an external-contact key.
    if provider == "dingtalk" and not STAFF_ID_RE.match(external_user_key):
        raise BusinessRuleError(
            "invalid dingtalk staffId charset",
            code="invalid_request",
            details={"external_user_key": external_user_key[:64]},
        )
    # Duplicate mapping pre-check (authoritative check stays at confirm).
    tenant_key = _tenant_key_for(provider, integration)
    # L1 issuance rate limit (per member + per target) BEFORE any code is
    # created/delivered (§3.1).
    await check_link_rate_limits(
        redis,
        member_id=member.id,
        provider=provider,
        tenant_key=tenant_key,
        external_user_key=external_user_key,
    )
    existing = await session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == provider,
            ExternalIdentity.provider_tenant_key == tenant_key,
            ExternalIdentity.external_user_key == external_user_key,
        )
    )
    if existing is not None:
        raise ConflictError("external account already linked", code="identity_already_linked")
    code = "".join(pysecrets.choice("0123456789") for _ in range(CODE_LENGTH))
    record = {
        "code_hash": _hash_code(code),
        "external_user_key": external_user_key,
        "tenant_key": tenant_key,
        "created_at": (now or datetime.now(UTC)).isoformat(),
    }
    await redis.set(
        _code_key(workspace_id, member.id, provider),
        json.dumps(record),
        ex=CODE_TTL_SECONDS,
    )
    await delivery.deliver(
        provider=provider,
        tenant_key=tenant_key,
        external_user_key=external_user_key,
        code=code,
    )
    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_member_id=member.id,
        actor_kind="member",
        action="external_identity.link_started",
        resource_type="external_identity",
        metadata={"provider": provider, "integration_id": str(integration.id)},
    )
    return {
        "provider": provider,
        "external_user_key": external_user_key,
        "code_ttl_seconds": CODE_TTL_SECONDS,
    }


async def confirm_link(
    session: AsyncSession,
    *,
    redis: Redis,
    workspace_id: uuid.UUID,
    member: Member,
    provider: str,
    code: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify code (match + unexpired + single-consume) → write the mapping.

    ``user_id`` is resolved from the requester's member row — the mapping
    target is ALWAYS the requester's own global identity (R4).
    """
    if member.user_id is None:
        raise BusinessRuleError("member has no global user identity", code="invalid_request")
    raw = await redis.get(_code_key(workspace_id, member.id, provider))
    if raw is None:
        raise BusinessRuleError("verification code expired or never issued", code="invalid_request")
    record = json.loads(raw)
    if not _safe_equals(_hash_code(code), str(record.get("code_hash") or "")):
        # Brute-force cap (M-1): count the mismatch and either persist the
        # counter (preserving the REMAINING ttl — a failure must never
        # extend the code's lifetime) or destroy the code once the budget
        # is exhausted. The error stays identical either way.
        key = _code_key(workspace_id, member.id, provider)
        attempts = int(record.get("failed_attempts") or 0) + 1
        if attempts >= MAX_CODE_ATTEMPTS:
            await redis.delete(key)
        else:
            record["failed_attempts"] = attempts
            ttl = await redis.ttl(key)
            if ttl is None or ttl < 0:
                ttl = CODE_TTL_SECONDS
            await redis.set(key, json.dumps(record), ex=ttl)
        raise BusinessRuleError("verification code mismatch", code="invalid_request")
    # Single consumption: delete BEFORE the insert so a failed insert cannot
    # be replayed into a retry loop with the same code.
    await redis.delete(_code_key(workspace_id, member.id, provider))
    moment = now or datetime.now(UTC)
    identity = ExternalIdentity(
        provider=provider,
        provider_tenant_key=str(record.get("tenant_key") or ""),
        external_user_key=str(record["external_user_key"]),
        user_id=member.user_id,
        created_in_workspace_id=workspace_id,
        verified_at=moment,
        created_at=moment,
        updated_at=moment,
    )
    session.add(identity)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "external account already linked",
            code="identity_already_linked",
        ) from exc
    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_member_id=member.id,
        actor_kind="member",
        action="external_identity.linked",
        resource_type="external_identity",
        resource_id=identity.id,
        metadata={
            "provider": provider,
            "created_in_workspace_id": str(workspace_id),
        },
    )
    return render_identity(identity)


async def unlink_identity(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    member: Member,
    identity_id: uuid.UUID,
) -> None:
    """Global unlink — owner-only, no admin bypass (R5, T29⑪)."""
    identity = await session.get(ExternalIdentity, identity_id)
    if identity is None:
        raise NotFoundError("external identity not found")
    if not await external_identity_unlink_allowed(session, identity_id=identity_id, member_id=member.id):
        raise ForbiddenError(
            "only the mapping owner may unlink this external identity",
            code="identity_unlink_forbidden",
        )
    provider, external_user_key = identity.provider, identity.external_user_key
    await session.delete(identity)
    await session.flush()
    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_member_id=member.id,
        actor_kind="member",
        action="external_identity.unlinked",
        resource_type="external_identity",
        metadata={"provider": provider, "external_user_key": external_user_key},
    )


async def list_own_identities(session: AsyncSession, *, member: Member) -> list[ExternalIdentity]:
    """The requester's OWN mappings (global table filtered by users.id)."""
    if member.user_id is None:
        return []
    rows = (
        (
            await session.execute(
                select(ExternalIdentity)
                .where(ExternalIdentity.user_id == member.user_id)
                .order_by(ExternalIdentity.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def lookup_identity(
    session: AsyncSession,
    *,
    provider: str,
    provider_tenant_key: str,
    external_user_key: str,
) -> ExternalIdentity | None:
    return await session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == provider,
            ExternalIdentity.provider_tenant_key == provider_tenant_key,
            ExternalIdentity.external_user_key == external_user_key,
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tenant_key_for(provider: str, integration: Integration) -> str:
    from mesh.integrations.connectors import adapter_for

    kind = f"{'im' if provider in ('feishu', 'slack', 'dingtalk') else 'vcs'}_{provider}"
    try:
        adapter = adapter_for(kind)
    except KeyError:
        # Provider connector not registered yet (e.g. DingTalk before its
        # connector lands): derive a stable best-effort tenant key from config
        # so the identity / rate-limit keys stay consistent.
        config = integration.config or {}
        return str(
            config.get("provider_tenant_id")
            or config.get("corp_id")
            or config.get("tenant_key")
            or ""
        )
    return adapter["tenant_key_from_config"](integration.config or {})


def _safe_equals(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a, b)


def render_identity(identity: ExternalIdentity) -> dict[str, Any]:
    return {
        "id": str(identity.id),
        "provider": identity.provider,
        "provider_tenant_key": identity.provider_tenant_key,
        "external_user_key": identity.external_user_key,
        "user_id": str(identity.user_id),
        "created_in_workspace_id": (
            str(identity.created_in_workspace_id) if identity.created_in_workspace_id else None
        ),
        "verified_at": identity.verified_at.isoformat() if identity.verified_at else None,
        "created_at": identity.created_at.isoformat() if identity.created_at else None,
    }


__all__ = [
    "CODE_LENGTH",
    "CODE_TTL_SECONDS",
    "DEV_OUTBOX_PREFIX",
    "LINK_CODE_MEMBER_PREFIX",
    "LINK_CODE_PER_MEMBER_PER_WINDOW",
    "LINK_CODE_PER_TARGET_PER_WINDOW",
    "LINK_CODE_TARGET_PREFIX",
    "LINK_CODE_WINDOW_SECONDS",
    "MAX_CODE_ATTEMPTS",
    "STAFF_ID_RE",
    "RedisDevCodeDelivery",
    "check_link_rate_limits",
    "confirm_link",
    "external_identity_unlink_allowed",
    "list_own_identities",
    "lookup_identity",
    "render_identity",
    "start_link",
    "unlink_identity",
]
