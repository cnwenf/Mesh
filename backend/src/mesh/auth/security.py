"""Cryptographic primitives for the auth module (auth.md §5.5).

- Passwords: argon2id hashing with constant-time verification (never plaintext,
  never reversible).
- Bearer/refresh/reset tokens: high-entropy random strings; the database stores
  only a SHA-256 hash, the plaintext exists only at creation/send time.
- At-rest secrets (MFA TOTP key): Fernet symmetric encryption with a key
  derived from the JWT signing secret, so a single env var drives both.

Every helper here is pure/stateless so it can be unit-tested in isolation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Awaitable, Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from mesh.errors import ValidationError

# argon2id with the OWASP-recommended floor (t=3, m=64 MiB, p=4). The hasher is
# stateless and thread-safe; verification re-reads parameters from the stored
# hash so cost parameters can be raised without invalidating existing hashes.
_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

PASSWORD_MIN_LENGTH = 8

# A small built-in blocklist of the most common leaked passwords (auth.md §5.1:
# reject common weak/leaked passwords). Kept short and lowercase; the strength
# rules below do the heavy lifting.
COMMON_WEAK_PASSWORDS: frozenset[str] = frozenset(
    {
        "password",
        "password1",
        "password123",
        "123456",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty",
        "qwerty123",
        "abc123",
        "111111",
        "letmein",
        "welcome",
        "iloveyou",
        "admin123",
        "mesh1234",
    }
)

_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"[0-9]")


class WeakPasswordError(ValidationError):
    """400 ``weak_password`` — password fails the strength policy."""

    code = "weak_password"
    message = "password does not meet the strength requirements"


def hash_password(password: str) -> str:
    """Return the argon2id hash of ``password`` (salt + cost params embedded)."""
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verify ``password`` against an argon2id hash.

    Returns False (never raises) on mismatch or a malformed/legacy hash so
    callers can take one uniform anti-enumeration path.
    """
    if not password_hash:
        return False
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_password_strength(password: str) -> None:
    """Enforce the password policy (auth.md §5.1); raise on violation.

    Rules: >= 8 chars, at least one letter and one digit, not a common/leaked
    password. ``details.reason`` names the failing rule.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        raise WeakPasswordError(
            "password too short",
            details={"reason": "too_short", "min_length": PASSWORD_MIN_LENGTH},
        )
    if not _HAS_LETTER.search(password) or not _HAS_DIGIT.search(password):
        raise WeakPasswordError(
            "password must contain both letters and digits",
            details={"reason": "needs_letter_and_digit"},
        )
    if password.lower() in COMMON_WEAK_PASSWORDS:
        raise WeakPasswordError(
            "password is too common",
            details={"reason": "too_common"},
        )


# --- opaque tokens (refresh / reset / verification / PAT) --------------------

# 32 bytes of entropy → 43 base64url chars; well above the 128-bit floor.
_TOKEN_BYTES = 32


def generate_token() -> str:
    """Generate a high-entropy URL-safe random token (plaintext, shown once)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a token — the only form ever stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    """Length-stable constant-time string comparison (defence in depth)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# --- device-code grant secrets (auth.md §2.4.2 / §5.5) ------------------------

# Keyed hash for device_code / user_code storage. The user_code is LOW entropy
# (>=20bit), so a bare SHA-256 would fall to offline dictionary attack against
# a leaked database — HMAC-SHA256 with a server-side pepper is mandatory, and
# a missing pepper fails closed rather than silently degrading to bare hashing.
def hmac_token(token: str, pepper: str | None) -> str:
    """Return the HMAC-SHA256 hex digest of ``token`` keyed by ``pepper``."""
    if not pepper:
        raise ValueError(
            "device code pepper is not configured (MESH_DEVICE_CODE_PEPPER)"
        )
    return hmac.new(pepper.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


# Unambiguous RFC 8628-style alphabet: 0/O/1/I/L are removed so a code read
# aloud or retyped from another device has no confusable glyphs.
USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 31 chars
USER_CODE_SEGMENT_LEN = 4
USER_CODE_SEGMENTS = 2  # 8 chars ⇒ 8·log2(31) ≈ 39.7bit ≥ 20bit (auth.md §5.5 ①)
USER_CODE_MAX_ATTEMPTS = 5  # active-collision retries before giving up
# 32 bytes from the CSPRNG ⇒ 256bit ≥ 128bit floor (auth.md §5.5 ①).
DEVICE_CODE_BYTES = 32


class DeviceCodeSpaceExhausted(RuntimeError):
    """The active user_code space kept colliding — raise 500 + alert (§2.4.2)."""


async def generate_user_code(
    is_taken: "Callable[[str], Awaitable[bool]] | None" = None,
) -> str:
    """Generate a grouped ``XXXX-XXXX`` user code (plaintext, shown once).

    ``is_taken`` (awaited per candidate) checks the partial unique index over
    ACTIVE codes; a collision regenerates, up to ``USER_CODE_MAX_ATTEMPTS``,
    then raises — the active set is TTL-bounded so collisions are vanishingly
    rare but must be recoverable (auth.md §2.4.2).
    """
    total = USER_CODE_SEGMENT_LEN * USER_CODE_SEGMENTS
    for _ in range(USER_CODE_MAX_ATTEMPTS):
        raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(total))
        code = "-".join(
            raw[i : i + USER_CODE_SEGMENT_LEN]
            for i in range(0, total, USER_CODE_SEGMENT_LEN)
        )
        if is_taken is None or not await is_taken(code):
            return code
    raise DeviceCodeSpaceExhausted("user_code space exhausted under the active set")


def generate_device_code() -> str:
    """Generate the high-entropy device_code (plaintext, shown once, never printed)."""
    return secrets.token_urlsafe(DEVICE_CODE_BYTES)


# Session refresh token prefix (auth.md §2.5.1 prefix registry). Type-semantics
# enforcement lives in the Bearer dependency: mesh_rft_ is ONLY accepted by
# POST /api/v1/auth/refresh — its appearance anywhere else is a 401.
REFRESH_TOKEN_PREFIX = "mesh_rft_"


def generate_refresh_token() -> str:
    """Generate a prefixed refresh token (plaintext; DB stores the SHA-256)."""
    return REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)


# --- at-rest secret encryption (MFA) -----------------------------------------


def derive_fernet_key(secret: str) -> bytes:
    """Derive a stable Fernet key from the JWT signing secret (SHA-256 → b64)."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(plaintext: str, signing_secret: str) -> str:
    """Fernet-encrypt ``plaintext`` for at-rest storage (MFA key)."""
    return Fernet(derive_fernet_key(signing_secret)).encrypt(plaintext.encode("utf-8")).decode()


def decrypt_secret(ciphertext: str, signing_secret: str) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`.

    Raises :class:`ValidationError` (``undecryptable_secret``) on tampering or a
    rotated key rather than leaking the crypto error to callers.
    """
    try:
        return Fernet(derive_fernet_key(signing_secret)).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise ValidationError(
            "stored secret could not be decrypted",
            code="undecryptable_secret",
        ) from exc
