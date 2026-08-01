"""Signed, query-bound opaque cursor (spec §3.2, §6.14 keyset paging).

Layout::

    cursor = base64url(json {
        "fp":  sha256(q + sorted types csv + workspace_id hex),
        "t":   [score_bucket, title_len, title_lex, type, id],
        "sig": hmac_sha256(secret, fp || 0x1f || canonical(t))
    })

The tuple mirrors the §4.6 total order factor for factor; ``sig`` is checked
BEFORE any internal field is trusted (MES-75 lineage) — a bad signature or a
fingerprint that does not match the current (q, types, workspace) is a 400
``validation_error``. Paging re-computes the bounded candidate pool and
applies keyset ``> tuple``, so the tuple must be fully database-derivable
(no local recency/frequency, §4.6 R2-H4).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid

from mesh.errors import ValidationError

# Unit separator between fingerprint components / signed chunks — cannot
# appear in uuid hex, type names or the numeric factors, so concatenation is
# unambiguous.
_SEP = "\x1f"

# Last-resort signing key when neither MESH_SEARCH_CURSOR_SECRET nor the
# server JWT secret is configured (the JWT secret always has a default, so
# this is effectively unreachable). Process-random: cursors issued by one
# replica / before a restart would fail verification — an acceptable
# degradation (clients simply restart paging; a 400, never a wrong page).
_PROCESS_FALLBACK_SECRET = secrets.token_bytes(32)


def resolve_cursor_secret(settings) -> bytes:
    """The cursor HMAC key: explicit setting → server JWT secret → random.

    Reuses the existing server signing key (``jwt_secret``) by default so
    cursors survive restarts and multi-replica deployments without extra
    configuration; ``MESH_SEARCH_CURSOR_SECRET`` overrides when an operator
    wants cursor signatures decoupled from token signing.
    """
    configured = (getattr(settings, "search_cursor_secret", "") or "").strip()
    if configured:
        return configured.encode("utf-8")
    jwt_secret = (getattr(settings, "jwt_secret", "") or "").strip()
    if jwt_secret:
        return jwt_secret.encode("utf-8")
    return _PROCESS_FALLBACK_SECRET


def binding_fingerprint(q: str, types_sorted: tuple[str, ...], workspace_id: uuid.UUID) -> str:
    """sha256 binding the cursor to exactly one (q, types, workspace)."""
    payload = _SEP.join((q, ",".join(types_sorted), workspace_id.hex))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_sort_factors(
    *, score_bucket: int, title_len: int, title_lex: str, result_type: str, result_id: str
) -> list:
    """The §4.6 total-order tuple, factor for factor (JSON-serializable)."""
    return [score_bucket, title_len, title_lex, result_type, result_id]


def _sign(secret: bytes, fp: str, factors: list) -> str:
    body = _SEP.join((fp, json.dumps(factors, separators=(",", ":"), ensure_ascii=False)))
    return hmac.new(secret, body.encode("utf-8"), hashlib.sha256).hexdigest()


def encode_cursor(secret: bytes, *, fp: str, factors: list) -> str:
    """Produce the opaque cursor string (urlsafe base64, unpadded)."""
    envelope = {"fp": fp, "t": factors, "sig": _sign(secret, fp, factors)}
    raw = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(secret: bytes, raw: str) -> tuple[str, list]:
    """Verify + unpack a cursor; ANY integrity failure → 400 validation_error.

    Returns ``(fingerprint, factors)``. Internal fields are untrusted until
    the HMAC check passes; structure violations raise the same neutral error
    (no detail leaks cursor internals).
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        envelope = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        fp = envelope["fp"]
        factors = envelope["t"]
        signature = envelope["sig"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationError("invalid cursor", code="validation_error") from exc
    if not isinstance(fp, str) or not isinstance(factors, list) or not isinstance(signature, str):
        raise ValidationError("invalid cursor", code="validation_error")
    expected = _sign(secret, fp, factors)
    if not hmac.compare_digest(expected, signature):
        raise ValidationError("invalid cursor", code="validation_error")
    return fp, factors


def factors_as_sort_key(factors: list) -> tuple[int, int, str, str, str]:
    """Comparable key mirroring the total order (bucket DESC via negation).

    Raises 400 when a signed cursor's factor list is well-formed JSON but not
    the exact five-factor shape — signature-valid yet malformed can only
    happen through a server bug or key reuse across schema versions; fail
    closed either way.
    """
    try:
        score_bucket, title_len, title_lex, result_type, result_id = factors
        return (
            -int(score_bucket),
            int(title_len),
            str(title_lex),
            str(result_type),
            str(result_id),
        )
    except (ValueError, TypeError) as exc:
        raise ValidationError("invalid cursor", code="validation_error") from exc
