"""Search cursor: base64(sort tuple + binding fingerprint + HMAC) (§3.2).

The cursor binds to the exact ``(q, sorted types, workspace_id)`` that
produced it — reusing a cursor across any of those is a ``400
validation_error`` — and every internal field is distrusted until the
HMAC-SHA256 signature verifies (MES-75 security continuation).

The sort tuple mirrors the §4.6 total order factor-for-factor:
``(score_bucket, title_len, title_lex, type, id)`` — every factor is
computable by the database for any result row, so keyset pagination is
strictly monotonic with no client-state dependence (R2-H4).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass

from mesh.errors import ValidationError


@dataclass(frozen=True)
class SearchCursor:
    """Decoded, verified cursor payload."""

    score_bucket: int
    title_len: int
    title_lex: str
    result_type: str
    row_id: uuid.UUID


def binding_fingerprint(q: str, types: frozenset[str], workspace_id: uuid.UUID) -> str:
    """sha256 over the query parameters the cursor is bound to."""
    material = "\x00".join((q, ",".join(sorted(types)), str(workspace_id)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _signature(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def encode_search_cursor(
    *,
    score_bucket: int,
    title_len: int,
    title_lex: str,
    result_type: str,
    row_id: uuid.UUID,
    fingerprint: str,
    secret: str,
) -> str:
    """Encode one page boundary as an opaque, signed cursor."""
    body = {
        "t": [score_bucket, title_len, title_lex, result_type, str(row_id)],
        "f": fingerprint,
    }
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    envelope = {"b": body, "s": _signature(body_bytes, secret)}
    raw = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_search_cursor(
    raw: str,
    *,
    expected_fingerprint: str,
    secret: str,
) -> SearchCursor:
    """Decode + verify a cursor; any failure is a 400 ``validation_error``.

    Per §3.2/§3.5 a bad/tampered/reused cursor is the canonical
    ``validation_error`` code (no module-private code). Internal fields are
    used ONLY after the HMAC verifies; the binding fingerprint is checked
    afterwards so cross-query reuse is rejected.
    """

    def _invalid() -> ValidationError:
        return ValidationError("invalid cursor")

    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        envelope = json.loads(decoded)
        body = envelope["b"]
        signature = envelope["s"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise _invalid() from None

    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if not hmac.compare_digest(_signature(body_bytes, secret), str(signature)):
        raise _invalid()
    if body.get("f") != expected_fingerprint:
        # Cursor is validly signed but was produced by another
        # (q, types, workspace) — probing keyset paths is a 400.
        raise _invalid()

    try:
        sort_tuple = body["t"]
        score_bucket = int(sort_tuple[0])
        title_len = int(sort_tuple[1])
        title_lex = str(sort_tuple[2])
        result_type = str(sort_tuple[3])
        row_id = uuid.UUID(str(sort_tuple[4]))
    except (KeyError, TypeError, ValueError, IndexError):
        raise _invalid() from None

    return SearchCursor(
        score_bucket=score_bucket,
        title_len=title_len,
        title_lex=title_lex,
        result_type=result_type,
        row_id=row_id,
    )
