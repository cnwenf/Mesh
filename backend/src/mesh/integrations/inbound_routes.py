"""Inbound callback endpoints (integrations.md §3.2) — platform-signature
authenticated, NOT Bearer. Responses are the bare JSON contract external
platforms expect (NOT the §6.14 envelope — same exemption as autopilot
inbound webhooks).

DoS hardening (MEDIUM-3): these endpoints are UNAUTHENTICATED (the
signature is verified inside the pipeline), so every route enforces a
rate limit and a bounded-stream body cap BEFORE any signature work or
ledger writes happen — an attacker without credentials can neither burn
CPU on O(N) decrypt attempts nor inflate the audit ledger. Oversize bodies
get a bare 413; generic over-rate callers get a bare 429. DingTalk instead
uses silent 200 limits to avoid retry amplification.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.integrations.cards import handle_card_callback
from mesh.integrations.inbound import _lookup_by_config_value, process_inbound

logger = logging.getLogger("mesh.integrations.inbound_routes")

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations-inbound"])

# Per-IP sliding window shared across ALL inbound endpoints (a malicious
# source rotating endpoint paths still consumes one budget).
INBOUND_RATE_LIMIT = 120
INBOUND_RATE_WINDOW_SECONDS = 60
# Body cap: IM/VCS event payloads are small; 1 MiB matches the issue.md
# attachment ceiling. Content-Length is checked BEFORE reading; streamed
# bytes are counted and rejected at the crossing chunk (chunked-transfer
# defense in depth).
INBOUND_BODY_MAX_BYTES = 1024 * 1024

# auth.md §3.6 inbound-callback row (MES-82/87): the pre-signature COARSE
# anti-abuse layer for the DingTalk callback is keyed by the (integration,
# IP) tuple and answers over-limit callers with a SILENT 200 — a non-2xx
# would trigger the external platform's retry amplification. Audit + alert
# only; the post-signature semantic guardrails (§2.10) are the next layer.
DINGTALK_PRE_LIMIT_PER_MIN = 120


async def _locate_dingtalk_pre_limit_integration(
    session: AsyncSession, payload: dict[str, object]
) -> uuid.UUID | None:
    """Resolve the same exact DingTalk route used by signature admission.

    The pre-signature limiter is a coarse anti-amplification layer, but its
    bucket still must not merge sibling robots that share a corp. Missing or
    ambiguous identities deliberately fall back to the unattributable IP
    bucket; no arbitrary first row is selected.
    """
    corp_id = str(payload.get("chatbotCorpId") or "")
    robot_code = str(payload.get("robotCode") or "")
    if not corp_id or not robot_code:
        return None
    rows = await _lookup_by_config_value(session, kind="im_dingtalk", key="corp_id", value=corp_id)
    matches = [
        row
        for row in rows
        if str((row[4] or {}).get("receive_mode") or "") == "http"
        and str((row[4] or {}).get("robot_code") or (row[4] or {}).get("app_key") or "") == robot_code
    ]
    return matches[0][0] if len(matches) == 1 else None


def _client_ip(request: Request) -> str:
    if request.client is not None:
        return request.client.host
    return "unknown"


def _declared_body_too_large(request: Request) -> JSONResponse | None:
    """Content-Length PRE-CHECK (§3.2 DoS hardening item 2, first pass).

    Rejects a declared-oversize body BEFORE it is buffered into memory;
    ``_read_body`` counts the ACTUAL bytes while streaming and stops on the
    first crossing chunk (second pass — a lying Content-Length cannot bypass
    the cap). Both passes are applied on EVERY inbound route, including the
    DingTalk callback (whose pre-signature limiter replaces the shared per-IP
    429, not the body cap).
    """
    declared = request.headers.get("content-length")
    if declared is None:
        return None  # chunked / absent → the post-read check still applies
    try:
        declared_bytes = int(declared)
    except ValueError:
        return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    if declared_bytes > INBOUND_BODY_MAX_BYTES:
        return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    return None


async def _guard(request: Request) -> JSONResponse | None:
    """Pre-body guards: per-IP rate limit + declared body size.

    Returns an error response to short-circuit, or None to proceed.
    """
    limiter = request.app.state.rate_limiter
    remaining, _reset_in = await limiter.check(
        f"integration-inbound:{_client_ip(request)}",
        limit=INBOUND_RATE_LIMIT,
        window_seconds=INBOUND_RATE_WINDOW_SECONDS,
    )
    if remaining < 0:
        return JSONResponse(status_code=429, content={"error": "rate_limited"})
    return _declared_body_too_large(request)


async def _read_body(request: Request) -> bytes | JSONResponse:
    """Read at most the configured cap, stopping at the crossing chunk."""
    raw_body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > INBOUND_BODY_MAX_BYTES - len(raw_body):
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        raw_body.extend(chunk)
    return bytes(raw_body)


def _tolerance(request: Request):
    return request.app.state.settings.integration_signature_tolerance


async def _run_inbound(request: Request, kind: str, *, raw_body: bytes | None = None) -> JSONResponse:
    """Run the pipeline; ``raw_body`` skips the guards when the caller has
    already applied them (the DingTalk route guards + pre-limits first)."""
    if raw_body is None:
        guarded = await _guard(request)
        if guarded is not None:
            return guarded
        raw_body = await _read_body(request)
        if isinstance(raw_body, JSONResponse):
            return raw_body
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    headers = dict(request.headers)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        status_code, body = await process_inbound(
            session,
            kind=kind,
            raw_body=raw_body,
            headers=headers,
            signing_secret=settings.jwt_secret,
            now=now,
            tolerance=_tolerance(request),
            redis=getattr(request.app.state, "redis", None),
            settings=settings,
            text_max_chars=settings.im_inbound_text_max_chars,
        )
    return JSONResponse(status_code=status_code, content=body)


async def _run_card(request: Request, kind: str) -> JSONResponse:
    guarded = await _guard(request)
    if guarded is not None:
        return guarded
    raw_body = await _read_body(request)
    if isinstance(raw_body, JSONResponse):
        return raw_body
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    headers = dict(request.headers)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        status_code, body = await handle_card_callback(
            session,
            session_factory,
            kind=kind,
            raw_body=raw_body,
            headers=headers,
            signing_secret=settings.jwt_secret,
            now=now,
            tolerance=_tolerance(request),
            app_base_url=settings.app_base_url or "",
        )
    return JSONResponse(status_code=status_code, content=body)


@router.post("/dingtalk/events")
async def dingtalk_events(request: Request) -> JSONResponse:
    """DingTalk HTTP receive mode callback (§3.2 ``receive_mode='http'``).

    Guard order (unauthenticated surface — DoS hardening): declared body
    size pre-check; IP-only 120/min silent coarse limit before consuming
    the body; bounded streaming actual-size check; then, when the payload
    locates an integration, its separate (integration, IP) 120/min silent
    limit. Unlocatable payloads are charged only once, to the IP window.
    The generic per-IP 429 guard is deliberately NOT applied here: §3.6
    reserves 429 for the other endpoint classes. Signature verification
    runs afterwards, inside ``process_inbound``.
    """
    oversized = _declared_body_too_large(request)
    if oversized is not None:
        return oversized  # pre-check pass: declared oversize → 413, never buffered

    # A source already over its coarse budget must not make the service
    # consume even the first chunk. Keep this key independent of integration
    # lookup, which necessarily requires a bounded body read.
    if await _dingtalk_pre_limit_exceeded(request, integration_id=None):
        return _dingtalk_silent_pre_limit_response(request, integration_id=None)

    raw_body = await _read_body(request)
    if isinstance(raw_body, JSONResponse):
        return raw_body  # re-check pass: actual oversize (lying Content-Length)

    # Locate the integration BEFORE signature work so a located callback also
    # consumes its exact (integration, IP) budget. Unlocatable payloads already
    # consumed the IP-only budget above and must not be double-counted.
    try:
        parsed = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(parsed, dict):
            parsed = {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {}
    integration_id: uuid.UUID | None = None
    if parsed:
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            integration_id = await _locate_dingtalk_pre_limit_integration(session, parsed)
    if integration_id is not None and await _dingtalk_pre_limit_exceeded(
        request, integration_id=integration_id
    ):
        return _dingtalk_silent_pre_limit_response(
            request, integration_id=integration_id
        )

    return await _run_inbound(request, "im_dingtalk", raw_body=raw_body)


def _dingtalk_silent_pre_limit_response(
    request: Request, *, integration_id: uuid.UUID | None
) -> JSONResponse:
    """Audit a coarse-limit rejection while keeping the caller response silent."""
    logger.warning(
        "AUDIT: dingtalk inbound pre-signature rate limit exceeded "
        "(integration=%s ip=%s) — silent 200, no distribution",
        integration_id,
        _client_ip(request),
    )
    return JSONResponse(
        status_code=200,
        content={
            "received": True,
            "event_id": "",
            "process_status": "received",
        },
    )


async def _dingtalk_pre_limit_exceeded(
    request: Request, *, integration_id: uuid.UUID | None
) -> bool:
    """IP-only or (integration, IP) sliding window — genuinely non-raising.

    Shares the Redis sliding-window primitive with the auth rate limiter
    but NEVER raises: over-limit inbound callbacks must answer 200, and a
    Redis hiccup must not turn every callback into a 500 (exactly the
    retry amplification this layer exists to prevent) — fail OPEN, the
    signature check is the hard gate. ``None`` selects the independent
    IP-only window used before body consumption; a UUID selects the located
    integration's second window.
    """
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return False  # fail OPEN: signature verification is the gate
    moment = time.time()
    window = INBOUND_RATE_WINDOW_SECONDS
    scope = str(integration_id) if integration_id is not None else "ip"
    key = f"mesh:dingtalk-prelimit:{scope}:{_client_ip(request)}"
    hit = f"{moment}:{uuid.uuid4().hex}"
    try:
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, moment - window)
        pipe.zcard(key)
        pipe.zadd(key, {hit: moment})
        pipe.expire(key, window)
        _removed, count, _added, _ttl = await pipe.execute()
    except Exception:  # noqa: BLE001 — Redis flakiness ⇒ fail OPEN, never 500
        logger.warning(
            "dingtalk pre-signature limiter unavailable (redis) — failing open for integration=%s ip=%s",
            integration_id,
            _client_ip(request),
        )
        return False
    exceeded = int(count) >= DINGTALK_PRE_LIMIT_PER_MIN
    if exceeded:
        # The hit was speculative: retain exactly the accepted budget, rather
        # than letting an unauthenticated flood grow this ZSET without bound.
        # Remove by the unique member (not score), so concurrent same-moment
        # accepted hits cannot be deleted accidentally.
        try:
            await redis.zrem(key, hit)
        except Exception:  # noqa: BLE001 — verdict stays silent-limit on cleanup failure
            logger.warning(
                "dingtalk pre-signature limiter cleanup unavailable (redis) "
                "for integration=%s ip=%s",
                integration_id,
                _client_ip(request),
            )
        return True
    return False


@router.post("/feishu/events")
async def feishu_events(request: Request) -> JSONResponse:
    return await _run_inbound(request, "im_feishu")


@router.post("/slack/events")
async def slack_events(request: Request) -> JSONResponse:
    return await _run_inbound(request, "im_slack")


@router.post("/github/events")
async def github_events(request: Request) -> JSONResponse:
    return await _run_inbound(request, "vcs_github")


@router.post("/gitlab/events")
async def gitlab_events(request: Request) -> JSONResponse:
    return await _run_inbound(request, "vcs_gitlab")


@router.post("/feishu/cards")
async def feishu_cards(request: Request) -> JSONResponse:
    return await _run_card(request, "im_feishu")


@router.post("/slack/cards")
async def slack_cards(request: Request) -> JSONResponse:
    return await _run_card(request, "im_slack")


@router.post("/dingtalk/cards")
async def dingtalk_cards(request: Request) -> JSONResponse:
    """DingTalk interactive-card HTTP callback (``callbackType='HTTP'``;
    Stream mode delivers the same topic over the long connection and the
    stream worker routes it to the same handler function). Signature
    scheme: §3.2 DingTalk row (``timestamp`` + ``sign`` headers, official
    ±3600s tolerance)."""
    return await _run_card(request, "im_dingtalk")


__all__ = [
    "DINGTALK_PRE_LIMIT_PER_MIN",
    "INBOUND_BODY_MAX_BYTES",
    "INBOUND_RATE_LIMIT",
    "INBOUND_RATE_WINDOW_SECONDS",
    "router",
]
