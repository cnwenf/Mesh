"""Inbound webhook: HMAC verify → dedup → audit → route (autopilot.md §2.5 / §3.2).

Security contract (red lines, §5.3):

* The endpoint is signature-authenticated — NOT Bearer. ``X-Signature:
  t=<unix-ts>,v1=<hex>`` where ``v1 = HMAC_SHA256(secret, "{t}.{raw_body}")``;
  the server recomputes with the stored secret and compares in CONSTANT
  TIME (``hmac.compare_digest``), and rejects timestamps outside the
  tolerance window (replay protection).
* ``invalid`` / ``missing`` signature → the event is stored
  (``process_status='rejected'``) and 401 is returned. NEVER dispatched,
  NEVER routed.
* Rejected events live in a SEPARATE ``rejected:<raw-hash>`` idempotency
  namespace so an unsigned forgery cannot pre-occupy a legitimate event's
  dedup key (§2.5 去重防预占).
* The payload is UNTRUSTED DATA (README §6.15): it enters run snapshots
  under the ``webhook`` root, which the template renderer structurally
  isolates before interpolating into agent prompts.
* Secrets: the URL token is stored HASHED (lookup only), the HMAC secret
  is Fernet CIPHERTEXT (ciphertext-only contract, README §6.16). Plaintext
  is shown exactly once at creation/rotation; responses and logs never
  echo it.

The response body is the bare JSON contract with external systems
(autopilot.md §3.2: NOT the §6.14 success envelope).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets as pysecrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.autopilot import runs as runs_mod
from mesh.autopilot.guardrails import evaluate_trigger
from mesh.db.models.autopilot import Autopilot, WebhookEvent, WebhookSecret
from mesh.db.models.member import Member
from mesh.outbox.service import emit_realtime
from mesh.runtime.credentials import decrypt_credential_value, encrypt_credential_value

TOKEN_PREFIX = "whk_"
SECRET_PREFIX = "whs_"
REJECTED_KEY_PREFIX = "rejected:"

# Only benign headers are persisted — never Authorization/Cookie/signatures.
_STORED_HEADERS = ("content-type", "user-agent", "x-event-type", "x-event-id")

SIGNATURE_HEADER = "x-signature"
EVENT_TYPE_HEADER = "x-event-type"
EVENT_ID_HEADER = "x-event-id"


def hash_token(token: str) -> str:
    """The lookup digest stored in webhook_secrets.token_hash."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_credential_pair() -> tuple[str, str]:
    """A fresh (URL token, HMAC secret) pair — high-entropy URL-safe."""
    return f"{TOKEN_PREFIX}{pysecrets.token_urlsafe(32)}", f"{SECRET_PREFIX}{pysecrets.token_urlsafe(32)}"


