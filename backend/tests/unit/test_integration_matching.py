"""Binding match-rule unit tests (integrations.md §2.6/§2.7).

Pure-function coverage: trigger_on semantics, mention_agents restriction,
keyword include/exclude (exclude wins), vcs_events action-qualified filter,
branch_pattern regex + fail-closed invalid pattern, config validation.
"""

from __future__ import annotations

import pytest

from mesh.errors import BusinessRuleError
from mesh.integrations.connectors import NormalizedEvent
from mesh.integrations.matching import (
    binding_matches,
    branch_matches,
    compute_im_signals,
    im_binding_matches,
    validate_match_config,
    vcs_binding_matches,
    vcs_event_matches,
    vcs_event_name,
)

pytestmark = pytest.mark.unit


def _im_event(text: str = "hello", **extra) -> NormalizedEvent:
    return NormalizedEvent(
        external_event_id="e1", event_type="im.message.receive_v1",
        external_ref="oc_chat", actor_key="ou_1", tenant_key="tk",
        text=text, extra=extra,
    )


def _vcs_event(event_type: str, **extra) -> NormalizedEvent:
    return NormalizedEvent(
        external_event_id="e2", event_type=event_type,
        external_ref="acme/web", actor_key="dev", tenant_key="1234567",
        text=str(extra.get("pr_title") or ""), extra=extra,
    )


# ---------------------------------------------------------------------------
# IM matching
# ---------------------------------------------------------------------------


def test_im_default_triggers_on_mention():
    assert im_binding_matches(
        {}, text="hi", bot_mentioned=True, is_direct_message=False, bound_agent_id=None
    )


def test_im_default_triggers_on_dm():
    assert im_binding_matches(
        {}, text="hi", bot_mentioned=False, is_direct_message=True, bound_agent_id=None
    )


def test_im_no_mention_no_dm_no_match():
    assert not im_binding_matches(
        {}, text="hi", bot_mentioned=False, is_direct_message=False, bound_agent_id=None
    )


def test_im_mention_agents_restriction_filters_foreign_agent():
    # mention fires, but mention_agents lists a different agent → no match.
    assert not im_binding_matches(
        {"mention_agents": ["11111111-1111-1111-1111-111111111111"]},
        text="@bot", bot_mentioned=True, is_direct_message=False,
        bound_agent_id="22222222-2222-2222-2222-222222222222",
    )


def test_im_mention_agents_restriction_passes_listed_agent():
    agent = "22222222-2222-2222-2222-222222222222"
    assert im_binding_matches(
        {"mention_agents": [agent]},
        text="@bot", bot_mentioned=True, is_direct_message=False,
        bound_agent_id=agent,
    )


def test_im_keyword_include_exclude():
    config = {"trigger_on": ["keyword"], "keyword_include": ["值班", "线上"]}
    assert im_binding_matches(
        config, text="线上告警", bot_mentioned=False, is_direct_message=False,
        bound_agent_id=None,
    )
    assert not im_binding_matches(
        config, text="日常讨论", bot_mentioned=False, is_direct_message=False,
        bound_agent_id=None,
    )
    # exclude wins over include.
    config_exclude = {**config, "keyword_exclude": ["忽略"]}
    assert not im_binding_matches(
        config_exclude, text="线上告警 请忽略", bot_mentioned=False,
        is_direct_message=False, bound_agent_id=None,
    )


def test_im_trigger_on_mention_only_ignores_dm():
    assert not im_binding_matches(
        {"trigger_on": ["mention"]}, text="dm", bot_mentioned=False,
        is_direct_message=True, bound_agent_id=None,
    )


# ---------------------------------------------------------------------------
# VCS matching
# ---------------------------------------------------------------------------


def test_vcs_event_name_github_merged_pr():
    event = _vcs_event("pull_request", action="closed", pr_merged=True, pr_title="x")
    names = set(vcs_event_name("github", event).split("|"))
    assert {"pull_request", "pull_request.closed", "pull_request.merged"} <= names


def test_vcs_event_name_github_push_bare():
    event = _vcs_event("push", ref="refs/heads/main")
    assert "push" in vcs_event_name("github", event).split("|")


def test_vcs_event_name_gitlab_merge_request_hook():
    event = _vcs_event("Merge Request Hook", action="merge", mr_state="merged")
    names = set(vcs_event_name("gitlab", event).split("|"))
    assert "merge_request" in names and "merge_request.merge" in names


def test_vcs_event_matches_filter():
    assert vcs_event_matches([], "pull_request|pull_request.closed")
    assert vcs_event_matches(["pull_request.merged"], "pull_request|pull_request.merged")
    assert not vcs_event_matches(["push"], "pull_request|pull_request.closed")


def test_vcs_branch_pattern():
    assert branch_matches(r"^(main|release/.*)$", "release/1.0")
    assert not branch_matches(r"^(main|release/.*)$", "feature/x")
    assert branch_matches(None, "anything")  # empty filter = pass
    assert not branch_matches(r"^(main$", "main")  # invalid regex fails CLOSED


def test_vcs_binding_matches_full():
    config = {
        "vcs_events": ["merge_request.merged"],
        "branch_pattern": r"^feature/",
    }
    event = _vcs_event(
        "Merge Request Hook", action="merge", mr_state="merged",
        source_branch="feature/login",
    )
    assert vcs_binding_matches(config, provider="gitlab", event=event)
    wrong_branch = _vcs_event(
        "Merge Request Hook", action="merge", mr_state="merged", source_branch="hotfix/x"
    )
    assert not vcs_binding_matches(config, provider="gitlab", event=wrong_branch)


def test_binding_matches_provider_dispatch():
    assert binding_matches(
        "slack", {}, _im_event("hi"), bot_mentioned=True, is_direct_message=False
    )
    assert not binding_matches("webhook", {}, _im_event("hi"))


# ---------------------------------------------------------------------------
# Signal derivation
# ---------------------------------------------------------------------------


def test_slack_signals_bot_mention_and_dm():
    event = _im_event("<@U_BOT> help", subtype=None)
    mentioned, is_dm = compute_im_signals(
        "slack", event, {"bot_user_id": "U_BOT"}
    )
    assert mentioned and not is_dm
    dm_event = NormalizedEvent(
        external_event_id="e", event_type="message", external_ref="D042",
        actor_key="U1", tenant_key="T", text="psst", extra={},
    )
    _, is_dm = compute_im_signals("slack", dm_event, {"bot_user_id": "U_BOT"})
    assert is_dm


def test_feishu_signals_mentions_and_p2p():
    event = _im_event("hi", mentions=[{"key": "@_user_1"}], chat_type="group")
    mentioned, is_dm = compute_im_signals("feishu", event, {})
    assert mentioned and not is_dm
    p2p = _im_event("psst", chat_type="p2p")
    _, is_dm = compute_im_signals("feishu", p2p, {})
    assert is_dm


# ---------------------------------------------------------------------------
# Config validation (binding-create time)
# ---------------------------------------------------------------------------


def test_validate_match_config_bad_regex_rejected():
    with pytest.raises(BusinessRuleError) as excinfo:
        validate_match_config("github", {"branch_pattern": "^(unclosed"})
    assert excinfo.value.code == "invalid_request"


def test_validate_match_config_bad_trigger_on_rejected():
    with pytest.raises(BusinessRuleError):
        validate_match_config("slack", {"trigger_on": ["telepathy"]})


def test_validate_match_config_accepts_valid():
    validate_match_config(
        "github",
        {"branch_pattern": "^main$", "vcs_events": ["push"]},
    )
    validate_match_config("slack", {"trigger_on": ["mention", "keyword"]})
