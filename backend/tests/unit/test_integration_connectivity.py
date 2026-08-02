"""Connector connectivity probe tests (integrations.md §3.1 :test, §2.2 / §4.1).

``test_connectivity`` classifies a connector exactly three ways — healthy /
auth_failed / unreachable — via a lightweight read-only platform API call.
The external platform is simulated with ``httpx.MockTransport`` (the only
seam); the classification logic under test is the real thing.
"""

from __future__ import annotations

import logging

import httpx
import pytest

import mesh.integrations.connectors as connectors_mod
from mesh.errors import BusinessRuleError, ValidationError
from mesh.integrations.connectors import (
    HEALTH_AUTH_FAILED,
    HEALTH_HEALTHY,
    HEALTH_UNREACHABLE,
    validate_instance_url,
    validate_integration_config,
)
from mesh.integrations.connectors import (
    test_connectivity as check_connectivity,
)

pytestmark = pytest.mark.unit


def _client(status_code: int = 200, json_body: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code, json=json_body or {}))
    )


def _raising_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# webhook_outbound + missing-credential short circuits (no HTTP)
# ---------------------------------------------------------------------------


async def test_webhook_outbound_is_healthy_without_credentials():
    state, detail = await check_connectivity("webhook_outbound", config={}, secret=None)
    assert state == HEALTH_HEALTHY
    assert detail == "outbound_only_no_credentials"


async def test_missing_secret_is_auth_failed():
    state, detail = await check_connectivity("im_slack", config={}, secret=None)
    assert state == HEALTH_AUTH_FAILED
    assert detail == "missing_credentials"


# ---------------------------------------------------------------------------
# Feishu — tenant_access_token (code == 0 ⇒ healthy)
# ---------------------------------------------------------------------------


async def test_feishu_healthy_when_code_zero():
    async with _client(200, {"code": 0}) as client:
        state, detail = await check_connectivity(
            "im_feishu",
            config={"app_id": "cli_1"},
            secret="app-secret",
            http_client=client,
        )
    assert (state, detail) == (HEALTH_HEALTHY, None)


async def test_feishu_auth_failed_when_code_nonzero():
    async with _client(200, {"code": 10003}) as client:
        state, detail = await check_connectivity(
            "im_feishu",
            config={"app_id": "cli_1"},
            secret="bad",
            http_client=client,
        )
    assert state == HEALTH_AUTH_FAILED
    assert detail == "code_10003"


async def test_feishu_missing_app_id_is_auth_failed():
    state, detail = await check_connectivity(
        "im_feishu", config={}, secret="s", http_client=_client(200, {"code": 0})
    )
    assert (state, detail) == (HEALTH_AUTH_FAILED, "missing_app_id")


async def test_feishu_http_error_is_unreachable():
    async with _client(500) as client:
        state, detail = await check_connectivity(
            "im_feishu", config={"app_id": "cli_1"}, secret="s", http_client=client
        )
    assert (state, detail) == (HEALTH_UNREACHABLE, "http_500")


# ---------------------------------------------------------------------------
# Slack — auth.test (ok: true ⇒ healthy)
# ---------------------------------------------------------------------------


async def test_slack_healthy_when_ok_true():
    async with _client(200, {"ok": True, "user_id": "U1"}) as client:
        state, detail = await check_connectivity(
            "im_slack", config={}, secret="xoxb-token", http_client=client
        )
    assert (state, detail) == (HEALTH_HEALTHY, None)


async def test_slack_auth_failed_when_ok_false():
    async with _client(200, {"ok": False, "error": "invalid_auth"}) as client:
        state, detail = await check_connectivity("im_slack", config={}, secret="bad", http_client=client)
    assert state == HEALTH_AUTH_FAILED
    assert detail == "invalid_auth"


async def test_slack_network_error_is_unreachable():
    async with _raising_client() as client:
        state, detail = await check_connectivity("im_slack", config={}, secret="x", http_client=client)
    assert state == HEALTH_UNREACHABLE
    assert detail == "ConnectError"


