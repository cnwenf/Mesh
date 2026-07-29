"""DingTalk OpenAPI transport layer tests (integrations.md §3.10)."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from mesh.config import load_settings
from mesh.integrations.dingtalk_api import (
    GROUP_MSG_PARAM_MAX_BYTES,
    MSG_KEYS,
    RATE_LIMIT_CODES,
    DingTalkRateLimited,
    DingTalkTokenManager,
    DingTalkUpstreamError,
    InvalidCredentials,
    TokenRefreshBusy,
    redact_body_for_log,
)

from tests.unit.integrations_dingtalk_support import (
    ScriptedDingTalkTransport,
    make_client,
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


# ---------------------------------------------------------------------------
# DingTalkTokenManager — multi-replica single-flight refresh (§3.10)
# ---------------------------------------------------------------------------


def _make_manager(
    redis_client,
    transport: ScriptedDingTalkTransport,
    *,
    integration_id: uuid.UUID | None = None,
    follower_wait: float = 12.0,
    lock_ttl: int = 30,
    refresh_timeout: float = 5.0,
    jitter=lambda: 0,
) -> DingTalkTokenManager:
    return DingTalkTokenManager(
        redis_client,
        http_client=make_client(transport),
        integration_id=integration_id or uuid.uuid4(),
        app_key="dingkey",
        app_secret="dingsecret",
        refresh_timeout=refresh_timeout,
        lock_ttl=lock_ttl,
        follower_wait=follower_wait,
        recheck_interval=0.05,
        jitter=jitter,
    )


async def test_token_fetched_and_cached_in_redis_with_ttl_jitter_bounds(redis_client):
    """Shared cache TTL = 7200 − 300 ± jitter(60) — the ±60s band (§3.10)."""
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport, jitter=lambda: 30)
    token = await manager.get_token()
    assert token == "tok-1"
    raw = await redis_client.get(manager.cache_key)
    assert raw is not None
    payload = json.loads(raw)
    assert payload["token"] == "tok-1"
    assert payload["expires_at"] > 0
    ttl = await redis_client.ttl(manager.cache_key)
    # 7200 − 300 + 30 = 6930 (allow a few seconds of test drift)
    assert 6920 <= ttl <= 6930


async def test_token_ttl_jitter_negative_bound(redis_client):
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport, jitter=lambda: -60)
    await manager.get_token()
    ttl = await redis_client.ttl(manager.cache_key)
    assert 7200 - 300 - 60 - 10 <= ttl <= 7200 - 300 - 60


async def test_local_lru_short_circuits_redis(redis_client):
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport)
    first = await manager.get_token()
    # Poison the shared cache — the fresh local entry must still serve.
    await redis_client.delete(manager.cache_key)
    second = await manager.get_token()
    assert first == second == "tok-1"
    assert transport.token_calls == 1


async def test_stale_local_entry_falls_through_to_redis(redis_client):
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport)
    await manager.get_token()
    # Age the local entry beyond the 30s LRU bound.
    token, expires_at, _ = manager._local
    manager._local = (token, expires_at, manager._now().timestamp() - 31)
    again = await manager.get_token()
    assert again == "tok-1"
    assert transport.token_calls == 1  # served from the shared cache


async def test_concurrent_refresh_single_flight(redis_client):
    """Two replicas refreshing concurrently → platform endpoint hit ONCE."""
    transport = ScriptedDingTalkTransport(token_delay=0.3)
    integration_id = uuid.uuid4()
    m1 = _make_manager(redis_client, transport, integration_id=integration_id)
    m2 = _make_manager(redis_client, transport, integration_id=integration_id)
    t1, t2 = await asyncio.gather(m1.get_token(), m2.get_token())
    assert t1 == t2 == "tok-1"
    assert transport.token_calls == 1


async def test_follower_waits_and_gets_token_within_window(redis_client):
    """Leader refresh takes ~0.5s; the follower outwaits it (zero failures)."""
    transport = ScriptedDingTalkTransport(token_delay=0.5)
    integration_id = uuid.uuid4()
    leader = _make_manager(redis_client, transport, integration_id=integration_id)
    follower = _make_manager(
        redis_client, transport, integration_id=integration_id, follower_wait=3.0
    )
    started = asyncio.ensure_future(leader.get_token())
    await asyncio.sleep(0.05)  # guarantee the leader holds the lock first
    follower_token = await follower.get_token()
    leader_token = await started
    assert leader_token == follower_token == "tok-1"
    assert transport.token_calls == 1


async def test_follower_wait_exhausted_raises_busy_not_terminal(redis_client):
    """Lock held with NO refresh completing → TokenRefreshBusy (retryable)."""
    transport = ScriptedDingTalkTransport()
    integration_id = uuid.uuid4()
    manager = _make_manager(
        redis_client, transport, integration_id=integration_id, follower_wait=0.3
    )
    # Simulate a crashed leader: lock held, refresh never lands.
    await redis_client.set(manager.lock_key, "crashed-owner", ex=30)
    with pytest.raises(TokenRefreshBusy):
        await manager.get_token()
    # NOT terminal: once the lock clears, the refresh succeeds.
    await redis_client.delete(manager.lock_key)
    assert await manager.get_token() == "tok-1"


async def test_follower_takeover_after_lease_expiry(redis_client):
    """Lease expiry during the wait → the follower re-acquires and refreshes."""
    transport = ScriptedDingTalkTransport()
    integration_id = uuid.uuid4()
    manager = _make_manager(
        redis_client,
        transport,
        integration_id=integration_id,
        follower_wait=1.2,  # outlasts the 1s lease → re-acquire can succeed
        lock_ttl=1,  # lease shorter than the wait → takeover path reachable
        refresh_timeout=0.8,  # must stay under the 1s lease
    )
    await redis_client.set(manager.lock_key, "dead-owner", ex=1)
    token = await manager.get_token()  # waits ~1.2s, lease expired, re-acquires
    assert token == "tok-1"
    assert transport.token_calls == 1


async def test_lua_release_refuses_stale_owner(redis_client):
    """A late owner token must never DEL the successor's lock."""
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport)
    await redis_client.set(manager.lock_key, "new-owner", ex=30)
    await manager._release_lock("stale-owner")
    assert await redis_client.get(manager.lock_key) == "new-owner"
    await manager._release_lock("new-owner")
    assert await redis_client.get(manager.lock_key) is None


