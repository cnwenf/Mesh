"""Member role changes — audited, event-sourced (member.md §3.3/§4.4).

The workspace increment owns the role-change path because workspace.md scope
requires the RBAC matrix, role-change audit and the member.role_changed event;
the member.md increment extends the roster endpoints (status, display name,
removal, reassignment) on top of this foundation.

Protections are server-enforced (UI disabling is not trusted, member.md §5.3):
last owner cannot be demoted; agents can never be owners (DB CHECK backstop).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from mesh.auth.audit import write_audit
from mesh.auth.rbac import role_satisfies
from mesh.db.models.member import MEMBER_ROLE_VALUES, Member
from mesh.db.tenant import set_tenant_context
from mesh.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from mesh.outbox.service import emit_realtime
from mesh.workspace.service import WORKSPACE_CHANNEL


def _member_dict(member: Member) -> dict:
    return {
        "id": member.id,
        "member_type": member.member_type,
        "role": member.role,
        "status": member.status,
    }


async def change_member_role(
    session_factory: async_sessionmaker,
    *,
    actor: Member,
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    new_role: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Change a roster entry's role; audit + emit on actual change.

    No-op when the role is unchanged (§6.9: no diff → no event, no audit).
    """
    if new_role not in MEMBER_ROLE_VALUES:
        raise ValidationError(
            "invalid role", details={"role": new_role, "allowed": list(MEMBER_ROLE_VALUES)}
        )

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        if not role_satisfies(actor.role, "workspace:manage_members"):
            raise ForbiddenError("managing members requires the admin role")

        target = await session.get(Member, member_id)
        if (
            target is None
            or target.workspace_id != workspace_id
            or target.status == "removed"
        ):
            raise NotFoundError("member not found")

        if target.role == new_role:
            return _member_dict(target)  # no-op: no event, no audit

        if target.member_type == "agent" and new_role == "owner":
            raise ConflictError(
                "agents cannot hold the owner role",
                code="agent_owner_not_allowed",
            )

        if target.role == "owner" and new_role != "owner":
            active_owners = await session.scalar(
                select(func.count(Member.id)).where(
                    Member.workspace_id == workspace_id,
                    Member.role == "owner",
                    Member.status == "active",
                )
            )
            if active_owners <= 1:
                raise ConflictError(
                    "cannot demote the last owner of the workspace",
                    code="last_owner",
                )

        old_role = target.role
        target.role = new_role
        target.updated_at = datetime.now(UTC)
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=WORKSPACE_CHANNEL.format(workspace_id=workspace_id),
            event="member.role_changed",
            data={
                "member_id": str(target.id),
                "old_role": old_role,
                "new_role": new_role,
            },
        )
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_member_id=actor.id,
            actor_kind="member",
            action="member.role_changed",
            resource_type="member",
            resource_id=target.id,
            metadata={
                "target_member_id": str(target.id),
                "old_role": old_role,
                "new_role": new_role,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        result = _member_dict(target)
    return result
