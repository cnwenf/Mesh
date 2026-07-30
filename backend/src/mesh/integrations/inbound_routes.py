"""Inbound callback endpoints (integrations.md §3.2) — platform-signature
authenticated, NOT Bearer. Responses are the bare JSON contract external
platforms expect (NOT the §6.14 envelope — same exemption as autopilot
inbound webhooks).

DoS hardening (MEDIUM-3): these endpoints are UNAUTHENTICATED (the
signature is verified inside the pipeline), so every route enforces a
per-IP sliding-window rate limit and a body size cap BEFORE any
signature work or ledger writes happen — an attacker without credentials
can neither burn CPU on O(N) decrypt attempts nor inflate the audit
ledger. Oversize bodies get a bare 413; over-rate callers get a bare
429 (platforms retry both, which is the desired backpressure).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mesh.integrations.cards import handle_card_callback
from mesh.integrations.inbound import process_inbound

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations-inbound"])

# Per-IP sliding window shared across ALL inbound endpoints (a malicious
# source rotating endpoint paths still consumes one budget).
INBOUND_RATE_LIMIT = 120
INBOUND_RATE_WINDOW_SECONDS = 60
# Body cap: IM/VCS event payloads are small; 1 MiB matches the issue.md
# attachment ceiling. Content-Length is checked BEFORE reading; the read
# length is re-checked (chunked-transfer defense in depth).
INBOUND_BODY_MAX_BYTES = 1024 * 1024


def _client_ip(request: Request) -> str:
    if request.client is not None:
        return request.client.host
    return "unknown"


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
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except ValueError:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        if declared_bytes > INBOUND_BODY_MAX_BYTES:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    return None


async def _read_body(request: Request) -> bytes | JSONResponse:
    """Read the body, enforcing the cap on the ACTUAL length too."""
    raw_body = await request.body()
    if len(raw_body) > INBOUND_BODY_MAX_BYTES:
        return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    return raw_body


def _tolerance(request: Request):
    return request.app.state.settings.integration_signature_tolerance


async def _run_inbound(request: Request, kind: str) -> JSONResponse:
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
            redis=request.app.state.redis,
            settings=settings,
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
        )
    return JSONResponse(status_code=status_code, content=body)


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


__all__ = [
    "INBOUND_BODY_MAX_BYTES",
    "INBOUND_RATE_LIMIT",
    "INBOUND_RATE_WINDOW_SECONDS",
    "router",
]
