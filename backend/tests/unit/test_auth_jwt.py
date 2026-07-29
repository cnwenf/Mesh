"""Unit tests for access-token JWT issuing/verification (auth.md §5.5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from mesh.auth import jwt as jwt_mod
from mesh.errors import UnauthorizedError

SECRET = "unit-test-signing-secret"
ALG = "HS256"


def test_encode_decode_roundtrip():
    user_id = uuid.uuid4()
    token, jti = jwt_mod.encode_access_token(
        subject=user_id, secret=SECRET, algorithm=ALG, ttl=timedelta(minutes=15)
    )
    decoded = jwt_mod.decode_access_token(token, secret=SECRET, algorithm=ALG)
    assert decoded.subject == user_id
    assert decoded.jti == jti
    assert decoded.expires_at > datetime.now(UTC)


def test_decode_rejects_expired_token():
    token, _ = jwt_mod.encode_access_token(
        subject=uuid.uuid4(),
        secret=SECRET,
        algorithm=ALG,
        ttl=timedelta(minutes=15),
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    with pytest.raises(UnauthorizedError):
        jwt_mod.decode_access_token(token, secret=SECRET, algorithm=ALG)


def test_decode_rejects_alg_none():
    """alg=none tokens must never verify (auth.md §5.5)."""
    user_id = uuid.uuid4()
    claims = {
        "sub": str(user_id),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "typ": "access",
    }
    unsigned = pyjwt.encode(claims, key=None, algorithm="none")
    with pytest.raises(UnauthorizedError):
        jwt_mod.decode_access_token(unsigned, secret=SECRET, algorithm=ALG)


def test_decode_rejects_wrong_algorithm_confusion():
    """A token signed with a different algorithm must not verify (HS/RS confusion)."""
    claims = {
        "sub": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "typ": "access",
    }
    hs512_token = pyjwt.encode(claims, SECRET, algorithm="HS512")
    with pytest.raises(UnauthorizedError):
        jwt_mod.decode_access_token(hs512_token, secret=SECRET, algorithm="HS256")


def test_decode_rejects_wrong_secret():
    token, _ = jwt_mod.encode_access_token(
        subject=uuid.uuid4(), secret=SECRET, algorithm=ALG, ttl=timedelta(minutes=5)
    )
    with pytest.raises(UnauthorizedError):
        jwt_mod.decode_access_token(token, secret="other-secret", algorithm=ALG)


def test_decode_rejects_non_access_typ():
    """A refresh/mfa-typed JWT must not be accepted as an access token."""
    claims = {
        "sub": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "typ": "mfa",
    }
    token = pyjwt.encode(claims, SECRET, algorithm=ALG)
    with pytest.raises(UnauthorizedError) as exc:
        jwt_mod.decode_access_token(token, secret=SECRET, algorithm=ALG)
    assert exc.value.details == {"reason": "wrong_token_type"}


def test_decode_rejects_garbage():
    with pytest.raises(UnauthorizedError):
        jwt_mod.decode_access_token("not.a.jwt", secret=SECRET, algorithm=ALG)


def test_decode_rejects_missing_exp():
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "iat": int(datetime.now(UTC).timestamp()), "typ": "access"},
        SECRET,
        algorithm=ALG,
    )
    with pytest.raises(UnauthorizedError):
        jwt_mod.decode_access_token(token, secret=SECRET, algorithm=ALG)


# --- MES-80 A4: session-bound claims (auth.md §2.4 access JWT 声明写死) -------


def test_sid_claim_roundtrip():
    """The access JWT carries sid = sessions.id (session-location invariant)."""
    session_id = uuid.uuid4()
    token, _ = jwt_mod.encode_access_token(
        subject=uuid.uuid4(),
        secret=SECRET,
        algorithm=ALG,
        ttl=timedelta(minutes=15),
        session_id=session_id,
    )
    decoded = jwt_mod.decode_access_token(token, secret=SECRET, algorithm=ALG)
    assert decoded.sid == session_id


def test_device_session_claims_roundtrip():
    """Device sessions bind workspace_id + fixed granted scopes into the JWT."""
    workspace_id = uuid.uuid4()
    token, _ = jwt_mod.encode_access_token(
        subject=uuid.uuid4(),
        secret=SECRET,
        algorithm=ALG,
        ttl=timedelta(minutes=15),
        session_id=uuid.uuid4(),
        workspace_id=workspace_id,
        scopes=["issue:read", "comment:write"],
    )
    decoded = jwt_mod.decode_access_token(token, secret=SECRET, algorithm=ALG)
    assert decoded.workspace_id == workspace_id
    assert decoded.scopes == frozenset({"issue:read", "comment:write"})


def test_legacy_token_without_sid_decodes():
    """Tokens issued before the increment decode with sid=None (forward compat)."""
    claims = {
        "sub": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
        "auth_time": int(datetime.now(UTC).timestamp()),
    }
    token = pyjwt.encode(claims, SECRET, algorithm=ALG)
    decoded = jwt_mod.decode_access_token(token, secret=SECRET, algorithm=ALG)
    assert decoded.sid is None
    assert decoded.workspace_id is None
    assert decoded.scopes == frozenset()
