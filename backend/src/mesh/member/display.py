"""Display-name resolution — the single authoritative order (member.md §2.4).

Every UI and API surface resolves a member's display name through
:func:`resolve_display_name` so the order never drifts between surfaces:

1. ``members.display_override`` (workspace-scoped override, when non-empty) →
2. human: ``users.display_name`` (the auth.md single name field; the
   ``users.full_name`` named in member.md §2.4 maps onto it — auth.md owns the
   users table and ships one name column) → email local part → short id;
   agent: ``agents.name`` → short id (the agents table lands with the agent.md
   increment; until then the fallback keeps the roster human-readable).

The roster itself never stores profile fields — names are resolved by JOINing
the identity tables at read time (member.md §2.4: no double-write drift).
"""

from __future__ import annotations

from mesh.db.models.member import Member
from mesh.db.models.user import User

# Placeholder prefix length — short id slices stay recognizable in a roster
# row without leaking full internal ids into display surfaces.
_SHORT_ID_LENGTH = 8


def _email_local_part(email: str | None) -> str | None:
    if not email:
        return None
    local = email.split("@", 1)[0].strip()
    return local or None


def resolve_display_name(
    *,
    member: Member,
    user: User | None,
    agent_name: str | None = None,
) -> str:
    """Resolve the single ``display_name`` for a roster row (member.md §2.4).

    ``user`` is the JOINed users row for human members (None if the row is
    gone); ``agent_name`` is the JOINed agents.name for agent members (None
    until the agents table exists). Never returns an empty string.
    """
    override = (member.display_override or "").strip()
    if override:
        return override

    if member.member_type == "human":
        if user is not None:
            name = (user.display_name or "").strip()
            if name:
                return name
        fallback = _email_local_part(user.email if user is not None else None)
        if fallback:
            return fallback
        return f"member-{str(member.id)[:_SHORT_ID_LENGTH]}"

    # agent member
    name = (agent_name or "").strip()
    if name:
        return name
    return f"agent-{str(member.agent_id)[:_SHORT_ID_LENGTH]}"
