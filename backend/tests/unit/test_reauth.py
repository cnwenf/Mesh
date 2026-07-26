"""Step-up re-authentication dependency tests (auth.md §5.5 — H3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from mesh.auth import jwt as jwt_mod
from mesh.auth.deps import require_recent_auth
from mesh.config import load_settings
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


def _request(settings, token: str):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_fresh_authentication_is_allowed(settings, session_factory):
    user = await _create_user(session_factory)
    token, _ = jwt_mod.encode_access_token(
        subject=user.id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl=timedelta(minutes=15),
        auth_time=datetime.now(UTC),  # just authenticated
    )
    async with session_factory() as session:
        resolved = await require_recent_auth(_request(settings, token), session)
    assert resolved.id == user.id


async def test_stale_authentication_requires_reauth(settings, session_factory):
    user = await _create_user(session_factory)
    # Authenticated 20 minutes ago — beyond the 15-minute reauth window.
    token, _ = jwt_mod.encode_access_token(
        subject=user.id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl=timedelta(minutes=15),
        auth_time=datetime.now(UTC) - timedelta(minutes=20),
    )
    async with session_factory() as session:
        with pytest.raises(ForbiddenError) as exc:
            await require_recent_auth(_request(settings, token), session)
    assert exc.value.code == "reauth_required"
