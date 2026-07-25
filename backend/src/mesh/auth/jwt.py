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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from mesh.errors import UnauthorizedError

ACCESS_TOKEN_TYPE = "access"


@dataclass(frozen=True)
class AccessToken:
    """Decoded, validated access-token claims."""

    subject: uuid.UUID
    jti: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def encode_access_token(
    *,
    subject: uuid.UUID,
    secret: str,
    algorithm: str,
    ttl: timedelta,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Issue an access JWT; returns ``(token, jti)``."""
    moment = now or _now()
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(subject),
        "iat": int(moment.timestamp()),
        "exp": int((moment + ttl).timestamp()),
        "jti": jti,
        "typ": ACCESS_TOKEN_TYPE,
    }
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

    return AccessToken(
        subject=subject,
        jti=str(claims.get("jti", "")),
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
    )
