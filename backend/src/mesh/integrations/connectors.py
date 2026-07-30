"""Connector adapters: signature verification + payload normalization.

integrations.md §3.2 / §5: each platform adapter implements the inbound
adaptation points — ``verify`` (constant-time signature check with replay
protection where the platform scheme carries a timestamp) and ``normalize``
(map the platform payload onto one :class:`NormalizedEvent`). Outbound
adapters (token cache / card push / notification send) live in
``outbound_im.py``; the ingestion pipeline itself (dedup / audit / match /
enqueue) is provider-agnostic (``inbound.py``, reusing the autopilot
``webhook_events`` paradigm).

Secrets reach adapters ONLY as decrypted plaintext arguments (the pipeline
decrypts ``config['*_ref']`` / ``secret_ref`` ciphertexts via the
``runtime_credentials`` contract, README §6.16); adapters never see
ciphertext and never log the values.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from mesh.errors import BusinessRuleError, ValidationError
from mesh.integrations.outbound import _pinned_http_transport
from mesh.runtime.checkout import is_forbidden_host
from mesh.skill.ssrf import PinnedTarget, Resolver, SourceUnreachableError, resolve_pinned

if TYPE_CHECKING:
    import httpx

# integrations.md §2.3 — kind → normalized provider.
KIND_TO_PROVIDER: dict[str, str] = {
    "im_feishu": "feishu",
    "im_slack": "slack",
    "vcs_github": "github",
    "vcs_gitlab": "gitlab",
    "webhook_outbound": "webhook",
}

PROVIDER_TO_KIND: dict[str, str] = {v: k for k, v in KIND_TO_PROVIDER.items() if v != "webhook"}

# Signature verification outcomes (same vocabulary as autopilot webhook.py).
SIG_VALID = "valid"
SIG_INVALID = "invalid"
SIG_MISSING = "missing"


@dataclass(frozen=True)
class NormalizedEvent:
    """One platform event mapped onto the ingestion pipeline's contract."""

    external_event_id: str
    event_type: str
    external_ref: str  # chat_id / channel_id / owner/repo
    actor_key: str  # open_id / slack user_id / vcs login ('' when absent)
    tenant_key: str  # tenant_key / team_id / installation_id / instance host
    text: str  # message text / PR title — UNTRUSTED data (§6.15)
    extra: dict[str, Any] = field(default_factory=dict)  # vcs action/state etc.


def _constant_time_equals(expected: str, presented: str) -> bool:
    return hmac.compare_digest(expected.lower(), (presented or "").lower())


def _timestamp_within_window(raw_ts: str | None, *, now: datetime, tolerance: timedelta) -> bool:
    """Replay protection: reject timestamps outside ±tolerance (autopilot §3.2)."""
    try:
        ts = float(str(raw_ts or "").strip())
    except (TypeError, ValueError):
        return False
    return abs(now.timestamp() - ts) <= tolerance.total_seconds()


# ---------------------------------------------------------------------------
# Feishu / Lark — signature = SHA256(timestamp + nonce + encrypt_key + body)
# ---------------------------------------------------------------------------

_FEISHU_SIGNATURE_HEADERS = ("x-lark-signature", "x-feishu-signature")


