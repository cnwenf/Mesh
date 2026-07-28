"""Binding match rules (integrations.md §2.6 / §2.7).

``match_config`` fields combine as AND across fields, OR within multi-value
fields. The keywords/patterns are MATCH CONDITIONS ONLY — they never enter
the agent context (README §6.15); the inbound message text does, structurally
isolated as untrusted data.

IM bindings:
* ``trigger_on`` ⊆ {mention, direct_message, keyword} — which signal fires
  the binding (default: mention OR direct_message, per the §6.9 「外部 IM
  消息触发」 row: @agent or DM);
* ``mention_agents`` — when set, the binding's target agent must be listed
  for a mention to fire (unmatched → audit only, no trigger, §6.9);
* ``keyword_include`` / ``keyword_exclude`` — exclude wins.

VCS bindings:
* ``vcs_events`` — action-qualified event names
  (``pull_request.closed`` / ``merge_request.merged`` / ``push`` …);
* ``branch_pattern`` — regex over the source/head branch (invalid regex is
  rejected at binding creation, never at ingestion);
* ``auto_status_map`` — consumed by vcs_links auto status flow, not matching.
"""

from __future__ import annotations

import re
from typing import Any

from mesh.integrations.connectors import NormalizedEvent

DEFAULT_IM_TRIGGERS = ("mention", "direct_message")

IM_PROVIDERS = frozenset({"feishu", "slack"})
VCS_PROVIDERS = frozenset({"github", "gitlab"})


def vcs_event_name(provider: str, event: NormalizedEvent) -> str:
    """Action-qualified VCS event name for ``vcs_events`` matching.

    github ``pull_request`` + action ``closed`` (merged) → both
    ``pull_request.closed`` and ``pull_request.merged`` qualify; ``push``
    stays bare. gitlab ``Merge Request Hook`` maps onto ``merge_request.*``.
    """
    action = str(event.extra.get("action") or "").lower()
    if provider == "github":
        base = event.event_type  # pull_request / push / issues / commit_comment …
        names = {base}
        if action:
            names.add(f"{base}.{action}")
        if base == "pull_request" and event.extra.get("pr_merged"):
            names.add("pull_request.merged")
        return "|".join(sorted(names))
    if provider == "gitlab":
        raw = event.event_type  # "Merge Request Hook" / "Push Hook" …
        normalized = raw.lower().replace(" hook", "").replace(" ", "_")
        if normalized == "merge_request":
            base = "merge_request"
        elif normalized == "push":
            base = "push"
        else:
            base = normalized
        names = {base}
        if action:
            names.add(f"{base}.{action}")
        if base == "merge_request" and event.extra.get("mr_state") == "merged":
            names.add("merge_request.merged")
        return "|".join(sorted(names))
    return event.event_type


def vcs_event_matches(allowed: list[Any], qualified_names: str) -> bool:
    """True when any of the event's qualified names is in the allow-list."""
    if not allowed:
        return True
    names = set(qualified_names.split("|"))
    return any(str(entry) in names for entry in allowed)


def branch_matches(pattern: Any, branch: str | None) -> bool:
    """Regex branch filter (empty pattern = no filter)."""
    if not pattern:
        return True
    if not branch:
        return False
    try:
        return re.search(str(pattern), branch) is not None
    except re.error:
        # Invalid patterns are rejected at binding creation; at ingestion a
        # leftover bad pattern fails CLOSED (no trigger), never raises.
        return False


def _keyword_match(config: dict[str, Any], text: str) -> bool:
    """keyword_include/keyword_exclude — exclude wins; empty include = any."""
    haystack = text.lower()
    excludes = [str(k).lower() for k in (config.get("keyword_exclude") or []) if str(k)]
    if any(k and k in haystack for k in excludes):
        return False
    includes = [str(k).lower() for k in (config.get("keyword_include") or []) if str(k)]
    if not includes:
        return True
    return any(k in haystack for k in includes)


