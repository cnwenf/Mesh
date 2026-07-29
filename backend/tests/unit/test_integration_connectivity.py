"""Connector connectivity probe tests (integrations.md §3.1 :test, §2.2 / §4.1).

``test_connectivity`` classifies a connector exactly three ways — healthy /
auth_failed / unreachable — via a lightweight read-only platform API call.
The external platform is simulated with ``httpx.MockTransport`` (the only
seam); the classification logic under test is the real thing.
"""

from __future__ import annotations

import httpx
import pytest

from mesh.integrations.connectors import (
    HEALTH_AUTH_FAILED,
    HEALTH_HEALTHY,
    HEALTH_UNREACHABLE,
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