def feishu_verify(
    *,
    encrypt_key: str,
    raw_body: bytes,
    headers: dict[str, str],
    now: datetime,
    tolerance: timedelta,
) -> str:
    """Feishu event signature (§3.2): ``SHA256(timestamp+nonce+encrypt_key+body)``.

    The signature is hex in ``X-Lark-Signature``; ``timestamp``/``nonce`` are
    request headers. Constant-time compare + timestamp replay window.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    presented = next((lowered[h] for h in _FEISHU_SIGNATURE_HEADERS if lowered.get(h)), None)
    if not presented:
        return SIG_MISSING
    timestamp = lowered.get("timestamp")
    nonce = lowered.get("nonce", "")
    if not _timestamp_within_window(timestamp, now=now, tolerance=tolerance):
        return SIG_INVALID
    material = f"{timestamp}{nonce}{encrypt_key}".encode() + raw_body
    expected = hashlib.sha256(material).hexdigest()
    if not _constant_time_equals(expected, presented):
        return SIG_INVALID
    return SIG_VALID


def feishu_tenant_key_from_config(config: dict[str, Any]) -> str:
    return str(config.get("tenant_key") or config.get("tenant_id") or "")


def feishu_normalize(payload: dict[str, Any], headers: dict[str, str]) -> NormalizedEvent:
    """Normalize ``im.message.receive_v1`` (schema 2.0 + legacy 1.0 shapes)."""
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    if not header and event:
        # Legacy v1 envelope: {"ts","uuid","event":{...},"token","type"}
        message = event.get("message") or event
        return NormalizedEvent(
            external_event_id=str(payload.get("uuid") or header.get("event_id") or ""),
            event_type=str(event.get("type") or payload.get("type") or "im.message.receive_v1"),
            external_ref=str(message.get("chat_id") or event.get("open_chat_id") or ""),
            actor_key=str(event.get("open_id") or (event.get("operator") or {}).get("open_id") or ""),
            tenant_key=str(payload.get("tenant_key") or ""),
            text=_feishu_message_text(message),
            extra={"schema": "1.0"},
        )
    message = event.get("message") or {}
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    return NormalizedEvent(
        external_event_id=str(header.get("event_id") or ""),
        event_type=str(header.get("event_type") or "im.message.receive_v1"),
        external_ref=str(message.get("chat_id") or ""),
        actor_key=str(sender_id.get("open_id") or ""),
        tenant_key=str(header.get("tenant_key") or ""),
        text=_feishu_message_text(message),
        extra={
            "message_type": str(message.get("message_type") or ""),
            "chat_type": str(message.get("chat_type") or ""),
            "mentions": event.get("message", {}).get("mentions") or [],
        },
    )


def _feishu_message_text(message: dict[str, Any]) -> str:
    """Extract plain text from feishu message content JSON (best effort)."""
    content = message.get("content")
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
            if isinstance(decoded, dict):
                return str(decoded.get("text") or "")
        except (json.JSONDecodeError, ValueError):
            return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return str(message.get("text") or "")


# ---------------------------------------------------------------------------
# Slack — X-Slack-Signature: v0=HMAC_SHA256(secret, "v0:" + ts + ":" + body)
# ---------------------------------------------------------------------------


def slack_verify(
    *,
    signing_secret: str,
    raw_body: bytes,
    headers: dict[str, str],
    now: datetime,
    tolerance: timedelta,
) -> str:
    lowered = {k.lower(): v for k, v in headers.items()}
    presented = lowered.get("x-slack-signature")
    if not presented:
        return SIG_MISSING
    timestamp = lowered.get("x-slack-request-timestamp")
    if not _timestamp_within_window(timestamp, now=now, tolerance=tolerance):
        return SIG_INVALID
    basestring = f"v0:{timestamp}:".encode() + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    if not _constant_time_equals(expected, presented):
        return SIG_INVALID
    return SIG_VALID


def slack_tenant_key_from_config(config: dict[str, Any]) -> str:
    return str(config.get("team_id") or "")


def slack_normalize(payload: dict[str, Any], headers: dict[str, str]) -> NormalizedEvent:
    """Normalize Events API payloads (``event_callback`` envelope)."""
    event = payload.get("event") or {}
    team_id = str(payload.get("team_id") or event.get("team") or "")
    event_ts = str(event.get("event_ts") or payload.get("event_time") or "")
    external_event_id = (
        f"{team_id}:{event_ts}" if team_id and event_ts else (event_ts or str(payload.get("event_id") or ""))
    )
    return NormalizedEvent(
        external_event_id=external_event_id,
        event_type=str(event.get("type") or payload.get("type") or "message"),
        external_ref=str(event.get("channel") or ""),
        actor_key=str(event.get("user") or ""),
        tenant_key=team_id,
        text=str(event.get("text") or ""),
        extra={"subtype": event.get("subtype"), "thread_ts": event.get("thread_ts")},
    )


# ---------------------------------------------------------------------------
# GitHub — X-Hub-Signature-256: sha256=HMAC_SHA256(webhook_secret, body)
# ---------------------------------------------------------------------------

GITHUB_DELIVERY_HEADER = "x-github-delivery"
GITHUB_EVENT_HEADER = "x-github-event"


def github_verify(
    *,
    webhook_secret: str,
    raw_body: bytes,
    headers: dict[str, str],
    now: datetime,
    tolerance: timedelta,
) -> str:
    """GitHub webhook HMAC (no timestamp in the scheme — dedup is the replay
    guard via ``X-GitHub-Delivery`` event ids)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    presented = lowered.get("x-hub-signature-256")
    if not presented:
        return SIG_MISSING
    expected = "sha256=" + hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not _constant_time_equals(expected, presented):
        return SIG_INVALID
    return SIG_VALID