def im_binding_matches(
    config: dict[str, Any],
    *,
    text: str,
    bot_mentioned: bool,
    is_direct_message: bool,
    bound_agent_id: str | None,
) -> bool:
    """IM match decision (§2.7: trigger_on + mention_agents + keywords)."""
    triggers = [str(t) for t in (config.get("trigger_on") or DEFAULT_IM_TRIGGERS)]
    fired = False
    if "mention" in triggers and bot_mentioned:
        mention_agents = [str(a) for a in (config.get("mention_agents") or []) if str(a)]
        if not mention_agents or (bound_agent_id is not None and bound_agent_id in mention_agents):
            fired = True
    if "direct_message" in triggers and is_direct_message:
        fired = True
    if "keyword" in triggers and _keyword_match(config, text):
        fired = True
    return fired


def vcs_binding_matches(
    config: dict[str, Any], *, provider: str, event: NormalizedEvent
) -> bool:
    """VCS match decision (§2.7: vcs_events + branch_pattern)."""
    qualified = vcs_event_name(provider, event)
    if not vcs_event_matches(list(config.get("vcs_events") or []), qualified):
        return False
    branch = str(
        event.extra.get("source_branch")
        or event.extra.get("ref")
        or ""
    )
    # Strip git ref prefixes (refs/heads/x → x) for pattern ergonomics.
    if branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/"):]
    return branch_matches(config.get("branch_pattern"), branch or None)


def binding_matches(
    provider: str,
    config: dict[str, Any],
    event: NormalizedEvent,
    *,
    bot_mentioned: bool = False,
    is_direct_message: bool = False,
    bound_agent_id: str | None = None,
) -> bool:
    """Provider-dispatched match decision for one binding row."""
    if provider in VCS_PROVIDERS:
        return vcs_binding_matches(config, provider=provider, event=event)
    if provider in IM_PROVIDERS:
        return im_binding_matches(
            config,
            text=event.text,
            bot_mentioned=bot_mentioned,
            is_direct_message=is_direct_message,
            bound_agent_id=bound_agent_id,
        )
    return False


def compute_im_signals(provider: str, event: NormalizedEvent, config: dict[str, Any]) -> tuple[bool, bool]:
    """Derive (bot_mentioned, is_direct_message) from the normalized event.

    Slack: bot mention = ``<@U0BOT>`` in text (``bot_user_id`` from the
    integration config) or a direct-message channel (``D*``); feishu: any
    mention entry or a ``p2p`` chat counts.
    """
    if provider == "slack":
        bot_user_id = str(config.get("bot_user_id") or "")
        mentioned = bool(bot_user_id and f"<@{bot_user_id}>" in event.text)
        if not mentioned and bot_user_id:
            mentions = event.extra.get("mentions") or []
            mentioned = any(bot_user_id in str(m) for m in mentions)
        is_dm = event.external_ref.startswith("D") or event.extra.get("subtype") == "im"
        return mentioned, bool(is_dm)
    if provider == "feishu":
        mentions = event.extra.get("mentions") or []
        mentioned = len(mentions) > 0 or "@" in event.text
        is_dm = str(event.extra.get("chat_type") or "") == "p2p"
        return mentioned, is_dm
    return False, False


def validate_match_config(provider: str, config: dict[str, Any]) -> None:
    """Binding-creation validation (invalid regex → 422 at create time)."""
    from mesh.errors import BusinessRuleError

    pattern = config.get("branch_pattern")
    if pattern:
        try:
            re.compile(str(pattern))
        except re.error as exc:
            raise BusinessRuleError(
                "invalid branch_pattern regex",
                code="invalid_request",
                details={"branch_pattern": str(pattern), "reason": str(exc)},
            ) from exc
    triggers = config.get("trigger_on")
    if triggers is not None:
        allowed = {"mention", "direct_message", "keyword"}
        bad = [t for t in triggers if str(t) not in allowed]
        if bad:
            raise BusinessRuleError(
                "invalid trigger_on values",
                code="invalid_request",
                details={"invalid": bad, "allowed": sorted(allowed)},
            )


__all__ = [
    "DEFAULT_IM_TRIGGERS",
    "binding_matches",
    "branch_matches",
    "compute_im_signals",
    "im_binding_matches",
    "validate_match_config",
    "vcs_binding_matches",
    "vcs_event_matches",
    "vcs_event_name",
]
