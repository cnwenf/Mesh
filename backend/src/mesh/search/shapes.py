"""Search result rendering: structured context, badges, canonical URLs (§3.2).

The server returns stable keys + structured data ONLY — visible sentences
are assembled by the frontend from the message catalog (§6.18). Highlight
offsets are Unicode code points into the ORIGINAL title (§3.2).
"""

from __future__ import annotations

from typing import Any

from mesh.search.scoring import highlight_ranges

ICONS = {
    "issue": "issue",
    "member": "member",
    "agent": "agent",
    "project": "project",
    "view": "view",
    "chat_session": "chat",
}

# status category → semantic color token name (§6.12 palette, §3.2 badge).
STATUS_CATEGORY_COLORS = {
    "backlog": "info",
    "todo": "info",
    "in_progress": "info",
    "in_review": "warn",
    "blocked": "danger",
    "done": "success",
    "cancelled": "status",
}


def canonical_url(result_type: str, payload: dict[str, Any], workspace_slug: str) -> str:
    """§3.4 canonical deep link for a result row."""
    ws = workspace_slug
    if result_type == "issue":
        return f"/w/{ws}/issues/by-identifier/{payload['identifier']}"
    if result_type in ("member", "agent"):
        return f"/w/{ws}/members/{payload['row_id']}"
    if result_type == "project":
        return f"/w/{ws}/projects/{payload['row_id']}"
    if result_type == "view":
        return f"/w/{ws}/views/{payload['row_id']}"
    if result_type == "chat_session":
        return f"/w/{ws}/chat/{payload['row_id']}"
    raise ValueError(f"unrenderable search result type: {result_type}")


def result_context(result_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Per-type structured context the frontend localizes into a subtitle."""
    if result_type == "issue":
        return {
            "identifier": payload["identifier"],
            "project": (
                {"id": str(payload["project_id"]), "name": payload["project_name"]}
                if payload.get("project_id") is not None
                else None
            ),
            "status": {
                "id": str(payload["status_id"]) if payload.get("status_id") else None,
                "name": payload.get("status_name") or "",
                "category": payload.get("state_category") or "todo",
            },
        }
    if result_type in ("member", "agent"):
        context: dict[str, Any] = {
            "member_type": "agent" if result_type == "agent" else "human",
            "role": payload["role"],
        }
        if result_type == "agent":
            capacity = payload.get("capacity")
            if capacity is not None:
                context["capacity"] = capacity
            if payload.get("lifecycle_status"):
                context["lifecycle_status"] = payload["lifecycle_status"]
        return context
    if result_type == "project":
        return {"visibility": payload["visibility"], "key": payload["key"]}
    if result_type == "view":
        context = {
            "scope": "project" if payload.get("project_id") is not None else "workspace",
            "owner_only": payload.get("visibility") == "private",
        }
        if payload.get("project_id") is not None:
            context["project"] = {
                "id": str(payload["project_id"]),
                "name": payload.get("project_name") or "",
            }
        return context
    if result_type == "chat_session":
        context = {"participants_count": 2 if payload.get("agent_id") else 1}
        if payload.get("agent_id"):
            context["agent"] = {
                "id": str(payload["agent_id"]),
                "name": payload.get("agent_name") or "",
            }
        return context
    raise ValueError(f"unrenderable search result type: {result_type}")


def result_badge(result_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Badge as message-catalog key + params (§3.2 / §6.18)."""
    if result_type == "issue":
        category = payload.get("state_category") or "todo"
        return {
            "kind": "status",
            "label_key": "issue.status.name",
            "label_params": {"name": payload.get("status_name") or ""},
            "color": STATUS_CATEGORY_COLORS.get(category, "info"),
        }
    if result_type in ("member", "agent"):
        is_agent = result_type == "agent"
        return {
            "kind": "member_type",
            "label_key": "member.type.agent" if is_agent else "member.type.human",
            "label_params": {},
            "color": "info",
        }
    if result_type == "project":
        visibility = payload.get("visibility") or "public"
        return {
            "kind": "visibility",
            "label_key": f"project.visibility.{visibility}",
            "label_params": {},
            "color": "warn" if visibility == "private" else "info",
        }
    if result_type == "view":
        scope = "project" if payload.get("project_id") is not None else "workspace"
        return {
            "kind": "view_scope",
            "label_key": f"view.scope.{scope}",
            "label_params": {},
            "color": "info",
        }
    return None


def render_result(
    row: dict[str, Any], *, workspace_slug: str, query: str
) -> dict[str, Any]:
    """One wire-format result item (§3.2 unified shape)."""
    result_type = row["type"]
    payload = row["payload"]
    title = row["title"]
    ranges = highlight_ranges(title, query)
    item: dict[str, Any] = {
        "type": result_type,
        "id": str(row["id"]),
        "title": title,
        "context": result_context(result_type, payload),
        "icon": ICONS[result_type],
        "url": canonical_url(result_type, payload, workspace_slug),
    }
    badge = result_badge(result_type, payload)
    if badge is not None:
        item["badge"] = badge
    if ranges:
        item["highlight"] = {
            "title": {"unit": "codepoint", "ranges": [list(r) for r in ranges]}
        }
    return item
