"""DingTalk interactive cards (integrations.md §3.10 card_1.0 / §4.4).

Approval/interaction cards are the IM presentation + callback surface of
the unified ``approvals`` entity (README §6.10 — the approval state and
decision semantics live there, never here):

- PUSH: ``POST /v1.0/card/instances/createAndDeliver`` with
  ``cardTemplateId`` + ``outTrackId`` (derived from the approval id and source
  integration id — the callback's unambiguous lookup key) + ``openSpaceId``
  (IM_GROUP / IM_ROBOT) +
  ``cardData`` + ``callbackType`` (STREAM preferred — reuses the module's
  long connection). ``sampleActionCard*`` (traditional ActionCards: no
  callback, no update capability) is FORBIDDEN for approval cards —
  enforced by a code-path assertion (:func:`assert_not_action_card`).
- UPDATE: ``PUT /v1.0/card/instances``, idempotent by ``outTrackId``.
- STREAMING: ``PUT /v1.0/card/streaming`` (guid idempotency, markdown
  ``isFull=true`` full replacement, ``isFinalize`` closure) — for future
  long-result streaming, not the approval path.
- CALLBACK (topic ``/v1.0/card/instances/callback``): clicker ``userId``
  normalized by staffId (written-in-stone; external contacts fall back to
  the ``x=<base64url(senderId)>`` encoding) → ``external_identities`` →
  global ``users.id`` → the integration's workspace →
  ``members(workspace_id, user_id)`` roster JOIN → forward to
  ``decide_approval`` (§6.10 permission row). Unmapped / no roster row /
  no permission → 403, approval UNCHANGED, audited. Repeat clicks are
  idempotent (decide_approval no-ops on decided approvals). The response
  body carries ``cardUpdateOptions`` + ``cardData`` / ``userPrivateData``
  writebacks (§4.4 lifecycle state table).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.config import normalize_app_base_url
from mesh.db.models.integration import Integration
from mesh.db.models.runtime import Approval, TaskExecution
from mesh.db.models.workspace import Workspace
from mesh.errors import ForbiddenError, MeshError, NotFoundError
from mesh.integrations.cards import extract_action, resolve_clicker_member
from mesh.integrations.dingtalk_api import (
    DingTalkError,
    DingTalkRateLimited,
    InvalidCredentials,
    TokenRefreshBusy,
)
from mesh.integrations.im_outbound import (
    CONVERSATION_DIRECT,
    REASON_INVALID_CREDENTIALS,
    REASON_RATE_LIMITED,
    REASON_TOKEN_BUSY,
    REASON_UPSTREAM_ERROR,
    SEND_STATUS_FAILED,
    SEND_STATUS_SENT,
    ConversationTarget,
    SendOutcome,
    encode_external_contact_key,
)
from mesh.runtime.approvals import decide_approval

logger = logging.getLogger("mesh.integrations.dingtalk_cards")

OUT_TRACK_PREFIX = "mesh-appr-"
OUT_TRACK_V2_PREFIX = "mesh-appr2-"

# §3.2 DingTalk row — the OFFICIAL timestamp tolerance (±3600s). Written in
# stone: narrowing it rejects legitimate callbacks (the platform signs with
# its own clock skew budget); the signature covers ONLY ``timestamp + "\n" +
# app_secret`` (body integrity rides on TLS — §5.6 M1 test boundary).
DINGTALK_CALLBACK_TOLERANCE = timedelta(seconds=3600)

# §3.10 — default card template identifier (deployment-configurable via
# integration config ``card_template_id``).
DEFAULT_CARD_TEMPLATE_ID = "mesh.approval.v1"

# Card lifecycle states (§4.4).
CARD_STATE_LOADING = "loading"
CARD_STATE_APPROVED = "approved"
CARD_STATE_REJECTED = "rejected"
CARD_STATE_FORBIDDEN = "forbidden"
CARD_STATE_EXPIRED = "expired"
CARD_STATE_FAILED = "failed"

_APPROVED_TEXT = "✅ 已批准 · {approver} · {decided_at}"
_REJECTED_TEXT = "❌ 已拒绝 · {approver} · {decided_at}"
_FORBIDDEN_TEXT = "⚠️ 无权限处理此审批"
_FORBIDDEN_HINT = "请在 Mesh 站内连接你的账号，或联系工作区管理员"
_EXPIRED_TEXT = "⏰ 审批已过期"
_FAILED_TEXT = "处理失败"
_MESH_FALLBACK_LABEL = "回 Mesh 处理"


# ---------------------------------------------------------------------------
# outTrackId / openSpaceId derivation (§3.10)
# ---------------------------------------------------------------------------


def _uuid_token(value: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(uuid.UUID(str(value)).bytes).decode().rstrip("=")


def _uuid_from_token(value: str) -> uuid.UUID | None:
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
        return uuid.UUID(bytes=raw) if len(raw) == 16 else None
    except (ValueError, TypeError):
        return None


def derive_out_track_id(
    approval_id: uuid.UUID,
    integration_id: uuid.UUID | None = None,
) -> str:
    """Stable card identity, optionally anchored to its source integration.

    V2 stays below DingTalk's 64-character bound while carrying both UUIDs.
    The legacy approval-only form remains parseable for already-issued cards,
    but new deliveries always provide ``integration_id``.
    """
    approval_id = uuid.UUID(str(approval_id))
    if integration_id is None:
        return f"{OUT_TRACK_PREFIX}{approval_id.hex}"
    return f"{OUT_TRACK_V2_PREFIX}{_uuid_token(approval_id)}.{_uuid_token(uuid.UUID(str(integration_id)))}"


def parse_out_track_context(out_track_id: str) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    raw = str(out_track_id or "")
    if raw.startswith(OUT_TRACK_V2_PREFIX):
        tokens = raw[len(OUT_TRACK_V2_PREFIX) :].split(".")
        if len(tokens) != 2:
            return None
        approval_id = _uuid_from_token(tokens[0])
        integration_id = _uuid_from_token(tokens[1])
        if approval_id is None or integration_id is None:
            return None
        return approval_id, integration_id
    if not raw.startswith(OUT_TRACK_PREFIX):
        return None
    try:
        return uuid.UUID(raw[len(OUT_TRACK_PREFIX) :]), None
    except ValueError:
        return None


def parse_out_track_id(out_track_id: str) -> uuid.UUID | None:
    context = parse_out_track_context(out_track_id)
    return context[0] if context is not None else None


def open_space_id(
    *,
    conversation_type: str,
    open_conversation_id: str | None = None,
    sender_staff_id: str | None = None,
) -> str:
    """``dtv1.card//IM_GROUP.<openConversationId>`` for groups,
    ``dtv1.card//IM_ROBOT.<senderStaffId>`` for single chats."""
    if conversation_type == CONVERSATION_DIRECT:
        if not sender_staff_id:
            raise ValueError("IM_ROBOT space requires sender_staff_id")
        return f"dtv1.card//IM_ROBOT.{sender_staff_id}"
    if not open_conversation_id:
        raise ValueError("IM_GROUP space requires open_conversation_id")
    return f"dtv1.card//IM_GROUP.{open_conversation_id}"


