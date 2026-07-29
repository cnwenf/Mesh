"""Full-channel secret redaction guard (README §6.16, runtime.md §5.2 red line).

Secrets must never leave the platform through ANY content channel. This
module is the single guard every outgoing-text path calls:

- **logs** — ``logs.py`` redacts before storage / push (per-line replacement);
- **attachments** — ``attachment/processing.py`` scans text-like uploads and
  BLOCKS them (quarantine gate never opens) + raises a critical security
  alert on hit;
- **comments** — the comment module (MES-58) must call
  :func:`assert_no_workspace_secrets` on comment content before insert;
- **result** — ``attempts.py`` calls :func:`redact_result` before persisting
  the terminal result (§2.5 S-06 server-side fallback);
- **diff** — ``checkout.py`` calls :func:`redact_diff_text` before persisting
  the diff (§2.5 S-06 server-side fallback).

Daemon redacts first; server redacts again as fallback — never trust daemon.
Hits are counted; the blocking variant raises 422 ``secret_detected`` and the
caller emits the §6.13 critical alert.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from mesh.errors import BusinessRuleError
from mesh.runtime.credentials import load_redaction_blacklist, redact_text

logger = logging.getLogger("mesh.runtime.redaction")

# Text-like MIME families whose attachment payloads are secret-scanned.
TEXT_SCAN_MIME_PREFIXES = ("text/",)
TEXT_SCAN_MIME_EXACT = frozenset(
    {
        "application/json",
        "application/javascript",
        "application/xml",
        "application/x-sh",
        "application/x-shellscript",
        "application/yaml",
    }
)


def mime_is_textual(mime_type: str | None) -> bool:
    if not mime_type:
        return False
    return mime_type.startswith(TEXT_SCAN_MIME_PREFIXES) or mime_type in TEXT_SCAN_MIME_EXACT


async def scan_text_for_secrets(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    content: str,
    signing_secret: str,
) -> int:
    """Count secret occurrences in ``content`` against the workspace's
    ``redact_in_logs`` blacklist (0 = clean)."""
    if not content:
        return 0
    blacklist = await load_redaction_blacklist(session, workspace_id, signing_secret)
    _, hits = redact_text(content, blacklist)
    return hits


async def scan_bytes_for_secrets(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    data: bytes,
    signing_secret: str,
) -> int:
    """Decode (lossy UTF-8 — secrets are ASCII-printable by convention) and
    scan; binary payloads that fail to decode meaningfully still scan on the
    replaced text."""
    if not data:
        return 0
    return await scan_text_for_secrets(
        session,
        workspace_id=workspace_id,
        content=data.decode("utf-8", errors="replace"),
        signing_secret=signing_secret,
    )


async def assert_no_workspace_secrets(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    content: str,
    signing_secret: str,
    channel: str,
) -> None:
    """Blocking guard for write paths: raise 422 ``secret_detected`` when the
    content carries any workspace secret. ``channel`` names the outlet for
    the error details (comments / attachments / ...)."""
    hits = await scan_text_for_secrets(
        session,
        workspace_id=workspace_id,
        content=content,
        signing_secret=signing_secret,
    )
    if hits:
        raise BusinessRuleError(
            "content contains workspace secrets",
            code="secret_detected",
            details={"channel": channel, "hits": hits},
        )


# ---------------------------------------------------------------------------
# §2.5 S-06: server-side fallback redaction for result and diff channels.
# Daemon redacts first; server MUST redact again (never trust daemon).
# ---------------------------------------------------------------------------

_REDACT_REPLACEMENT = "***"


async def redact_result(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    result: dict | None,
    signing_secret: str,
) -> tuple[dict | None, int]:
    """§2.5 S-06: redact secrets from a terminal result dict before persist.

    Serializes the result to JSON, redacts all secret occurrences, and
    returns (redacted_result, hit_count). If hit_count > 0 a security
    alert is logged (the daemon failed to redact — ISO-13).
    """
    if not result:
        return result, 0
    blacklist = await load_redaction_blacklist(session, workspace_id, signing_secret)
    if not blacklist:
        return result, 0
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    redacted_text, hits = redact_text(serialized, blacklist)
    if hits > 0:
        logger.warning(
            "server-side result redaction: %d secret hit(s) in workspace %s "
            "(daemon first-layer redaction failed)",
            hits,
            workspace_id,
        )
        try:
            return json.loads(redacted_text), hits
        except (json.JSONDecodeError, ValueError):
            # Redaction broke JSON structure — return a safe stub.
            return {"output": _REDACT_REPLACEMENT, "redacted": True}, hits
    return result, 0


async def redact_diff_text(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    diff: str | None,
    signing_secret: str,
) -> tuple[str | None, int]:
    """§2.5 S-06: redact secrets from a diff string before persist to
    object storage. Returns (redacted_diff, hit_count)."""
    if not diff:
        return diff, 0
    blacklist = await load_redaction_blacklist(session, workspace_id, signing_secret)
    if not blacklist:
        return diff, 0
    redacted, hits = redact_text(diff, blacklist)
    if hits > 0:
        logger.warning(
            "server-side diff redaction: %d secret hit(s) in workspace %s "
            "(daemon first-layer redaction failed)",
            hits,
            workspace_id,
        )
    return redacted, hits
