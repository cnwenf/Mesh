"""Step-up re-authentication dependency tests (auth.md §5.5 — H3).

Per the session-location invariant (auth.md §1.1, MES-80 A8), the freshness
verdict reads the authoritative ``sessions.authenticated_at`` by the access
JWT's ``sid`` — the claim alone never decides. Tests therefore mint a real
web session row and bind the access token to it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from mesh.auth import jwt as jwt_mod
from mesh.auth.deps import require_recent_auth
from mesh.auth.security import generate_refresh_token, hash_token
from mesh.config import load_settings
from mesh.db.models.user import Session
from mesh.errors import ForbiddenError

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(db_url, redis_url):
    return load_settings(
        database_url=db_url, redis_url=redis_url, auth_mode="dev", jwt_secret="reauth-secret"
    )


async def _create_user(session_factory) -> object:
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'U') RETURNING id"),
                {"e": f"{__import__('uuid').uuid4().hex[:12]}@corp.com"},
            )
        ).scalar_one()
    async with session_factory() as session:
        from mesh.db.models.user import User

        return await session.get(User, user_id)


async def _create_web_session(session_factory, user_id, *, authenticated_at) -> Session:
    now = datetime.now(UTC)
    row = Session(
        user_id=user_id,
        token_hash=hash_token(generate_refresh_token()),
        type="web",
        expires_at=now + timedelta(days=14),
        last_active_at=now,
        authenticated_at=authenticated_at,
    )
    async with session_factory() as session, session.begin():
        session.add(row)
    return row


def _request(settings, token: str):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_fresh_authentication_is_allowed(settings, session_factory):
    user = await _create_user(session_factory)
    row = await _create_web_session(
        session_factory, user.id, authenticated_at=datetime.now(UTC)  # just authenticated
    )
    token, _ = jwt_mod.encode_access_token(
        subject=user.id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl=timedelta(minutes=15),
        auth_time=datetime.now(UTC),
        session_id=row.id,
    )
    async with session_factory() as session:
        resolved = await require_recent_auth(_request(settings, token), session)
    assert resolved.id == user.id


async def test_stale_authentication_requires_reauth(settings, session_factory):
    user = await _create_user(session_factory)
    # Primary auth 20 minutes ago — beyond the 15-minute reauth window.
    row = await _create_web_session(
        session_factory, user.id,
        authenticated_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    token, _ = jwt_mod.encode_access_token(
        subject=user.id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl=timedelta(minutes=15),
        auth_time=datetime.now(UTC) - timedelta(minutes=20),
        session_id=row.id,
    )
    async with session_factory() as session:
        with pytest.raises(ForbiddenError) as exc:
            await require_recent_auth(_request(settings, token), session)
    assert exc.value.code == "reauth_required"
