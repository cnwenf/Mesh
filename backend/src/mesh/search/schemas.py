"""Search API constants + result-shape builders (spec §3.2/§3.4/§3.5).

The server returns ONLY stable keys + structured data — visible sentences
are assembled by the frontend from the i18n catalog (§6.18): badge text
travels as ``label_key`` + ``label_params``; deep links use the workspace
SLUG (§3.4 canonical form).
"""

from __future__ import annotations

# -- Request contract ---------------------------------------------------------
SEARCH_TYPES: tuple[str, ...] = (
    "issue",
    "member",
    "agent",
    "project",
    "view",
    "chat_session",
)
MAX_QUERY_LENGTH = 120  # code points (§3.2)
DEFAULT_LIMIT = 20
MAX_LIMIT = 50

# -- Badge i18n catalog keys (frontend localizes; §3.2 / i18n.md) -------------
BADGE_KEY_STATUS = "search.badge.status"  # "{name}"
BADGE_KEY_MEMBER_AGENT = "search.badge.memberType.agent"  # "AI"
BADGE_KEY_MEMBER_HUMAN = "search.badge.memberType.human"  # "Human"
BADGE_KEY_VISIBILITY_PRIVATE = "search.badge.visibility.private"  # "Private"

# Semantic color names only (theme.md — never raw hex).
COLORS: frozenset[str] = frozenset(
    {"info", "success", "warning", "danger", "neutral", "accent"}
)

# Issue status category → semantic badge color.
CATEGORY_COLOR: dict[str, str] = {
    "backlog": "neutral",
    "todo": "neutral",
    "in_progress": "info",
    "in_review": "accent",
    "blocked": "warning",
    "done": "success",
    "cancelled": "danger",
}


def status_badge(status_name: str, category: str) -> dict:
    """Issue status badge: catalog key + original status name param."""
    return {
        "kind": "status",
        "label_key": BADGE_KEY_STATUS,
        "label_params": {"name": status_name},
        "color": CATEGORY_COLOR.get(category, "neutral"),
    }


def member_type_badge(member_type: str) -> dict:
    """Member/agent roster badge (agent → AI, human → Human)."""
    is_agent = member_type == "agent"
    return {
        "kind": "member_type",
        "label_key": BADGE_KEY_MEMBER_AGENT if is_agent else BADGE_KEY_MEMBER_HUMAN,
        "label_params": {},
        "color": "info" if is_agent else "neutral",
    }


def private_visibility_badge() -> dict:
    """Private-project badge (existence is already filtered per §3.3)."""
    return {
        "kind": "visibility",
        "label_key": BADGE_KEY_VISIBILITY_PRIVATE,
        "label_params": {},
        "color": "warning",
    }


# -- Canonical deep links (§3.4, workspace SLUG form) --------------------------
def issue_url(slug: str, identifier: str) -> str:
    return f"/w/{slug}/issues/by-identifier/{identifier}"


def project_url(slug: str, project_id: str) -> str:
    return f"/w/{slug}/projects/{project_id}"


def member_url(slug: str, member_id: str) -> str:
    return f"/w/{slug}/members/{member_id}"


def view_url(slug: str, view_id: str) -> str:
    return f"/w/{slug}/views/{view_id}"


def chat_url(slug: str, session_id: str) -> str:
    return f"/w/{slug}/chat/{session_id}"
