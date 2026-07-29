"""Integration management routes (integrations.md §3.1 / §3.3).

Middleware chain per README §6.14: Bearer → membership → RBAC → rate limit.
Writes require ``integration:manage`` (admin/owner); reads need workspace
membership. External-identity link/unlink is member-level (owner-only
semantics enforced in the service, R5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.db.models.integration import Integration, VcsLink
from mesh.db.models.issue import Issue
from mesh.db.models.user import User
from mesh.errors import BusinessRuleError, NotFoundError
from mesh.integrations import identities as identities_mod
from mesh.integrations import oauth as oauth_mod
from mesh.integrations import outbound as outbound_mod
from mesh.integrations import queue_api as queue_api_mod
from mesh.integrations import vcs_links as vcs_links_mod
from mesh.integrations.schemas import (
    CreateBindingRequest,
    CreateIntegrationRequest,
    CreateSubscriptionRequest,
    LinkConfirmRequest,
    LinkIdentityRequest,
    PatchBindingRequest,
    PatchIntegrationRequest,
    PatchSubscriptionRequest,
    RotateSecretRequest,
    VcsLinkCreateRequest,
    VcsResolveRequest,
)
from mesh.integrations.service import (
    IntegrationService,
    render_event,
    render_integration,
)

router = APIRouter(prefix="/api/v1", tags=["integrations"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

# §4.1 connected-list column "近7天事件量" window.
EVENTS_WINDOW_DAYS = 7


def _service(request: Request) -> IntegrationService:
    return request.app.state.integration_service


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _path_uuid(value: str, *, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(f"{what} not found") from exc


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"integration-write:{user.id}:{_client_ip(request)}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


# ---------------------------------------------------------------------------
# Integrations CRUD
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/integrations")
async def list_integrations(
    request: Request,
    workspace_id: str,
    kind: str | None = None,
    status: str | None = None,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _service(request)
    page = await service.list_integrations(
        workspace_id=context.workspace.id,
        kind=kind,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    counts = await service.event_counts_since(
        workspace_id=context.workspace.id,
        integration_ids=[row.id for row in page.items],
        since=datetime.now(UTC) - timedelta(days=EVENTS_WINDOW_DAYS),
    )
    return {
        "data": [render_integration(row, events_7d=counts.get(row.id, 0)) for row in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("/workspaces/{workspace_id}/integrations", status_code=201)
async def create_integration(
    request: Request,
    response: Response,
    workspace_id: str,
    body: CreateIntegrationRequest,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    created = await _service(request).create_integration(
        workspace_id=context.workspace.id,
        creator=context.member,
        kind=body.kind,
        name=body.name,
        config=body.config,
        secret=body.secret,
    )
    # §6.14 success envelope (MEDIUM-4: the one admin endpoint that
    # previously bypassed it).
    return {"data": created}


@router.get("/workspaces/{workspace_id}/integrations/{integration_id}")
async def get_integration(
    request: Request,
    workspace_id: str,
    integration_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    integration = await _service(request).get_integration(
        workspace_id=context.workspace.id,
        integration_id=_path_uuid(integration_id, what="integration"),
    )
    return {"data": render_integration(integration)}


@router.patch("/workspaces/{workspace_id}/integrations/{integration_id}")
async def patch_integration(
    request: Request,
    response: Response,
    workspace_id: str,
    integration_id: str,
    body: PatchIntegrationRequest,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    return {
        "data": await _service(request).update_integration(
            workspace_id=context.workspace.id,
            integration_id=_path_uuid(integration_id, what="integration"),
            name=body.name,
            status=body.status,
            config=body.config,
        )
    }


@router.delete("/workspaces/{workspace_id}/integrations/{integration_id}", status_code=204)
async def delete_integration(
    request: Request,
    response: Response,
    workspace_id: str,
    integration_id: str,
    force: str | None = Query(default=None),
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> Response:
    """Soft-delete; ``?force=cancel`` first drains every binding's queue
    (§3.9 delete protection) before the soft delete."""
    await _rate_limit_write(request, user, response)
    await _service(request).delete_integration(
        workspace_id=context.workspace.id,
        integration_id=_path_uuid(integration_id, what="integration"),
        force=force,
        force_cancel_wait_seconds=request.app.state.settings.im_force_cancel_wait_seconds,
        actor_member_id=context.member.id,
    )
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/integrations/{integration_id}/rotate-secret")
async def rotate_integration_secret(
    request: Request,
    response: Response,
    workspace_id: str,
    integration_id: str,
    body: RotateSecretRequest,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    return {
        "data": await _service(request).rotate_secret(
            workspace_id=context.workspace.id,
            integration_id=_path_uuid(integration_id, what="integration"),
            secret=body.secret,
        )
    }


@router.post("/workspaces/{workspace_id}/integrations/{integration_id}:test")
async def test_integration(
    request: Request,
    response: Response,
    workspace_id: str,
    integration_id: str,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    """Test connection (§3.1, P1): lightweight platform API round-trip.

    Verifies credentials/connectivity WITHOUT side effects and drives the
    connector health fields (MEDIUM-P2): the outcome is persisted to
    ``health_state`` / ``last_error`` / ``last_success_at`` so the §4.1
    badge and "re-authorize" banner reflect reality.
    """
    await _rate_limit_write(request, user, response)
    return {
        "data": await _service(request).test_connection(
            workspace_id=context.workspace.id,
            integration_id=_path_uuid(integration_id, what="integration"),
        )
    }


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/integrations/{integration_id}/bindings")
async def list_bindings(
    request: Request,
    workspace_id: str,
    integration_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    return {
        "data": await _service(request).list_bindings(
            workspace_id=context.workspace.id,
            integration_id=_path_uuid(integration_id, what="integration"),
        )
    }


@router.post(
    "/workspaces/{workspace_id}/integrations/{integration_id}/bindings",
    status_code=201,
)
async def create_binding(
    request: Request,
    response: Response,
    workspace_id: str,
    integration_id: str,
    body: CreateBindingRequest,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    return {
        "data": await _service(request).create_binding(
            workspace_id=context.workspace.id,
            integration_id=_path_uuid(integration_id, what="integration"),
            external_ref=body.external_ref,
            scope=body.scope,
            project_id=_path_uuid(body.project_id, what="project") if body.project_id else None,
            match_config=body.match_config,
            bound_agent_id=(_path_uuid(body.bound_agent_id, what="agent") if body.bound_agent_id else None),
        )
    }


@router.patch("/workspaces/{workspace_id}/integration-bindings/{binding_id}")
async def patch_binding(
    request: Request,
    response: Response,
    workspace_id: str,
    binding_id: str,
    body: PatchBindingRequest,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    bound_agent: uuid.UUID | None
    if body.clear_bound_agent:
        bound_agent = None
    elif body.bound_agent_id:
        bound_agent = _path_uuid(body.bound_agent_id, what="agent")
    else:
        bound_agent = ...  # type: ignore[assignment]  # unchanged sentinel
    return {
        "data": await _service(request).update_binding(
            workspace_id=context.workspace.id,
            binding_id=_path_uuid(binding_id, what="binding"),
            match_config=body.match_config,
            bound_agent_id=bound_agent,
            status=body.status,
        )
    }


@router.delete("/workspaces/{workspace_id}/integration-bindings/{binding_id}", status_code=204)
async def delete_binding(
    request: Request,
    response: Response,
    workspace_id: str,
    binding_id: str,
    force: str | None = Query(default=None),
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> Response:
    """Delete a binding (physical). Delete protection (§3.9): non-terminal
    queue items + no ``?force=cancel`` → 409 ``binding_has_active_queue``;
    ``?force=cancel`` drains them first (orphans survive as audit rows)."""
    await _rate_limit_write(request, user, response)
    await _service(request).delete_binding(
        workspace_id=context.workspace.id,
        binding_id=_path_uuid(binding_id, what="binding"),
        force=force,
        force_cancel_wait_seconds=request.app.state.settings.im_force_cancel_wait_seconds,
        actor_member_id=context.member.id,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Event ledger (observability, §5.5)
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/integrations/{integration_id}/events")
async def list_events(
    request: Request,
    workspace_id: str,
    integration_id: str,
    signature_status: str | None = None,
    process_status: str | None = None,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    page = await _service(request).list_events(
        workspace_id=context.workspace.id,
        integration_id=_path_uuid(integration_id, what="integration"),
        signature_status=signature_status,
        process_status=process_status,
        cursor=cursor,
        limit=limit,
    )
    return {
        "data": [render_event(row) for row in page.items],
        "next_cursor": page.next_cursor,
    }


# ---------------------------------------------------------------------------
# External identities (link / link-confirm / unlink, R5 owner-only)
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/external-identities")
async def list_external_identities(
    request: Request,
    workspace_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    async with request.app.state.session_factory() as session:
        rows = await identities_mod.list_own_identities(session, member=context.member)
    return {"data": [identities_mod.render_identity(row) for row in rows]}


@router.post("/workspaces/{workspace_id}/external-identities:link")
async def link_external_identity(
    request: Request,
    response: Response,
    workspace_id: str,
    body: LinkIdentityRequest,
    context: WorkspaceContext = Depends(require_workspace()),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _service(request)
    integration = await service.get_integration(
        workspace_id=context.workspace.id,
        integration_id=_path_uuid(body.integration_id, what="integration"),
    )
    from mesh.db.tenant import set_tenant_context as _set_tenant

    async with request.app.state.session_factory() as session, session.begin():
        await _set_tenant(session, context.workspace.id)
        result = await identities_mod.start_link(
            session,
            redis=request.app.state.redis,
            delivery=request.app.state.identity_code_delivery,
            workspace_id=context.workspace.id,
            member=context.member,
            provider=body.provider,
            integration=integration,
            external_user_key=body.external_user_key,
        )
    return {"data": result}


@router.post("/workspaces/{workspace_id}/external-identities:link-confirm")
async def confirm_external_identity(
    request: Request,
    response: Response,
    workspace_id: str,
    body: LinkConfirmRequest,
    context: WorkspaceContext = Depends(require_workspace()),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    from mesh.db.tenant import set_tenant_context as _set_tenant

    async with request.app.state.session_factory() as session, session.begin():
        await _set_tenant(session, context.workspace.id)
        identity = await identities_mod.confirm_link(
            session,
            redis=request.app.state.redis,
            workspace_id=context.workspace.id,
            member=context.member,
            provider=body.provider,
            code=body.code,
        )
    return {"data": identity}


@router.delete("/workspaces/{workspace_id}/external-identities/{identity_id}", status_code=204)
async def unlink_external_identity(
    request: Request,
    response: Response,
    workspace_id: str,
    identity_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
    user: User = Depends(get_current_user),
) -> Response:
    await _rate_limit_write(request, user, response)
    from mesh.db.tenant import set_tenant_context as _set_tenant

    async with request.app.state.session_factory() as session, session.begin():
        await _set_tenant(session, context.workspace.id)
        await identities_mod.unlink_identity(
            session,
            workspace_id=context.workspace.id,
            member=context.member,
            identity_id=_path_uuid(identity_id, what="external identity"),
        )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# OAuth authorization-code + PKCE (§3.1)
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/integrations/oauth/{kind}/authorize",
    include_in_schema=False,
)
async def oauth_authorize(
    request: Request,
    workspace_id: str,
    kind: str,
    name: str | None = None,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
) -> RedirectResponse:
    settings = request.app.state.settings
    callback_url = f"{settings.app_base_url or ''}/api/v1/integrations/oauth/{kind}/callback"
    url = await oauth_mod.begin_authorization(
        request.app.state.redis,
        workspace_id=context.workspace.id,
        member_id=context.member.id,
        kind=kind,
        callback_url=callback_url,
        name=name,
    )
    return RedirectResponse(url, status_code=302)


@router.get("/integrations/oauth/{kind}/callback", include_in_schema=False)
async def oauth_callback(
    request: Request,
    kind: str,
    state: str = "",
    code: str = "",
) -> RedirectResponse:
    """OAuth round-trip completion (MEDIUM-1: credentials ARE persisted).

    §3.1 line 523: the refresh token is stored as ciphertext ONLY
    (``secret_ref``, §6.16) — the callback creates the integration row
    itself (state carries workspace/member/name), so the token never
    depends on a later manual step and never touches plaintext storage.
    """
    from mesh.db.models.member import Member
    from mesh.db.tenant import set_tenant_context

    settings = request.app.state.settings
    front = settings.app_base_url or ""
    try:
        record = await oauth_mod.consume_state(request.app.state.redis, state=state)
        if record is None or record.get("kind") != kind:
            return RedirectResponse(f"{front}/integrations?oauth=error", status_code=302)
        tokens = await oauth_mod.exchange_code(
            kind=kind,
            code=code,
            code_verifier=str(record["code_verifier"]),
            callback_url=str(record["callback_url"]),
        )
        # Refresh token preferred (long-lived); providers without refresh
        # tokens (e.g. GitHub) fall back to the access token — ciphertext
        # either way, minimal scope enforced at authorize time.
        secret_value = str(tokens.get("refresh_token") or tokens.get("access_token") or "")
        if not secret_value:
            return RedirectResponse(f"{front}/integrations?oauth=error", status_code=302)
        config: dict[str, object] = {}
        provider_tenant_id = tokens.get("team_id") or (tokens.get("team") or {}).get("id")
        if provider_tenant_id:
            config["provider_tenant_id"] = str(provider_tenant_id)
        workspace_id = uuid.UUID(str(record["workspace_id"]))
        member_id = uuid.UUID(str(record["member_id"]))
        async with request.app.state.session_factory() as session:
            await set_tenant_context(session, workspace_id)
            member = await session.get(Member, member_id)
        if member is None or member.status != "active":
            return RedirectResponse(f"{front}/integrations?oauth=error", status_code=302)
        name = str(record.get("name") or "") or f"{kind}-oauth"
        service: IntegrationService = request.app.state.integration_service
        created = await service.create_integration(
            workspace_id=workspace_id,
            creator=member,
            kind=kind,
            name=name,
            config=config,
            secret=secret_value,
        )
        integration_id = created["integration"]["id"]
        return RedirectResponse(f"{front}/integrations?oauth=success&id={integration_id}", status_code=302)
    except (BusinessRuleError, ValueError):
        # ValueError guards uuid parsing of a tampered state record.
        return RedirectResponse(f"{front}/integrations?oauth=error", status_code=302)


# ---------------------------------------------------------------------------
# Outbound webhook subscriptions (§3.1 / §3.4)
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/webhook-subscriptions")
async def list_subscriptions(
    request: Request,
    workspace_id: str,
    status: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from mesh.db.models.integration import WebhookSubscription, WebhookSubscriptionDelivery
    from mesh.db.tenant import set_tenant_context

    async with request.app.state.session_factory() as session:
        await set_tenant_context(session, context.workspace.id)
        stmt = select(WebhookSubscription).where(WebhookSubscription.workspace_id == context.workspace.id)
        if status:
            stmt = stmt.where(WebhookSubscription.status == status)
        rows = (await session.execute(stmt.order_by(WebhookSubscription.created_at.desc()))).scalars().all()
        # §4.1 "成功率" per subscription (lifetime ledger counts).
        stats: dict[uuid.UUID, tuple[int, int]] = {}
        if rows:
            stats = {
                stat_row[0]: (stat_row[1], stat_row[2])
                for stat_row in (
                    await session.execute(
                        select(
                            WebhookSubscriptionDelivery.subscription_id,
                            sa_func.count(),
                            sa_func.count(WebhookSubscriptionDelivery.id).filter(
                                WebhookSubscriptionDelivery.state == "sent"
                            ),
                        )
                        .where(
                            WebhookSubscriptionDelivery.workspace_id == context.workspace.id,
                            WebhookSubscriptionDelivery.subscription_id.in_([row.id for row in rows]),
                        )
                        .group_by(WebhookSubscriptionDelivery.subscription_id)
                    )
                ).all()
            }
    return {"data": [outbound_mod.render_subscription(row, delivery_stats=stats.get(row.id)) for row in rows]}


@router.post("/workspaces/{workspace_id}/webhook-subscriptions", status_code=201)
async def create_subscription(
    request: Request,
    response: Response,
    workspace_id: str,
    body: CreateSubscriptionRequest,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, context.workspace.id)
        subscription, secret = await outbound_mod.create_subscription(
            session,
            workspace_id=context.workspace.id,
            creator_member_id=context.member.id,
            url=body.url,
            event_types=body.event_types,
            integration_id=(
                _path_uuid(body.integration_id, what="integration") if body.integration_id else None
            ),
            signing_secret=settings.jwt_secret,
        )
    rendered = outbound_mod.render_subscription(subscription)
    rendered["secret"] = secret  # shown EXACTLY ONCE (§6.16)
    return {"data": rendered}


@router.get("/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}")
async def get_subscription(
    request: Request,
    workspace_id: str,
    subscription_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    async with request.app.state.session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, context.workspace.id)
        subscription = await outbound_mod.get_subscription(
            session,
            workspace_id=context.workspace.id,
            subscription_id=_path_uuid(subscription_id, what="subscription"),
        )
    return {"data": outbound_mod.render_subscription(subscription)}


@router.patch("/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}")
async def patch_subscription(
    request: Request,
    response: Response,
    workspace_id: str,
    subscription_id: str,
    body: PatchSubscriptionRequest,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    async with request.app.state.session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, context.workspace.id)
        subscription = await outbound_mod.get_subscription(
            session,
            workspace_id=context.workspace.id,
            subscription_id=_path_uuid(subscription_id, what="subscription"),
        )
        updated = await outbound_mod.update_subscription(
            session,
            subscription=subscription,
            url=body.url,
            event_types=body.event_types,
            status=body.status,
            now=datetime.now(UTC),
        )
    return {"data": outbound_mod.render_subscription(updated)}


@router.delete(
    "/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}",
    status_code=204,
)
async def delete_subscription(
    request: Request,
    response: Response,
    workspace_id: str,
    subscription_id: str,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> Response:
    await _rate_limit_write(request, user, response)
    async with request.app.state.session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, context.workspace.id)
        subscription = await outbound_mod.get_subscription(
            session,
            workspace_id=context.workspace.id,
            subscription_id=_path_uuid(subscription_id, what="subscription"),
        )
        await outbound_mod.delete_subscription(session, subscription=subscription, now=datetime.now(UTC))
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/resume")
async def resume_subscription(
    request: Request,
    response: Response,
    workspace_id: str,
    subscription_id: str,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    async with request.app.state.session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, context.workspace.id)
        subscription = await outbound_mod.get_subscription(
            session,
            workspace_id=context.workspace.id,
            subscription_id=_path_uuid(subscription_id, what="subscription"),
        )
        updated = await outbound_mod.resume_subscription(
            session, subscription=subscription, now=datetime.now(UTC)
        )
    return {"data": outbound_mod.render_subscription(updated)}


@router.post(
    "/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}:send-test",
    status_code=201,
)
async def send_subscription_test_event(
    request: Request,
    response: Response,
    workspace_id: str,
    subscription_id: str,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    """Send test event (§3.1, P1): a synthetic ``webhook.test`` delivery.

    Walks the FULL delivery path — the worker signs it (``Mesh-Signature``),
    posts ``Mesh-Event: webhook.test`` + payload body, and records it in
    the ledger like any domain-event delivery.
    """
    await _rate_limit_write(request, user, response)
    async with request.app.state.session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, context.workspace.id)
        subscription = await outbound_mod.get_subscription(
            session,
            workspace_id=context.workspace.id,
            subscription_id=_path_uuid(subscription_id, what="subscription"),
        )
        delivery = await outbound_mod.send_test_event(
            session,
            workspace_id=context.workspace.id,
            subscription=subscription,
            actor_member_id=context.member.id,
        )
    return {"data": outbound_mod.render_delivery(delivery)}


@router.get("/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/deliveries")
async def list_deliveries(
    request: Request,
    workspace_id: str,
    subscription_id: str,
    state: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    from sqlalchemy import select

    from mesh.db.models.integration import WebhookSubscriptionDelivery
    from mesh.db.tenant import set_tenant_context

    sub_id = _path_uuid(subscription_id, what="subscription")
    async with request.app.state.session_factory() as session:
        await set_tenant_context(session, context.workspace.id)
        stmt = select(WebhookSubscriptionDelivery).where(
            WebhookSubscriptionDelivery.workspace_id == context.workspace.id,
            WebhookSubscriptionDelivery.subscription_id == sub_id,
        )
        if state:
            stmt = stmt.where(WebhookSubscriptionDelivery.state == state)
        rows = (
            (await session.execute(stmt.order_by(WebhookSubscriptionDelivery.created_at.desc()).limit(limit)))
            .scalars()
            .all()
        )
    return {"data": [outbound_mod.render_delivery(row) for row in rows]}


@router.post(
    "/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/deliveries/{delivery_id}/retry"
)
async def retry_delivery(
    request: Request,
    response: Response,
    workspace_id: str,
    subscription_id: str,
    delivery_id: str,
    context: WorkspaceContext = Depends(require_workspace("integration:manage")),
    user: User = Depends(get_current_user),
) -> dict:
    await _rate_limit_write(request, user, response)
    async with request.app.state.session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, context.workspace.id)
        subscription = await outbound_mod.get_subscription(
            session,
            workspace_id=context.workspace.id,
            subscription_id=_path_uuid(subscription_id, what="subscription"),
        )
        delivery = await outbound_mod.retry_delivery(
            session,
            workspace_id=context.workspace.id,
            subscription=subscription,
            delivery_id=_path_uuid(delivery_id, what="delivery"),
        )
    return {"data": outbound_mod.render_delivery(delivery)}


# ---------------------------------------------------------------------------
# VCS links (§3.3) — paths carry no workspace segment: the owning workspace
# is resolved from the referenced resource (SECURITY DEFINER bootstrap read,
# RLS fail-closed) and the caller is gated through resolve_workspace_context.
# ---------------------------------------------------------------------------


async def _resource_workspace(request: Request, fn: str, resource_id: uuid.UUID):
    """Resolve a resource's workspace_id without a tenant GUC (RLS would
    hide the row); falls back to a direct read under the owner role."""
    from sqlalchemy import text as sql_text

    async with request.app.state.session_factory() as session:
        try:
            # SAVEPOINT: a missing function aborts only the savepoint, never
            # the session — the ORM fallback below stays usable.
            async with session.begin_nested():
                return (
                    await session.execute(sql_text(f"SELECT {fn}(:id)"), {"id": resource_id})
                ).scalar_one_or_none()
        except Exception:  # noqa: BLE001 — function absent (owner-role tests)
            model = {
                "mesh_integration_workspace_id": Integration,
                "mesh_vcs_link_workspace_id": VcsLink,
                "mesh_issue_workspace_id": Issue,
            }[fn]
            row = await session.get(model, resource_id)
            return row.workspace_id if row is not None else None


async def _context_for_resource(
    request: Request, user: User, fn: str, resource_id: uuid.UUID, permission: str | None
):
    from mesh.auth.rbac import resolve_workspace_context
    from mesh.errors import NotFoundError as _NotFound

    workspace_id = await _resource_workspace(request, fn, resource_id)
    if workspace_id is None:
        raise _NotFound("resource not found")
    async with request.app.state.session_factory() as session:
        return await resolve_workspace_context(
            session, user=user, workspace_id=workspace_id, permission=permission
        )


@router.post("/integrations/vcs/links", status_code=201)
async def create_vcs_link(
    request: Request,
    response: Response,
    body: VcsLinkCreateRequest,
    user: User = Depends(get_current_user),
) -> dict:
    integration_id = _path_uuid(body.integration_id, what="integration")
    context = await _context_for_resource(
        request, user, "mesh_integration_workspace_id", integration_id, "issue:write"
    )
    await _rate_limit_write(request, user, response)
    service = _service(request)
    integration = await service.get_integration(
        workspace_id=context.workspace.id, integration_id=integration_id
    )
    if integration.kind not in ("vcs_github", "vcs_gitlab"):
        raise BusinessRuleError("integration is not a VCS connector", code="vcs_link_invalid")
    provider = "github" if integration.kind == "vcs_github" else "gitlab"
    from mesh.db.tenant import set_tenant_context
    from mesh.integrations.connectors import adapter_for

    tenant_key = adapter_for(integration.kind)["tenant_key_from_config"](integration.config or {})
    async with request.app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, context.workspace.id)
        link = await vcs_links_mod.explicit_link(
            session,
            workspace_id=context.workspace.id,
            integration=integration,
            provider=provider,
            provider_tenant_key=tenant_key,
            vcs_ref=body.vcs_ref.model_dump(),
            issue_id=_path_uuid(body.issue_id, what="issue"),
            created_by=context.member.id,
            now=datetime.now(UTC),
        )
    return {"data": link}


@router.delete("/integrations/vcs/links/{link_id}", status_code=204)
async def delete_vcs_link(
    request: Request,
    response: Response,
    link_id: str,
    user: User = Depends(get_current_user),
) -> Response:
    link_uuid = _path_uuid(link_id, what="vcs link")
    context = await _context_for_resource(request, user, "mesh_vcs_link_workspace_id", link_uuid, None)
    await _rate_limit_write(request, user, response)
    from mesh.db.tenant import set_tenant_context

    async with request.app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, context.workspace.id)
        await vcs_links_mod.delete_link(
            session,
            workspace_id=context.workspace.id,
            link_id=link_uuid,
            now=datetime.now(UTC),
        )
    return Response(status_code=204)


@router.get("/issues/{issue_id}/vcs-links")
async def list_issue_vcs_links(
    request: Request,
    issue_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    issue_uuid = _path_uuid(issue_id, what="issue")
    context = await _context_for_resource(request, user, "mesh_issue_workspace_id", issue_uuid, None)
    from mesh.db.tenant import set_tenant_context

    async with request.app.state.session_factory() as session:
        await set_tenant_context(session, context.workspace.id)
        rows = await vcs_links_mod.list_issue_links(
            session, workspace_id=context.workspace.id, issue_id=issue_uuid
        )
    return {"data": [vcs_links_mod.render_link(row) for row in rows]}


@router.post("/integrations/vcs/resolve")
async def resolve_vcs_identifiers(
    request: Request,
    response: Response,
    body: VcsResolveRequest,
    user: User = Depends(get_current_user),
) -> dict:
    integration_id = _path_uuid(body.integration_id, what="integration")
    context = await _context_for_resource(
        request, user, "mesh_integration_workspace_id", integration_id, None
    )
    await _rate_limit_write(request, user, response)
    service = _service(request)
    integration = await service.get_integration(
        workspace_id=context.workspace.id, integration_id=integration_id
    )
    if integration.kind not in ("vcs_github", "vcs_gitlab"):
        raise BusinessRuleError("integration is not a VCS connector", code="vcs_link_invalid")
    provider = "github" if integration.kind == "vcs_github" else "gitlab"
    from mesh.db.tenant import set_tenant_context
    from mesh.integrations.connectors import adapter_for

    tenant_key = adapter_for(integration.kind)["tenant_key_from_config"](integration.config or {})
    async with request.app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, context.workspace.id)
        result = await vcs_links_mod.resolve_from_text(
            session,
            workspace_id=context.workspace.id,
            integration=integration,
            provider=provider,
            provider_tenant_key=tenant_key,
            source_text=body.source_text,
            vcs_ref=body.vcs_ref.model_dump(),
            now=datetime.now(UTC),
        )
    return {"data": result}


# Queue query / operation endpoints (§3.9). queue_api.router carries NO prefix;
# this router already supplies /api/v1, so the paths compose without duplication.
router.include_router(queue_api_mod.router)


__all__ = ["router"]