async def create_secret(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    member: Member,
    label: str = "default",
    signing_secret: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create an active credential pair.

    Returns the plaintext token + secret EXACTLY ONCE — they are never
    recoverable afterwards (token stored hashed, secret as ciphertext).
    """
    moment = now if now is not None else datetime.now(UTC)
    token, secret = generate_credential_pair()
    row = WebhookSecret(
        workspace_id=workspace_id,
        label=label,
        token_hash=hash_token(token),
        encrypted_secret=encrypt_credential_value(secret, signing_secret),
        status="active",
        created_by=member.id,
        created_at=moment,
        updated_at=moment,
    )
    session.add(row)
    await session.flush()
    return {
        "id": str(row.id),
        "label": row.label,
        "status": row.status,
        "token": token,
        "secret": secret,
        "created_at": row.created_at.isoformat(),
    }


async def rotate_secret(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    secret_id: uuid.UUID,
    member: Member,
    signing_secret: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Rotate the credential pair IN PLACE (same row id, fresh token+secret).

    Rules bind ``trigger_config.secret_id`` — rotating in place keeps bound
    rules working while the OLD token immediately stops verifying (its hash
    is replaced). Plaintext shown exactly once; missing row → 404.
    """
    from mesh.errors import NotFoundError

    moment = now if now is not None else datetime.now(UTC)
    row = await session.scalar(
        select(WebhookSecret).where(
            WebhookSecret.id == secret_id, WebhookSecret.workspace_id == workspace_id
        )
    )
    if row is None:
        raise NotFoundError("webhook secret not found")
    token, secret = generate_credential_pair()
    row.token_hash = hash_token(token)
    row.encrypted_secret = encrypt_credential_value(secret, signing_secret)
    row.status = "active"
    row.revoked_at = None
    row.updated_at = moment
    await session.flush()
    return {
        "id": str(row.id),
        "label": row.label,
        "status": row.status,
        "token": token,
        "secret": secret,
        "created_at": row.created_at.isoformat(),
    }


def public_secret_row(row: WebhookSecret) -> dict[str, Any]:
    """List rendering — NEVER includes the token or any secret material."""
    return {
        "id": str(row.id),
        "label": row.label,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


async def lookup_secret_by_token(
    session: AsyncSession, token: str
) -> tuple[uuid.UUID, uuid.UUID, str, str] | None:
    """Resolve the URL token → (id, workspace_id, status, encrypted_secret).

    Uses the SECURITY DEFINER bootstrap function (migration 0023): the
    endpoint has no Bearer identity, so the tenant GUC cannot be set before
    the lookup — RLS would hide every row from the restricted app role.
    Falls back to a direct query when the function is absent (owner-role
    unit tests pre-migration reuse).
    """
    token_digest = hash_token(token)
    try:
        row = (
            await session.execute(
                text("SELECT id, workspace_id, status, encrypted_secret "
                     "FROM mesh_webhook_secret_by_token_hash(:h)"),
                {"h": token_digest},
            )
        ).first()
    except Exception:  # noqa: BLE001 — function missing (owner-role tests)
        row = (
            await session.execute(
                select(
                    WebhookSecret.id,
                    WebhookSecret.workspace_id,
                    WebhookSecret.status,
                    WebhookSecret.encrypted_secret,
                ).where(WebhookSecret.token_hash == token_digest)
            )
        ).first()
    if row is None:
        return None
    return row[0], row[1], row[2], row[3]


def _parse_signature_header(header: str | None) -> tuple[int | None, str | None]:
    """Parse ``t=<ts>,v1=<hex>``; malformed → (None, None)."""
    if not header:
        return None, None
    timestamp: int | None = None
    signature: str | None = None
    for part in header.split(","):
        key, sep, value = part.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None, None
        elif key == "v1":
            signature = value
    return timestamp, signature


def verify_signature(
    secret: str,
    raw_body: bytes,
    signature_header: str | None,
    *,
    now: datetime,
    tolerance: timedelta,
) -> str:
    """Constant-time signature verification with replay protection (§3.2).

    Returns one of ``valid`` / ``invalid`` / ``missing``.
    """
    if not signature_header:
        return "missing"
    timestamp, presented = _parse_signature_header(signature_header)
    if timestamp is None or presented is None:
        return "invalid"
    drift = abs(now.timestamp() - timestamp)
    if drift > tolerance.total_seconds():
        return "invalid"  # replay window exceeded
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, presented.lower()):
        return "invalid"
    return "valid"


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Persist only benign headers — never credentials or signatures."""
    return {
        key: str(value)
        for key, value in ((k.lower(), v) for k, v in headers.items())
        if key in _STORED_HEADERS
    }


def _content_idempotency_key(raw_body: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw_body).hexdigest()}"


async def _route_matching_rules(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    secret_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> list[Autopilot]:
    """Active webhook_received rules bound to this secret + event type."""
    rules = (
        (
            await session.execute(
                select(Autopilot).where(
                    Autopilot.workspace_id == workspace_id,
                    Autopilot.trigger_type == "webhook_received",
                    Autopilot.status == "active",
                    Autopilot.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    matched: list[Autopilot] = []
    for rule in rules:
        config = rule.trigger_config or {}
        if str(config.get("secret_id") or "") != str(secret_id):
            continue  # rule is bound to a different credential pair
        allowed_types = config.get("event_types")
        if allowed_types and event_type not in [str(t) for t in allowed_types]:
            continue
        if not _payload_matches(config.get("payload_match") or [], payload):
            continue
        matched.append(rule)
    return matched


def _payload_matches(matchers: list[Any], payload: dict[str, Any]) -> bool:
    """filter_config.payload_match — AND across entries (§2.6)."""
    from mesh.autopilot.filters import match_payload_rules

    return match_payload_rules(matchers, payload)


async def process_inbound(
    session: AsyncSession,
    *,
    token: str,
    raw_body: bytes,
    headers: dict[str, str],
    signing_secret: str,
    now: datetime,
    tolerance: timedelta,
) -> tuple[int, dict[str, Any]]:
    """The full inbound pipeline. Returns (http_status, bare-JSON body).

    Runs inside the caller's transaction so the webhook_events audit row,
    the dedup INSERT and any dispatched runs commit atomically.
    """
    resolved = await lookup_secret_by_token(session, token)
    if resolved is None or resolved[2] != "active":
        # Unknown / revoked token → indistinguishable from a bad signature.
        return 401, {
            "error": {
                "code": "invalid_signature",
                "message": "webhook signature verification failed",
                "details": {},
            }
        }
    secret_id, workspace_id, _status, encrypted_secret = resolved
    # The token lookup happened through the SECURITY DEFINER bootstrap
    # function (workspace unknown until then); now that the tenant is
    # resolved, set the RLS GUC so the app-role writes below are permitted.
    from mesh.db.tenant import set_tenant_context

    await set_tenant_context(session, workspace_id)
    secret_plaintext = decrypt_credential_value(encrypted_secret, signing_secret)
    signature_header = headers.get(SIGNATURE_HEADER) or headers.get(
        SIGNATURE_HEADER.title()
    )
    signature_status = verify_signature(
        secret_plaintext,
        raw_body,
        signature_header,
        now=now,
        tolerance=tolerance,
    )

    event_type = str(headers.get(EVENT_TYPE_HEADER) or headers.get("X-Event-Type") or "unknown")
    event_id = str(headers.get(EVENT_ID_HEADER) or headers.get("X-Event-Id") or "")
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {"raw": raw_body[:4096].decode("utf-8", errors="replace")}

    # SIGNATURE FAILURE → reject + audit + 401. Never dispatched (§5.3).
    if signature_status in ("invalid", "missing"):
        try:
            async with session.begin_nested():
                await _store_event(
                    session,
                    workspace_id=workspace_id,
                    autopilot_id=None,
                    idempotency_key=f"{REJECTED_KEY_PREFIX}{hashlib.sha256(raw_body).hexdigest()}",
                    event_type=event_type,
                    headers=_sanitize_headers(headers),
                    payload=payload,
                    signature_status=signature_status,
                    process_status="rejected",
                    now=now,
                )
        except IntegrityError:
            pass  # repeated forgery with the same body — already audited
        return 401, {
            "error": {
                "code": "invalid_signature",
                "message": "webhook signature verification failed",
                "details": {},
            }
        }

    # Valid signature → dedup INSERT (first writer wins). The INSERT runs in
    # a SAVEPOINT: a unique-key conflict rolls back only the savepoint so the
    # caller's transaction stays usable (§2.5 idempotent 200, never twice).
    idempotency_key = event_id or _content_idempotency_key(raw_body)
    try:
        async with session.begin_nested():
            event = await _store_event(
                session,
                workspace_id=workspace_id,
                autopilot_id=None,
                idempotency_key=idempotency_key,
                event_type=event_type,
                headers=_sanitize_headers(headers),
                payload=payload,
                signature_status="valid",
                process_status="received",
                now=now,
            )
    except IntegrityError:
        # Duplicate event — idempotent 200, never dispatch twice (§2.5).
        return 200, {
            "received": True,
            "event_id": event_id,
            "process_status": "deduped",
            "run_id": None,
        }

    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"workspace:{workspace_id}:autopilots",
        event="webhook_events.received",
        data={
            "event_id": str(event.id),
            "external_event_id": event_id,
            "process_status": "received",
            "signature_status": "valid",
        },
        idempotency_key=f"webhook-event:{event.id}:received",
    )

    # Route → guardrail gate → dispatch a run per matching rule.
    matched = await _route_matching_rules(
        session,
        workspace_id=workspace_id,
        secret_id=secret_id,
        event_type=event_type,
        payload=payload,
    )
    run_ids: list[uuid.UUID] = []
    for rule in matched:
        decision = await evaluate_trigger(
            session, rule=rule, dedup_key=idempotency_key, now=now
        )
        if not decision.allowed:
            continue
        snapshot = {
            "event_id": event_id or str(event.id),
            "event_type": event_type,
            "dedup_key": idempotency_key,
            # External payload lives under the "webhook" root — the template
            # renderer isolates every string under it (§6.15).
            "webhook": {"payload": payload, "headers": _sanitize_headers(headers)},
        }
        run = await runs_mod.create_run(
            session,
            rule=rule,
            trigger_snapshot=snapshot,
            webhook_event_id=event.id,
            now=now,
        )
        run_ids.append(run.id)

    if run_ids:
        event.autopilot_id = matched[0].id
        event.process_status = "dispatched"
    elif matched:
        event.process_status = "matched"
    event.updated_at = now
    await session.flush()

    return 200, {
        "received": True,
        "event_id": event_id,
        "process_status": event.process_status,
        "run_id": str(run_ids[0]) if run_ids else None,
    }


async def _store_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    autopilot_id: uuid.UUID | None,
    idempotency_key: str,
    event_type: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    signature_status: str,
    process_status: str,
    now: datetime,
) -> WebhookEvent:
    event = WebhookEvent(
        workspace_id=workspace_id,
        autopilot_id=autopilot_id,
        idempotency_key=idempotency_key,
        event_type=event_type,
        headers=headers or None,
        payload=payload,
        signature_status=signature_status,
        process_status=process_status,
        received_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(event)
    await session.flush()
    return event


__all__ = [
    "EVENT_ID_HEADER",
    "EVENT_TYPE_HEADER",
    "REJECTED_KEY_PREFIX",
    "SIGNATURE_HEADER",
    "create_secret",
    "generate_credential_pair",
    "hash_token",
    "lookup_secret_by_token",
    "process_inbound",
    "public_secret_row",
    "rotate_secret",
    "verify_signature",
]
