"""Integration & binding management service (integrations.md §3.1).

Stateless orchestrator over the session factory (house pattern). Enforces
the credential ciphertext contract (README §6.16): ``config`` never holds
plaintext secrets (secret-shaped keys must be ``*_ref`` ciphertext
references); the optional top-level ``secret`` plaintext is encrypted into
``secret_ref`` and echoed exactly once at creation/rotation, never after.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import Page, paginate
from mesh.auth.audit import write_audit
from mesh.db.constraints import violates
from mesh.db.models.integration import (
    QUEUE_TERMINAL_STATES,
    Integration,
    IntegrationBinding,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, UpstreamError
from mesh.integrations.connectors import (
    HEALTH_AUTH_FAILED,
    HEALTH_HEALTHY,
    KIND_TO_PROVIDER,
    adapter_for,
    test_connectivity,
    validate_integration_config,
)
from mesh.integrations.matching import validate_match_config
from mesh.integrations.visibility import binding_visibility_clause, event_visibility_clause
from mesh.outbox.service import emit_realtime
from mesh.runtime.credentials import decrypt_credential_value, encrypt_credential_value

logger = logging.getLogger("mesh.integrations.service")

# Secret-shaped config keys must carry ciphertext references, never plaintext.
_SECRET_KEY_RE = re.compile(r"(secret|token|password|credential)$", re.IGNORECASE)

VALID_KINDS = tuple(KIND_TO_PROVIDER.keys())
VALID_STATUSES = ("active", "disabled")

# Delete protection (integrations.md §2.10 / §3.9): force-cancel reason stamped
# into the audit trail when a protected parent deletion drains its queue items.
BINDING_DELETED_REASON = "binding_deleted"
# §3.9 force path: bounded wait for in-flight items to reach a terminal state
# (driven by the execution.finished write-back) before forcing them cancelled.
DEFAULT_FORCE_CANCEL_WAIT_SECONDS = 30.0
# Poll cadence for the bounded wait — short so a pending-only drain returns
# immediately and an in-flight drain checks the write-back frequently.
FORCE_CANCEL_POLL_INTERVAL_SECONDS = 0.05


def assert_config_non_secret(config: dict[str, Any]) -> None:
    """§6.16: reject plaintext-looking secrets in ``config`` (§5.4 scan).

    Keys matching secret shapes must end in ``_ref`` (ciphertext reference).
    """
    for key, value in (config or {}).items():
        if key.endswith("_ref"):
            continue
        if _SECRET_KEY_RE.search(str(key)) and isinstance(value, str) and value:
            raise BusinessRuleError(
                "config must not contain plaintext secrets; store ciphertext as '<name>_ref'",
                code="invalid_request",
                details={"key": key},
            )


# Per-kind config defaults materialized at EVERY config write (R1,
# integrations.md §2.7:295 / §2.10:649 / §3.2:826): a DingTalk integration
# with no explicit dispatch-mode choice gets the SPEC default —
# ``serial_conversation`` (conversation FIFO, at most one in-flight
# execution). The queue module reads ``config.inbound_queue`` at enqueue
# time and its code-level fallback is ``parallel`` (the feishu/slack
# §6.9 baseline), so the DingTalk default MUST be made explicit here —
# an API-created/updated integration without the key would otherwise
# silently parallel direct-dispatch against the Spec. Explicit client
# values always win over the defaults.
_KIND_CONFIG_DEFAULTS: dict[str, dict[str, Any]] = {
    "im_dingtalk": {
        "receive_mode": "stream",
        "inbound_queue": "serial_conversation",
        "verbosity": "final_only",
    },
}


def _config_with_kind_defaults(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    defaults = _KIND_CONFIG_DEFAULTS.get(kind)
    if not defaults:
        return config
    return {**defaults, **config}


def _canonical_dingtalk_stream_reconnect(config: dict[str, Any]) -> tuple[float, float, float]:
    reconnect = config.get("stream_reconnect") or {}
    if not isinstance(reconnect, dict):
        reconnect = {}
    return (
        float(reconnect.get("base_seconds") or 2.0),
        float(reconnect.get("max_seconds") or 300.0),
        float(reconnect.get("heartbeat_timeout_seconds") or 90.0),
    )


class IntegrationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        signing_secret: str,
        *,
        dingtalk_credential_verifier=None,
        dingtalk_oapi_base: str = "https://oapi.dingtalk.com",
    ):
        self._sf = session_factory
        self._signing_secret = signing_secret
        self._dingtalk_oapi_base = dingtalk_oapi_base.rstrip("/")
        self._dingtalk_credential_verifier = dingtalk_credential_verifier or self._verify_dingtalk_credentials

    async def _verify_dingtalk_credentials(
        self, config: dict[str, Any], secret: str
    ) -> tuple[str, str | None]:
        return await test_connectivity(
            "im_dingtalk",
            config=config,
            secret=secret,
            base_urls={"im_dingtalk": self._dingtalk_oapi_base},
        )

    # ------------------------------------------------------------------
    # Integrations CRUD
    # ------------------------------------------------------------------

    async def _admit_dingtalk_app_config(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        config: dict[str, Any],
        secret_ref: str | None,
        exclude_integration_id: uuid.UUID | None = None,
    ) -> str | None:
        """Serialize and validate one DingTalk app-key group admission.

        The restricted API role cannot inspect foreign integration rows under
        RLS. A narrow SECURITY DEFINER lookup, called only after a transaction
        advisory lock, provides race-free global ownership admission without
        exposing another tenant's integration data.
        """
        app_key = str(config.get("app_key") or "")
        if app_key == "":
            return secret_ref
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"dingtalk_app_owner:{app_key}"},
        )
        owner_workspace = await session.scalar(
            text("SELECT mesh_dingtalk_app_owner_workspace(:app_key)"),
            {"app_key": app_key},
        )
        if owner_workspace is not None and uuid.UUID(str(owner_workspace)) != workspace_id:
            raise ConflictError(
                "DingTalk app key is already owned by another workspace",
                code="dingtalk_app_key_conflict",
            )

        # New API writes accept credentials only through the dedicated
        # top-level secret/rotate-secret contract. Legacy config references
        # are read from already-persisted sibling rows below, never admitted
        # from this candidate config.
        candidate_cipher = str(secret_ref or "")

        def decrypt_cipher(cipher: str) -> str | None:
            if cipher == "":
                return None
            try:
                return decrypt_credential_value(cipher, self._signing_secret)
            except Exception:  # noqa: BLE001 — malformed group credentials fail closed
                return None

        candidate_plaintext = decrypt_cipher(candidate_cipher)
        if owner_workspace is None:
            if candidate_plaintext is None:
                raise BusinessRuleError(
                    "DingTalk app ownership requires valid app credentials",
                    code="dingtalk_credentials_invalid",
                )
            health, detail = await self._dingtalk_credential_verifier(config, candidate_plaintext)
            if health == HEALTH_AUTH_FAILED:
                raise BusinessRuleError(
                    "DingTalk rejected the app credentials",
                    code="dingtalk_credentials_invalid",
                    details={"reason": detail},
                )
            if health != HEALTH_HEALTHY:
                raise UpstreamError(
                    "DingTalk credential ownership verification is unavailable",
                    code="dingtalk_credential_verification_unavailable",
                    details={"reason": detail},
                )

        stmt = select(Integration).where(
            Integration.workspace_id == workspace_id,
            Integration.kind == "im_dingtalk",
            Integration.deleted_at.is_(None),
            Integration.config["app_key"].astext == app_key,
        )
        if exclude_integration_id is not None:
            stmt = stmt.where(Integration.id != exclude_integration_id)
        siblings = (await session.execute(stmt)).scalars().all()

        identity = (
            str(config.get("corp_id") or ""),
            str(config.get("robot_code") or app_key),
        )
        if any(
            (
                str((row.config or {}).get("corp_id") or ""),
                str((row.config or {}).get("robot_code") or (row.config or {}).get("app_key") or ""),
            )
            == identity
            for row in siblings
        ):
            raise ConflictError(
                "DingTalk corp and robot route is already configured for this app",
                code="dingtalk_route_conflict",
            )

        if str(config.get("receive_mode") or "") == "stream":
            reconnect_policy = _canonical_dingtalk_stream_reconnect(config)
            if any(
                str((row.config or {}).get("receive_mode") or "") == "stream"
                and _canonical_dingtalk_stream_reconnect(dict(row.config or {})) != reconnect_policy
                for row in siblings
            ):
                raise ConflictError(
                    "DingTalk integrations sharing a Stream app must share one reconnect policy",
                    code="dingtalk_stream_config_conflict",
                )

        existing_ciphers = [
            str((row.config or {}).get("app_secret_ref") or row.secret_ref or "") for row in siblings
        ]
        if not existing_ciphers:
            return secret_ref
        existing_plaintexts = [decrypt_cipher(cipher) for cipher in existing_ciphers]
        if (
            any(value is None for value in existing_plaintexts)
            or len(set(existing_plaintexts)) != 1
            or (candidate_cipher != "" and candidate_plaintext is None)
            or (candidate_plaintext is not None and candidate_plaintext != existing_plaintexts[0])
        ):
            raise ConflictError(
                "DingTalk integrations sharing an app key must share one credential",
                code="dingtalk_app_credential_conflict",
            )
        # Reuse the group's encrypted credential when the caller omitted it.
        # This reveals no plaintext and keeps one credential truth per app.
        return secret_ref or existing_ciphers[0]

    async def create_integration(
        self,
        *,
        workspace_id: uuid.UUID,
        creator: Member,
        kind: str,
        name: str,
        config: dict[str, Any] | None = None,
        secret: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if kind not in VALID_KINDS:
            raise BusinessRuleError(
                "invalid integration kind",
                code="invalid_request",
                details={"kind": kind, "allowed": list(VALID_KINDS)},
            )
        config = _config_with_kind_defaults(kind, dict(config or {}))
        assert_config_non_secret(config)
        validate_integration_config(kind, config)
        moment = now or datetime.now(UTC)
        encrypted_secret = encrypt_credential_value(secret, self._signing_secret) if secret else None
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            if kind == "im_dingtalk":
                encrypted_secret = await self._admit_dingtalk_app_config(
                    session,
                    workspace_id=workspace_id,
                    config=config,
                    secret_ref=encrypted_secret,
                )
            integration = Integration(
                workspace_id=workspace_id,
                kind=kind,
                name=name,
                config=config,
                secret_ref=encrypted_secret,
                created_by=creator.id,
                created_at=moment,
                updated_at=moment,
            )
            session.add(integration)
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_integrations_ws_name"):
                    raise ConflictError("integration name already exists", code="conflict") from exc
                raise
            await self._emit_integration_updated(session, integration, moment, "created")
            rendered = render_integration(integration)
        return {"integration": rendered, "secret_accepted": bool(secret)}

    async def list_integrations(
        self,
        *,
        workspace_id: uuid.UUID,
        kind: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.deleted_at.is_(None),
            )
            if kind:
                stmt = stmt.where(Integration.kind == kind)
            if status:
                stmt = stmt.where(Integration.status == status)
            return await paginate(
                session,
                stmt,
                sort_column=Integration.created_at,
                id_column=Integration.id,
                sort_value_of=lambda row: row.created_at,
                id_of=lambda row: row.id,
                cursor=cursor,
                limit=limit,
            )

    async def event_counts_since(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_ids: list[uuid.UUID],
        since: datetime,
        viewer: Member,
    ) -> dict[uuid.UUID, int]:
        """Inbound event counts per integration since ``since`` (§4.1 近7天事件量)."""
        if not integration_ids:
            return {}
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(IntegrationEvent.integration_id, func.count())
                .where(
                    IntegrationEvent.workspace_id == workspace_id,
                    IntegrationEvent.integration_id.in_(integration_ids),
                    IntegrationEvent.received_at >= since,
                )
                .group_by(IntegrationEvent.integration_id)
            )
            visibility = event_visibility_clause(viewer)
            if visibility is not None:
                stmt = stmt.where(visibility)
            rows = (await session.execute(stmt)).all()
            return {row[0]: row[1] for row in rows}

    async def record_health(
        self,
        *,
        workspace_id: uuid.UUID,
        integration: Integration,
        health_state: str,
        last_error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Persist a connector-health transition (§2.2 / §4.1 badge).

        ``healthy`` stamps ``last_success_at`` and clears ``last_error``;
        failure states stamp ``last_error`` and keep the previous success
        instant (the UI shows "last ok" context next to the badge).
        """
        moment = now or datetime.now(UTC)
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            row = await session.get(Integration, integration.id)
            if row is None or row.deleted_at is not None:
                raise NotFoundError("integration not found")
            row.health_state = health_state
            if health_state == "healthy":
                row.last_error = None
                row.last_success_at = moment
            else:
                row.last_error = last_error
            row.updated_at = moment
            await self._emit_integration_updated(session, row, moment, "updated")

    async def test_connection(self, *, workspace_id: uuid.UUID, integration_id: uuid.UUID) -> dict[str, Any]:
        """POST .../integrations/{id}:test (§3.1, P1) + health drive (P2).

        Decrypts the credential IN-MEMORY only (§6.16), runs the
        connector's lightweight platform-API check, and persists the
        outcome to the health fields (§4.1 badge / re-authorize banner).
        """
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            integration = await session.get(Integration, integration_id)
            if integration is None or integration.deleted_at is not None:
                raise NotFoundError("integration not found")
            kind = integration.kind
            config = dict(integration.config or {})
            secret = (
                decrypt_credential_value(integration.secret_ref, self._signing_secret)
                if integration.secret_ref
                else None
            )
        health_state, detail = await test_connectivity(kind, config=config, secret=secret)
        await self.record_health(
            workspace_id=workspace_id,
            integration=integration,
            health_state=health_state,
            last_error=detail,
        )
        return {"health_state": health_state, "detail": detail}

    async def get_integration(self, *, workspace_id: uuid.UUID, integration_id: uuid.UUID) -> Integration:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            integration = await session.scalar(
                select(Integration).where(
                    Integration.id == integration_id,
                    Integration.workspace_id == workspace_id,
                    Integration.deleted_at.is_(None),
                )
            )
        if integration is None:
            raise NotFoundError("integration not found")
        return integration

    async def test_send(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        conversation_ref: str,
        conversation_type: str = "group",
        user_key: str = "",
        redis: object,
        api_base: str | None = None,
        http_client: object | None = None,
    ) -> dict[str, Any]:
        """POST .../integrations/{id}/test-send (§3.9, diagnostics split).

        Sends a test text through the OpenAPI outbound adapter. Outbound
        NEVER depends on the Stream receive channel — failures surface as
        502 ``upstream_error`` (or credential hints), NEVER as 503
        ``stream_channel_unavailable`` (that code is receive-diagnostic
        only, §3.5).
        """
        from mesh.errors import UpstreamError
        from mesh.integrations.im_outbound import (
            ConversationTarget,
            make_adapter,
        )

        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            integration = await session.get(Integration, integration_id)
            if integration is None or integration.deleted_at is not None:
                raise NotFoundError("integration not found")
            if integration.kind != "im_dingtalk":
                raise BusinessRuleError(
                    "test-send is only supported for im_dingtalk integrations",
                    code="invalid_request",
                )
            corp_id = str((integration.config or {}).get("corp_id") or "")
            adapter = await make_adapter(
                redis=redis,
                integration=integration,
                signing_secret=self._signing_secret,
                api_base=api_base or "https://api.dingtalk.com",
                http_client=http_client,
            )
        if adapter is None:
            raise UpstreamError(
                "integration is disabled or has no credentials",
                code="upstream_error",
                details={"reason": "integration_unavailable"},
            )
        target = ConversationTarget(
            workspace_id=workspace_id,
            integration_id=integration_id,
            provider_tenant_key=corp_id,
            external_ref=conversation_ref,
            conversation_type=conversation_type,
            sender_key=user_key,
        )
        outcome = await adapter.send_text(target, "Mesh 测试发送成功 ✅")
        if outcome.sent:
            return {"status": "sent", "conversation_ref": conversation_ref}
        raise UpstreamError(
            "test send failed",
            code="upstream_error",
            details={"reason": outcome.reason, "rate_limit_code": outcome.rate_limit_code},
        )

    async def get_stream_status(
        self, *, workspace_id: uuid.UUID, integration_id: uuid.UUID
    ) -> dict[str, Any]:
        """GET .../integrations/{id}/stream-status (§3.9, receive-side
        diagnostics only — reads the persisted ``stream_state``, never
        initiates outbound). ``state='down'`` → 503
        ``stream_channel_unavailable`` (§3.5: that code lives here and
        nowhere else)."""
        from mesh.errors import ServiceUnavailableError

        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            integration = await session.get(Integration, integration_id)
        if integration is None or integration.deleted_at is not None:
            raise NotFoundError("integration not found")
        if integration.kind != "im_dingtalk":
            raise BusinessRuleError(
                "stream-status is only available for im_dingtalk integrations",
                code="invalid_request",
            )
        state = dict(integration.stream_state or {})
        if integration.status == "disabled":
            return {
                "state": "disabled",
                "last_frame_at": state.get("last_frame_at"),
                "last_attempt_at": state.get("last_attempt_at"),
                "backoff_seconds": state.get("backoff_seconds"),
            }
        connection_state = str(state.get("state") or "down")
        body = {
            "state": connection_state,
            "last_frame_at": state.get("last_frame_at"),
            "last_attempt_at": state.get("last_attempt_at"),
            "backoff_seconds": state.get("backoff_seconds"),
        }
        if connection_state == "down":
            raise ServiceUnavailableError(
                "dingtalk stream receive channel is down",
                code="stream_channel_unavailable",
                details=body,
            )
        return body

    async def request_stream_reconnect(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        now: datetime | None = None,
    ) -> dict[str, bool]:
        """Persist an explicit Stream reconnect request for the lock owner.

        API and Stream workers can live in different processes.  The opaque
        request id therefore lives in ``stream_state`` rather than process
        memory; the worker's connection fingerprint observes it during the
        next reconciliation scan and closes/rebuilds the shared app_key
        connection.  State and realtime invalidation commit atomically.
        """
        moment = now or datetime.now(UTC)
        reconnect_request_id = str(uuid.uuid4())
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            integration = await session.scalar(
                select(Integration)
                .where(
                    Integration.id == integration_id,
                    Integration.workspace_id == workspace_id,
                    Integration.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if integration is None:
                raise NotFoundError("integration not found")
            if integration.kind != "im_dingtalk":
                raise BusinessRuleError(
                    "reconnect is only supported for im_dingtalk integrations",
                    code="invalid_request",
                )
            if integration.status != "active":
                raise BusinessRuleError(
                    "integration must be active to reconnect",
                    code="invalid_request",
                )
            if str((integration.config or {}).get("receive_mode") or "") != "stream":
                raise BusinessRuleError(
                    "reconnect is only supported in Stream receive mode",
                    code="invalid_request",
                )
            app_key = str((integration.config or {}).get("app_key") or "")
            group = [integration]
            if app_key:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"dingtalk_app_owner:{app_key}"},
                )
                group = (
                    (
                        await session.execute(
                            select(Integration)
                            .where(
                                Integration.workspace_id == workspace_id,
                                Integration.kind == "im_dingtalk",
                                Integration.deleted_at.is_(None),
                                Integration.status == "active",
                                Integration.config["app_key"].astext == app_key,
                                Integration.config["receive_mode"].astext == "stream",
                            )
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
            for row in group:
                previous = dict(row.stream_state or {})
                row.stream_state = {
                    **previous,
                    "state": "reconnecting",
                    "last_attempt_at": moment.isoformat(),
                    "backoff_seconds": 0,
                    "reconnect_request_id": reconnect_request_id,
                }
                row.updated_at = moment
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=f"workspace:{workspace_id}:integrations",
                    event="integration.updated",
                    data={
                        "integration_id": str(row.id),
                        "kind": row.kind,
                        "status": row.status,
                        "subject": "stream_channel",
                        "stream_state": "reconnecting",
                    },
                    idempotency_key=(f"integration-stream-reconnect:{row.id}:{reconnect_request_id}"),
                )
        return {"accepted": True}

    async def update_integration(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        name: str | None = None,
        status: str | None = None,
        config: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        if config is not None:
            assert_config_non_secret(config)
        if status is not None and status not in VALID_STATUSES:
            raise BusinessRuleError("invalid status", code="invalid_request")
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            integration = await session.scalar(
                select(Integration).where(
                    Integration.id == integration_id,
                    Integration.workspace_id == workspace_id,
                    Integration.deleted_at.is_(None),
                )
            )
            if integration is None:
                raise NotFoundError("integration not found")
            if config is not None:
                # §6.16: per-kind guards at EVERY config write (the row's
                # kind is authoritative — config carries no kind of its own).
                # R1: the per-kind Spec defaults are materialized here too —
                # a wholesale config replacement must not silently drop a
                # DingTalk integration back onto the parallel code fallback.
                config = _config_with_kind_defaults(integration.kind, dict(config))
                validate_integration_config(integration.kind, config)
                if integration.kind == "im_dingtalk":
                    effective_secret_ref = str(
                        (integration.config or {}).get("app_secret_ref") or integration.secret_ref or ""
                    )
                    integration.secret_ref = await self._admit_dingtalk_app_config(
                        session,
                        workspace_id=workspace_id,
                        config=config,
                        secret_ref=effective_secret_ref or None,
                        exclude_integration_id=integration.id,
                    )
            if name is not None:
                integration.name = name
            if status is not None:
                integration.status = status
            if config is not None:
                integration.config = config
            integration.updated_at = moment
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_integrations_ws_name"):
                    raise ConflictError("integration name already exists", code="conflict") from exc
                raise
            await self._emit_integration_updated(session, integration, moment, "updated")
            return render_integration(integration)

    async def delete_integration(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        now: datetime | None = None,
        force: str | None = None,
        force_cancel_wait_seconds: float | None = None,
        actor_member_id: uuid.UUID | None = None,
    ) -> None:
        """Soft-delete the integration (§3.1).

        ``force='cancel'`` first walks EVERY binding through the binding
        delete-protection force path (§3.9: drain non-terminal queue items,
        hard-delete the binding → orphan audit rows), then soft-deletes the
        integration. Without ``force`` the existing soft-delete runs unchanged
        (it does not cascade into queue items, so no protection is needed).
        """
        if force is not None and force != "cancel":
            raise BusinessRuleError("invalid force value", code="invalid_request")
        if force == "cancel":
            async with self._sf() as session:
                await set_tenant_context(session, workspace_id)
                binding_ids = (
                    (
                        await session.execute(
                            select(IntegrationBinding.id).where(
                                IntegrationBinding.workspace_id == workspace_id,
                                IntegrationBinding.integration_id == integration_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for binding_id in binding_ids:
                await self.delete_binding(
                    workspace_id=workspace_id,
                    binding_id=binding_id,
                    force="cancel",
                    force_cancel_wait_seconds=force_cancel_wait_seconds,
                    actor_member_id=actor_member_id,
                )
        moment = now or datetime.now(UTC)
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            integration = await session.scalar(
                select(Integration).where(
                    Integration.id == integration_id,
                    Integration.workspace_id == workspace_id,
                    Integration.deleted_at.is_(None),
                )
            )
            if integration is None:
                raise NotFoundError("integration not found")
            integration.deleted_at = moment
            integration.updated_at = moment
            await session.flush()
            await self._emit_integration_updated(session, integration, moment, "deleted")

    async def rotate_secret(
        self, *, workspace_id: uuid.UUID, integration_id: uuid.UUID, secret: str
    ) -> dict[str, Any]:
        """Rotate the credential; returns the new plaintext ONCE."""
        encrypted_secret = encrypt_credential_value(secret, self._signing_secret)
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            integration = await session.scalar(
                select(Integration).where(
                    Integration.id == integration_id,
                    Integration.workspace_id == workspace_id,
                    Integration.deleted_at.is_(None),
                )
            )
            if integration is None:
                raise NotFoundError("integration not found")
            rows = [integration]
            app_key = (
                str((integration.config or {}).get("app_key") or "")
                if integration.kind == "im_dingtalk"
                else ""
            )
            if app_key:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"dingtalk_app_owner:{app_key}"},
                )
                rows = (
                    (
                        await session.execute(
                            select(Integration)
                            .where(
                                Integration.workspace_id == workspace_id,
                                Integration.kind == "im_dingtalk",
                                Integration.deleted_at.is_(None),
                                Integration.config["app_key"].astext == app_key,
                            )
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
            rotated_at = datetime.now(UTC)
            for row in rows:
                # Legacy rows may carry app_secret_ref in config. Remove that
                # precedence source so every sibling reads the same rotated
                # top-level encrypted credential.
                row.config = {
                    key: value for key, value in (row.config or {}).items() if key != "app_secret_ref"
                }
                row.secret_ref = encrypted_secret
                row.updated_at = rotated_at
                await self._emit_integration_updated(session, row, rotated_at, "rotated")
            await session.flush()
        return {"id": str(integration_id), "rotated": True}

    def decrypt_integration_secret(self, integration: Integration) -> str | None:
        if not integration.secret_ref:
            return None
        try:
            return decrypt_credential_value(integration.secret_ref, self._signing_secret)
        except Exception:  # noqa: BLE001 — rotated key: treat as absent
            return None

    async def _emit_integration_updated(
        self, session: AsyncSession, integration: Integration, moment: datetime, subject: str
    ) -> None:
        await emit_realtime(
            session,
            workspace_id=integration.workspace_id,
            channel=f"workspace:{integration.workspace_id}:integrations",
            event="integration.updated",
            data={
                "subject": "integration",
                "integration_id": str(integration.id),
                "kind": integration.kind,
                "status": integration.status,
                "change": subject,
            },
            idempotency_key=(f"integration:{integration.id}:{subject}:{int(moment.timestamp() * 1000)}"),
        )

    # ------------------------------------------------------------------
    # Bindings CRUD (§2.3 / §3.1)
    # ------------------------------------------------------------------

    async def create_binding(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        external_ref: str,
        scope: str = "workspace",
        project_id: uuid.UUID | None = None,
        match_config: dict[str, Any] | None = None,
        bound_agent_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        match_config = dict(match_config or {})
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            integration = await session.scalar(
                select(Integration).where(
                    Integration.id == integration_id,
                    Integration.workspace_id == workspace_id,
                    Integration.deleted_at.is_(None),
                )
            )
            if integration is None:
                raise NotFoundError("integration not found")
            provider = KIND_TO_PROVIDER.get(integration.kind)
            if provider is None or provider == "webhook":
                raise BusinessRuleError("integration kind does not support bindings", code="invalid_request")
            validate_match_config(provider, match_config)
            adapter = adapter_for(integration.kind)
            tenant_key = adapter["tenant_key_from_config"](integration.config or {})
            binding = IntegrationBinding(
                workspace_id=workspace_id,
                integration_id=integration_id,
                provider=provider,
                provider_tenant_key=tenant_key,
                scope=scope,
                project_id=project_id,
                external_ref=external_ref,
                match_config=match_config,
                bound_agent_id=bound_agent_id,
                created_at=moment,
                updated_at=moment,
            )
            session.add(binding)
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_binding_external_identity"):
                    raise ConflictError(
                        "external identity already bound (possibly in another workspace)",
                        code="binding_conflict",
                    ) from exc
                if violates(exc, "ck_binding_scope"):
                    raise BusinessRuleError(
                        "scope/project mismatch (workspace scope takes no "
                        "project; project scope requires one)",
                        code="invalid_request",
                    ) from exc
                raise
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"workspace:{workspace_id}:integrations",
                event="integration.updated",
                data={
                    "subject": "binding",
                    "binding_id": str(binding.id),
                    "integration_id": str(integration_id),
                    "status": binding.status,
                },
                idempotency_key=f"binding:{binding.id}:created",
            )
            return render_binding(binding)

    async def list_bindings(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        viewer: Member,
    ) -> list[dict[str, Any]]:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(IntegrationBinding).where(
                IntegrationBinding.workspace_id == workspace_id,
                IntegrationBinding.integration_id == integration_id,
            )
            visibility = binding_visibility_clause(viewer)
            if visibility is not None:
                stmt = stmt.where(visibility)
            rows = (
                (await session.execute(stmt.order_by(IntegrationBinding.created_at.desc()))).scalars().all()
            )
            return [render_binding(row) for row in rows]

    async def update_binding(
        self,
        *,
        workspace_id: uuid.UUID,
        binding_id: uuid.UUID,
        match_config: dict[str, Any] | None = None,
        bound_agent_id: uuid.UUID | None = ...,  # type: ignore[assignment]
        status: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            binding = await session.scalar(
                select(IntegrationBinding).where(
                    IntegrationBinding.id == binding_id,
                    IntegrationBinding.workspace_id == workspace_id,
                )
            )
            if binding is None:
                raise NotFoundError("binding not found")
            if match_config is not None:
                validate_match_config(binding.provider, match_config)
                binding.match_config = match_config
            if bound_agent_id is not ...:
                binding.bound_agent_id = bound_agent_id
            if status is not None:
                if status not in VALID_STATUSES:
                    raise BusinessRuleError("invalid status", code="invalid_request")
                binding.status = status
            binding.updated_at = moment
            await session.flush()
            return render_binding(binding)

    async def delete_binding(
        self,
        *,
        workspace_id: uuid.UUID,
        binding_id: uuid.UUID,
        force: str | None = None,
        force_cancel_wait_seconds: float | None = None,
        actor_member_id: uuid.UUID | None = None,
    ) -> None:
        """Hard delete with delete protection (§2.10 / §3.9).

        Hard delete releases the global external-identity slot (§2.3: disabled
        bindings still occupy the key; re-binding elsewhere requires deleting
        the row).

        * No ``force``: non-terminal queue items → 409
          ``binding_has_active_queue``. Terminal items do NOT block — they ride
          the FK ``ON DELETE SET NULL`` into self-describing orphan audit rows.
        * ``force='cancel'``: force-terminate every item first (pending →
          cancelled; in-flight → runtime cancel + bounded wait, then forced
          cancelled), THEN delete the parent — the SET NULL turns the
          now-terminal items into orphan audit rows (success-path closure).
        """
        if force is not None and force != "cancel":
            raise BusinessRuleError("invalid force value", code="invalid_request")
        if force == "cancel":
            await self._force_terminate_binding_items(
                workspace_id=workspace_id,
                binding_id=binding_id,
                wait_seconds=force_cancel_wait_seconds,
                actor_member_id=actor_member_id,
            )
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            binding = await session.scalar(
                select(IntegrationBinding).where(
                    IntegrationBinding.id == binding_id,
                    IntegrationBinding.workspace_id == workspace_id,
                )
            )
            if binding is None:
                raise NotFoundError("binding not found")
            if force is None:
                active = (
                    await session.execute(
                        select(func.count())
                        .select_from(IntegrationMessageQueue)
                        .where(
                            IntegrationMessageQueue.workspace_id == workspace_id,
                            IntegrationMessageQueue.binding_id == binding_id,
                            IntegrationMessageQueue.state.not_in(QUEUE_TERMINAL_STATES),
                        )
                    )
                ).scalar_one()
                if active:
                    raise ConflictError(
                        "binding has non-terminal queue items; use ?force=cancel",
                        code="binding_has_active_queue",
                        details={"active_items": int(active)},
                    )
            await session.delete(binding)
            await session.flush()

    async def _force_terminate_binding_items(
        self,
        *,
        workspace_id: uuid.UUID,
        binding_id: uuid.UUID,
        wait_seconds: float | None = None,
        actor_member_id: uuid.UUID | None = None,
    ) -> None:
        """§3.9 force path: drive every item of a binding to a terminal state.

        ① Cancel tx (committed): ``pending`` → ``cancelled``; in-flight
        (``dispatching/processing/cancelling``) → request execution cancel and
        mark ``cancelling`` (cancel intent persisted in the same transaction;
        the heartbeat downlink completes the stop). An audit row records
        ``reason='binding_deleted'``.
        ② Poll on fresh sessions (no open transaction) up to ``wait_seconds``
        for all items to reach a terminal state — the ``execution.finished``
        write-back drives in-flight items to ``cancelled``.
        ③ Force any survivor to ``cancelled`` and log an alert (the
        execution-side cancel intent is already durable, so the daemon finishes
        the stop on recovery); the survivors then ride SET NULL into orphan
        audit rows when the parent is deleted.
        """
        budget = DEFAULT_FORCE_CANCEL_WAIT_SECONDS if wait_seconds is None else float(wait_seconds)
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            binding = await session.scalar(
                select(IntegrationBinding).where(
                    IntegrationBinding.id == binding_id,
                    IntegrationBinding.workspace_id == workspace_id,
                )
            )
            if binding is None:
                raise NotFoundError("binding not found")
            items = (
                (
                    await session.execute(
                        select(IntegrationMessageQueue)
                        .where(
                            IntegrationMessageQueue.workspace_id == workspace_id,
                            IntegrationMessageQueue.binding_id == binding_id,
                            IntegrationMessageQueue.state.not_in(QUEUE_TERMINAL_STATES),
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for item in items:
                if item.state == "pending":
                    item.state = "cancelled"
                    item.finished_at = func.now()
                else:  # dispatching / processing / cancelling
                    if item.execution_id is not None:
                        await self._request_execution_cancel(
                            session,
                            workspace_id=workspace_id,
                            execution_id=item.execution_id,
                            member_id=actor_member_id,
                        )
                    if item.state != "cancelling":
                        item.state = "cancelling"
                item.updated_at = func.now()
            if items:
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_member_id=actor_member_id,
                    actor_kind="member" if actor_member_id is not None else "system",
                    action="integration_queue.force_cancel",
                    resource_type="integration_binding",
                    resource_id=binding_id,
                    metadata={
                        "reason": BINDING_DELETED_REASON,
                        "item_ids": [str(item.id) for item in items],
                    },
                )
            await session.flush()

        # ② Bounded wait for in-flight items to reach a terminal state.
        deadline = time.monotonic() + budget
        while True:
            remaining = await self._count_active_binding_items(
                workspace_id=workspace_id, binding_id=binding_id
            )
            if remaining == 0 or time.monotonic() >= deadline:
                break
            await asyncio.sleep(FORCE_CANCEL_POLL_INTERVAL_SECONDS)

        # ③ Force any survivor to cancelled (+ alert).
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            survivors = (
                (
                    await session.execute(
                        select(IntegrationMessageQueue)
                        .where(
                            IntegrationMessageQueue.workspace_id == workspace_id,
                            IntegrationMessageQueue.binding_id == binding_id,
                            IntegrationMessageQueue.state.not_in(QUEUE_TERMINAL_STATES),
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for item in survivors:
                item.state = "cancelled"
                item.finished_at = func.now()
                item.updated_at = func.now()
            await session.flush()
        if survivors:
            logger.warning(
                "force-cancelled %d queue items after %.1fs wait "
                "(binding=%s reason=%s) — execution cancel intent persisted; "
                "daemon completes the stop on recovery",
                len(survivors),
                budget,
                binding_id,
                BINDING_DELETED_REASON,
            )

    async def _count_active_binding_items(self, *, workspace_id: uuid.UUID, binding_id: uuid.UUID) -> int:
        """Non-terminal queue-item count for a binding (fresh session)."""
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(IntegrationMessageQueue)
                        .where(
                            IntegrationMessageQueue.workspace_id == workspace_id,
                            IntegrationMessageQueue.binding_id == binding_id,
                            IntegrationMessageQueue.state.not_in(QUEUE_TERMINAL_STATES),
                        )
                    )
                ).scalar_one()
            )

    async def _request_execution_cancel(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        execution_id: uuid.UUID,
        member_id: uuid.UUID | None,
    ) -> None:
        """Persist an execution cancel intent inside the caller's transaction.

        Prefers the in-transaction runtime cancel service when present; falls
        back to a direct conditional UPDATE that persists the same cancel intent
        (claimed/running → cancelling). The heartbeat downlink delivers the
        actual stop; this never creates a new outbox event (§3.7).
        """
        try:
            from mesh.runtime import attempts as _attempts
        except ImportError:  # pragma: no cover — runtime always present
            _attempts = None
        cancel_tx = getattr(_attempts, "request_execution_cancel_tx", None)
        if cancel_tx is not None:
            await cancel_tx(
                session,
                workspace_id=workspace_id,
                execution_id=execution_id,
                member_id=member_id,
            )
            return
        from mesh.db.models.runtime import TaskExecution

        await session.execute(
            update(TaskExecution)
            .where(
                TaskExecution.id == execution_id,
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.status.in_(("claimed", "running", "cancelling")),
            )
            .values(
                cancel_requested_at=func.now(),
                cancel_requested_by=member_id,
                status="cancelling",
                updated_at=func.now(),
            )
        )

    # ------------------------------------------------------------------
    # Event ledger (§3.1 / §5.5 observability)
    # ------------------------------------------------------------------

    async def list_events(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        viewer: Member,
        signature_status: str | None = None,
        process_status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(IntegrationEvent).where(
                IntegrationEvent.workspace_id == workspace_id,
                IntegrationEvent.integration_id == integration_id,
            )
            visibility = event_visibility_clause(viewer)
            if visibility is not None:
                stmt = stmt.where(visibility)
            if signature_status:
                stmt = stmt.where(IntegrationEvent.signature_status == signature_status)
            if process_status:
                stmt = stmt.where(IntegrationEvent.process_status == process_status)
            return await paginate(
                session,
                stmt,
                sort_column=IntegrationEvent.received_at,
                id_column=IntegrationEvent.id,
                sort_value_of=lambda row: row.received_at,
                id_of=lambda row: row.id,
                cursor=cursor,
                limit=limit,
                descending=True,
            )

    async def event_counts_7d(
        self,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        since: datetime,
        viewer: Member,
    ) -> int:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(func.count())
                .select_from(IntegrationEvent)
                .where(
                    IntegrationEvent.workspace_id == workspace_id,
                    IntegrationEvent.integration_id == integration_id,
                    IntegrationEvent.received_at >= since,
                )
            )
            visibility = event_visibility_clause(viewer)
            if visibility is not None:
                stmt = stmt.where(visibility)
            return int((await session.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# Delete protection — project deletion guard (integrations.md §2.10 / §5.6 ④)
# ---------------------------------------------------------------------------


async def assert_no_active_project_queue(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """Fail-closed guard run before a project deletion (§2.10 / §5.6 ④).

    Deleting a project cascades its project-scoped bindings; that cascade would
    ``SET NULL`` the bindings' queue items, which ``ck_imq_orphan_terminal``
    rejects while ANY item is non-terminal (the whole DELETE rolls back — no
    silent message loss). The project service therefore refuses to delete a
    project that still has non-terminal items on a project-scoped binding; the
    caller must first drain them through the binding ``?force=cancel`` path.
    Module-level (takes a session factory) so the project module can call it
    with a lazy import and no circular dependency on ``IntegrationService``.
    """
    async with session_factory() as session:
        await set_tenant_context(session, workspace_id)
        active = (
            await session.execute(
                select(func.count())
                .select_from(IntegrationMessageQueue)
                .join(
                    IntegrationBinding,
                    (IntegrationBinding.workspace_id == IntegrationMessageQueue.workspace_id)
                    & (IntegrationBinding.id == IntegrationMessageQueue.binding_id),
                )
                .where(
                    IntegrationMessageQueue.workspace_id == workspace_id,
                    IntegrationBinding.scope == "project",
                    IntegrationBinding.project_id == project_id,
                    IntegrationMessageQueue.state.not_in(QUEUE_TERMINAL_STATES),
                )
            )
        ).scalar_one()
    if active:
        raise ConflictError(
            "project has non-terminal integration queue items; force-cancel them first",
            code="binding_has_active_queue",
            details={"active_items": int(active)},
        )


# ---------------------------------------------------------------------------
# Renderers — secrets never echoed (README §6.16)
# ---------------------------------------------------------------------------


def _redacted_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """§6.16 defense in depth: NEVER echo ``*_ref`` ciphertext values.

    The ciphertext itself is not plaintext, but rendering it hands every
    list/get reader offline attack material; responses keep the KEY (the
    config shape is non-secret) with a fixed mask value.
    """
    return {
        key: ("***" if str(key).endswith("_ref") and value else value)
        for key, value in (config or {}).items()
    }


def render_integration(integration: Integration, *, events_7d: int | None = None) -> dict[str, Any]:
    rendered = {
        "id": str(integration.id),
        "workspace_id": str(integration.workspace_id),
        "kind": integration.kind,
        "name": integration.name,
        "status": integration.status,
        # Connector health (§2.2): independent of the manual active/disabled
        # status; ``auth_failed`` drives the "re-authorize" banner (§4.1).
        "health_state": integration.health_state,
        "last_error": integration.last_error,
        "last_success_at": (integration.last_success_at.isoformat() if integration.last_success_at else None),
        "config": _redacted_config(integration.config),
        "has_secret": bool(integration.secret_ref),
        "created_by": str(integration.created_by),
        "created_at": integration.created_at.isoformat() if integration.created_at else None,
        "updated_at": integration.updated_at.isoformat() if integration.updated_at else None,
    }
    if events_7d is not None:
        # §4.1 connected-list column "近7天事件量".
        rendered["events_7d"] = events_7d
    return rendered


def render_binding(binding: IntegrationBinding) -> dict[str, Any]:
    return {
        "id": str(binding.id),
        "integration_id": str(binding.integration_id),
        "provider": binding.provider,
        "provider_tenant_key": binding.provider_tenant_key,
        "scope": binding.scope,
        "project_id": str(binding.project_id) if binding.project_id else None,
        "external_ref": binding.external_ref,
        "match_config": binding.match_config or {},
        "bound_agent_id": str(binding.bound_agent_id) if binding.bound_agent_id else None,
        "status": binding.status,
        "created_at": binding.created_at.isoformat() if binding.created_at else None,
        "updated_at": binding.updated_at.isoformat() if binding.updated_at else None,
    }


def render_event(event: IntegrationEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "integration_id": str(event.integration_id),
        "external_event_id": event.external_event_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "signature_status": event.signature_status,
        "process_status": event.process_status,
        "received_at": event.received_at.isoformat() if event.received_at else None,
    }


__all__ = [
    "BINDING_DELETED_REASON",
    "DEFAULT_FORCE_CANCEL_WAIT_SECONDS",
    "IntegrationService",
    "assert_config_non_secret",
    "assert_no_active_project_queue",
    "render_binding",
    "render_event",
    "render_integration",
]
