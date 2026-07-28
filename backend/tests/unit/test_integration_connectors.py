"""Connector adapter unit tests (integrations.md §3.2 / §5.2).

Pure-function coverage for all four platform signature schemes (valid /
tampered / missing / replay-window) plus payload normalization fixtures.
Real platform payloads are reconstructed in-test with the documented
signature algorithms — nothing on the verification path is mocked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from mesh.integrations.connectors import (
    KIND_TO_PROVIDER,
    SIG_INVALID,
    SIG_MISSING,
    SIG_VALID,
    adapter_for,
    feishu_normalize,
    feishu_tenant_key_from_config,
    feishu_verify,
    github_normalize,
    github_tenant_key_from_config,
    github_verify,
    gitlab_normalize,
    gitlab_tenant_key_from_config,
    gitlab_verify,
    slack_normalize,
    slack_tenant_key_from_config,
    slack_verify,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
TOLERANCE = timedelta(seconds=300)
BODY = b'{"hello":"mesh"}'


# ---------------------------------------------------------------------------
# Feishu
# ---------------------------------------------------------------------------


def _feishu_headers(encrypt_key: str, body: bytes, ts: str | None = None, nonce: str = "n0nce") -> dict:
    ts = ts if ts is not None else str(int(NOW.timestamp()))
    signature = hashlib.sha256(f"{ts}{nonce}{encrypt_key}".encode() + body).hexdigest()
    return {"timestamp": ts, "nonce": nonce, "X-Lark-Signature": signature}


def test_feishu_verify_valid():
    headers = _feishu_headers("ek-secret", BODY)
    assert feishu_verify(
        encrypt_key="ek-secret", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_VALID


def test_feishu_verify_tampered_body():
    headers = _feishu_headers("ek-secret", BODY)
    assert feishu_verify(
        encrypt_key="ek-secret", raw_body=BODY + b"x", headers=headers, now=NOW,
        tolerance=TOLERANCE,
    ) == SIG_INVALID


def test_feishu_verify_wrong_key():
    headers = _feishu_headers("ek-secret", BODY)
    assert feishu_verify(
        encrypt_key="other-key", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_INVALID


def test_feishu_verify_missing_signature():
    assert feishu_verify(
        encrypt_key="ek-secret", raw_body=BODY,
        headers={"timestamp": str(int(NOW.timestamp()))},
        now=NOW, tolerance=TOLERANCE,
    ) == SIG_MISSING


def test_feishu_verify_replay_window_rejects_stale_timestamp():
    stale_ts = str(int(NOW.timestamp()) - 301)
    headers = _feishu_headers("ek-secret", BODY, ts=stale_ts)
    assert feishu_verify(
        encrypt_key="ek-secret", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_INVALID


def test_feishu_verify_rejects_non_numeric_timestamp():
    headers = _feishu_headers("ek-secret", BODY, ts="not-a-number")
    assert feishu_verify(
        encrypt_key="ek-secret", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_INVALID


def test_feishu_normalize_im_message_receive_v1():
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt-feishu-1",
            "event_type": "im.message.receive_v1",
            "tenant_key": "tk-001",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_alice"}},
            "message": {
                "chat_id": "oc_chat_9",
                "message_type": "text",
                "chat_type": "group",
                "content": json.dumps({"text": "@Mesh 值班看一下线上"}),
            },
        },
    }
    event = feishu_normalize(payload, {})
    assert event.external_event_id == "evt-feishu-1"
    assert event.event_type == "im.message.receive_v1"
    assert event.external_ref == "oc_chat_9"
    assert event.actor_key == "ou_alice"
    assert event.tenant_key == "tk-001"
    assert "值班" in event.text


def test_feishu_tenant_key_from_config():
    assert feishu_tenant_key_from_config({"tenant_key": "tk-7"}) == "tk-7"
    assert feishu_tenant_key_from_config({}) == ""


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def _slack_headers(secret: str, body: bytes, ts: str | None = None) -> dict:
    ts = ts if ts is not None else str(int(NOW.timestamp()))
    sig = hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Signature": f"v0={sig}", "X-Slack-Request-Timestamp": ts}


def test_slack_verify_valid():
    headers = _slack_headers("ss-secret", BODY)
    assert slack_verify(
        signing_secret="ss-secret", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_VALID


def test_slack_verify_tampered():
    headers = _slack_headers("ss-secret", BODY)
    assert slack_verify(
        signing_secret="ss-secret", raw_body=BODY + b" ", headers=headers,
        now=NOW, tolerance=TOLERANCE,
    ) == SIG_INVALID


def test_slack_verify_missing():
    assert slack_verify(
        signing_secret="ss-secret", raw_body=BODY, headers={}, now=NOW, tolerance=TOLERANCE
    ) == SIG_MISSING


def test_slack_verify_replay():
    stale = str(int(NOW.timestamp()) + 301)
    headers = _slack_headers("ss-secret", BODY, ts=stale)
    assert slack_verify(
        signing_secret="ss-secret", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_INVALID


def test_slack_normalize_event_callback():
    payload = {
        "type": "event_callback",
        "team_id": "T042",
        "event": {
            "type": "message",
            "channel": "C099",
            "user": "U777",
            "text": "<@U0BOT> 帮我查一下",
            "event_ts": "1753790400.000200",
        },
    }
    event = slack_normalize(payload, {})
    assert event.external_event_id == "T042:1753790400.000200"
    assert event.event_type == "message"
    assert event.external_ref == "C099"
    assert event.actor_key == "U777"
    assert event.tenant_key == "T042"


def test_slack_tenant_key_from_config():
    assert slack_tenant_key_from_config({"team_id": "T1"}) == "T1"
    assert slack_tenant_key_from_config({}) == ""


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def _github_headers(secret: str, body: bytes) -> dict:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={sig}",
        "X-GitHub-Delivery": "delivery-0001",
        "X-GitHub-Event": "pull_request",
    }


def test_github_verify_valid():
    headers = _github_headers("gh-secret", BODY)
    assert github_verify(
        webhook_secret="gh-secret", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_VALID


def test_github_verify_tampered():
    headers = _github_headers("gh-secret", BODY)
    assert github_verify(
        webhook_secret="gh-secret", raw_body=b"{}", headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_INVALID


def test_github_verify_missing():
    assert github_verify(
        webhook_secret="gh-secret", raw_body=BODY, headers={}, now=NOW, tolerance=TOLERANCE
    ) == SIG_MISSING


def test_github_normalize_pull_request():
    payload = {
        "action": "closed",
        "repository": {"full_name": "acme/web"},
        "installation": {"id": 1234567},
        "sender": {"login": "octocat"},
        "pull_request": {
            "number": 123,
            "title": "WEB-123 fix the login redirect",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-07-29T11:00:00Z",
            "head": {"ref": "fix/login"},
        },
    }
    event = github_normalize(payload, _github_headers("s", BODY))
    assert event.external_event_id == "delivery-0001"
    assert event.event_type == "pull_request"
    assert event.external_ref == "acme/web"
    assert event.actor_key == "octocat"
    assert event.tenant_key == "1234567"
    assert event.extra["pr_merged"] is True
    assert "WEB-123" in event.text


def test_github_tenant_key_from_config():
    assert github_tenant_key_from_config({"installation_id": "999"}) == "999"
    assert github_tenant_key_from_config({"org": "acme"}) == "acme"


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def test_gitlab_verify_shared_token_valid():
    headers = {"X-Gitlab-Token": "gl-shared", "X-Gitlab-Event": "Merge Request Hook"}
    assert gitlab_verify(
        webhook_token="gl-shared", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_VALID


def test_gitlab_verify_shared_token_invalid():
    headers = {"X-Gitlab-Token": "wrong"}
    assert gitlab_verify(
        webhook_token="gl-shared", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_INVALID


def test_gitlab_verify_missing():
    assert gitlab_verify(
        webhook_token="gl-shared", raw_body=BODY, headers={}, now=NOW, tolerance=TOLERANCE
    ) == SIG_MISSING


def test_gitlab_verify_hmac_signature():
    sig = hmac.new(b"gl-shared", BODY, hashlib.sha256).hexdigest()
    headers = {"X-Gitlab-Signature": f"sha256={sig}"}
    assert gitlab_verify(
        webhook_token="gl-shared", raw_body=BODY, headers=headers, now=NOW, tolerance=TOLERANCE
    ) == SIG_VALID
    bad = dict(headers, **{"X-Gitlab-Signature": "sha256=" + "0" * 64})
    assert gitlab_verify(
        webhook_token="gl-shared", raw_body=BODY, headers=bad, now=NOW, tolerance=TOLERANCE
    ) == SIG_INVALID


def test_gitlab_normalize_merge_request_hook():
    payload = {
        "event_uuid": "uuid-mr-1",
        "project": {"path_with_namespace": "acme/api"},
        "user": {"username": "alice"},
        "object_attributes": {
            "iid": 42,
            "title": "MES-77 merge it",
            "state": "merged",
            "action": "merge",
            "source_branch": "feature/x",
        },
    }
    event = gitlab_normalize(payload, {"X-Gitlab-Event": "Merge Request Hook"})
    assert event.external_event_id == "uuid-mr-1"
    assert event.event_type == "Merge Request Hook"
    assert event.external_ref == "acme/api"
    assert event.actor_key == "alice"
    assert event.extra["mr_state"] == "merged"


def test_gitlab_tenant_key_from_config():
    assert gitlab_tenant_key_from_config({"instance_url": "https://gitlab.com"}) == "gitlab.com"
    assert gitlab_tenant_key_from_config({"instance_url": "https://code.corp.example/"}) == "code.corp.example"
    assert gitlab_tenant_key_from_config({}) == "gitlab.com"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_kind_to_provider_mapping():
    assert KIND_TO_PROVIDER == {
        "im_feishu": "feishu",
        "im_slack": "slack",
        "vcs_github": "github",
        "vcs_gitlab": "gitlab",
        "webhook_outbound": "webhook",
    }


def test_adapter_for_all_kinds():
    for kind in ("im_feishu", "im_slack", "vcs_github", "vcs_gitlab"):
        adapter = adapter_for(kind)
        assert callable(adapter["verify"])
        assert callable(adapter["normalize"])
        assert callable(adapter["tenant_key_from_config"])
        assert adapter["secret_config_key"].endswith("_ref")


def test_adapter_for_webhook_outbound_raises():
    with pytest.raises(KeyError):
        adapter_for("webhook_outbound")
