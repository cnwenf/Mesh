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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth.security import hash_token
from mesh.db.models.api_token import RUNTIME_TOKEN_PREFIX
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


def assert_daemon_tls(
    request: Request, *, tls_required: bool, trusted_proxies: str = "127.0.0.1,::1"
) -> None:
    """NEW-M3 red line: machine API is TLS-only.

    Plaintext transport of claim/refetch responses would expose credential
    material; refuse anything that is not HTTPS — directly terminated, or
    reported via ``X-Forwarded-Proto`` BY A TRUSTED PROXY ONLY (review M3:
    the raw header from an arbitrary client is spoofable and must not bypass
    the gate).
    """
    if not tls_required:
        return
    if request.url.scheme == "https":
        return
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    peer = request.client.host if request.client else None
    trusted = {p.strip() for p in trusted_proxies.split(",") if p.strip()}
    if forwarded == "https" and peer in trusted:
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
                    "SELECT id, workspace_id, status, deleted_at "
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
        # §2.4 S-11: daemon_auth only validates the runtime hash — no
        # api_tokens backstop (single source of truth). Pause/decommission
        # clears runtime_token_hash; the hash comparison below is the gate.

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
    assert_daemon_tls(
        request,
        tls_required=settings.daemon_tls_required,
        trusted_proxies=settings.daemon_trusted_proxies,
    )
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("missing bearer token")
    return await resolve_runtime_token(request.app.state.session_factory, token.strip())


# ---------------------------------------------------------------------------
# §2.2 S-05 / auth.md §2.5.1: task principal (mesh_task_ prefix).
# Routes that explicitly declare task principal support use this dependency.
# The unified Bearer chain discriminates by prefix: mesh_task_ → task token
# validation → frozen scope enforcement.
# ---------------------------------------------------------------------------

TASK_TOKEN_PREFIX = "mesh_task_"


async def resolve_task_principal(request: Request):
    """FastAPI dependency for routes accepting ``mesh_task_`` task tokens.

    §2.2 S-05 / auth.md §2.5.1: validates the task token (not expired,
    not revoked, attempt in-flight, lease_seq, runtime attribution,
    resource scope). Returns the AttemptTaskToken row on success.

    Only routes that explicitly declare task principal support should
    use this dependency — regular console routes reject mesh_task_.
    """
    from mesh.db.models.runtime import AttemptTaskToken
    from mesh.db.tenant import set_tenant_context
    from mesh.runtime.task_tokens import _hash_token, validate_task_token

    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("missing bearer token")
    token = token.strip()
    if not token.startswith(TASK_TOKEN_PREFIX):
        raise UnauthorizedError("not a task token")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Preliminary lookup to get workspace_id for tenant context.
        # attempt_task_tokens has no RLS, so this works without GUC.
        token_hash = _hash_token(token)
        row = (
            await session.execute(
                select(AttemptTaskToken.workspace_id).where(
                    AttemptTaskToken.token_hash == token_hash,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise UnauthorizedError("invalid task token")
        # Set tenant context BEFORE validate_task_token queries
        # execution_attempts (which has RLS on workspace_id).
        await set_tenant_context(session, row)
        task_token = await validate_task_token(session, token=token)
        return task_token
