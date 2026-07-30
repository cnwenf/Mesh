"""DingTalk OpenAPI transport layer (integrations.md §3.10).

The concrete outbound adapter for ``kind='im_dingtalk'`` integrations:

- :class:`DingTalkTokenManager` — ``accessToken`` cache with MULTI-REPLICA
  single-flight refresh (Redis shared key + random owner-token ``SET NX EX``
  lease + Lua conditional release + TTL jitter + follower bounded wait).
  Refresh contention is a RETRYABLE non-failure outcome
  (:class:`TokenRefreshBusy`) — the outbox relay moves ``available_at``
  forward without consuming the failure budget (README §6.6 R4-4); only
  :class:`InvalidCredentials` (platform rejects the app secret) or refresh
  budget exhaustion are terminal.
- :class:`DingTalkClient` — robot message send (group ``groupMessages/send``
  / direct ``oToMessages/batchSend``) and interactive card APIs
  (``card/instances/createAndDeliver`` / ``card/instances`` /
  ``card/streaming``) with token-invalidate-once + retry-once on
  platform-reported token expiry, and error classification
  (:class:`DingTalkRateLimited` carries ``flowControlledStaffIdList``).

Full-channel redaction (README §6.16): the decrypted ``app_secret`` joins the
workspace ``redact_in_logs`` blacklist (runtime/credentials.py); the token
value and secret-bearing request bodies are NEVER echoed — logged request
bodies pass through :func:`redact_body_for_log` (structural ``***`` on the
sensitive keys) and failures record ONLY ``method/url/status``.

Outbound targets are FIXED to the official platform domains; the base URLs
are deployment-time environment (settings ``dingtalk_api_base`` /
``dingtalk_oapi_base``) and never runtime-writable, so there is no
user-controlled outbound address on this path (README §6.16 — the SSRF
pinned-resolver guard applies to user-controlled targets only).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("mesh.integrations.dingtalk")

# ---------------------------------------------------------------------------
# Platform message catalog (§3.10 — 13 official robot msgKey types)
# ---------------------------------------------------------------------------

# The official enterprise-internal robot message keys (there is NO
# sampleActionCard1 — the single-action variant is sampleActionCard, the
# multi-action variants are numbered 2..6).
MSG_KEYS: frozenset[str] = frozenset(
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

MSG_KEY_TEXT = "sampleText"
MSG_KEY_MARKDOWN = "sampleMarkdown"

# §3.10: robot message APIs report rate limiting as ERROR CODES (there are
# no published per-minute numbers); the adapter backs off per code instead
# of failing the delivery wholesale, same policy as HTTP 429.
RATE_LIMIT_CODES: frozenset[str] = frozenset(
    {
        "send.too.fast",
        "too.many.group",
        "too.many.people",
        "send.byToken.tooFast",
    }
)

# Platform codes meaning the accessToken is invalid/expired → invalidate the
# shared cache and force-refresh ONCE before retrying the request once.
INVALID_TOKEN_CODES: frozenset[str] = frozenset({"40014", "88", "invalidAuthentication"})

# Platform codes meaning the CREDENTIALS themselves are rejected (refresh
# endpoint) → terminal InvalidCredentials (no retry budget spent).
INVALID_CREDENTIAL_CODES: frozenset[str] = frozenset(
    {"40014", "88", "invalidAuthentication", "invalidAppSecret", "err_param_appsecret"}
)

# §3.10 platform payload constraints.
GROUP_MSG_PARAM_MAX_BYTES = 15000  # groupMessages/send msgParam hard cap
CARD_PARAM_KEY_MAX_BYTES = 100  # cardParamMap key cap
CARD_PARAM_VALUE_MAX_BYTES = 1024  # cardParamMap value cap
STREAM_FRAME_MAX_BYTES = 1024  # streaming card single frame cap
STREAM_TOTAL_MAX_BYTES = 3072  # streaming card total cap

# Token cache tuning (§3.10): platform token lifetime 7200s; shared cache
# TTL = lifetime − refresh buffer ± jitter (thwarts multi-integration
# expiry thundering herds).
TOKEN_LIFETIME_SECONDS = 7200
TOKEN_TTL_BUFFER_SECONDS = 300
TOKEN_TTL_JITTER_SECONDS = 60
TOKEN_REFRESH_AHEAD_SECONDS = 300  # proactive refresh when ≤5 min remain
LOCAL_CACHE_MAX_AGE_SECONDS = 30.0  # per-process LRU freshness bound
FOLLOWER_RECHECK_INTERVAL_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class DingTalkError(Exception):
    """Base for all DingTalk OpenAPI outcomes."""

    def __init__(self, message: str, *, code: str = "", http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class TokenRefreshBusy(DingTalkError):
    """RETRYABLE non-failure: another replica holds the refresh lease and the
    follower bounded wait exhausted (§3.10). The outbox event stays pending
    with ``available_at`` moved forward — the failure budget is NEVER
    consumed and this never reaches a terminal state."""

    def __init__(self, message: str = "token refresh in flight") -> None:
        super().__init__(message, code="token_refresh_busy")


class InvalidCredentials(DingTalkError):
    """TERMINAL: the platform rejected the app credentials (revoked /
    rotated secret). Delivery records ``failed`` + alert."""

    def __init__(self, message: str = "invalid_credentials", *, code: str = "") -> None:
        super().__init__(message, code=code or "invalid_credentials")


class DingTalkRateLimited(DingTalkError):
    """Platform rate limit (error-code presented, §3.10). Carries the
    flow-controlled recipient list so the caller can delay just those
    staff ids instead of failing the batch."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        flow_controlled_staff_ids: tuple[str, ...] = (),
        http_status: int | None = None,
    ) -> None:
        super().__init__(message, code=code, http_status=http_status)
        self.flow_controlled_staff_ids = tuple(flow_controlled_staff_ids)