def github_tenant_key_from_config(config: dict[str, Any]) -> str:
    return str(config.get("installation_id") or config.get("org") or "")


def github_normalize(payload: dict[str, Any], headers: dict[str, str]) -> NormalizedEvent:
    lowered = {k.lower(): v for k, v in headers.items()}
    repository = payload.get("repository") or {}
    installation = payload.get("installation") or {}
    pull_request = payload.get("pull_request") or {}
    sender = payload.get("sender") or {}
    extra: dict[str, Any] = {"action": payload.get("action")}
    if pull_request:
        extra.update(
            {
                "pr_number": pull_request.get("number"),
                "pr_title": pull_request.get("title"),
                "pr_state": pull_request.get("state"),
                "pr_merged": pull_request.get("merged"),
                "pr_merged_at": pull_request.get("merged_at"),
                "source_branch": (pull_request.get("head") or {}).get("ref"),
            }
        )
    ref = payload.get("ref")
    if ref:
        extra["ref"] = ref
    return NormalizedEvent(
        external_event_id=str(lowered.get(GITHUB_DELIVERY_HEADER) or ""),
        event_type=str(lowered.get(GITHUB_EVENT_HEADER) or "unknown"),
        external_ref=str(repository.get("full_name") or ""),
        actor_key=str(sender.get("login") or ""),
        tenant_key=str(installation.get("id") or repository.get("owner") or ""),
        text=str(pull_request.get("title") or payload.get("comment", {}).get("body") or ""),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# GitLab — X-Gitlab-Token shared secret (constant-time) or HMAC signature
# ---------------------------------------------------------------------------

GITLAB_EVENT_HEADER = "x-gitlab-event"


def gitlab_verify(
    *,
    webhook_token: str,
    raw_body: bytes,
    headers: dict[str, str],
    now: datetime,
    tolerance: timedelta,
) -> str:
    lowered = {k.lower(): v for k, v in headers.items()}
    signature = lowered.get("x-gitlab-signature")
    if signature:
        expected = "sha256=" + hmac.new(webhook_token.encode(), raw_body, hashlib.sha256).hexdigest()
        if not _constant_time_equals(expected, signature):
            return SIG_INVALID
        return SIG_VALID
    presented = lowered.get("x-gitlab-token")
    if not presented:
        return SIG_MISSING
    if not hmac.compare_digest(webhook_token, presented):
        return SIG_INVALID
    return SIG_VALID


def gitlab_tenant_key_from_config(config: dict[str, Any]) -> str:
    instance_url = str(config.get("instance_url") or "https://gitlab.com")
    # Normalize to bare host (the provider-tenant dimension of the key).
    host = instance_url.split("://", 1)[-1].split("/", 1)[0]
    return host.strip().lower() or "gitlab.com"


def gitlab_normalize(payload: dict[str, Any], headers: dict[str, str]) -> NormalizedEvent:
    lowered = {k.lower(): v for k, v in headers.items()}
    project = payload.get("project") or (payload.get("repository") or {})
    object_attributes = payload.get("object_attributes") or {}
    user = payload.get("user") or {}
    extra: dict[str, Any] = {
        "action": object_attributes.get("action") or payload.get("action"),
    }
    if object_attributes:
        extra.update(
            {
                "mr_iid": object_attributes.get("iid"),
                "mr_title": object_attributes.get("title"),
                "mr_state": object_attributes.get("state"),
                "source_branch": object_attributes.get("source_branch"),
            }
        )
    if payload.get("ref"):
        extra["ref"] = payload.get("ref")
    return NormalizedEvent(
        external_event_id=str(payload.get("event_uuid") or object_attributes.get("uuid") or ""),
        event_type=str(lowered.get(GITLAB_EVENT_HEADER) or payload.get("event_name") or "unknown"),
        external_ref=str(project.get("path_with_namespace") or ""),
        actor_key=str(user.get("username") or payload.get("user_username") or ""),
        tenant_key=gitlab_tenant_key_from_config({}),  # refined by the pipeline
        text=str(object_attributes.get("title") or object_attributes.get("description") or ""),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

ADAPTERS: dict[str, dict[str, Any]] = {
    "feishu": {
        "verify": feishu_verify,
        "normalize": feishu_normalize,
        "tenant_key_from_config": feishu_tenant_key_from_config,
        "secret_config_key": "encrypt_key_ref",
    },
    "slack": {
        "verify": slack_verify,
        "normalize": slack_normalize,
        "tenant_key_from_config": slack_tenant_key_from_config,
        "secret_config_key": "signing_secret_ref",
    },
    "github": {
        "verify": github_verify,
        "normalize": github_normalize,
        "tenant_key_from_config": github_tenant_key_from_config,
        "secret_config_key": "webhook_secret_ref",
    },
    "gitlab": {
        "verify": gitlab_verify,
        "normalize": gitlab_normalize,
        "tenant_key_from_config": gitlab_tenant_key_from_config,
        "secret_config_key": "webhook_token_ref",
    },
}


# ---------------------------------------------------------------------------
# Config write-time guards (README §6.16 — SSRF)
# ---------------------------------------------------------------------------


def validate_instance_url(url: str) -> None:
    """Write-time guard for self-hosted VCS ``instance_url`` (README §6.16).

    https-only + forbidden-host refusal, isomorphic with the webhook
    subscription URL guard (``outbound.validate_subscription_url``): a
    literal private / loopback / link-local / cloud-metadata target can
    never be persisted into integration config. DNS-level defeat (a public
    hostname resolving into private space, DNS rebinding) is closed at
    request time by :func:`test_connectivity`'s pinned resolver — the two
    layers mirror the webhook delivery path's create-time + deliver-time
    guards.
    """
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https":
        raise ValidationError(
            "instance_url must use https",
            code="invalid_url_scheme",
            details={"scheme": parsed.scheme},
        )
    host = parsed.hostname or ""
    if not host or is_forbidden_host(host):
        raise BusinessRuleError(
            "instance_url target is forbidden",
            code="ssrf_blocked",
            details={"host": host},
        )


def validate_integration_config(kind: str, config: dict[str, Any] | None) -> None:
    """Per-kind config guards applied at EVERY config write (§6.16).

    Currently: GitLab self-hosted ``instance_url`` must pass
    :func:`validate_instance_url`. Kinds without user-controlled outbound
    targets (platform APIs are hardcoded whitelisted hosts) pass through.
    """
    config = config or {}
    if kind == "vcs_gitlab":
        instance_url = config.get("instance_url")
        if instance_url:
            validate_instance_url(str(instance_url))


# ---------------------------------------------------------------------------
# Connectivity test (§3.1 :test — lightweight credential/connectivity check)
# ---------------------------------------------------------------------------

TEST_TIMEOUT_SECONDS = 5.0

# Per-kind default platform API bases (injectable for tests / private
# instances — GitLab self-hosted reads ``instance_url`` from config).
TEST_BASE_URLS: dict[str, str] = {
    "im_feishu": "https://open.feishu.cn",
    "im_slack": "https://slack.com",
    "im_dingtalk": "https://oapi.dingtalk.com",
    "vcs_github": "https://api.github.com",
    "vcs_gitlab": "https://gitlab.com",
}

HEALTH_HEALTHY = "healthy"
HEALTH_AUTH_FAILED = "auth_failed"
HEALTH_UNREACHABLE = "unreachable"


async def test_connectivity(
    kind: str,
    *,
    config: dict[str, Any] | None,
    secret: str | None,
    http_client: httpx.AsyncClient | None = None,
    base_urls: dict[str, str] | None = None,
    resolver: Resolver | None = None,
) -> tuple[str, str | None]:
    """Lightweight platform-API round-trip; returns ``(health_state, detail)``.

    Classifies exactly three ways (§3.1): credentials accepted →
    ``healthy``; credentials rejected by the platform (401/403 or a
    provider-level auth error code) → ``auth_failed`` (drives the
    "re-authorize" banner, §4.1); network/DNS/timeout failure →
    ``unreachable``. No side effects on the platform side (read-only
    identity/token endpoints only). ``webhook_outbound`` has no platform
    credentials — it reports ``healthy`` with an explanatory detail
    (subscriptions verify via ``:send-test`` instead).

    SSRF (README §6.16): every platform base except GitLab self-hosted is
    a hardcoded whitelisted host. A user-configured ``instance_url`` is
    treated exactly like a webhook delivery target — resolve ONCE through
    the shared ``resolve_pinned`` guard and connect ONLY to the pinned
    public IPs (DNS-rebinding TOCTOU closure, redirects never followed);
    a refused target classifies as ``unreachable``/``ssrf_blocked`` and
    nothing is dialed. ``resolver`` is injectable for tests.
    """
    import httpx as _httpx

    config = config or {}
    bases = {**TEST_BASE_URLS, **(base_urls or {})}
    if kind == "webhook_outbound":
        return HEALTH_HEALTHY, "outbound_only_no_credentials"
    if not secret:
        return HEALTH_AUTH_FAILED, "missing_credentials"

    # GitLab self-hosted: validate + pin BEFORE building the client (write-
    # time validate_instance_url already refuses literal private targets;
    # this closes public hostnames resolving into private space).
    pinned: PinnedTarget | None = None
    if kind == "vcs_gitlab" and http_client is None:
        base = str(config.get("instance_url") or bases[kind]).rstrip("/")
        try:
            pinned = resolve_pinned(base, resolver=resolver)
        except SourceUnreachableError:
            return HEALTH_UNREACHABLE, "ssrf_blocked"

    if http_client is not None:
        client = http_client
        owns_client = False
    elif pinned is not None:
        client = _httpx.AsyncClient(
            timeout=TEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=_pinned_http_transport(pinned.pinned_ips),
        )
        owns_client = True
    else:
        client = _httpx.AsyncClient(timeout=TEST_TIMEOUT_SECONDS)
        owns_client = True
    try:
        if kind == "im_feishu":
            app_id = str(config.get("app_id") or "")
            if not app_id:
                return HEALTH_AUTH_FAILED, "missing_app_id"
            try:
                resp = await client.post(
                    f"{bases[kind]}/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": app_id, "app_secret": secret},
                )
            except (_httpx.HTTPError, OSError) as exc:
                return HEALTH_UNREACHABLE, type(exc).__name__
            if resp.status_code != 200:
                return HEALTH_UNREACHABLE, f"http_{resp.status_code}"
            body = _json_or_empty(resp)
            return (
                (HEALTH_HEALTHY, None)
                if body.get("code") == 0
                else (HEALTH_AUTH_FAILED, f"code_{body.get('code')}")
            )
        if kind == "im_slack":
            try:
                resp = await client.post(
                    f"{bases[kind]}/api/auth.test",
                    headers={"Authorization": f"Bearer {secret}"},
                )
            except (_httpx.HTTPError, OSError) as exc:
                return HEALTH_UNREACHABLE, type(exc).__name__
            if resp.status_code != 200:
                return HEALTH_UNREACHABLE, f"http_{resp.status_code}"
            body = _json_or_empty(resp)
            return (
                (HEALTH_HEALTHY, None)
                if body.get("ok")
                else (HEALTH_AUTH_FAILED, str(body.get("error") or "invalid_auth"))
            )
        if kind == "im_dingtalk":
            app_key = str(config.get("app_key") or config.get("app_id") or "")
            if not app_key:
                return HEALTH_AUTH_FAILED, "missing_app_key"
            try:
                resp = await client.get(
                    f"{bases[kind]}/gettoken",
                    params={"appkey": app_key, "appsecret": secret},
                )
            except (_httpx.HTTPError, OSError) as exc:
                return HEALTH_UNREACHABLE, type(exc).__name__
            if resp.status_code != 200:
                return HEALTH_UNREACHABLE, f"http_{resp.status_code}"
            body = _json_or_empty(resp)
            return (
                (HEALTH_HEALTHY, None)
                if body.get("errcode") == 0
                else (HEALTH_AUTH_FAILED, f"errcode_{body.get('errcode')}")
            )
        if kind == "vcs_github":
            try:
                resp = await client.get(
                    f"{bases[kind]}/user",
                    headers={
                        "Authorization": f"Bearer {secret}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            except (_httpx.HTTPError, OSError) as exc:
                return HEALTH_UNREACHABLE, type(exc).__name__
            if resp.status_code == 200:
                return HEALTH_HEALTHY, None
            if resp.status_code in (401, 403):
                return HEALTH_AUTH_FAILED, f"http_{resp.status_code}"
            return HEALTH_UNREACHABLE, f"http_{resp.status_code}"
        if kind == "vcs_gitlab":
            base = str(config.get("instance_url") or bases[kind]).rstrip("/")
            try:
                resp = await client.get(f"{base}/api/v4/user", headers={"PRIVATE-TOKEN": secret})
            except (_httpx.HTTPError, OSError) as exc:
                return HEALTH_UNREACHABLE, type(exc).__name__
            if resp.status_code == 200:
                return HEALTH_HEALTHY, None
            if resp.status_code in (401, 403):
                return HEALTH_AUTH_FAILED, f"http_{resp.status_code}"
            return HEALTH_UNREACHABLE, f"http_{resp.status_code}"
        return HEALTH_AUTH_FAILED, "unsupported_kind"
    finally:
        if owns_client:
            await client.aclose()


def _json_or_empty(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def adapter_for(kind: str) -> dict[str, Any]:
    provider = KIND_TO_PROVIDER.get(kind)
    adapter = ADAPTERS.get(provider or "")
    if adapter is None:
        raise KeyError(f"no connector adapter for kind {kind!r}")
    return adapter


__all__ = [
    "ADAPTERS",
    "GITHUB_DELIVERY_HEADER",
    "GITHUB_EVENT_HEADER",
    "GITLAB_EVENT_HEADER",
    "KIND_TO_PROVIDER",
    "NormalizedEvent",
    "PROVIDER_TO_KIND",
    "SIG_INVALID",
    "SIG_MISSING",
    "SIG_VALID",
    "adapter_for",
    "feishu_normalize",
    "feishu_tenant_key_from_config",
    "feishu_verify",
    "github_normalize",
    "github_tenant_key_from_config",
    "github_verify",
    "gitlab_normalize",
    "gitlab_tenant_key_from_config",
    "gitlab_verify",
    "slack_normalize",
    "slack_tenant_key_from_config",
    "slack_verify",
    "validate_instance_url",
    "validate_integration_config",
]