def assert_not_action_card(template_or_msg_key: str) -> None:
    """§3.10 written-in-stone: traditional ActionCards (``sampleActionCard*``)
    have NO callback / update capability and must NEVER carry §6.10
    approval cards. Code-path assertion on every approval-card push."""
    value = str(template_or_msg_key or "")
    if value.startswith("sampleActionCard"):
        raise AssertionError(f"approval cards must not use {value!r} (no callback/update capability)")


# ---------------------------------------------------------------------------
# Card data builders (§4.4 field table ↔ approvals.action_summary)
# ---------------------------------------------------------------------------


def build_approval_card_param_map(
    approval: Approval,
    *,
    agent_name: str,
    status_text: str = "待处理",
    buttons_disabled: bool = False,
    approver_name: str = "",
    decided_at: str = "",
    detail_url: str = "",
) -> dict[str, str]:
    """§4.4 — every card block maps 1:1 to ``approvals.action_summary``."""
    summary = approval.action_summary or {}
    action = str(summary.get("action") or "")
    capability = str(summary.get("capability") or "")
    permission = str(summary.get("permission") or "")
    impact_scope = str(summary.get("impact_scope") or "")
    estimated_cost = str(summary.get("estimated_cost") or "")
    tools_summary = str(summary.get("tools_summary") or summary.get("resume_hint") or "")
    expires_at = approval.expires_at.isoformat() if approval.expires_at else ""
    resume_hint = f"批准后将由 agent 从审批点恢复：待执行 {tools_summary}" if tools_summary else ""
    return {
        "title": f"Mesh 审批请求 · {action}" if action else "Mesh 审批请求",
        "agent_name": agent_name,
        "action": action,
        "capability": capability,
        "permission": permission,
        "impact_scope": impact_scope,
        "estimated_cost": estimated_cost,
        "expires_at": expires_at,
        "resume_hint": resume_hint,
        "status_text": status_text,
        "buttons_disabled": "true" if buttons_disabled else "false",
        "approver_name": approver_name,
        "decided_at": decided_at,
        "detail_url": detail_url,
        "fallback_label": _MESH_FALLBACK_LABEL,
    }


