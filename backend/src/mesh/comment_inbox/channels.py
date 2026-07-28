"""Per-channel authorization for member-private ``member:{member_id}:*`` channels.

Covers the inbox (``member:{id}:inbox``) and the onboarding progress channel
(``member:{id}:onboarding``, onboarding.md §3.7): both are OWNER-ONLY, so
every subscription re-checks that the principal owns that member row
(README §6.7 — the channel string is never the isolation boundary).
Registered on BOTH the API and the realtime gateway factories so the
independently-deployed processes cannot drift (CWE-862).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select

from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import MEMBER_PRIVATE_SUFFIXES, PrefixChecker, Principal
from mesh.realtime.channels import parse_channel

INBOX_SUFFIX = ":inbox"


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_inbox_checkers(authorizer: _CheckerRegistrar, session_factory) -> None:
    """Register the ``member`` entity checker everywhere at once."""
    authorizer.register_prefix_checker("member", make_inbox_channel_checker(session_factory))


def make_inbox_channel_checker(session_factory) -> PrefixChecker:
    """Build the ``member`` entity checker bound to a session factory."""

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        suffix = next(
            (s for s in MEMBER_PRIVATE_SUFFIXES if info.key.endswith(s)), None
        )
        if suffix is None:
            return False
        member_raw = info.key[: -len(suffix)]
        try:
            member_id = uuid.UUID(member_raw)
        except ValueError:
            return False
        try:
            user_id = uuid.UUID(principal.subject)
        except ValueError:
            # Development principal (auth_mode=dev): workspace-scoped by
            # definition (same convention as the issue checker).
            return True
        for workspace_id in sorted(principal.workspace_ids):
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                member = await session.scalar(
                    select(Member).where(
                        Member.id == member_id,
                        Member.workspace_id == workspace_id,
                        Member.status == "active",
                    )
                )
                if member is not None and member.user_id == user_id:
                    return True
        return False

    return check


__all__ = ["make_inbox_channel_checker", "register_inbox_checkers"]
