"""Search cursor contract (§3.2): signing, binding and fail-closed parsing."""

from __future__ import annotations

import base64
import json
import uuid
from types import SimpleNamespace

import pytest

from mesh.errors import ValidationError
from mesh.search.cursor import (
    binding_fingerprint,
    canonical_sort_factors,
    decode_cursor,
    encode_cursor,
    factors_as_sort_key,
    resolve_cursor_secret,
)
from mesh.search.service import validate_search_params

pytestmark = pytest.mark.unit

SECRET = b"unit-test-search-cursor-secret"


def _factors(**overrides) -> list:
    values = {
        "score_bucket": 80,
        "title_len": 9,
        "title_lex": "登录页崩溃",
        "result_type": "issue",
        "result_id": str(uuid.uuid4()),
    }
    values.update(overrides)
    return canonical_sort_factors(**values)


def _encoded(*, fp: str = "fp-1", secret: bytes = SECRET, factors: list | None = None) -> str:
    return encode_cursor(secret, fp=fp, factors=factors or _factors())


def _envelope(raw: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def _pack(envelope: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(envelope, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()


def test_roundtrip() -> None:
    factors = _factors(score_bucket=90, title_len=3, title_lex="abc", result_type="member")
    raw = _encoded(fp="fp-x", factors=factors)
    fp, decoded = decode_cursor(SECRET, raw)
    assert fp == "fp-x"
    assert decoded == factors
    assert factors_as_sort_key(decoded) == (-90, 3, "abc", "member", factors[4])


def test_cursor_secret_precedence_and_fallback() -> None:
    assert resolve_cursor_secret(
        SimpleNamespace(search_cursor_secret=" cursor-secret ", jwt_secret="jwt-secret")
    ) == b"cursor-secret"
    assert resolve_cursor_secret(
        SimpleNamespace(search_cursor_secret="   ", jwt_secret=" jwt-secret ")
    ) == b"jwt-secret"

    fallback = resolve_cursor_secret(SimpleNamespace())
    assert isinstance(fallback, bytes)
    assert len(fallback) == 32


def test_tampered_payload_rejected() -> None:
    envelope = _envelope(_encoded())
    envelope["t"][0] = 999
    with pytest.raises(ValidationError) as excinfo:
        decode_cursor(SECRET, _pack(envelope))
    assert excinfo.value.code == "validation_error"


def test_tampered_signature_and_wrong_secret_rejected() -> None:
    envelope = _envelope(_encoded())
    envelope["sig"] = "0" * 64
    with pytest.raises(ValidationError):
        decode_cursor(SECRET, _pack(envelope))
    with pytest.raises(ValidationError):
        decode_cursor(b"another-secret", _encoded())


def test_cross_query_reuse_rejected_by_request_binding() -> None:
    ws = uuid.uuid4()
    fp = binding_fingerprint("q-a", ("issue",), ws)
    raw = _encoded(fp=fp)
    with pytest.raises(ValidationError) as excinfo:
        validate_search_params(
            q="q-b",
            types=("issue",),
            limit=20,
            cursor_raw=raw,
            workspace_id=ws,
            secret=SECRET,
        )
    assert excinfo.value.code == "validation_error"


def test_garbage_rejected() -> None:
    for garbage in ("", "!!!", "aGVsbG8=", base64.urlsafe_b64encode(b"{}").decode()):
        with pytest.raises(ValidationError):
            decode_cursor(SECRET, garbage)


@pytest.mark.parametrize(
    "envelope",
    [
        {"fp": 1, "t": [], "sig": "signature"},
        {"fp": "fp", "t": {}, "sig": "signature"},
        {"fp": "fp", "t": [], "sig": 1},
    ],
)
def test_structurally_invalid_envelope_rejected(envelope: dict) -> None:
    with pytest.raises(ValidationError):
        decode_cursor(SECRET, _pack(envelope))


def test_non_string_cursor_rejected() -> None:
    with pytest.raises(ValidationError):
        decode_cursor(SECRET, None)  # type: ignore[arg-type]


def test_fingerprint_caller_sorts_types_and_binds_query_and_workspace() -> None:
    ws_a, ws_b = uuid.uuid4(), uuid.uuid4()
    types_a = tuple(sorted({"issue", "member"}))
    types_b = tuple(sorted({"member", "issue"}))
    base = binding_fingerprint("q", types_a, ws_a)
    assert binding_fingerprint("q", types_b, ws_a) == base
    assert binding_fingerprint("q2", types_a, ws_a) != base
    assert binding_fingerprint("q", types_a, ws_b) != base


@pytest.mark.parametrize(
    "factors",
    [
        [80, 3, "abc", "issue"],
        [80, 3, "abc", "issue", "not-a-uuid"],
        [80, 3, "abc", "unknown_type", str(uuid.uuid4())],
        [True, 3, "abc", "issue", str(uuid.uuid4())],
        [80, -1, "abc", "issue", str(uuid.uuid4())],
        [80, 3, 123, "issue", str(uuid.uuid4())],
        [80, 3, "abc", "issue", 123],
        [80, 3, "abc", "issue", str(uuid.uuid4()).upper()],
    ],
)
def test_validly_signed_malformed_factors_fail_closed(factors: list) -> None:
    _, decoded = decode_cursor(SECRET, _encoded(factors=factors))
    with pytest.raises(ValidationError):
        factors_as_sort_key(decoded)
