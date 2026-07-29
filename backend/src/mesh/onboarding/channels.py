"""Onboarding realtime channel naming (onboarding.md §3.7).

Member-private channel ``member:{member_id}:onboarding`` — mirrors the inbox
channel shape; subscription authorization resolves ownership from the roster
(README §6.7 resource-level authorization, §6.2 rule 8).
"""

from __future__ import annotations

import uuid

ONBOARDING_SUFFIX = ":onboarding"


def onboarding_channel(member_id: uuid.UUID) -> str:
    """The member-private onboarding progress channel."""
    return f"member:{member_id}{ONBOARDING_SUFFIX}"


__all__ = ["ONBOARDING_SUFFIX", "onboarding_channel"]
