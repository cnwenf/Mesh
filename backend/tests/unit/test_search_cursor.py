"""Search cursor contract (§3.2): signing, binding, tamper rejection."""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from mesh.errors import ValidationError
from mesh.search.cursor import (
    binding_fingerprint,
    decode_search_cursor,
    encode_search_cursor,
)

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret"


def _encoded(**overrides) -> str:
    params = {
        "score_bucket": 6,
        "title_len": 9,
        "title_lex": "登录页崩溃",
        "result_type": "issue",
        "row_id": uuid.uuid4(),
        "fingerprint": overrides.pop("fingerprint", "fp-1"),
        "secret": overrides.pop("secret", SECRET),
    }
    params.update(overrides)
    return encode_search_cursor(**params)


def test_roundtrip():
    row_id = uuid.uuid4()
    raw = encode_search_cursor(
        score_bucket=8,
        title_len=3,
        title_lex="abc",
        result_type="member",
        row_id=row_id,
        fingerprint="fp-x",
        secret=SECRET,
    )
    decoded = decode_search_cursor(raw, expected_fingerprint="fp-x", secret=SECRET)
    assert decoded.score_bucket == 8
    assert decoded.title_len == 3
    assert decoded.title_lex == "abc"
    assert decoded.result_type == "member"
    assert decoded.row_id == row_id


def test_tampered_payload_rejected():
    raw = _encoded()
    envelope = json.loads(base64.urlsafe_b64decode(raw))
    envelope["b"]["t"][0] = 9  # inflate the score bucket
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
    ).decode()
    with pytest.raises(ValidationError) as exc:
        decode_search_cursor(tampered, expected_fingerprint="fp-1", secret=SECRET)
    assert exc.value.code == "invalid_cursor"


def test_tampered_signature_rejected():
    raw = _encoded()
    envelope = json.loads(base64.urlsafe_b64decode(raw))
    envelope["s"] = "0" * 64
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
    ).decode()
    with pytest.raises(ValidationError):
        decode_search_cursor(tampered, expected_fingerprint="fp-1", secret=SECRET)


def test_wrong_secret_rejected():
    raw = _encoded(secret="other-secret")
    with pytest.raises(ValidationError):
        decode_search_cursor(raw, expected_fingerprint="fp-1", secret=SECRET)


def test_cross_query_reuse_rejected():
    raw = _encoded(fingerprint="fp-a")
    with pytest.raises(ValidationError) as exc:
        decode_search_cursor(raw, expected_fingerprint="fp-b", secret=SECRET)
    assert exc.value.code == "invalid_cursor"


def test_garbage_rejected():
    for garbage in ("", "!!!", "aGVsbG8=", base64.urlsafe_b64encode(b"{}").decode()):
        with pytest.raises(ValidationError):
            decode_search_cursor(garbage, expected_fingerprint="fp-1", secret=SECRET)


def test_fingerprint_order_independent_over_types():
    fp1 = binding_fingerprint("q", frozenset({"issue", "member"}), uuid.UUID(int=1))
    fp2 = binding_fingerprint("q", frozenset({"member", "issue"}), uuid.UUID(int=1))
    fp3 = binding_fingerprint("q2", frozenset({"issue", "member"}), uuid.UUID(int=1))
    fp4 = binding_fingerprint("q", frozenset({"issue", "member"}), uuid.UUID(int=2))
    assert fp1 == fp2
    assert {fp1, fp3, fp4} == {fp1, fp3, fp4}  # distinct inputs → distinct fingerprints
    assert fp1 != fp3
    assert fp1 != fp4
