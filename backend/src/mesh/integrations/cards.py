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
        user_key = str(
            payload.get("open_id")
            or (payload.get("operator") or {}).get("open_id")
            or ""
        )
        tenant = str(payload.get("tenant_key") or config.get("tenant_key") or "")
        return ("feishu", tenant, user_key) if user_key else None
    if kind == "im_slack":
        user = payload.get("user") or {}
        user_key = str(user.get("id") or payload.get("user_id") or "")
        team = payload.get("team") or {}
        tenant = str(team.get("id") or payload.get("team_id") or config.get("team_id") or "")
        return ("slack", tenant, user_key) if user_key else None
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
            await _lookup_by_config_value(session, kind=kind, key="team_id", value=team_id)
            if team_id else []
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
) -> tuple[int, dict[str, Any]]:
    """Full card-callback pipeline. Returns (status, bare-JSON body)."""
    if kind not in ("im_feishu", "im_slack"):
        return 401, _error("invalid_signature", "unsupported card callback")
    payload = parse_card_payload(raw_body, headers)
    integration = await _locate_card_integration(session, kind=kind, payload=payload)
    if integration is None:
        return 401, _error("invalid_signature", "signature verification failed")
    if not await _verify_card_signature(
        session, kind=kind, integration=integration, raw_body=raw_body,
        headers=headers, signing_secret=signing_secret, now=now, tolerance=tolerance,
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

    # Chain link 1: external identity → global users.id.
    identity = await lookup_identity(
        session, provider=provider, provider_tenant_key=tenant_key,
        external_user_key=external_user_key,
    )
    if identity is None:
        await _audit_denial(
            session, workspace_id=workspace_id, approval_id=approval_id,
            reason="identity_unmapped", provider=provider,
            external_user_key=external_user_key,
        )
        return 403, _error("forbidden", "clicker identity is not mapped to a Mesh user")

    # Chain link 2: roster row in THIS workspace (active member required).
    member = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.user_id == identity.user_id,
            Member.status == "active",
            Member.member_type == "human",
        )
    )
    if member is None:
        await _audit_denial(
            session, workspace_id=workspace_id, approval_id=approval_id,
            reason="no_roster_row", provider=provider,
            external_user_key=external_user_key,
        )
        return 403, _error("forbidden", "clicker has no active membership in this workspace")

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
            session, workspace_id=workspace_id, approval_id=approval_id,
            reason=f"permission_denied:{exc.code}", provider=provider,
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
]
