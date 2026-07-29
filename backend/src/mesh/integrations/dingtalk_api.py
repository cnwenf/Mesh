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

from typing import Any

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


__all__ = [
    "CARD_PARAM_KEY_MAX_BYTES",
    "CARD_PARAM_VALUE_MAX_BYTES",
    "DingTalkError",
    "DingTalkRateLimited",
    "DingTalkUpstreamError",
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