class DingTalkUpstreamError(DingTalkError):
    """Non-classified platform failure (5xx / unexpected body). Ledger
    records ONLY method/url/status — never the body (§6.16)."""


# ---------------------------------------------------------------------------
# Full-channel redaction (README §6.16)
# ---------------------------------------------------------------------------

# Request-body keys whose VALUES are secrets and may never reach logs /
# error ledgers / delivery details.
_SENSITIVE_BODY_KEYS: frozenset[str] = frozenset({"appSecret", "clientSecret", "accessToken"})

# Request headers whose values are secrets (token bearer).
REDACT_HEADERS: frozenset[str] = frozenset({"x-acs-dingtalk-access-token"})


def redact_body_for_log(body: dict[str, Any] | None) -> dict[str, Any]:
    """Structural ``***`` replacement for secret-bearing body keys.

    Independent of the workspace blacklist (which covers the stored
    app_secret): the ephemeral accessToken is never a stored credential, so
    it is masked by key name here. Non-dict input yields an empty dict
    (there is nothing structured to log).
    """
    if not isinstance(body, dict):
        return {}
    return {key: ("***" if key in _SENSITIVE_BODY_KEYS else value) for key, value in body.items()}


# ---------------------------------------------------------------------------
# accessToken cache — multi-replica single-flight refresh (§3.10)
# ---------------------------------------------------------------------------

# Conditional lock release: DEL only when the lock still carries OUR random
# owner token — a stale owner returning after its lease expired must never
# delete the successor's lock (at most one effective refresher at any
# instant).
_RELEASE_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _default_jitter() -> int:
    return random.randint(-TOKEN_TTL_JITTER_SECONDS, TOKEN_TTL_JITTER_SECONDS)