# ---------------------------------------------------------------------------
# DingTalk — gettoken (errcode == 0 ⇒ healthy)
# ---------------------------------------------------------------------------


async def test_dingtalk_healthy_when_errcode_zero():
    async with _client(200, {"errcode": 0, "access_token": "t"}) as client:
        state, detail = await check_connectivity(
            "im_dingtalk", config={"app_key": "ak"}, secret="as", http_client=client
        )
    assert (state, detail) == (HEALTH_HEALTHY, None)


async def test_dingtalk_query_secret_is_redacted_from_httpx_info_log():
    secret = "mes90-super-sensitive-app-secret"
    httpx_logger = logging.getLogger("httpx")
    messages: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = Capture()
    old_level, old_disabled = httpx_logger.level, httpx_logger.disabled
    httpx_logger.setLevel(logging.INFO)
    httpx_logger.disabled = False
    httpx_logger.addHandler(handler)
    try:
        async with _client(200, {"errcode": 0, "access_token": "t"}) as client:
            state, detail = await check_connectivity(
                "im_dingtalk",
                config={"app_key": "ak"},
                secret=secret,
                http_client=client,
            )
    finally:
        httpx_logger.removeHandler(handler)
        httpx_logger.setLevel(old_level)
        httpx_logger.disabled = old_disabled
    assert (state, detail) == (HEALTH_HEALTHY, None)
    logged = "\n".join(messages)
    assert "HTTP Request" in logged  # useful request log remains
    assert secret not in logged


async def test_dingtalk_auth_failed_when_errcode_nonzero():
    async with _client(200, {"errcode": 40014}) as client:
        state, detail = await check_connectivity(
            "im_dingtalk", config={"app_key": "ak"}, secret="bad", http_client=client
        )
    assert state == HEALTH_AUTH_FAILED
    assert detail == "errcode_40014"


async def test_dingtalk_missing_app_key_is_auth_failed():
    state, detail = await check_connectivity(
        "im_dingtalk", config={}, secret="s", http_client=_client(200, {"errcode": 0})
    )
    assert (state, detail) == (HEALTH_AUTH_FAILED, "missing_app_key")


# ---------------------------------------------------------------------------
# GitHub — /user (200 ⇒ healthy, 401/403 ⇒ auth_failed, other ⇒ unreachable)
# ---------------------------------------------------------------------------


async def test_github_healthy_on_200():
    async with _client(200, {"login": "octocat"}) as client:
        state, detail = await check_connectivity(
            "vcs_github", config={}, secret="ghp_token", http_client=client
        )
    assert (state, detail) == (HEALTH_HEALTHY, None)


@pytest.mark.parametrize("status", [401, 403])
async def test_github_auth_failed_on_credential_rejection(status):
    async with _client(status) as client:
        state, detail = await check_connectivity("vcs_github", config={}, secret="bad", http_client=client)
    assert state == HEALTH_AUTH_FAILED
    assert detail == f"http_{status}"


async def test_github_unreachable_on_server_error():
    async with _client(502) as client:
        state, detail = await check_connectivity("vcs_github", config={}, secret="t", http_client=client)
    assert (state, detail) == (HEALTH_UNREACHABLE, "http_502")


# ---------------------------------------------------------------------------
# GitLab — /api/v4/user (200/401/403) incl. self-hosted instance_url
# ---------------------------------------------------------------------------


async def test_gitlab_healthy_on_200():
    async with _client(200, {"username": "alice"}) as client:
        state, detail = await check_connectivity(
            "vcs_gitlab", config={}, secret="glpat-token", http_client=client
        )
    assert (state, detail) == (HEALTH_HEALTHY, None)


@pytest.mark.parametrize("status", [401, 403])
async def test_gitlab_auth_failed_on_credential_rejection(status):
    async with _client(status) as client:
        state, detail = await check_connectivity("vcs_gitlab", config={}, secret="bad", http_client=client)
    assert state == HEALTH_AUTH_FAILED
    assert detail == f"http_{status}"


