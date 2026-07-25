"""Unit tests for the auth cryptographic primitives (auth.md §5.5)."""

from __future__ import annotations

import pytest

from mesh.auth import security
from mesh.errors import ValidationError


class TestPasswordHashing:
    def test_hash_then_verify_roundtrip(self):
        hashed = security.hash_password("correct-horse-battery-9")
        assert hashed != "correct-horse-battery-9"
        assert hashed.startswith("$argon2id$")
        assert security.verify_password("correct-horse-battery-9", hashed) is True

    def test_verify_rejects_wrong_password(self):
        hashed = security.hash_password("correct-horse-battery-9")
        assert security.verify_password("wrong-password-1", hashed) is False

    def test_verify_handles_malformed_hash_without_raising(self):
        assert security.verify_password("anything", "not-a-valid-hash") is False
        assert security.verify_password("anything", "") is False

    def test_hashes_are_salted_and_distinct(self):
        a = security.hash_password("same-password-1")
        b = security.hash_password("same-password-1")
        assert a != b  # distinct salts
        assert security.verify_password("same-password-1", a)
        assert security.verify_password("same-password-1", b)


class TestPasswordStrength:
    @pytest.mark.parametrize(
        "password",
        ["short1", "allletters", "12345678", "longenoughbutnodigits"],
    )
    def test_rejects_weak_passwords(self, password):
        with pytest.raises(security.WeakPasswordError):
            security.validate_password_strength(password)

    def test_rejects_common_password(self):
        with pytest.raises(security.WeakPasswordError) as exc:
            security.validate_password_strength("password123")
        assert exc.value.details["reason"] == "too_common"

    def test_accepts_strong_password(self):
        security.validate_password_strength("a-strong-passw0rd")  # no raise

    def test_too_short_includes_min_length_detail(self):
        with pytest.raises(security.WeakPasswordError) as exc:
            security.validate_password_strength("ab1")
        assert exc.value.details["min_length"] == security.PASSWORD_MIN_LENGTH


class TestTokens:
    def test_generate_token_is_high_entropy_and_unique(self):
        tokens = {security.generate_token() for _ in range(100)}
        assert len(tokens) == 100
        assert all(len(t) >= 40 for t in tokens)

    def test_hash_token_is_sha256_hex_and_deterministic(self):
        token = security.generate_token()
        h = security.hash_token(token)
        assert len(h) == 64  # sha256 hex
        assert h == security.hash_token(token)
        assert h != token  # never the plaintext

    def test_constant_time_equals(self):
        assert security.constant_time_equals("abc", "abc") is True
        assert security.constant_time_equals("abc", "abd") is False


class TestSecretEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        cipher = security.encrypt_secret("JBSWY3DPEHPK3PXP", "signing-secret")
        assert cipher != "JBSWY3DPEHPK3PXP"
        assert security.decrypt_secret(cipher, "signing-secret") == "JBSWY3DPEHPK3PXP"

    def test_decrypt_with_wrong_key_raises_validation(self):
        cipher = security.encrypt_secret("payload", "key-a")
        with pytest.raises(ValidationError) as exc:
            security.decrypt_secret(cipher, "key-b")
        assert exc.value.code == "undecryptable_secret"

    def test_decrypt_garbage_raises_validation(self):
        with pytest.raises(ValidationError):
            security.decrypt_secret("not-fernet", "key-a")

    def test_derive_fernet_key_is_stable(self):
        assert security.derive_fernet_key("s") == security.derive_fernet_key("s")