class DingTalkTokenManager:
    """``accessToken`` cache shared across ``mesh.workers`` replicas.

    Protocol (§3.10, ownership-safe):

    - SHARED cache: Redis ``dingtalk:access_token:<integration_id>`` →
      ``{token, expires_at}`` with TTL ``lifetime − 300 ± 60s`` (jitter
      thwarts synchronized expiry across integrations). A per-process LRU
      entry (≤30s) sits in front of Redis.
    - REFRESH: ``SET lock <random owner> NX EX <lease>``; the winner
      DOUBLE-CHECKS the shared cache is still stale, then calls
      ``POST /v1.0/oauth2/accessToken`` (timeout strictly UNDER the lease —
      the lease cannot expire mid-refresh), writes the shared cache, and
      releases via the Lua owner compare.
    - FOLLOWER: re-checks the shared cache every 500ms up to
      ``follower_wait`` (default 12s = refresh timeout + buffer) — a
      legitimate leader refresh (≤10s) is always outwaited, never a
      terminal failure during it. Window exhausted → ONE re-acquire attempt
      (the leader may have crashed and its lease expired) → still nothing →
      :class:`TokenRefreshBusy` (retryable non-failure; the outbox relay
      moves ``available_at`` without consuming the failure budget).
    """

    def __init__(
        self,
        redis: Redis,
        *,
        http_client: httpx.AsyncClient,
        integration_id: uuid.UUID | str,
        app_key: str,
        app_secret: str,
        api_base: str = "https://api.dingtalk.com",
        refresh_timeout: float = 10.0,
        lock_ttl: int = 30,
        follower_wait: float = 12.0,
        recheck_interval: float = FOLLOWER_RECHECK_INTERVAL_SECONDS,
        now: Callable[[], datetime] | None = None,
        jitter: Callable[[], int] | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        if refresh_timeout >= lock_ttl:
            # §3.10 — the refresh request must complete inside the lease; a
            # lease expiring mid-refresh would admit a second refresher.
            raise ValueError("refresh_timeout must be strictly less than lock_ttl")
        self._redis = redis
        self._client = http_client
        self._integration_id = str(integration_id)
        self._app_key = app_key
        self._app_secret = app_secret
        self._api_base = api_base.rstrip("/")
        self._refresh_timeout = refresh_timeout
        self._lock_ttl = lock_ttl
        self._follower_wait = follower_wait
        self._recheck_interval = recheck_interval
        self._now = now or (lambda: datetime.now(UTC))
        self._jitter = jitter or _default_jitter
        self._sleep = sleep or asyncio.sleep
        # (token, expires_at_epoch, cached_at_epoch)
        self._local: tuple[str, float, float] | None = None

    # -- cache keys ------------------------------------------------------

    @property
    def cache_key(self) -> str:
        return f"dingtalk:access_token:{self._integration_id}"

    @property
    def lock_key(self) -> str:
        return f"dingtalk:token_lock:{self._integration_id}"

    # -- public API ------------------------------------------------------

    async def get_token(self, *, force: bool = False) -> str:
        """A valid accessToken, refreshing through the ownership protocol.

        ``force=True`` skips both cache layers (platform-reported token
        invalidation) and goes straight at the refresh protocol.
        """
        now_ts = self._now().timestamp()
        if not force:
            local = self._local
            if local is not None:
                token, expires_at, cached_at = local
                if (
                    now_ts - cached_at <= LOCAL_CACHE_MAX_AGE_SECONDS
                    and expires_at - now_ts > TOKEN_REFRESH_AHEAD_SECONDS
                ):
                    return token
            shared = await self._read_shared()
            if shared is not None:
                token, expires_at = shared
                if expires_at - now_ts > TOKEN_REFRESH_AHEAD_SECONDS:
                    self._cache_local(token, expires_at)
                    return token
        return await self._refresh()

    async def invalidate(self) -> None:
        """Drop both cache layers (platform reported the token invalid)."""
        self._local = None
        await self._redis.delete(self.cache_key)

    # -- refresh protocol --------------------------------------------------

    async def _refresh(self) -> str:
        owner = uuid.uuid4().hex
        acquired = await self._redis.set(self.lock_key, owner, nx=True, ex=self._lock_ttl)
        if acquired:
            try:
                return await self._refresh_as_leader()
            finally:
                await self._release_lock(owner)
        return await self._wait_as_follower()

    async def _refresh_as_leader(self) -> str:
        # Double-check: another leader may have refreshed between our stale
        # read and the lock acquisition.
        shared = await self._read_shared()
        now_ts = self._now().timestamp()
        if shared is not None:
            token, expires_at = shared
            if expires_at - now_ts > TOKEN_REFRESH_AHEAD_SECONDS:
                self._cache_local(token, expires_at)
                return token
        token, expires_at, expire_in = await self._call_refresh_endpoint()
        await self._write_shared(token, expires_at, expire_in)
        self._cache_local(token, expires_at)
        return token

    async def _wait_as_follower(self) -> str:
        deadline = self._now().timestamp() + self._follower_wait
        while True:
            await self._sleep(self._recheck_interval)
            shared = await self._read_shared()
            now_ts = self._now().timestamp()
            if shared is not None:
                token, expires_at = shared
                if expires_at - now_ts > TOKEN_REFRESH_AHEAD_SECONDS:
                    self._cache_local(token, expires_at)
                    return token
            if now_ts >= deadline:
                break
        # Window exhausted: the leader may have crashed and its lease expired —
        # ONE re-acquire attempt (takeover), then give up retryable.
        owner = uuid.uuid4().hex
        acquired = await self._redis.set(self.lock_key, owner, nx=True, ex=self._lock_ttl)
        if acquired:
            try:
                return await self._refresh_as_leader()
            finally:
                await self._release_lock(owner)
        raise TokenRefreshBusy(
            f"token refresh still in flight for integration {self._integration_id}"
        )

    async def _release_lock(self, owner: str) -> None:
        try:
            await self._redis.eval(_RELEASE_LOCK_LUA, 1, self.lock_key, owner)
        except Exception:  # noqa: BLE001 — lock TTL is the safety net
            logger.warning("dingtalk token lock release failed", exc_info=True)

    # -- platform call -----------------------------------------------------

    async def _call_refresh_endpoint(self) -> tuple[str, float, int]:
        """``POST /v1.0/oauth2/accessToken`` → (token, expires_at, expire_in).

        The request body carries the appSecret in plaintext — it is NEVER
        logged (failures record method/url/status only, §6.16).
        """
        url = f"{self._api_base}/v1.0/oauth2/accessToken"
        body = {"appKey": self._app_key, "appSecret": self._app_secret}
        try:
            response = await self._client.post(url, json=body, timeout=self._refresh_timeout)
        except (httpx.HTTPError, OSError) as exc:
            raise DingTalkUpstreamError(
                f"POST {url} failed: {type(exc).__name__}", code="upstream_error"
            ) from exc
        payload = _json_body(response)
        if response.status_code != 200:
            code = str(payload.get("code") or "")
            if code in INVALID_CREDENTIAL_CODES:
                raise InvalidCredentials("dingtalk rejected the app credentials", code=code)
            raise DingTalkUpstreamError(
                f"POST {url} status={response.status_code}",
                code=code or "upstream_error",
                http_status=response.status_code,
            )
        token = str(payload.get("accessToken") or "")
        code = str(payload.get("code") or "")
        if not token:
            if code in INVALID_CREDENTIAL_CODES:
                raise InvalidCredentials("dingtalk rejected the app credentials", code=code)
            raise DingTalkUpstreamError(
                f"POST {url} returned no accessToken", code=code or "upstream_error"
            )
        try:
            expire_in = int(payload.get("expireIn") or TOKEN_LIFETIME_SECONDS)
        except (TypeError, ValueError):
            expire_in = TOKEN_LIFETIME_SECONDS
        expires_at = self._now().timestamp() + expire_in
        return token, expires_at, expire_in

    # -- shared cache IO -----------------------------------------------------

    async def _read_shared(self) -> tuple[str, float] | None:
        raw = await self._redis.get(self.cache_key)
        if not raw:
            return None
        try:
            decoded = json.loads(raw)
            token = str(decoded["token"])
            expires_at = float(decoded["expires_at"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if expires_at <= self._now().timestamp():
            return None  # expired entry — treat as absent
        return token, expires_at

    async def _write_shared(self, token: str, expires_at: float, expire_in: int) -> None:
        ttl = max(60, expire_in - TOKEN_TTL_BUFFER_SECONDS + self._jitter())
        await self._redis.set(
            self.cache_key, json.dumps({"token": token, "expires_at": expires_at}), ex=ttl
        )

    def _cache_local(self, token: str, expires_at: float) -> None:
        self._local = (token, expires_at, self._now().timestamp())


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


# ---------------------------------------------------------------------------
# DingTalkClient — robot message send + interactive card APIs (§3.10)
# ---------------------------------------------------------------------------

GROUP_SEND_PATH = "/v1.0/robot/groupMessages/send"
DIRECT_SEND_PATH = "/v1.0/robot/oToMessages/batchSend"
CARD_CREATE_PATH = "/v1.0/card/instances/createAndDeliver"
CARD_UPDATE_PATH = "/v1.0/card/instances"
CARD_STREAM_PATH = "/v1.0/card/streaming"


class DingTalkClient:
    """OpenAPI request executor bound to one integration's robot.

    Every request carries ``x-acs-dingtalk-access-token`` (token from the
    shared :class:`DingTalkTokenManager`). Error classification:

    - HTTP 429 or a body ``code`` in :data:`RATE_LIMIT_CODES` →
      :class:`DingTalkRateLimited` with the ``flowControlledStaffIdList``
      (the caller delays just those recipients, §3.10);
    - platform-reported token invalidity → invalidate the shared cache,
      force-refresh ONCE, retry the original request ONCE (still failing →
      :class:`DingTalkUpstreamError`);
    - anything else non-2xx → :class:`DingTalkUpstreamError` recording
      method/url/status ONLY — never the body (it may echo platform error
      detail; §6.16 full-channel redaction).
    """

    def __init__(
        self,
        token_manager: DingTalkTokenManager,
        *,
        http_client: httpx.AsyncClient,
        api_base: str = "https://api.dingtalk.com",
        robot_code: str,
        request_timeout: float = 10.0,
    ) -> None:
        self._token_manager = token_manager
        self._client = http_client
        self._api_base = api_base.rstrip("/")
        self._robot_code = robot_code
        self._request_timeout = request_timeout

    @property
    def robot_code(self) -> str:
        return self._robot_code

    # -- robot messages ----------------------------------------------------

    async def send_group(
        self, open_conversation_id: str, msg_key: str, msg_param: dict[str, Any]
    ) -> dict[str, Any]:
        """Group message via ``groupMessages/send`` (§3.10).

        ``msgParam`` is the JSON-encoded parameter object; the platform
        caps it at 15000 bytes and does NOT support @ mentions — the
        semantic layer enforces both before calling.
        """
        body = {
            "robotCode": self._robot_code,
            "openConversationId": open_conversation_id,
            "msgKey": msg_key,
            "msgParam": _encode_msg_param(msg_param),
        }
        return await self._request("POST", GROUP_SEND_PATH, body)

    async def send_direct(
        self, user_ids: list[str], msg_key: str, msg_param: dict[str, Any]
    ) -> dict[str, Any]:
        """Single-chat message via ``oToMessages/batchSend`` (per-staffId)."""
        body = {
            "robotCode": self._robot_code,
            "userIds": list(user_ids),
            "msgKey": msg_key,
            "msgParam": _encode_msg_param(msg_param),
        }
        return await self._request("POST", DIRECT_SEND_PATH, body)

    # -- interactive cards (card_1.0) ----------------------------------------

    async def create_and_deliver_card(self, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /v1.0/card/instances/createAndDeliver`` (cardTemplateId +
        outTrackId + openSpaceId + cardData + callbackType)."""
        return await self._request("POST", CARD_CREATE_PATH, body)

    async def update_card(self, body: dict[str, Any]) -> dict[str, Any]:
        """``PUT /v1.0/card/instances`` — idempotent by ``outTrackId``."""
        return await self._request("PUT", CARD_UPDATE_PATH, body)

    async def stream_card(self, body: dict[str, Any]) -> dict[str, Any]:
        """``PUT /v1.0/card/streaming`` (guid idempotency, markdown
        ``isFull=true`` full replacement, ``isFinalize`` closure)."""
        return await self._request("PUT", CARD_STREAM_PATH, body)

    # -- core request --------------------------------------------------------

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, body)

    async def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", path, body)

    async def _request(
        self, method: str, path: str, body: dict[str, Any], *, _retried: bool = False
    ) -> dict[str, Any]:
        token = await self._token_manager.get_token()
        url = f"{self._api_base}{path}"
        headers = {"x-acs-dingtalk-access-token": token}
        try:
            response = await self._client.request(
                method, url, json=body, headers=headers, timeout=self._request_timeout
            )
        except (httpx.HTTPError, OSError) as exc:
            raise DingTalkUpstreamError(
                f"{method} {url} failed: {type(exc).__name__}", code="upstream_error"
            ) from exc
        payload = _json_body(response)
        code = str(payload.get("code") or "")

        if response.status_code == 429 or code in RATE_LIMIT_CODES:
            controlled = payload.get("flowControlledStaffIdList") or []
            if not isinstance(controlled, list):
                controlled = []
            raise DingTalkRateLimited(
                f"{method} {path} rate limited code={code or 'http_429'}",
                code=code or "rate_limited",
                flow_controlled_staff_ids=tuple(str(item) for item in controlled),
                http_status=response.status_code,
            )

        if 200 <= response.status_code < 300:
            return payload

        # Platform-reported token invalidity: invalidate + force-refresh
        # ONCE + retry the original request ONCE (idempotent sends would
        # dedup anyway; card updates are idempotent by outTrackId).
        token_invalid = code in INVALID_TOKEN_CODES or response.status_code == 401
        if token_invalid and not _retried:
            await self._token_manager.invalidate()
            return await self._request(method, path, body, _retried=True)

        raise DingTalkUpstreamError(
            f"{method} {url} status={response.status_code}",
            code=code or "upstream_error",
            http_status=response.status_code,
        )


def _encode_msg_param(msg_param: dict[str, Any]) -> str:
    return json.dumps(msg_param, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "CARD_CREATE_PATH",
    "CARD_PARAM_KEY_MAX_BYTES",
    "CARD_PARAM_VALUE_MAX_BYTES",
    "CARD_STREAM_PATH",
    "CARD_UPDATE_PATH",
    "DIRECT_SEND_PATH",
    "DingTalkClient",
    "DingTalkError",
    "DingTalkRateLimited",
    "DingTalkTokenManager",
    "DingTalkUpstreamError",
    "GROUP_SEND_PATH",
    "FOLLOWER_RECHECK_INTERVAL_SECONDS",
    "GROUP_MSG_PARAM_MAX_BYTES",
    "INVALID_CREDENTIAL_CODES",
    "INVALID_TOKEN_CODES",
    "InvalidCredentials",
    "LOCAL_CACHE_MAX_AGE_SECONDS",
    "MSG_KEYS",
    "MSG_KEY_MARKDOWN",
    "MSG_KEY_TEXT",
    "RATE_LIMIT_CODES",
    "REDACT_HEADERS",
    "STREAM_FRAME_MAX_BYTES",
    "STREAM_TOTAL_MAX_BYTES",
    "TOKEN_LIFETIME_SECONDS",
    "TOKEN_REFRESH_AHEAD_SECONDS",
    "TOKEN_TTL_BUFFER_SECONDS",
    "TOKEN_TTL_JITTER_SECONDS",
    "TokenRefreshBusy",
    "redact_body_for_log",
]
