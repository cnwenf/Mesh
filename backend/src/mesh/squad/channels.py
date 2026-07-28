"""Per-channel subscription authorization for ``squad:{squad_id}`` (README §6.7).

A subscriber may join a squad channel only if, in some workspace they belong
to, they are an active member of that squad (any role, incl. observer) or a
workspace admin/owner. Re-checked on every subscribe; the channel string is
never the tenant boundary (README §6.2 rule 8).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.member import Member
from mesh.db.models.squad import Squad, SquadMember
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_squad_checkers(
    authorizer: _CheckerRegistrar, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    authorizer.register_prefix_checker("squad", make_squad_channel_checker(session_factory))


def make_squad_channel_checker(
    session_factory: async_sessionmaker[AsyncSession],
) -> PrefixChecker:
    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None or info.entity != "squad":
            return False
        try:
            squad_id = uuid.UUID(info.key)
        except ValueError:
            return False
        try:
            user_id = uuid.UUID(principal.subject)
        except ValueError:
            return True  # dev principal: workspace-scoped by definition
        for workspace_id in sorted(principal.workspace_ids):
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                member = await session.scalar(
                    select(Member).where(
                        Member.workspace_id == workspace_id,
                        Member.user_id == user_id,
                        Member.status == "active",
                    )
                )
                if member is None:
                    continue
                if member.role in ("admin", "owner"):
                    squad_exists = await session.scalar(
                        select(Squad.id).where(
                            Squad.workspace_id == workspace_id,
                            Squad.id == squad_id,
                            Squad.deleted_at.is_(None),
                        )
                    )
                    if squad_exists is not None:
                        return True
                    continue
                on_squad = await session.scalar(
                    select(SquadMember.id).where(
                        SquadMember.workspace_id == workspace_id,
                        SquadMember.squad_id == squad_id,
                        SquadMember.member_id == member.id,
                        SquadMember.left_at.is_(None),
                    )
                )
                if on_squad is not None:
                    return True
        return False

    return check


__all__ = ["make_squad_channel_checker", "register_squad_checkers"]