def lifecycle_response(
    state: str,
    *,
    user_id: str = "",
    approval: Approval | None = None,
    agent_name: str = "",
    approver_name: str = "",
    decided_at: str = "",
    detail_url: str = "",
) -> dict[str, Any]:
    """§4.4 lifecycle state table → card-callback response body.

    ``cardUpdateOptions`` makes the platform apply ``cardData`` (public)
    and ``userPrivateData`` (per-clicker) by key merge; the loading state
    is clicker-private, terminal states are public + disable all buttons.
    """
    body: dict[str, Any] = {
        "cardUpdateOptions": {
            "updateCardDataByKey": True,
            "updatePrivateDataByKey": True,
        },
    }
    if state == CARD_STATE_LOADING:
        if user_id:
            body["userPrivateData"] = {user_id: {"cardParamMap": {"status_text": "处理中…"}}}
        return body
    if state == CARD_STATE_APPROVED:
        status_text = _APPROVED_TEXT.format(approver=approver_name, decided_at=decided_at)
    elif state == CARD_STATE_REJECTED:
        status_text = _REJECTED_TEXT.format(approver=approver_name, decided_at=decided_at)
    elif state == CARD_STATE_FORBIDDEN:
        status_text = f"{_FORBIDDEN_TEXT}（{_FORBIDDEN_HINT}）"
    elif state == CARD_STATE_EXPIRED:
        status_text = _EXPIRED_TEXT
    elif state == CARD_STATE_FAILED:
        status_text = _FAILED_TEXT
    else:
        raise ValueError(f"unknown card state {state!r}")
    card_param_map: dict[str, str] = {
        "status_text": status_text,
        "buttons_disabled": "true",
        "approver_name": approver_name,
        "decided_at": decided_at,
    }
    if detail_url:
        card_param_map["detail_url"] = detail_url
        card_param_map["fallback_label"] = _MESH_FALLBACK_LABEL
    if approval is not None:
        card_param_map["title"] = (
            f"Mesh 审批请求 · {approval.action_summary.get('action', '')}"
            if (approval.action_summary or {}).get("action")
            else "Mesh 审批请求"
        )
        card_param_map["agent_name"] = agent_name
    body["cardData"] = {"cardParamMap": card_param_map}
    return body


# ---------------------------------------------------------------------------
# Callback signature (§3.2 DingTalk row — HTTP callbackType only; Stream
# frames are channel-authenticated, no per-frame signature)
# ---------------------------------------------------------------------------

SIG_VALID = "valid"
SIG_INVALID = "invalid"
SIG_MISSING = "missing"


def verify_callback_signature(
    *,
    app_secret: str,
    timestamp: str | None,
    sign: str | None,
    now: datetime,
    tolerance: timedelta = DINGTALK_CALLBACK_TOLERANCE,
) -> str:
    """``sign = Base64(HMAC_SHA256(app_secret, timestamp + "\\n" + app_secret))``
    with constant-time compare and the official ±3600s replay window."""
    if not sign:
        return SIG_MISSING
    try:
        ts_ms = float(str(timestamp or "").strip())
    except (TypeError, ValueError):
        return SIG_INVALID
    if abs(now.timestamp() * 1000 - ts_ms) > tolerance.total_seconds() * 1000:
        return SIG_INVALID
    material = f"{timestamp}\n{app_secret}".encode()
    expected = base64.b64encode(
        hmac.new(app_secret.encode("utf-8"), material, hashlib.sha256).digest()
    ).decode("ascii")
    if not hmac.compare_digest(expected, str(sign).strip()):
        return SIG_INVALID
    return SIG_VALID


# ---------------------------------------------------------------------------
# Clicker extraction (§3.10 — userId normalized by staffId)
# ---------------------------------------------------------------------------


