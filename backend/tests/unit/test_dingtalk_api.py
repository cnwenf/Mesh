"""DingTalk OpenAPI transport layer tests (integrations.md §3.10)."""

from __future__ import annotations

import pytest

from mesh.config import load_settings
from mesh.integrations.dingtalk_api import (
    GROUP_MSG_PARAM_MAX_BYTES,
    MSG_KEYS,
    RATE_LIMIT_CODES,
    DingTalkRateLimited,
    TokenRefreshBusy,
    redact_body_for_log,
)


# ---------------------------------------------------------------------------
# Platform catalog constants
# ---------------------------------------------------------------------------


def test_msg_keys_are_the_13_official_types():
    """§3.10 — the official robot msgKey catalog (no sampleActionCard1)."""
    assert MSG_KEYS == frozenset(
        {
            "sampleText",
            "sampleMarkdown",
            "sampleImageMsg",
            "sampleLink",
            "sampleAudio",
            "sampleVideo",
            "sampleFile",
            "sampleActionCard",
            "sampleActionCard2",
            "sampleActionCard3",
            "sampleActionCard4",
            "sampleActionCard5",
            "sampleActionCard6",
        }
    )
    assert len(MSG_KEYS) == 13
    assert "sampleActionCard1" not in MSG_KEYS


def test_rate_limit_codes():
    assert RATE_LIMIT_CODES == frozenset(
        {"send.too.fast", "too.many.group", "too.many.people", "send.byToken.tooFast"}
    )


def test_group_msg_param_cap():
    assert GROUP_MSG_PARAM_MAX_BYTES == 15000


def test_rate_limited_carries_flow_controlled_staff_ids():
    exc = DingTalkRateLimited(
        "rate limited",
        code="send.too.fast",
        flow_controlled_staff_ids=["staff1", "staff2"],
        http_status=429,
    )
    assert exc.code == "send.too.fast"
    assert exc.flow_controlled_staff_ids == ("staff1", "staff2")
    assert exc.http_status == 429


def test_token_refresh_busy_is_retryable_marker():
    exc = TokenRefreshBusy()
    assert exc.code == "token_refresh_busy"


# ---------------------------------------------------------------------------
# Request-body redaction (README §6.16)
# ---------------------------------------------------------------------------


def test_redact_body_replaces_secret_keys_only():
    body = {
        "appKey": "dingxxxx",
        "appSecret": "s3cr3t-value",
        "robotCode": "dingxxxx",
    }
    redacted = redact_body_for_log(body)
    assert redacted == {"appKey": "dingxxxx", "appSecret": "***", "robotCode": "dingxxxx"}
    # the original dict is NOT mutated (immutability)
    assert body["appSecret"] == "s3cr3t-value"


def test_redact_body_covers_client_secret_and_access_token():
    redacted = redact_body_for_log(
        {"clientId": "a", "clientSecret": "b", "accessToken": "c", "keep": "d"}
    )
    assert redacted == {"clientId": "a", "clientSecret": "***", "accessToken": "***", "keep": "d"}


def test_redact_body_none_and_non_dict():
    assert redact_body_for_log(None) == {}
    assert redact_body_for_log("not-a-dict") == {}  # type: ignore[arg-type]


def test_redact_body_without_sensitive_keys_is_identity():
    body = {"robotCode": "dingxxxx", "msgKey": "sampleText"}
    assert redact_body_for_log(body) == body


# ---------------------------------------------------------------------------
# Settings (env names align with the spec, MESH_ prefix)
# ---------------------------------------------------------------------------


_MINIMAL_SETTINGS = {
    "database_url": "postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test_mes89",
    "redis_url": "redis://127.0.0.1:6390/3",
}


def test_settings_defaults_for_im_and_dingtalk():
    settings = load_settings(**_MINIMAL_SETTINGS)
    assert settings.im_ack_coalesce_window == 5.0
    assert settings.im_max_chunks == 5
    assert settings.im_ack_send_timeout == 3.0
    assert settings.token_follower_wait == 12.0
    assert settings.dingtalk_api_base == "https://api.dingtalk.com"
    assert settings.dingtalk_oapi_base == "https://oapi.dingtalk.com"
    assert settings.dingtalk_token_refresh_timeout == 10.0
    assert settings.dingtalk_token_lock_ttl == 30
    # refresh timeout MUST be strictly under the lock lease (§3.10 — the lock
    # cannot expire while a legitimate refresh request is in flight)
    assert settings.dingtalk_token_refresh_timeout < settings.dingtalk_token_lock_ttl


def test_settings_env_override_naming(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MESH_IM_ACK_COALESCE_WINDOW", "7.5")
    monkeypatch.setenv("MESH_TOKEN_FOLLOWER_WAIT", "15")
    monkeypatch.setenv("MESH_IM_MAX_CHUNKS", "8")
    settings = load_settings(**_MINIMAL_SETTINGS)
    assert settings.im_ack_coalesce_window == 7.5
    assert settings.token_follower_wait == 15.0
    assert settings.im_max_chunks == 8
