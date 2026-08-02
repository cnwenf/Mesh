"""IM card callback auth chain (integrations.md §3.2 / §4.3 / §5.2, HIGH-1/R4).

Approval/interaction cards pushed into feishu/Slack carry approve/reject
buttons; the callback is the TRIGGER SURFACE for ``POST /approvals/{id}/
approve|reject`` — the approval entity and decision semantics stay in
``approvals`` (README §6.10). Before forwarding, the clicker's external
identity MUST pass the full chain:

    callback payload → clicker (provider + tenant + external user key)
      → external_identities → global users.id
      → JOIN the integration's workspace members(workspace_id, user_id)
        (active roster row required)
      → decide_approval applies the §6.10 permission row

Unmapped / no roster row / inactive / no permission → 403, approval
unchanged, audit trail. Repeat clicks are idempotent (decide_approval
no-ops on decided approvals, §6.10).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth.audit import write_audit
from mesh.db.models.integration import Integration
from mesh.db.models.member import Member
from mesh.errors import ForbiddenError, MeshError, NotFoundError
from mesh.integrations.connectors import adapter_for
from mesh.integrations.identities import lookup_identity
from mesh.integrations.inbound import (
    _decrypt_ref,
    _integration_from_row,
    _lookup_active_by_kind,
    _lookup_by_config_value,
)
from mesh.runtime.approvals import decide_approval

logger = logging.getLogger("mesh.integrations.cards")


def parse_card_payload(raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    """Slack interaction payloads arrive form-encoded (``payload=<json>``);
    feishu sends JSON. Accept both."""
    content_type = ""
    for key, value in headers.items():
        if key.lower() == "content-type":
            content_type = value
            break
    text = raw_body.decode("utf-8", errors="replace")
    if "application/x-www-form-urlencoded" in content_type:
        form = parse_qs(text)
        payload_raw = form.get("payload", [""])[0]
        try:
            return json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            return {}
    try:
        parsed = json.loads(text) if text else {}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        return {}


def extract_clicker(
    kind: str, payload: dict[str, Any], integration: Integration
) -> tuple[str, str, str] | None:
    """(provider, provider_tenant_key, external_user_key) of the clicker."""
    config = integration.config or {}
    if kind == "im_feishu":
        user_key = str(payload.get("open_id") or (payload.get("operator") or {}).get("open_id") or "")
        tenant = str(payload.get("tenant_key") or config.get("tenant_key") or "")
        return ("feishu", tenant, user_key) if user_key else None
    if kind == "im_slack":
        user = payload.get("user") or {}
        user_key = str(user.get("id") or payload.get("user_id") or "")
        team = payload.get("team") or {}
        tenant = str(team.get("id") or payload.get("team_id") or config.get("team_id") or "")
        return ("slack", tenant, user_key) if user_key else None
    if kind == "im_dingtalk":
        from mesh.integrations.dingtalk_cards import extract_dingtalk_clicker

        return extract_dingtalk_clicker(payload, integration)
    return None


def extract_action(payload: dict[str, Any]) -> tuple[uuid.UUID, bool] | None:
    """(approval_id, approve) from the card action value."""
    action = payload.get("action") or {}
    value = action.get("value")
    if value is None:
        actions = payload.get("actions") or []
        value = (actions[0] or {}).get("value") if actions else None
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    try:
        approval_id = uuid.UUID(str(value.get("approval_id") or ""))
    except ValueError:
        return None
    decision = str(value.get("decision") or "").lower()
    if decision not in ("approve", "reject"):
        return None
    return approval_id, decision == "approve"


async def _locate_card_integration(
    session: AsyncSession, *, kind: str, payload: dict[str, Any]
) -> Integration | None:
    if kind == "im_feishu":
        rows = await _lookup_active_by_kind(session, kind=kind)
    elif kind == "im_slack":
        team = payload.get("team") or {}
        team_id = str(team.get("id") or payload.get("team_id") or "")
        rows = (
            await _lookup_by_config_value(session, kind=kind, key="team_id", value=team_id) if team_id else []
        )
    else:
        rows = []
    # Build detached integrations from the SECURITY DEFINER rows — ORM
    # reads are RLS-hidden before the tenant GUC is known (fail-closed).
    for row in rows:
        return _integration_from_row(row)
    return None


async def _verify_card_signature(
    session: AsyncSession,
    *,
    kind: str,
    integration: Integration,
    raw_body: bytes,
    headers: dict[str, str],
    signing_secret: str,
    now: datetime,
    tolerance: timedelta,
) -> bool:
    adapter = adapter_for(kind)
    config = integration.config or {}
    secret_cipher = config.get(adapter["secret_config_key"]) or integration.secret_ref
    plaintext = _decrypt_ref(signing_secret, secret_cipher)
    if not plaintext:
        return False
    lowered = {k.lower(): v for k, v in headers.items()}
    status = adapter["verify"](
        **{
            "encrypt_key_ref": {"encrypt_key": plaintext},
            "signing_secret_ref": {"signing_secret": plaintext},
        }[adapter["secret_config_key"]],
        raw_body=raw_body,
        headers=lowered,
        now=now,
        tolerance=tolerance,
    )
    return status == "valid"


async def resolve_clicker_member(
    session: AsyncSession,
    *,
    provider: str,
    provider_tenant_key: str,
    external_user_key: str,
    workspace_id: uuid.UUID,
) -> tuple[Member | None, str]:
    """Shared callback auth chain links 1+2 (§3.2, R4 mapping model).

    Returns ``(member, denial_reason)``; ``denial_reason`` is ``""`` on
    success, else ``identity_unmapped`` / ``no_roster_row``. The §6.10
    permission row itself is applied by ``decide_approval`` (link 3).
    """
    identity = await lookup_identity(
        session,
        provider=provider,
        provider_tenant_key=provider_tenant_key,
        external_user_key=external_user_key,
    )
    if identity is None:
        return None, "identity_unmapped"
    member = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.user_id == identity.user_id,
            Member.status == "active",
            Member.member_type == "human",
        )
    )
    if member is None:
        return None, "no_roster_row"
    return member, ""


async def handle_card_callback(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    raw_body: bytes,
    headers: dict[str, str],
    signing_secret: str,
    now: datetime,
    tolerance: timedelta,
    app_base_url: str = "",
) -> tuple[int, dict[str, Any]]:
    """Full card-callback pipeline. Returns (status, bare-JSON body)."""
    if kind not in ("im_feishu", "im_slack", "im_dingtalk"):
        return 401, _error("invalid_signature", "unsupported card callback")
    payload = parse_card_payload(raw_body, headers)
    if kind == "im_dingtalk":
        # HTTP callbackType (§3.10): the DingTalk signature scheme covers
        # timestamp + "\n" + app_secret with the OFFICIAL ±3600s tolerance
        # (written in stone — narrowing rejects legitimate callbacks).
        from mesh.db.models.runtime import Approval
        from mesh.db.tenant import set_tenant_context
        from mesh.integrations.dingtalk_cards import (
            extract_dingtalk_action,
            handle_dingtalk_card_callback,
            parse_out_track_context,
            verify_callback_signature,
        )

        corp_id = str(payload.get("corpId") or "")
        rows = (
            await _lookup_by_config_value(session, kind=kind, key="corp_id", value=corp_id) if corp_id else []
        )
        rows = [row for row in rows if str((row[4] or {}).get("receive_mode") or "") == "http"]
        candidates = [_integration_from_row(row) for row in rows]
        if not candidates:
            return 401, _error("invalid_signature", "signature verification failed")
        lowered = {k.lower(): v for k, v in headers.items()}
        verified: list[Integration] = []
        for candidate in candidates:
            config = candidate.config or {}
            app_secret = _decrypt_ref(
                signing_secret,
                str(config.get("app_secret_ref") or candidate.secret_ref),
            )
            if (
                app_secret
                and verify_callback_signature(
                    app_secret=app_secret,
                    timestamp=lowered.get("timestamp"),
                    sign=lowered.get("sign"),
                    now=now,
                )
                == "valid"
            ):
                verified.append(candidate)
        if not verified:
            return 401, _error("invalid_signature", "signature verification failed")

        # The immutable card instance identity and the clicked action must
        # name the same approval. Validate only after signature admission so
        # unauthenticated callers learn nothing about callback structure.
        track_context = parse_out_track_context(str(payload.get("outTrackId") or ""))
        approval_id = track_context[0] if track_context is not None else None
        source_integration_id = track_context[1] if track_context is not None else None
        action = extract_dingtalk_action(payload)
        if approval_id is None or action is None or action[0] != approval_id:
            return 400, _error("invalid_request", "card callback approval identity mismatch")

        if source_integration_id is not None:
            verified = [candidate for candidate in verified if candidate.id == source_integration_id]
            if len(verified) != 1:
                return 401, _error("invalid_signature", "signature verification failed")

        by_workspace: dict[uuid.UUID, list[Integration]] = {}
        for candidate in verified:
            by_workspace.setdefault(candidate.workspace_id, []).append(candidate)
        if len(by_workspace) > 1:
            matching_workspaces: list[uuid.UUID] = []
            for workspace_id in by_workspace:
                async with session_factory() as probe:
                    await set_tenant_context(probe, workspace_id)
                    approval = await probe.get(Approval, approval_id)
                if approval is not None and approval.workspace_id == workspace_id:
                    matching_workspaces.append(workspace_id)
            if len(matching_workspaces) != 1:
                return 401, _error("invalid_signature", "signature verification failed")
            by_workspace = {matching_workspaces[0]: by_workspace[matching_workspaces[0]]}
        workspace_candidates = next(iter(by_workspace.values()))
        # Legacy cards carry only approval identity. They remain compatible
        # when the signed corp route has exactly one integration; shared-app
        # sibling ambiguity fails closed instead of selecting an arbitrary id.
        if len(workspace_candidates) != 1:
            return 401, _error("invalid_signature", "signature verification failed")
        integration = workspace_candidates[0]
        return await handle_dingtalk_card_callback(
            session,
            session_factory,
            integration=integration,
            payload=payload,
            now=now,
            app_base_url=app_base_url,
        )
    integration = await _locate_card_integration(session, kind=kind, payload=payload)
    if integration is None:
        return 401, _error("invalid_signature", "signature verification failed")
    if not await _verify_card_signature(
        session,
        kind=kind,
        integration=integration,
        raw_body=raw_body,
        headers=headers,
        signing_secret=signing_secret,
        now=now,
        tolerance=tolerance,
    ):
        return 401, _error("invalid_signature", "signature verification failed")

    clicker = extract_clicker(kind, payload, integration)
    action = extract_action(payload)
    if clicker is None or action is None:
        return 400, _error("invalid_request", "malformed card callback payload")
    provider, tenant_key, external_user_key = clicker
    approval_id, approve = action
    workspace_id = integration.workspace_id
    from mesh.db.tenant import set_tenant_context

    await set_tenant_context(session, workspace_id)

    # Chain links 1+2: external identity → users.id → workspace roster row.
    member, denial = await resolve_clicker_member(
        session,
        provider=provider,
        provider_tenant_key=tenant_key,
        external_user_key=external_user_key,
        workspace_id=workspace_id,
    )
    if member is None:
        await _audit_denial(
            session,
            workspace_id=workspace_id,
            approval_id=approval_id,
            reason=denial,
            provider=provider,
            external_user_key=external_user_key,
        )
        message = (
            "clicker identity is not mapped to a Mesh user"
            if denial == "identity_unmapped"
            else "clicker has no active membership in this workspace"
        )
        return 403, _error("forbidden", message)

    # Chain link 3: forward to the unified approval endpoint (§6.10
    # permission row + idempotent repeat handling live there).
    try:
        result = await decide_approval(
            session_factory,
            approval_id=approval_id,
            workspace_id=workspace_id,
            member=member,
            approve=approve,
            comment=f"via {provider} card callback",
        )
    except (ForbiddenError, NotFoundError) as exc:
        await _audit_denial(
            session,
            workspace_id=workspace_id,
            approval_id=approval_id,
            reason=f"permission_denied:{exc.code}",
            provider=provider,
            external_user_key=external_user_key,
        )
        status = 403 if isinstance(exc, ForbiddenError) else 404
        return status, _error(exc.code, exc.message)
    except MeshError as exc:
        return exc.status_code, _error(exc.code, exc.message)
    return 200, {"ok": True, "approval": result}


async def _audit_denial(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    approval_id: uuid.UUID,
    reason: str,
    provider: str,
    external_user_key: str,
) -> None:
    """Card-callback denials are always audited (§5.2 审计记录)."""
    try:
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_member_id=None,
            actor_kind="system",
            action="integration.card_callback_denied",
            resource_type="approval",
            resource_id=approval_id,
            metadata={
                "reason": reason,
                "provider": provider,
                "external_user_key": external_user_key,
            },
        )
    except Exception:  # noqa: BLE001 — audit must not break the 403 response
        logger.exception("card callback denial audit failed")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": {}}}


__all__ = [
    "extract_action",
    "extract_clicker",
    "handle_card_callback",
    "parse_card_payload",
    "resolve_clicker_member",
]