async def test_gitlab_self_hosted_instance_url_is_used():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"username": "alice"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state, _ = await check_connectivity(
            "vcs_gitlab",
            config={"instance_url": "https://code.corp.example/"},
            secret="t",
            http_client=client,
        )
    assert state == HEALTH_HEALTHY
    assert seen == ["https://code.corp.example/api/v4/user"]


async def test_unsupported_kind_is_auth_failed():
    state, detail = await check_connectivity(
        "carrier_pigeon", config={}, secret="s", http_client=_client(200)
    )
    assert (state, detail) == (HEALTH_AUTH_FAILED, "unsupported_kind")


# ---------------------------------------------------------------------------
# Network-error + non-200 + non-JSON branches (per kind) → unreachable
# ---------------------------------------------------------------------------


async def test_feishu_network_error_is_unreachable():
    async with _raising_client() as client:
        state, detail = await check_connectivity(
            "im_feishu", config={"app_id": "cli_1"}, secret="s", http_client=client
        )
    assert (state, detail) == (HEALTH_UNREACHABLE, "ConnectError")


async def test_slack_non_200_is_unreachable():
    async with _client(500) as client:
        state, detail = await check_connectivity("im_slack", config={}, secret="s", http_client=client)
    assert (state, detail) == (HEALTH_UNREACHABLE, "http_500")


async def test_dingtalk_network_error_is_unreachable():
    async with _raising_client() as client:
        state, detail = await check_connectivity(
            "im_dingtalk", config={"app_key": "ak"}, secret="s", http_client=client
        )
    assert (state, detail) == (HEALTH_UNREACHABLE, "ConnectError")


async def test_dingtalk_non_200_is_unreachable():
    async with _client(503) as client:
        state, detail = await check_connectivity(
            "im_dingtalk", config={"app_key": "ak"}, secret="s", http_client=client
        )
    assert (state, detail) == (HEALTH_UNREACHABLE, "http_503")


async def test_github_network_error_is_unreachable():
    async with _raising_client() as client:
        state, detail = await check_connectivity("vcs_github", config={}, secret="s", http_client=client)
    assert (state, detail) == (HEALTH_UNREACHABLE, "ConnectError")


async def test_gitlab_network_error_is_unreachable():
    async with _raising_client() as client:
        state, detail = await check_connectivity("vcs_gitlab", config={}, secret="s", http_client=client)
    assert (state, detail) == (HEALTH_UNREACHABLE, "ConnectError")


async def test_gitlab_non_200_non_auth_is_unreachable():
    async with _client(502) as client:
        state, detail = await check_connectivity("vcs_gitlab", config={}, secret="s", http_client=client)
    assert (state, detail) == (HEALTH_UNREACHABLE, "http_502")


async def test_non_json_200_body_is_auth_failed():
    # A 200 with a non-JSON body parses to {} → provider "code" missing →
    # auth_failed (the _json_or_empty guard never raises).
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>not json</html>"))
    )
    async with client:
        state, detail = await check_connectivity(
            "im_feishu", config={"app_id": "cli_1"}, secret="s", http_client=client
        )
    assert state == HEALTH_AUTH_FAILED
    assert detail == "code_None"


# ---------------------------------------------------------------------------
# GitLab self-hosted instance_url — SSRF guard (README §6.16, security HIGH-1)
# ---------------------------------------------------------------------------
#
# Two layers, isomorphic with the webhook delivery path (outbound.py):
#   1. WRITE-TIME — validate_instance_url refuses non-https and forbidden
#      (private / loopback / link-local / metadata) hosts at config write,
#      so a literal intranet target can never be persisted.
#   2. TEST-TIME — when no http_client is injected, the probe resolves ONCE
#      through the shared resolve_pinned guard and connects ONLY to the
#      pinned public IPs (DNS-rebinding TOCTOU closure, no redirect follow).


