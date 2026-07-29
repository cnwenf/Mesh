"""Device-code crypto primitives (auth.md §2.4.2 / §5.5, quantified).

The spec makes these QUANTITATIVE acceptance points: user_code entropy >=20bit
over an unambiguous alphabet (0/O/1/I/L removed), device_code entropy >=128bit
from a CSPRNG, and BOTH codes stored only as HMAC-SHA256 keyed by a server
pepper — bare SHA-256 is explicitly insufficient for the low-entropy user_code.
"""

from __future__ import annotations

import math
import re

import pytest

from mesh.auth.security import (
    DEVICE_CODE_BYTES,
    REFRESH_TOKEN_PREFIX,
    USER_CODE_ALPHABET,
    USER_CODE_MAX_ATTEMPTS,
    DeviceCodeSpaceExhausted,
    generate_device_code,
    generate_refresh_token,
    generate_user_code,
    hash_token,
    hmac_token,
)

pytestmark = pytest.mark.unit

USER_CODE_RE = re.compile(r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")


class TestHmacToken:
    def test_deterministic_for_same_pepper(self):
        assert hmac_token("WDJB-MJHT", "pepper-1") == hmac_token("WDJB-MJHT", "pepper-1")

    def test_pepper_sensitive(self):
        # A keyed hash: different pepper ⇒ different digest (unlike bare SHA-256
        # which a leaked DB could dictionary-attack offline).
        assert hmac_token("WDJB-MJHT", "pepper-1") != hmac_token("WDJB-MJHT", "pepper-2")

    def test_differs_from_bare_sha256(self):
        assert hmac_token("WDJB-MJHT", "pepper-1") != hash_token("WDJB-MJHT")

    def test_empty_pepper_fails_closed(self):
        with pytest.raises(ValueError):
            hmac_token("WDJB-MJHT", "")

    def test_none_pepper_fails_closed(self):
        with pytest.raises(ValueError):
            hmac_token("WDJB-MJHT", None)  # type: ignore[arg-type]


class TestUserCode:
    def test_format_grouped_and_unambiguous(self):
        code = generate_user_code_sync()
        assert USER_CODE_RE.fullmatch(code), code
        # Ambiguous glyphs are excluded from the alphabet entirely.
        for ambiguous in "0O1IL":
            assert ambiguous not in USER_CODE_ALPHABET

    def test_entropy_floor(self):
        # auth.md §5.5 ①: >=20bit. 31-char alphabet ^ 8 positions ≈ 39.7bit.
        length = sum(1 for c in generate_user_code_sync() if c != "-")
        assert math.log2(len(USER_CODE_ALPHABET)) * length >= 20

    async def test_collision_retry_until_free(self):
        taken: set[str] = set()
        seen: list[str] = []

        async def is_taken(code: str) -> bool:
            seen.append(code)
            # Claim the first two candidates as collisions.
            if len(seen) <= 2:
                taken.add(code)
                return True
            return code in taken

        code = await generate_user_code(is_taken)
        assert len(seen) == 3
        assert USER_CODE_RE.fullmatch(code)

    async def test_exhaustion_raises_after_limit(self):
        calls = 0

        async def always_taken(code: str) -> bool:
            nonlocal calls
            calls += 1
            return True

        with pytest.raises(DeviceCodeSpaceExhausted):
            await generate_user_code(always_taken)
        assert calls == USER_CODE_MAX_ATTEMPTS

    async def test_optional_collision_check(self):
        # No callback → pure generation, no await dependency.
        assert USER_CODE_RE.fullmatch(await generate_user_code())


class TestDeviceCode:
    def test_entropy_floor(self):
        assert DEVICE_CODE_BYTES * 8 >= 128
        code = generate_device_code()
        # base64url of 32 bytes ⇒ 43 chars; never printed, only hashed.
        assert len(code) >= 43

    def test_uniqueness(self):
        assert len({generate_device_code() for _ in range(100)}) == 100


class TestRefreshTokenPrefix:
    def test_prefix_and_entropy(self):
        token = generate_refresh_token()
        assert token.startswith(REFRESH_TOKEN_PREFIX)
        assert REFRESH_TOKEN_PREFIX == "mesh_rft_"
        # Prefix + 32 bytes base64url.
        assert len(token) > len(REFRESH_TOKEN_PREFIX) + 40


def generate_user_code_sync() -> str:
    """Sync wrapper for pure (collision-unchecked) generation in sync tests."""
    import asyncio

    return asyncio.run(generate_user_code())