def extract_dingtalk_clicker(
    payload: dict[str, Any], integration: Integration
) -> tuple[str, str, str] | None:
    """(provider, provider_tenant_key, external_user_key) of the clicker.

    The clicker is anchored on the callback's ``userId`` (``userIdType``
    normalized to staffId — written in stone); external contacts without a
    staffId fall back to the ``x=<base64url(senderId)>`` encoding (same
    mapping as inbound, §3.10).
    """
    config = integration.config or {}
    tenant = str(payload.get("corpId") or config.get("corp_id") or "")
    user_id = str(payload.get("userId") or "").strip()
    if user_id:
        return ("dingtalk", tenant, user_id)
    sender_id = str(payload.get("senderId") or "").strip()
    if sender_id:
        return ("dingtalk", tenant, encode_external_contact_key(sender_id))
    return None


def extract_dingtalk_action(payload: dict[str, Any]) -> tuple[uuid.UUID, bool] | None:
    """(approval_id, approve) from ``content.cardPrivateData.params`` —
    delegates to the shared parser (which accepts a params dict value)."""
    content = payload.get("content") or {}
    private = content.get("cardPrivateData") or {}
    params = private.get("params")
    if isinstance(params, dict) and params:
        return extract_action({"action": {"value": params}})
    return extract_action(payload)


# ---------------------------------------------------------------------------
# Card push (§3.10 createAndDeliver)
# ---------------------------------------------------------------------------


async def push_approval_card(
    adapter_client: Any,
    *,
    approval: Approval,
    target: ConversationTarget,
    card_template_id: str = DEFAULT_CARD_TEMPLATE_ID,
    callback_type: str = "STREAM",
    agent_name: str = "",
    detail_url: str = "",
) -> SendOutcome:
    """Deliver the approval card via ``createAndDeliver`` (the four
    elements + callbackType). ActionCard templates are rejected by
    assertion — approval cards REQUIRE callback + update capability."""
    assert_not_action_card(card_template_id)
    if target.conversation_type == CONVERSATION_DIRECT:
        if not target.sender_key or target.sender_key.startswith("x="):
            return SendOutcome(SEND_STATUS_FAILED, reason="no_staff_id")
        space = open_space_id(
            conversation_type=target.conversation_type,
            sender_staff_id=target.sender_key,
        )
    else:
        space = open_space_id(
            conversation_type=target.conversation_type,
            open_conversation_id=target.external_ref,
        )
    if target.integration_id is None:
        return SendOutcome(SEND_STATUS_FAILED, reason="invalid_configuration")
    card_param_map = build_approval_card_param_map(approval, agent_name=agent_name, detail_url=detail_url)
    body = {
        "cardTemplateId": card_template_id,
        "outTrackId": derive_out_track_id(approval.id, target.integration_id),
        "openSpaceId": space,
        "callbackType": callback_type,
        "cardData": {"cardParamMap": card_param_map},
    }
    try:
        await adapter_client.create_and_deliver_card(body)
        return SendOutcome(SEND_STATUS_SENT)
    except DingTalkRateLimited as exc:
        return SendOutcome(
            SEND_STATUS_FAILED,
            reason=REASON_RATE_LIMITED,
            rate_limit_code=exc.code,
            flow_controlled_staff_ids=exc.flow_controlled_staff_ids,
        )
    except InvalidCredentials:
        return SendOutcome(SEND_STATUS_FAILED, reason=REASON_INVALID_CREDENTIALS)
    except TokenRefreshBusy:
        # §3.10 retryable NON-failure — classify before the DingTalkError
        # catch-all (TokenRefreshBusy subclasses it); the relay defers
        # available_at without consuming the failure budget.
        return SendOutcome(SEND_STATUS_FAILED, reason=REASON_TOKEN_BUSY)
    except DingTalkError:
        return SendOutcome(SEND_STATUS_FAILED, reason=REASON_UPSTREAM_ERROR)


# ---------------------------------------------------------------------------
# Callback pipeline (§3.2 chain / §4.4 lifecycle)
# ---------------------------------------------------------------------------


async def _agent_name_for_approval(session: AsyncSession, approval: Approval) -> str:
    if approval.subject_execution_id is None:
        return ""
    from mesh.db.models.agent import Agent

    execution = await session.get(TaskExecution, approval.subject_execution_id)
    if execution is None or execution.agent_id is None:
        return ""
    agent = await session.get(Agent, execution.agent_id)
    return str(agent.name) if agent is not None else ""


async def _member_display_name(session: AsyncSession, member: Any) -> str:
    from mesh.db.models.user import User

    user = await session.get(User, member.user_id) if member.user_id else None
    if user is not None and getattr(user, "display_name", None):
        return str(user.display_name)
    return str(member.id)


