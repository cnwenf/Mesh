"""Daemon (machine API) authentication — runtime.md §3.2 / §3.5.

Bearer tokens carrying the ``mesh_rt_`` prefix resolve to exactly one runtime
through the SECURITY DEFINER bootstrap function ``mesh_runtime_by_token_hash``
(RLS is fail-closed and the workspace is unknown until the lookup succeeds).
The workspace is ALWAYS taken from the stored runtime row — never from the
request body (§2.5 / §3.5 red line).

Also hosts two §3 security gates:

* **TLS enforcement (NEW-M3)** — the machine API carries credential plaintext
  in claim/refetch responses; non-TLS requests are refused with 403.
* **Injected env-name validation (NEW-M1)** — ``env_declarations`` and
  ``credentials[].env`` may not name loader / runtime / daemon-reserved
  variables (``LD_*``, ``PATH``, ``PYTHON*``, ``NODE_OPTIONS``, ``DYLD_*``,
  ``MESH_DAEMON_*``, ``MESH_INTERNAL_*``).
"""

from __future__ import annotations

import re

from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth.security import hash_token
from mesh.db.models.api_token import RUNTIME_TOKEN_PREFIX, ApiToken
from mesh.db.models.runtime import Runtime
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ForbiddenError, UnauthorizedError

# Runtime statuses allowed to call the machine API. ``pending`` has no token
# yet; ``paused`` / ``decommissioned`` / soft-deleted runtimes have their token
# revoked (NEW-L2) and are refused here as well. ``unavailable`` daemons may
# still heartbeat their way back online; ``draining`` runtimes keep reporting
# in-flight work but the claim SQL refuses them new tasks.
MACHINE_API_ALLOWED_STATUSES = frozenset({"online", "unavailable", "draining"})

ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# NEW-M1: process-loader / runtime / daemon-reserved names must never be
# injected into a task sandbox.
RESERVED_ENV_PREFIXES = ("LD_", "DYLD_", "PYTHON", "MESH_DAEMON_", "MESH_INTERNAL_")
RESERVED_ENV_EXACT = frozenset({"PATH", "NODE_OPTIONS"})


def validate_env_name(name: str) -> None:
    """Raise 422 ``reserved_env_name`` when an injected env name is unsafe."""
    if not ENV_NAME_PATTERN.match(name):
        raise BusinessRuleError(
            "invalid environment variable name",
            code="reserved_env_name",
            details={"env_name": name, "pattern": ENV_NAME_PATTERN.pattern},
        )
    upper = name.upper()
    if upper in RESERVED_ENV_EXACT or any(upper.startswith(p) for p in RESERVED_ENV_PREFIXES):
        raise BusinessRuleError(
            "reserved environment variable name",
            code="reserved_env_name",
            details={"env_name": name},
        )


def validate_env_names(names: list[str] | None) -> None:
    for name in names or []:
        validate_env_name(name)


def assert_daemon_tls(request: Request, *, tls_required: bool) -> None:
    """NEW-M3 red line: machine API is TLS-only.

    Plaintext transport of claim/refetch responses would expose credential
    material; refuse anything that is not HTTPS (directly terminated or via a
    trusted proxy's ``X-Forwarded-Proto``).
    """
    if not tls_required:
        return
    scheme = request.url.scheme
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if scheme == "https" or forwarded == "https":
        return
    raise ForbiddenError(
        "the machine API requires TLS",
        code="tls_required",
    )


async def resolve_runtime_token(
    session_factory: async_sessionmaker[AsyncSession], token: str
) -> Runtime:
    """Resolve a ``mesh_rt_`` bearer token to its runtime row.

    The workspace comes from the stored row (token-derived), never from the
    request. Raises 401 on any mismatch — one uniform message so unknown /
    revoked / decommissioned tokens are indistinguishable.
    """
    if not token.startswith(RUNTIME_TOKEN_PREFIX):
        raise UnauthorizedError("invalid runtime token")
    token_hash = hash_token(token)

    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, workspace_id, status, deleted_at, runtime_token_id "
                    "FROM mesh_runtime_by_token_hash(:h)"
                ),
                {"h": token_hash},
            )
        ).mappings().one_or_none()
        if row is None:
            raise UnauthorizedError("invalid runtime token")
        if row["deleted_at"] is not None or row["status"] not in MACHINE_API_ALLOWED_STATUSES:
            raise UnauthorizedError("invalid runtime token")

        # Tenant GUC before any RLS-guarded read (the bootstrap lookup above
        # is SECURITY DEFINER; everything below runs under the policy).
        await set_tenant_context(session, row["workspace_id"])
        # Token revocation backstop (NEW-L2): pause/decommission revokes the
        # api_tokens row; a cached runtime row must not outlive that.
        if row["runtime_token_id"] is not None:
            revoked = (
                await session.execute(
                    select(ApiToken.revoked_at).where(ApiToken.id == row["runtime_token_id"])
                )
            ).one_or_none()
            if revoked is None or revoked[0] is not None:
                raise UnauthorizedError("invalid runtime token")

        runtime = await session.get(Runtime, row["id"])
        if runtime is None or runtime.runtime_token_hash != token_hash:
            raise UnauthorizedError("invalid runtime token")
        # Detach: the route opens its own transaction per operation.
        await session.refresh(runtime)
        session.expunge(runtime)
        return runtime


async def require_runtime(request: Request) -> Runtime:
    """FastAPI dependency for every ``/api/v1/daemon/`` endpoint."""
    settings = request.app.state.settings
    assert_daemon_tls(request, tls_required=settings.daemon_tls_required)
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("missing bearer token")
    return await resolve_runtime_token(request.app.state.session_factory, token.strip())
