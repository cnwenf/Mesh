"""Full-channel secret redaction guard (README §6.16, runtime.md §5.2 red line).

Secrets must never leave the platform through ANY content channel. This
module is the single guard every outgoing-text path calls:

- **logs** — ``logs.py`` redacts before storage / push (per-line replacement);
- **attachments** — ``attachment/processing.py`` scans text-like uploads and
  BLOCKS them (quarantine gate never opens) + raises a critical security
  alert on hit;
- **comments** — the comment module (MES-58) must call
  :func:`assert_no_workspace_secrets` on comment content before insert; the
  guard is provided here so that path is a one-liner when the module lands.

Hits are counted; the blocking variant raises 422 ``secret_detected`` and the
caller emits the §6.13 critical alert.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from mesh.errors import BusinessRuleError
from mesh.runtime.credentials import load_redaction_blacklist, redact_text

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