async def _approval_detail_url(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    approval_id: uuid.UUID,
    app_base_url: str,
) -> str:
    """Build the actual frontend approval route from deployment config.

    The browser route is slug-based; workspace UUID API paths are not valid
    frontend deep links. DingTalk renders links outside Mesh, so a relative
    path would resolve against DingTalk and is never returned.
    """
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        return ""
    path = (
        f"/w/{quote(str(workspace.slug), safe='')}/approvals?approval_id={quote(str(approval_id), safe='')}"
    )
    try:
        base = normalize_app_base_url(app_base_url) or ""
    except ValueError:
        base = ""
    return f"{base}{path}" if base else ""


def _has_valid_app_base_url(app_base_url: str) -> bool:
    try:
        return normalize_app_base_url(app_base_url) is not None
    except ValueError:
        return False


async def handle_dingtalk_card_callback(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    integration: Integration,
    payload: dict[str, Any],
    now: datetime,
    app_base_url: str = "",
) -> tuple[int, dict[str, Any]]:
    """Full card-callback pipeline. Returns (status, bare-JSON body) — the
    body always carries the §4.4 lifecycle writeback where applicable (the
    Stream worker places it in the frame ACK ``data``; the HTTP endpoint
    returns it directly)."""
    from mesh.auth.audit import write_audit
    from mesh.db.tenant import set_tenant_context

    clicker = extract_dingtalk_clicker(payload, integration)
    action = extract_dingtalk_action(payload)
    track_context = parse_out_track_context(str(payload.get("outTrackId") or ""))
    track_approval_id = track_context[0] if track_context is not None else None
    track_integration_id = track_context[1] if track_context is not None else None
    if (
        clicker is None
        or action is None
        or track_approval_id is None
        or track_approval_id != action[0]
        or (track_integration_id is not None and track_integration_id != integration.id)
    ):
        return 400, _bare_error("invalid_request", "malformed card callback payload")
    provider, tenant_key, external_user_key = clicker
    approval_id, approve = action
    workspace_id = integration.workspace_id
    await set_tenant_context(session, workspace_id)

    user_id = str(payload.get("userId") or external_user_key)

    async def _denial(reason: str, *, status: int = 403) -> tuple[int, dict[str, Any]]:
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
        except Exception:  # noqa: BLE001 — audit must not break the response
            logger.exception("dingtalk card callback denial audit failed")
        return status, lifecycle_response(CARD_STATE_FORBIDDEN, user_id=user_id)

    # HTTP location is intentionally status-agnostic until after the caller
    # proves the app secret, so an unauthenticated probe cannot distinguish a
    # disabled integration. Once authenticated, disabled is a hard inbound
    # boundary: audit the rejection and leave approval truth untouched.
    if integration.status != "active":
        await _denial("integration_disabled", status=401)
        return 401, _bare_error(
            "integration_disabled",
            "integration is disabled; card callbacks are rejected",
        )

    # Card links are opened outside Mesh. Without an absolute deployment
    # origin, both a terminal writeback and a successful decision would leave
    # the operator with an unusable relative fallback. Fail before touching
    # approval truth and retain a durable denial reason for operators.
    if not _has_valid_app_base_url(app_base_url):
        await _denial("app_base_url_missing", status=500)
        return 500, _bare_error(
            "integration_misconfigured",
            "MESH_APP_BASE_URL is required for DingTalk approval cards",
        )

    # Chain link 1+2: external identity → users.id → workspace roster row.
    member, denial = await resolve_clicker_member(
        session,
        provider=provider,
        provider_tenant_key=tenant_key,
        external_user_key=external_user_key,
        workspace_id=workspace_id,
    )
    if member is None:
        return await _denial(denial)

    approval = await session.get(Approval, approval_id)
    if approval is None or approval.workspace_id != workspace_id:
        return 404, _bare_error("not_found", "approval not found")
    agent_name = await _agent_name_for_approval(session, approval)
    detail_url = await _approval_detail_url(
        session,
        workspace_id=workspace_id,
        approval_id=approval_id,
        app_base_url=app_base_url,
    )

    # Expired approvals: no decision, explicit lifecycle card.
    if approval.status == "expired":
        return 200, lifecycle_response(CARD_STATE_EXPIRED, user_id=user_id, detail_url=detail_url)

    # Chain link 3: the unified approval endpoint applies the §6.10
    # permission row; repeat clicks no-op there (idempotent).
    try:
        result = await decide_approval(
            session_factory,
            approval_id=approval_id,
            workspace_id=workspace_id,
            member=member,
            approve=approve,
            comment="via dingtalk card callback",
        )
    except ForbiddenError as exc:
        return await _denial(f"permission_denied:{exc.code}")
    except NotFoundError as exc:
        return 404, _bare_error(exc.code, exc.message)
    except MeshError as exc:
        logger.error(
            "dingtalk card callback forwarding failed approval=%s code=%s",
            approval_id,
            exc.code,
        )
        return 500, lifecycle_response(CARD_STATE_FAILED, user_id=user_id, detail_url=detail_url)

    approver_name = await _member_display_name(session, member)
    decided_at = str(result.get("decided_at") or "")
    # The row-locked decision result is the only lifecycle truth. Both
    # callbacks may have observed ``pending`` before opposite clicks race;
    # deriving from request intent would let the loser overwrite the card
    # with the opposite of the committed approval state.
    state = {
        "approved": CARD_STATE_APPROVED,
        "rejected": CARD_STATE_REJECTED,
        "expired": CARD_STATE_EXPIRED,
        "cancelled": CARD_STATE_FAILED,
    }.get(str(result.get("status") or ""), CARD_STATE_FAILED)
    return 200, lifecycle_response(
        state,
        user_id=user_id,
        approval=approval,
        agent_name=agent_name,
        approver_name=approver_name,
        decided_at=decided_at,
        detail_url=detail_url,
    )


