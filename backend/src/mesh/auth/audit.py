"""Append-only audit trail writer (auth.md §2.6/§5.5).

Every sensitive action (role changes, workspace deletion, invitation
revocation, ...) is recorded here inside the caller's transaction, so the
audit row commits atomically with the action it describes.

Actor model is de-polymorphised (README §6.1): ``actor_member_id`` names the
roster entry; human vs agent is derived by JOINing ``members.member_type``,
never stored. ``actor_kind='system'`` covers non-member actions (sweeps,
retention) with a NULL actor. Immutability is enforced by the database
(trigger + revoked privileges), not by this module.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.audit import AuditLog


async def write_audit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    actor_member_id: uuid.UUID | None,
    actor_kind: Literal["member", "system"],
    action: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """Insert one audit row in the caller's transaction and return it."""
    entry = AuditLog(
        workspace_id=workspace_id,
        actor_member_id=actor_member_id,
        actor_kind=actor_kind,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_=metadata or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(entry)
    await session.flush()
    return entry
