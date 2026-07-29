"""OAuth authorization-code + PKCE for IM/VCS connectors (integrations.md
§3.1, auth.md pattern).

``state`` (CSRF) + the PKCE verifier live in Redis for 10 minutes; the
callback exchanges the code, verifies the platform user identity against
the requester session, and stores the refresh token as ciphertext ONLY
(``secret_ref``, README §6.16) with minimal scope. Token exchange is
injectable for tests (dev mode uses a mock provider round-trip, same
approach as auth/oauth.py).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets as pysecrets
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis

from mesh.errors import BusinessRuleError

STATE_PREFIX = "mesh:integration-oauth-state:"
STATE_TTL_SECONDS = 600

# Minimal-scope defaults per connector (least privilege, §6.16).
AUTHORIZE_URLS: dict[str, str] = {
    "im_feishu": "https://open.feishu.cn/open-apis/authen/v1/authorize",
    "im_slack": "https://slack.com/oauth/v2/authorize",
    "vcs_github": "https://github.com/login/oauth/authorize",
    "vcs_gitlab": "https://gitlab.com/oauth/authorize",
}

DEFAULT_SCOPES: dict[str, str] = {
    "im_feishu": "contact:user.base:readonly im:message",
    "im_slack": "channels:read chat:write",
    "vcs_github": "repo:read",
    "vcs_gitlab": "read_api",
}


def generate_pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = pysecrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


async def begin_authorization(
    redis: Redis,
    *,
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    kind: str,
    callback_url: str,
    name: str | None = None,
    app_client_id: str | None = None,
    authorize_base_url: str | None = None,
) -> str:
    """Store the state + PKCE verifier; return the provider authorize URL.

    ``name`` rides along in the state record so the callback can create
    the integration row (with the refresh token persisted as ciphertext,
    §3.1 line 523) under the admin's chosen name.
    """
    if kind not in AUTHORIZE_URLS:
        raise BusinessRuleError("unsupported oauth kind", code="invalid_request")
    verifier, challenge = generate_pkce_pair()
    state = pysecrets.token_urlsafe(24)
    record = {
        "workspace_id": str(workspace_id),
        "member_id": str(member_id),
        "kind": kind,
        "code_verifier": verifier,
        "callback_url": callback_url,
        "name": name or "",
    }
    await redis.set(f"{STATE_PREFIX}{state}", json.dumps(record), ex=STATE_TTL_SECONDS)
    base = authorize_base_url or AUTHORIZE_URLS[kind]
    params = {
        "client_id": app_client_id or "mesh-app",
        "redirect_uri": callback_url,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": DEFAULT_SCOPES[kind],
        "response_type": "code",
    }
    return f"{base}?{urlencode(params)}"


async def consume_state(redis: Redis, *, state: str) -> dict[str, Any] | None:
    """Single-use state consumption (CSRF + replay guard)."""
    key = f"{STATE_PREFIX}{state}"
    raw = await redis.get(key)
    if raw is None:
        return None
    await redis.delete(key)
    return json.loads(raw)


async def exchange_code(
    *,
    kind: str,
    code: str,
    code_verifier: str,
    callback_url: str,
    client_secret: str | None = None,
    token_url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Exchange the authorization code for tokens.

    Returns the token response dict (must contain ``refresh_token`` or
    ``access_token``). Raises ``oauth_failed`` on provider error.
    """
    url = token_url or _default_token_url(kind)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback_url,
        "code_verifier": code_verifier,
        "client_secret": client_secret or "",
    }
    client = http_client or httpx.AsyncClient(timeout=10)
    try:
        response = await client.post(url, data=data)
    except httpx.HTTPError as exc:
        raise BusinessRuleError(
            "oauth token exchange failed", code="oauth_failed",
            details={"reason": type(exc).__name__},
        ) from exc
    finally:
        if http_client is None:
            await client.aclose()
    if response.status_code != 200:
        raise BusinessRuleError(
            "oauth token exchange rejected", code="oauth_failed",
            details={"status": response.status_code},
        )
    try:
        tokens = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise BusinessRuleError(
            "oauth token exchange returned invalid response", code="oauth_failed"
        ) from exc
    if not isinstance(tokens, dict) or not (
        tokens.get("refresh_token") or tokens.get("access_token")
    ):
        raise BusinessRuleError(
            "oauth response missing tokens (scope insufficient?)", code="oauth_failed"
        )
    return tokens


def _default_token_url(kind: str) -> str:
    return {
        "im_feishu": "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
        "im_slack": "https://slack.com/api/oauth.v2.access",
        "vcs_github": "https://github.com/login/oauth/access_token",
        "vcs_gitlab": "https://gitlab.com/oauth/token",
    }[kind]


__all__ = [
    "AUTHORIZE_URLS",
    "DEFAULT_SCOPES",
    "STATE_TTL_SECONDS",
    "begin_authorization",
    "consume_state",
    "exchange_code",
    "generate_pkce_pair",
]
