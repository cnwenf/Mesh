"""Credential fencing — runtime.md §2.2 protocol, README §6.16.

Secrets exist server-side as Fernet ciphertext (``encrypted_value``; key
derived from the JWT signing secret). Plaintext is materialized ONLY inside
claim / refetch responses as a short-lived envelope bound to the attempt and
lease; every other surface sees metadata at best and ``***`` otherwise.

``redact_text`` is the shared full-channel scanner (§6.16): logs, agent
comments and attachment artifacts all run their outgoing text through the
workspace's ``redact_in_logs`` blacklist before it is written anywhere.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.auth.security import decrypt_secret, encrypt_secret
from mesh.db.models.runtime import (
    ExecutionCredential,
    RuntimeCredential,
)
from mesh.errors import BusinessRuleError, ConflictError

REDACTED = "***"


def encrypt_credential_value(plaintext: str, signing_secret: str) -> str:
    return encrypt_secret(plaintext, signing_secret)


def decrypt_credential_value(ciphertext: str, signing_secret: str) -> str:
    return decrypt_secret(ciphertext, signing_secret)


def redact_text(content: str, secret_values: list[str]) -> tuple[str, int]:
    """Replace every secret occurrence with ``***``; return (text, hits).

    Empty / whitespace-only secrets are ignored (they would match everything).
    Longest values first so overlapping secrets redact greedily.
    """
    hits = 0
    for value in sorted({v for v in secret_values if v and v.strip()}, key=len, reverse=True):
        count = content.count(value)
        if count:
            content = content.replace(value, REDACTED)
            hits += count
    return content, hits


async def load_redaction_blacklist(
    session: AsyncSession, workspace_id: uuid.UUID, signing_secret: str
) -> list[str]:
    """Decrypt the workspace's redact-flagged secrets for scanning.

    Called on the log-append hot path; a row that fails to decrypt (rotated
    key) is skipped rather than breaking ingestion.
    """
    rows = (
        await session.execute(
            select(RuntimeCredential.encrypted_value).where(
                RuntimeCredential.workspace_id == workspace_id,
                RuntimeCredential.redact_in_logs.is_(True),
                RuntimeCredential.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    values: list[str] = []
    for ciphertext in rows:
        try:
            values.append(decrypt_credential_value(ciphertext, signing_secret))
        except Exception:  # noqa: BLE001 — undecryptable row: skip, never fail
            continue
    return values


@dataclass(frozen=True)
class DeliveredCredential:
    """One credential as delivered in a claim / refetch response (only place
    plaintext may appear)."""

    id: uuid.UUID
    kind: str
    env: str | None
    value: str
    envelope: str
    expires_at: datetime


async def issue_envelopes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    attempt_id: uuid.UUID,
    credential_ids: list[uuid.UUID],
    signing_secret: str,
    envelope_ttl: timedelta,
    now: datetime | None = None,
) -> list[DeliveredCredential]:
    """Bind credentials to the attempt and return one-shot envelopes.

    Idempotent per (attempt, credential): an existing unrevoked binding is
    re-delivered with a FRESH envelope (the claim response is the sole
    plaintext channel; a lost response is recovered via refetch semantics).
    """
    now = now or datetime.now(timezone.utc)
    if not credential_ids:
        return []
    rows = (
        await session.execute(
            select(RuntimeCredential).where(
                RuntimeCredential.workspace_id == workspace_id,
                RuntimeCredential.id.in_(credential_ids),
                RuntimeCredential.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    expires_at = now + envelope_ttl
    delivered: list[DeliveredCredential] = []
    for credential_id in credential_ids:
        cred = by_id.get(credential_id)
        if cred is None:
            # Unknown / deleted credential in task_spec: skip delivery but keep
            # the claim going (the task runs without that secret).
            continue
        existing = await session.get(
            ExecutionCredential, (attempt_id, credential_id)
        )
        if existing is not None and existing.revoked_at is None:
            envelope_ref = f"env-{uuid.uuid4().hex[:16]}"
            existing.envelope_ref = envelope_ref
        else:
            envelope_ref = f"env-{uuid.uuid4().hex[:16]}"
            session.add(
                ExecutionCredential(
                    attempt_id=attempt_id,
                    credential_id=credential_id,
                    workspace_id=workspace_id,
                    envelope_ref=envelope_ref,
                    injected_at=now,
                )
            )
        delivered.append(
            DeliveredCredential(
                id=cred.id,
                kind=cred.kind,
                env=cred.env_name,
                value=decrypt_credential_value(cred.encrypted_value, signing_secret),
                envelope=envelope_ref,
                expires_at=expires_at,
            )
        )
    return delivered


async def refetch_envelopes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    attempt_id: uuid.UUID,
    signing_secret: str,
    envelope_ttl: timedelta,
    refetch_limit: int,
    now: datetime | None = None,
) -> list[DeliveredCredential]:
    """Re-issue envelopes after a lost response (§2.2 refetch protocol).

    Old envelopes are revoked immediately (``revoked_at``), the per-attempt
    counter advances, and exceeding the cap raises — the caller freezes the
    execution for human review.
    """
    now = now or datetime.now(timezone.utc)
    bindings = (
        await session.execute(
            select(ExecutionCredential).where(
                ExecutionCredential.attempt_id == attempt_id,
                ExecutionCredential.workspace_id == workspace_id,
            )
        )
    ).scalars().all()
    if not bindings:
        return []
    if any(b.refetch_count >= refetch_limit for b in bindings):
        raise ConflictError(
            "credential refetch limit exceeded",
            code="credential_refetch_limit",
            details={"limit": refetch_limit},
        )
    cred_rows = (
        await session.execute(
            select(RuntimeCredential).where(
                RuntimeCredential.id.in_([b.credential_id for b in bindings])
            )
        )
    ).scalars().all()
    by_id = {row.id: row for row in cred_rows}
    expires_at = now + envelope_ttl
    delivered: list[DeliveredCredential] = []
    for binding in bindings:
        if binding.revoked_at is not None:
            # A freeze / terminal revocation is final — refetch cannot
            # resurrect envelopes for a dead attempt.
            raise ConflictError(
                "attempt envelopes revoked",
                code="envelope_revoked",
            )
        # Rotate in place: the previous envelope id becomes unreachable (there
        # is no envelope store to redeem it against), the counter advances and
        # the audit row records how many times this happened.
        binding.envelope_ref = f"env-{uuid.uuid4().hex[:16]}"
        binding.refetch_count = binding.refetch_count + 1
        cred = by_id.get(binding.credential_id)
        if cred is None:
            continue
        delivered.append(
            DeliveredCredential(
                id=cred.id,
                kind=cred.kind,
                env=cred.env_name,
                value=decrypt_credential_value(cred.encrypted_value, signing_secret),
                envelope=binding.envelope_ref,
                expires_at=expires_at,
            )
        )
    return delivered


async def revoke_attempt_envelopes(
    session: AsyncSession, *, attempt_id: uuid.UUID, now: datetime | None = None
) -> int:
    """Terminal-state / freeze revocation: set revoked_at on live envelopes."""
    now = now or datetime.now(timezone.utc)
    result = await session.execute(
        update(ExecutionCredential)
        .where(
            ExecutionCredential.attempt_id == attempt_id,
            ExecutionCredential.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    return result.rowcount or 0


async def revoke_execution_envelopes(
    session: AsyncSession, *, execution_id: uuid.UUID, now: datetime | None = None
) -> int:
    """Freeze path (§4.10): revoke every envelope of every attempt at once."""
    from mesh.db.models.runtime import ExecutionAttempt

    now = now or datetime.now(timezone.utc)
    result = await session.execute(
        update(ExecutionCredential)
        .where(
            ExecutionCredential.attempt_id.in_(
                select(ExecutionAttempt.id).where(ExecutionAttempt.execution_id == execution_id)
            ),
            ExecutionCredential.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    return result.rowcount or 0