@pytest.mark.parametrize(
    "url",
    [
        "http://gitlab.example.com",  # non-https
        "HTTP://GITLAB.EXAMPLE.COM",
        "gitlab.example.com",  # schemeless
        "",  # empty
        "ftp://gitlab.example.com",
    ],
)
def test_validate_instance_url_rejects_non_https(url):
    # Arrange / Act / Assert
    with pytest.raises(ValidationError) as excinfo:
        validate_instance_url(url)
    assert excinfo.value.code == "invalid_url_scheme"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1",  # loopback
        "https://127.0.0.1:8443",
        "https://10.0.0.8",  # RFC1918
        "https://192.168.1.4",
        "https://172.16.5.5",
        "https://169.254.169.254",  # cloud metadata
        "https://[::1]",  # IPv6 loopback
        "https://localhost",  # known-bad hostname
        "https://user:pass@127.0.0.1",  # userinfo smuggling attempt
    ],
)
def test_validate_instance_url_rejects_forbidden_hosts(url):
    with pytest.raises(BusinessRuleError) as excinfo:
        validate_instance_url(url)
    assert excinfo.value.code == "ssrf_blocked"


def test_validate_instance_url_accepts_public_https():
    assert validate_instance_url("https://gitlab.corp.example.com") is None
    assert validate_instance_url("https://gitlab.com/") is None


def test_validate_integration_config_guards_gitlab_only():
    # Arrange — a forbidden target...
    forbidden = {"instance_url": "https://127.0.0.1"}
    # Act / Assert — ...is refused for vcs_gitlab...
    with pytest.raises(BusinessRuleError) as excinfo:
        validate_integration_config("vcs_gitlab", forbidden)
    assert excinfo.value.code == "ssrf_blocked"
    # ...ignored for kinds that never read instance_url...
    assert validate_integration_config("im_slack", forbidden) is None
    # ...and absent instance_url (gitlab.com default) is fine.
    assert validate_integration_config("vcs_gitlab", {}) is None
    assert validate_integration_config("vcs_gitlab", None) is None


@pytest.mark.parametrize(
    ("config", "field"),
    [
        ({"corp_id": "dingcorp", "receive_mode": "stream"}, "app_key"),
        ({"app_key": "dingapp", "receive_mode": "stream"}, "corp_id"),
        (
            {"app_key": "dingapp", "corp_id": "dingcorp", "receive_mode": "socket"},
            "receive_mode",
        ),
        (
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "receive_mode": "stream",
                "inbound_queue": "unordered",
            },
            "inbound_queue",
        ),
        (
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "receive_mode": "stream",
                "verbosity": "everything",
            },
            "verbosity",
        ),
        (
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "receive_mode": "stream",
                "stream_reconnect": "fast",
            },
            "stream_reconnect",
        ),
        (
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "receive_mode": "stream",
                "stream_reconnect": {"base_seconds": 0},
            },
            "stream_reconnect.base_seconds",
        ),
        (
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "receive_mode": "stream",
                "stream_reconnect": {"base_seconds": 5, "max_seconds": 4},
            },
            "stream_reconnect.max_seconds",
        ),
        (
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "receive_mode": "stream",
                "stream_reconnect": {"heartbeat_timeout_seconds": float("nan")},
            },
            "stream_reconnect.heartbeat_timeout_seconds",
        ),
        (
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "receive_mode": "stream",
                "gateway_base": "https://attacker.example",
            },
            "gateway_base",
        ),
        (
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "receive_mode": "stream",
                "app_secret_ref": "encrypted-bypass",
            },
            "app_secret_ref",
        ),
    ],
)
def test_validate_dingtalk_config_rejects_missing_or_unbounded_fields(config, field):
    with pytest.raises(ValidationError) as excinfo:
        validate_integration_config("im_dingtalk", config)
    assert excinfo.value.code == "invalid_request"
    assert excinfo.value.details["field"] == field


