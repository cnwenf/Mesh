"""API token service — PAT / agent runtime credentials (auth.md §2.5/§3.2).

Holders are de-polymorphised (README §6.1): ``owner_member_id`` names a roster
entry — a human PAT points at the requester's own member row, an agent runtime
credential at the agent's member row. Security invariants enforced here:

- **hash-only storage** — the plaintext (``mesh_pat_…`` / ``mesh_agt_…`` +
  ≥32 bytes base64url) is returned ONLY by ``create_token``; the DB keeps the
  SHA-256 ``token_hash`` and a non-secret ``prefix`` for listing;
- **``role_override`` double validation** (auth.md §5.5) — checked at creation
  AND at use: it may never exceed the holder's CURRENT role (422 otherwise);
- **least privilege** — effective scopes = requested scopes ∩ the holder role's
  matrix permissions, so a token can never outrank its owner;
- **agent anti-loop** — agent credentials drop ``agent:trigger`` by default so
  an agent cannot trigger agents (auth.md §5.2 / Z5);
- every create/revoke writes an append-only audit row in-transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth import security
from mesh.auth.audit import write_audit
from mesh.auth.rbac import PERMISSION_MATRIX, ROLE_RANK
from mesh.auth.realtime import SESSION_REVOKED_EVENT
from mesh.db.models.api_token import (
    AGENT_TOKEN_PREFIX,
    DISPLAY_PREFIX_LEN,
    PAT_TOKEN_PREFIX,
    ApiToken,
)
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError, ValidationError
from mesh.outbox.service import emit_realtime

TOKEN_NAME_MAX = 120
ROLE_OVERRIDE_TOO_HIGH = "role_override_too_high"
_TOKEN_NOT_FOUND = "token not found"


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _role_perms(role: str) -> frozenset[str]:
    """The set of permissions the §2.7 matrix grants to ``role``."""
    return frozenset(p for p, roles in PERMISSION_MATRIX.items() if role in roles)


@dataclass(frozen=True)
class ResolvedToken:
    """A validated PAT/agent credential resolved to its effective principal."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    owner_member_id: uuid.UUID
    member_type: str
    role: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    name: str = ""

    def can(self, permission: str) -> bool:
        """Least-privilege check: permission must be in the effective scopes."""
        return permission in self.scopes


