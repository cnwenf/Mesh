"""Display-name resolution tests (member.md §2.4 — 唯一解析顺序).

All UI and API surfaces resolve display names through one server-side
function: ``display_override`` → human ``users.display_name`` → ``users.full_name``
(or agent ``agents.name``). The resolver is pure (no DB) so the order can be
pinned exhaustively.
"""

from __future__ import annotations

import uuid

import pytest

from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.member.display import resolve_display_name

pytestmark = pytest.mark.unit


def _member(
    member_type: str = "human",
    display_override: str | None = None,
) -> Member:
    return Member(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        member_type=member_type,
        user_id=uuid.uuid4() if member_type == "human" else None,
        agent_id=uuid.uuid4() if member_type == "agent" else None,
        role="member",
        status="active",
        display_override=display_override,
    )


def _user(display_name: str = "Jane Doe", email: str = "jane@acme.com") -> User:
    return User(id=uuid.uuid4(), email=email, display_name=display_name)


def test_override_wins_over_user_display_name():
    member = _member(display_override="小李")
    result = resolve_display_name(member=member, user=_user(display_name="Jane Doe"))
    assert result == "小李"


def test_override_wins_for_agent_rows_too():
    member = _member(member_type="agent", display_override="代码助手(本区)")
    result = resolve_display_name(member=member, user=None, agent_name="代码助手")
    assert result == "代码助手(本区)"


def test_empty_override_is_ignored():
    member = _member(display_override="")
    result = resolve_display_name(member=member, user=_user(display_name="Jane Doe"))
    assert result == "Jane Doe"


def test_human_falls_back_to_user_display_name():
    member = _member()
    result = resolve_display_name(member=member, user=_user(display_name="Jane Doe"))
    assert result == "Jane Doe"


def test_human_without_user_row_falls_back_to_email_local_part():
    # users row vanished (ON DELETE CASCADE race) — never render "None".
    member = _member()
    user = _user(display_name="", email="someone@acme.com")
    result = resolve_display_name(member=member, user=user)
    assert result == "someone"


def test_human_without_any_profile_renders_member_placeholder():
    member = _member()
    result = resolve_display_name(member=member, user=None)
    assert result == f"member-{str(member.id)[:8]}"


def test_agent_uses_agent_name_when_available():
    member = _member(member_type="agent")
    result = resolve_display_name(member=member, user=None, agent_name="代码助手")
    assert result == "代码助手"


def test_agent_without_agents_row_falls_back_to_short_id():
    # The agents table lands with the agent.md increment; until then a roster
    # row's display falls back to a stable short-id label (never empty).
    member = _member(member_type="agent")
    result = resolve_display_name(member=member, user=None, agent_name=None)
    assert result == f"agent-{str(member.agent_id)[:8]}"


def test_status_does_not_affect_resolution():
    member = _member(display_override="昵称")
    member.status = "disabled"
    result = resolve_display_name(member=member, user=_user())
    assert result == "昵称"