def test_validate_dingtalk_config_accepts_the_documented_shape():
    assert (
        validate_integration_config(
            "im_dingtalk",
            {
                "app_key": "dingapp",
                "corp_id": "dingcorp",
                "robot_code": "dingrobot",
                "receive_mode": "stream",
                "inbound_queue": "serial_conversation",
                "verbosity": "final_only",
                "ack_template": "received",
                "stream_reconnect": {
                    "base_seconds": 2,
                    "max_seconds": 300,
                    "heartbeat_timeout_seconds": 90,
                },
            },
        )
        is None
    )


def _public_resolver(ip: str):
    return lambda hostname, port: [ip]


async def test_gitlab_connectivity_refuses_private_resolution():
    # Arrange — public-looking hostname resolving into private space
    # (the rebinding oracle the guard must refuse). No http_client seam:
    # the real guarded path runs; the injected resolver stands in for DNS.
    # Act
    state, detail = await check_connectivity(
        "vcs_gitlab",
        config={"instance_url": "https://gitlab.example.com"},
        secret="glpat-x",
        resolver=_public_resolver("127.0.0.1"),
    )
    # Assert — neutral refusal, nothing dialed.
    assert (state, detail) == (HEALTH_UNREACHABLE, "ssrf_blocked")


async def test_gitlab_connectivity_refuses_literal_private_ip_without_resolver():
    # Arrange / Act — literal private target, DEFAULT resolver (getaddrinfo
    # on a literal IP is local + deterministic): refused before any dial.
    state, detail = await check_connectivity(
        "vcs_gitlab",
        config={"instance_url": "https://10.0.0.8"},
        secret="glpat-x",
    )
    # Assert
    assert (state, detail) == (HEALTH_UNREACHABLE, "ssrf_blocked")


async def test_gitlab_connectivity_dials_only_pinned_public_ips(monkeypatch):
    # Arrange — resolver answers a single public IP; capture what the
    # transport builder receives and serve the probe via MockTransport.
    captured: dict[str, object] = {}
    seen_urls: list[str] = []

    def fake_transport(pinned_ips):
        captured["pinned_ips"] = pinned_ips

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            return httpx.Response(200, json={"id": 1, "username": "admin"})

        return httpx.MockTransport(handler)

    monkeypatch.setattr(connectors_mod, "_pinned_http_transport", fake_transport)
    # Act
    state, detail = await check_connectivity(
        "vcs_gitlab",
        config={"instance_url": "https://gitlab.example.com/"},
        secret="glpat-x",
        resolver=_public_resolver("93.184.216.34"),
    )
    # Assert — healthy, and the validated IP was the ONLY dial target.
    assert (state, detail) == (HEALTH_HEALTHY, None)
    assert captured["pinned_ips"] == ("93.184.216.34",)
    assert seen_urls == ["https://gitlab.example.com/api/v4/user"]


async def test_gitlab_connectivity_default_base_is_gitlab_com(monkeypatch):
    # Arrange — no instance_url configured → the hardcoded public default.
    seen: list[str] = []

    def fake_transport(pinned_ips):
        return httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(401))

    monkeypatch.setattr(connectors_mod, "_pinned_http_transport", fake_transport)
    # Act
    state, detail = await check_connectivity(
        "vcs_gitlab", config={}, secret="glpat-x", resolver=_public_resolver("172.65.251.78")
    )
    # Assert — 401 classifies auth_failed against the default host.
    assert (state, detail) == (HEALTH_AUTH_FAILED, "http_401")
    assert seen == ["https://gitlab.com/api/v4/user"]


async def test_gitlab_connectivity_injected_client_bypasses_pin_for_tests():
    # Arrange — the http_client test seam stays authoritative (unit tests
    # simulate platforms via MockTransport; no resolve_pinned involved).
    async with _client(200, {"id": 1}) as client:
        # Act
        state, detail = await check_connectivity(
            "vcs_gitlab",
            config={"instance_url": "https://127.0.0.1"},  # seam: not guarded
            secret="glpat-x",
            http_client=client,
        )
    # Assert
    assert (state, detail) == (HEALTH_HEALTHY, None)