def _bare_error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": {}}}


# ---------------------------------------------------------------------------
# IMSendRelay 'card' kind handler (wired as ``card_pusher`` in workers)
# ---------------------------------------------------------------------------


async def push_card_from_event(
    session: AsyncSession,
    adapter: Any,
    payload: dict[str, Any],
    *,
    app_base_url: str = "",
) -> SendOutcome:
    """Push the approval card described by an ``im.send`` ``kind='card'``
    event. ``callbackType`` aligns with the integration's receive mode
    (STREAM reuses the long connection; HTTP needs the callback endpoint).
    """
    from mesh.integrations.im_outbound import target_from_payload

    # A relative detail URL is invalid in the external DingTalk client. Keep
    # the delivery in Mesh's retry/failure ledger and never call the platform
    # when the deployment origin is absent.
    if not _has_valid_app_base_url(app_base_url):
        return SendOutcome(SEND_STATUS_FAILED, reason="invalid_configuration")
    try:
        approval_id = uuid.UUID(str(payload.get("approval_id") or ""))
        workspace_id = uuid.UUID(str(payload.get("workspace_id") or ""))
        integration_id = uuid.UUID(str(payload.get("integration_id") or ""))
    except ValueError:
        return SendOutcome(SEND_STATUS_FAILED, reason="invalid_request")
    approval = await session.get(Approval, approval_id)
    if approval is None or approval.workspace_id != workspace_id:
        return SendOutcome(SEND_STATUS_FAILED, reason="not_found")
    integration = await session.get(Integration, integration_id)
    config = (integration.config if integration is not None else None) or {}
    callback_type = "HTTP" if str(config.get("receive_mode")) == "http" else "STREAM"
    card_template_id = str(config.get("card_template_id") or DEFAULT_CARD_TEMPLATE_ID)
    agent_name = await _agent_name_for_approval(session, approval)
    detail_url = await _approval_detail_url(
        session,
        workspace_id=workspace_id,
        approval_id=approval_id,
        app_base_url=app_base_url,
    )
    target = target_from_payload(payload, workspace_id, integration_id)
    return await push_approval_card(
        adapter.client,
        approval=approval,
        target=target,
        card_template_id=card_template_id,
        callback_type=callback_type,
        agent_name=agent_name,
        detail_url=detail_url,
    )


__all__ = [
    "CARD_STATE_APPROVED",
    "CARD_STATE_EXPIRED",
    "CARD_STATE_FAILED",
    "CARD_STATE_FORBIDDEN",
    "CARD_STATE_LOADING",
    "CARD_STATE_REJECTED",
    "DEFAULT_CARD_TEMPLATE_ID",
    "DINGTALK_CALLBACK_TOLERANCE",
    "assert_not_action_card",
    "build_approval_card_param_map",
    "derive_out_track_id",
    "extract_dingtalk_action",
    "extract_dingtalk_clicker",
    "handle_dingtalk_card_callback",
    "lifecycle_response",
    "open_space_id",
    "parse_out_track_context",
    "parse_out_track_id",
    "push_approval_card",
    "push_card_from_event",
    "verify_callback_signature",
]
