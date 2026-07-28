"""Inbound callback endpoints (integrations.md §3.2) — platform-signature
authenticated, NOT Bearer. Responses are the bare JSON contract external
platforms expect (NOT the §6.14 envelope — same exemption as autopilot
inbound webhooks).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mesh.integrations.cards import handle_card_callback
from mesh.integrations.inbound import process_inbound

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations-inbound"])


def _tolerance(request: Request):
    return request.app.state.settings.integration_signature_tolerance


async def _run_inbound(request: Request, kind: str) -> JSONResponse:
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    raw_body = await request.body()
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
        )
    return JSONResponse(status_code=status_code, content=body)


async def _run_card(request: Request, kind: str) -> JSONResponse:
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    raw_body = await request.body()
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


__all__ = ["router"]
