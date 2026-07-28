"""Per-channel authorization for the chat realtime channels (README §6.7).

Two resource-scoped channels, each with an explicit checker registered on
BOTH the API and the realtime gateway factories so the independently-deployed
processes cannot drift (CWE-862); the channel string is never the isolation
boundary:

- ``chat_session:{session_id}`` — the requester must OWN the session
  (chat-session.md §3.5 — sessions are owner-only).
- ``chat_list:{owner_member_id}`` — the owner-private session-list channel
  (H2 fix). Only the member whose id is in the key may subscribe; it carries
  the live list preview with content stripped. This replaced the earlier
  workspace-wide ``workspace:{ws}:chat_sessions`` list channel, which leaked
  every member's private session terminal events (incl. interrupted partial
  content) to any co-tenant subscriber, so it was removed.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select

from mesh.db.models.chat import ChatSession
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_chat_checkers(authorizer: _CheckerRegistrar, session_factory) -> None:
    """Register the chat entity checkers everywhere at once."""
    authorizer.register_prefix_checker("chat_session", make_chat_session_checker(session_factory))
    authorizer.register_prefix_checker("chat_list", make_chat_list_checker(session_factory))


def make_chat_list_checker(session_factory) -> PrefixChecker:
    """``chat_list:{owner_member_id}`` — H1 owner-private session-list channel.

    Only the member whose id is in the key (i.e. the principal's own user) may
    subscribe, mirroring the inbox checker. This is what carries the live list
    preview; because it is owner-scoped it never leaks another member's private
    sessions (the earlier workspace-wide channel did, hence its removal).
    """

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        try:
            member_id = uuid.UUID(info.key)
        except ValueError:
            return False
        try:
            user_id = uuid.UUID(principal.subject)
        except ValueError:
            # Development principal (auth_mode=dev): workspace-scoped by
            # definition (same convention as the inbox checker).
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


def make_chat_session_checker(session_factory) -> PrefixChecker:
    """``chat_session:{id}`` — only the session owner may subscribe."""

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        try:
            session_id = uuid.UUID(info.key)
        except ValueError:
            return False
        try:
            user_id = uuid.UUID(principal.subject)
        except ValueError:
            # Development principal (auth_mode=dev): workspace-scoped by
            # definition (same convention as the inbox checker).
            return True
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
                owner_id = await session.scalar(
                    select(ChatSession.owner_id).where(
                        ChatSession.workspace_id == workspace_id,
                        ChatSession.id == session_id,
                    )
                )
                if owner_id is not None and owner_id == member.id:
                    return True
        return False

    return check


__all__ = [
    "make_chat_list_checker",
    "make_chat_session_checker",
    "register_chat_checkers",
]
