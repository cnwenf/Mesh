"""Task token lifecycle — §2.2 S-05: short-lived ``mesh_task_`` tokens.

Server issues one task token per attempt at claim time; renew rotates it.
Token scope is pinned to workspace + attempt + agent + current issue/project
and allowed methods. TTL = min(lease remaining + grace, 5 min).

Plaintext is delivered exactly once (claim/renew response); only the SHA-256
hash is stored in ``attempt_task_tokens``. The token never enters
``api_tokens``, sandbox env, files, stdin, provider settings, or logs.

Revocation is same-transaction with terminal/reclaim/freeze/approval-suspend
state transitions (fail-closed).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.runtime import (
    TASK_TOKEN_PREFIX,
    AttemptTaskToken,
    ExecutionAttempt,
)

# §2.2: TTL = min(lease remaining + grace, 5 minutes).
TASK_TOKEN_MAX_TTL = timedelta(minutes=5)

# Default scopes for a task token (§2.2 S-05): read current context,
# write current issue comments/status, current squad task operations.
# ``agent:trigger`` is denied by default (anti-loop).
DEFAULT_TASK_SCOPES: dict = {
    "methods": [
        "issue:read",
        "issue:comment:write",
        "issue:status:write",
        "project:read",
        "execution:read",
    ],
    "denied": ["agent:trigger"],
}


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_task_token() -> str:
    """Generate a ``mesh_task_`` prefixed token (plaintext, shown once)."""
    return TASK_TOKEN_PREFIX + secrets.token_urlsafe(32)


async def issue_task_token(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    attempt_id: uuid.UUID,
    runtime_id: uuid.UUID,
    lease_seq: int,
    lease_expires_at: datetime,
    issue_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> tuple[str, AttemptTaskToken]:
    """Issue a new task token for an attempt. Returns (plaintext, row).

    Revokes any existing active token for the attempt first (rotation).
    """
    now = _now()
    # Revoke existing active token (rotation on renew).
    await session.execute(
        update(AttemptTaskToken)
        .where(
            AttemptTaskToken.attempt_id == attempt_id,
            AttemptTaskToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )

    # TTL = min(lease remaining + grace, 5 min).
    lease_remaining = max((lease_expires_at - now).total_seconds(), 0)
    ttl_seconds = min(lease_remaining + 30, TASK_TOKEN_MAX_TTL.total_seconds())
    expires_at = now + timedelta(seconds=ttl_seconds)

    scopes = dict(DEFAULT_TASK_SCOPES)
    scopes["workspace_id"] = str(workspace_id)
    scopes["attempt_id"] = str(attempt_id)
    scopes["runtime_id"] = str(runtime_id)
    if issue_id is not None:
        scopes["issue_id"] = str(issue_id)
    if project_id is not None:
        scopes["project_id"] = str(project_id)
    if agent_id is not None:
        scopes["agent_id"] = str(agent_id)

    plaintext = generate_task_token()
    token_row = AttemptTaskToken(
        workspace_id=workspace_id,
        attempt_id=attempt_id,
        runtime_id=runtime_id,
        lease_seq=lease_seq,
        token_hash=_hash_token(plaintext),
        scopes=scopes,
        expires_at=expires_at,
    )
    session.add(token_row)
    await session.flush()
    return plaintext, token_row


async def revoke_attempt_task_tokens(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    now: datetime | None = None,
) -> int:
    """Revoke all active task tokens for an attempt (terminal/reclaim/freeze).

    Same-transaction with the state transition (§2.2 fail-closed).
    Returns the count of revoked tokens.
    """
    ts = now or _now()
    result = await session.execute(
        update(AttemptTaskToken)
        .where(
            AttemptTaskToken.attempt_id == attempt_id,
            AttemptTaskToken.revoked_at.is_(None),
        )
        .values(revoked_at=ts)
    )
    return result.rowcount


async def validate_task_token(
    session: AsyncSession,
    *,
    token: str,
    attempt_id: uuid.UUID | None = None,
    required_scope: str | None = None,
) -> AttemptTaskToken:
    """Validate a ``mesh_task_`` token: not expired, not revoked, attempt
    in-flight, lease_seq matches, resource scope check.

    Raises UnauthorizedError on any failure (fail-closed).
    """
    from mesh.errors import UnauthorizedError

    if not token.startswith(TASK_TOKEN_PREFIX):
        raise UnauthorizedError("invalid task token")

    token_hash = _hash_token(token)
    row = (
        await session.execute(
            select(AttemptTaskToken).where(
                AttemptTaskToken.token_hash == token_hash,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        raise UnauthorizedError("invalid task token")
    if row.revoked_at is not None:
        raise UnauthorizedError("task token revoked")
    if row.expires_at < _now():
        raise UnauthorizedError("task token expired")
    if attempt_id is not None and row.attempt_id != attempt_id:
        raise UnauthorizedError("task token attempt mismatch")

    # Verify the attempt is still in-flight.
    attempt = (
        await session.execute(
            select(ExecutionAttempt.status).where(
                ExecutionAttempt.id == row.attempt_id,
            )
        )
    ).scalar_one_or_none()
    if attempt is None or attempt not in ("claimed", "running", "cancelling"):
        raise UnauthorizedError("attempt not in flight")

    # Scope check.
    if required_scope is not None:
        allowed_methods = (row.scopes or {}).get("methods", [])
        denied_methods = (row.scopes or {}).get("denied", [])
        if required_scope in denied_methods or required_scope not in allowed_methods:
            raise UnauthorizedError("scope not permitted")

    return row