class TokenService:
    """Issues, lists, revokes and validates workspace access tokens."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sf = session_factory
        self._clock = clock

    # -- creation --------------------------------------------------------------

    async def create_token(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        name: str,
        scopes: list[str] | None = None,
        role_override: str | None = None,
        expires_at: datetime | None = None,
        owner_member_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Create a token; the plaintext is returned ONLY here (auth.md §2.5)."""
        name = (name or "").strip()
        if not name or len(name) > TOKEN_NAME_MAX:
            raise ValidationError(
                "invalid token name",
                code="validation_error",
                details={"max_length": TOKEN_NAME_MAX},
            )
        if role_override is not None and role_override not in ROLE_RANK:
            raise ValidationError(
                "invalid role_override",
                code="validation_error",
                details={"role_override": role_override, "allowed": sorted(ROLE_RANK)},
            )
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            owner = await self._resolve_owner(
                session, workspace_id=workspace_id, actor=actor, owner_member_id=owner_member_id
            )
            self._check_role_override(role_override, owner.role)

            kind = "agent" if owner.member_type == "agent" else "pat"
            granted = self._prepare_scopes(scopes, member_type=owner.member_type)

            token_prefix = AGENT_TOKEN_PREFIX if kind == "agent" else PAT_TOKEN_PREFIX
            plaintext = token_prefix + security.generate_token()
            row = ApiToken(
                workspace_id=workspace_id,
                owner_member_id=owner.id,
                name=name,
                token_hash=security.hash_token(plaintext),
                prefix=plaintext[:DISPLAY_PREFIX_LEN],
                scopes=granted,
                role_override=role_override,
                expires_at=expires_at,
            )
            session.add(row)
            await session.flush()
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="token.created",
                resource_type="api_token",
                resource_id=row.id,
                metadata={
                    "name": name,
                    "kind": kind,
                    "owner_member_id": str(owner.id),
                    "scopes": granted,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            result = self._render(row, owner_role=owner.role, include_token=plaintext)
        return result

    async def _resolve_owner(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        actor: Member,
        owner_member_id: uuid.UUID | None,
    ) -> Member:
        """Resolve the token holder. Self when unspecified; otherwise the named
        member must be active and in the same workspace (admin creates for
        agents/others — the route enforces ``agent:manage``/``token:manage``)."""
        if owner_member_id is None:
            owner = actor
        else:
            owner = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id, Member.id == owner_member_id
                )
            )
            if owner is None:
                raise NotFoundError("owner member not found")
        if owner.status != "active":
            raise BusinessRuleError(
                "token owner is not an active member", code="owner_not_active"
            )
        return owner

    @staticmethod
    def _check_role_override(role_override: str | None, owner_role: str) -> None:
        """§5.5: role_override may never exceed the holder's current role (422)."""
        if role_override is None:
            return
        if ROLE_RANK[role_override] > ROLE_RANK.get(owner_role, -1):
            raise BusinessRuleError(
                "role_override must not exceed the holder's role",
                code=ROLE_OVERRIDE_TOO_HIGH,
                details={"role_override": role_override, "holder_role": owner_role},
            )

    @staticmethod
    def _prepare_scopes(scopes: list[str] | None, *, member_type: str) -> list[str]:
        """De-duplicate scopes and apply the agent anti-loop default-deny."""
        granted = sorted({s for s in (scopes or []) if s})
        if member_type == "agent":
            # Z5 / §5.2: agent credentials must not trigger agents by default.
            granted = [s for s in granted if s != "agent:trigger"]
        return granted

    # -- listing / revocation --------------------------------------------------

    async def list_tokens(
        self, *, actor: Member, workspace_id: uuid.UUID
    ) -> list[dict]:
        """List tokens visible to ``actor``: own tokens for a member, all for
        admin/owner (auth.md §3.2). Never exposes the hash or plaintext."""
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(ApiToken)
                .where(ApiToken.workspace_id == workspace_id, ApiToken.revoked_at.is_(None))
                .order_by(ApiToken.created_at.desc())
            )
            if actor.role not in ("admin", "owner"):
                stmt = stmt.where(ApiToken.owner_member_id == actor.id)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._render(row, owner_role=None, include_token=None) for row in rows]

    async def revoke_token(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        token_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Revoke a token (holder or admin/owner). Immediate: subsequent use 401."""
        now = _now(self._clock)
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            row = await session.scalar(
                select(ApiToken).where(
                    ApiToken.workspace_id == workspace_id, ApiToken.id == token_id
                )
            )
            if row is None:
                raise NotFoundError(_TOKEN_NOT_FOUND)
            is_holder = row.owner_member_id == actor.id
            if not is_holder and actor.role not in ("admin", "owner"):
                raise ForbiddenError("cannot revoke a token you do not own")
            if row.revoked_at is None:
                row.revoked_at = now
                row.updated_at = now
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_member_id=actor.id,
                    actor_kind="member",
                    action="token.revoked",
                    resource_type="api_token",
                    resource_id=row.id,
                    metadata={"name": row.name},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                # C4: broadcast revocation on the workspace channel so any live
                # connection bearing this token fails re-auth (outbox → realtime).
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=f"workspace:{workspace_id}",
                    event=SESSION_REVOKED_EVENT,
                    data={
                        "token_id": str(row.id),
                        "owner_member_id": str(row.owner_member_id),
                    },
                )

    # -- validation (Bearer → principal) ---------------------------------------

    async def resolve_pat(
        self,
        *,
        token: str,
        ip_address: str | None = None,
        now: datetime | None = None,
    ) -> ResolvedToken | None:
        """Validate a plaintext PAT/agent token and resolve its principal.

        Returns ``None`` for an unknown/revoked/expired token (the dependency
        layer maps that to 401). Re-validates ``role_override`` against the
        holder's CURRENT role at use time (§5.5 double validation) and reduces
        scopes to the role intersection (least privilege).
        """
        moment = now if now is not None else _now(self._clock)
        token_hash = security.hash_token(token)
        async with self._sf() as session, session.begin():
            # Bootstrap read by hash via the SECURITY DEFINER function: the
            # workspace is unknown until the token is found, so the fail-closed
            # RLS policy can't gate this lookup (mirrors invitation acceptance).
            rec = (
                await session.execute(
                    text("SELECT * FROM mesh_api_token_by_hash(:h)"), {"h": token_hash}
                )
            ).mappings().first()
            if rec is None:
                return None
            if rec["revoked_at"] is not None or (
                rec["expires_at"] is not None and rec["expires_at"] < moment
            ):
                return None
            workspace_id = rec["workspace_id"]
            await set_tenant_context(session, workspace_id)
            owner = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id,
                    Member.id == rec["owner_member_id"],
                )
            )
            if owner is None or owner.status != "active":
                return None
            # Use-time role_override re-validation (§5.5): a later role downgrade
            # below the override invalidates the token rather than escalating it.
            role_override = rec["role_override"]
            if role_override is not None:
                if ROLE_RANK.get(role_override, -1) > ROLE_RANK.get(owner.role, -1):
                    raise BusinessRuleError(
                        "role_override must not exceed the holder's role",
                        code=ROLE_OVERRIDE_TOO_HIGH,
                        details={
                            "role_override": role_override,
                            "holder_role": owner.role,
                        },
                    )
                effective_role = role_override
            else:
                effective_role = owner.role
            effective_scopes = frozenset(set(rec["scopes"] or []) & _role_perms(effective_role))
            # Touch last_used under the tenant GUC (RLS-visible update).
            await session.execute(
                update(ApiToken)
                .where(ApiToken.id == rec["id"])
                .values(last_used_at=moment, last_used_ip=ip_address, updated_at=moment)
            )
            return ResolvedToken(
                id=rec["id"],
                workspace_id=workspace_id,
                owner_member_id=owner.id,
                member_type=owner.member_type,
                role=effective_role,
                scopes=effective_scopes,
                name=rec["name"],
            )

    # -- rendering -------------------------------------------------------------

    @staticmethod
    def _render(row: ApiToken, *, owner_role: str | None, include_token: str | None) -> dict:
        data = {
            "id": row.id,
            "name": row.name,
            "prefix": row.prefix,
            "scopes": list(row.scopes or []),
            "role_override": row.role_override,
            "owner_member_id": row.owner_member_id,
            "expires_at": row.expires_at,
            "last_used_at": row.last_used_at,
            "revoked_at": row.revoked_at,
            "created_at": row.created_at,
        }
        if include_token is not None:
            # The ONLY place the plaintext ever leaves the service.
            data["token"] = include_token
        return data
