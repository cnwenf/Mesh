"""Access-token JWT issuing/verification (auth.md §4.5/§5.5).

The access token is a short-lived, stateless JWT. Verification is deliberately
strict:

- the algorithm is FIXED from configuration (default HS256) — the ``alg`` header
  declared by the token is never trusted, which rejects ``alg=none`` and defeats
  HS/RS confusion attacks;
- ``exp`` is required and enforced;
- the ``typ`` claim must be ``access`` so a refresh/reset token can never be
  replayed as an access token.

Refresh tokens are NOT JWTs — they are opaque strings stored server-side as
SHA-256 hashes (see security.py / the sessions table), which is what makes them
revocable.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from mesh.errors import UnauthorizedError

ACCESS_TOKEN_TYPE = "access"


@dataclass(frozen=True)
class AccessToken:
    """Decoded, validated access-token claims.

    ``authenticated_at`` is when the user last performed a primary
    authentication (password / TOTP) — carried across silent refreshes — and
    backs step-up re-authentication (§5.5): sensitive operations require it to be
    recent.
    """

    subject: uuid.UUID
    jti: str
    expires_at: datetime
    authenticated_at: datetime
    # Session-location invariant anchor (auth.md §1.1): names the sessions row.
    # Regular routes never look it up; lifecycle operations (refresh /
    # introspect / revoke / reauth / device approve) locate the session by it.
    sid: uuid.UUID | None = None
    # Device-session bindings fixed at approval (auth.md §2.4 access JWT 声明):
    # web sessions carry None / empty — their workspace resolves per request.
    workspace_id: uuid.UUID | None = None
    scopes: frozenset[str] = frozenset()


def _now() -> datetime:
    return datetime.now(UTC)


def encode_access_token(
    *,
    subject: uuid.UUID,
    secret: str,
    algorithm: str,
    ttl: timedelta,
    now: datetime | None = None,
    auth_time: datetime | None = None,
    session_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    scopes: Sequence[str] | None = None,
) -> tuple[str, str]:
    """Issue an access JWT; returns ``(token, jti)``.

    ``auth_time`` records the last primary authentication (defaults to now);
    silent refresh forwards the original value so step-up re-auth (§5.5) reflects
    the real authentication age, not the token re-issue time. ``session_id``
    (the ``sid`` claim) names the sessions row; ``workspace_id`` / ``scopes``
    bind a device session to its approved workspace and fixed scope set.
    """
    moment = now or _now()
    auth_moment = auth_time or moment
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(subject),
        "iat": int(moment.timestamp()),
        "exp": int((moment + ttl).timestamp()),
        "jti": jti,
        "typ": ACCESS_TOKEN_TYPE,
        "auth_time": int(auth_moment.timestamp()),
    }
    if session_id is not None:
        claims["sid"] = str(session_id)
    if workspace_id is not None:
        claims["workspace_id"] = str(workspace_id)
    if scopes:
        claims["scope"] = sorted(scopes)
    token = jwt.encode(claims, secret, algorithm=algorithm)
    # PyJWT returns str for HS* algorithms; normalise for type safety.
    return token if isinstance(token, str) else token.decode("ascii"), jti


def decode_access_token(token: str, *, secret: str, algorithm: str) -> AccessToken:
    """Verify and decode an access JWT; raise 401 ``unauthorized`` on any failure.

    ``algorithms=[algorithm]`` pins the expected algorithm — PyJWT rejects any
    token whose header ``alg`` differs (including ``none``), so the client can
    never dictate the verification algorithm.
    """
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={
                "require": ["exp", "sub", "iat"],
                "verify_exp": True,
                "verify_iat": True,
                "verify_signature": True,
            },
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("invalid or expired token") from exc

    if claims.get("typ") != ACCESS_TOKEN_TYPE:
        raise UnauthorizedError("invalid or expired token", details={"reason": "wrong_token_type"})

    try:
        subject = uuid.UUID(str(claims["sub"]))
    except (ValueError, KeyError) as exc:
        raise UnauthorizedError("invalid or expired token") from exc

    # auth_time falls back to iat for tokens issued before step-up existed.
    auth_time_raw = claims.get("auth_time", claims["iat"])
    # Optional session/device claims — absent on pre-increment tokens.
    sid: uuid.UUID | None = None
    if claims.get("sid"):
        try:
            sid = uuid.UUID(str(claims["sid"]))
        except ValueError as exc:
            raise UnauthorizedError("invalid or expired token") from exc
    workspace_id: uuid.UUID | None = None
    if claims.get("workspace_id"):
        try:
            workspace_id = uuid.UUID(str(claims["workspace_id"]))
        except ValueError as exc:
            raise UnauthorizedError("invalid or expired token") from exc
    scope_raw = claims.get("scope") or []
    if not isinstance(scope_raw, list):
        raise UnauthorizedError("invalid or expired token")
    return AccessToken(
        subject=subject,
        jti=str(claims.get("jti", "")),
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
        authenticated_at=datetime.fromtimestamp(int(auth_time_raw), tz=UTC),
        sid=sid,
        workspace_id=workspace_id,
        scopes=frozenset(str(s) for s in scope_raw),
    )