async def test_invalid_credentials_is_terminal(redis_client):
    transport = ScriptedDingTalkTransport(
        token_status=400, token_body={"code": "invalidAuthentication", "message": "bad secret"}
    )
    manager = _make_manager(redis_client, transport)
    with pytest.raises(InvalidCredentials) as excinfo:
        await manager.get_token()
    assert excinfo.value.code == "invalidAuthentication"


async def test_refresh_network_error_is_upstream(redis_client):
    transport = ScriptedDingTalkTransport(token_exc=httpx.ConnectError("boom"))
    manager = _make_manager(redis_client, transport)
    with pytest.raises(DingTalkUpstreamError):
        await manager.get_token()


async def test_invalidate_forces_new_refresh(redis_client):
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport)
    assert await manager.get_token() == "tok-1"
    await manager.invalidate()
    assert await redis_client.get(manager.cache_key) is None
    assert manager._local is None
    assert await manager.get_token(force=True) == "tok-2"
    assert transport.token_calls == 2


async def test_force_refresh_skips_local_cache(redis_client):
    """force=True never serves the local LRU copy (platform said invalid)."""
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport)
    assert await manager.get_token() == "tok-1"
    # Another replica invalidated the shared cache; our local copy is stale.
    await redis_client.delete(manager.cache_key)
    assert await manager.get_token(force=True) == "tok-2"
    assert transport.token_calls == 2


async def test_forced_refresh_leader_double_check_returns_fresher_token(redis_client):
    """force + still-valid shared token: the leader double-check returns it
    (a concurrent replica may have just refreshed — no redundant call)."""
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport)
    assert await manager.get_token() == "tok-1"
    assert await manager.get_token(force=True) == "tok-1"
    assert transport.token_calls == 1


def test_refresh_timeout_must_be_under_lease(redis_client):
    """§3.10 — a lease expiring mid-refresh would admit a second refresher."""
    transport = ScriptedDingTalkTransport()
    with pytest.raises(ValueError):
        DingTalkTokenManager(
            redis_client,
            http_client=make_client(transport),
            integration_id=uuid.uuid4(),
            app_key="k",
            app_secret="s",
            refresh_timeout=30.0,
            lock_ttl=30,
        )


async def test_expired_shared_entry_triggers_refresh(redis_client):
    transport = ScriptedDingTalkTransport()
    manager = _make_manager(redis_client, transport)
    await redis_client.set(
        manager.cache_key,
        json.dumps({"token": "old", "expires_at": manager._now().timestamp() - 10}),
    )
    assert await manager.get_token() == "tok-1"
    assert transport.token_calls == 1
