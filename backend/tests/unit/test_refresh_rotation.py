"""Refresh rotation race contract (auth.md §3.8, serial-equivalence protocol).

The REAL-PARALLEL e2e (concurrent HTTP refreshes, assertion list §3.8 ①–⑦)
lives in tests/e2e/test_device_auth_e2e.py; this module verifies the protocol
JUDGEMENT LOGIC deterministically with an injected clock: rowcount
arbitration, the grace window's access-only path, no second rotation, and the
hard predicates (revoked / expired sessions never mint new credentials
through either path — an expired session is never resurrected).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.auth import jwt as jwt_mod
from mesh.auth.service import AuthService, RefreshGrace, RefreshWinner
from mesh.config import load_settings
from mesh.db.models.member import Member
from mesh.db.models.user import Session, User
from mesh.db.models.workspace import Workspace
from mesh.errors import UnauthorizedError

pytestmark = pytest.mark.unit

PASSWORD = "a-strong-passw0rd"


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def settings(db_url, redis_url):
    return load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="rotation-test-signing-secret",
        refresh_rotation_grace_seconds=30,
    )


@pytest.fixture
def clock():
    return Clock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def service(session_factory, settings, clock):
    return AuthService(session_factory, settings, clock=clock)


async def _login(service) -> tuple[str, str]:
    """Register + login; returns (access, refresh)."""
    email = f"{uuid.uuid4().hex[:10]}@corp.dev"
    await service.register(email=email, password=PASSWORD, display_name="rot")
    tokens = await service.login(email=email, password=PASSWORD)
    return tokens.access_token, tokens.refresh_token


def _decode(settings, token):
    return jwt_mod.decode_access_token(
        token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
    )


async def _session_rows(session_factory) -> list[Session]:
    async with session_factory() as session:
        return list((await session.execute(select(Session))).scalars().all())


class TestWinnerArbitration:
    async def test_rotation_is_in_place_with_previous_hash(self, service, session_factory):
        _access, refresh = await _login(service)
        old_hash_rows = await _session_rows(session_factory)
        assert len(old_hash_rows) == 1
        original = old_hash_rows[0]
        original_id = original.id
        original_hash = original.token_hash

        outcome = await service.refresh(presented_token=refresh)

        assert isinstance(outcome, RefreshWinner)
        assert outcome.refresh_token != refresh
        rows = await _session_rows(session_factory)
        # In-place rotation: SAME session row — not revoke + new row.
        assert len(rows) == 1
        row = rows[0]
        assert row.id == original_id
        assert row.token_hash != original_hash
        assert row.previous_token_hash == original_hash
        assert row.rotated_at is not None
        assert row.revoked_at is None  # rotation is NOT revocation

    async def test_winner_refresh_is_valid_next_credential(self, service):
        _access, refresh = await _login(service)
        winner1 = await service.refresh(presented_token=refresh)
        assert isinstance(winner1, RefreshWinner)
        # The winner's new refresh is the live credential now.
        winner2 = await service.refresh(presented_token=winner1.refresh_token)
        assert isinstance(winner2, RefreshWinner)

    async def test_winner_access_carries_sid_and_auth_time(self, service, settings, clock):
        access, refresh = await _login(service)
        login_claims = _decode(settings, access)
        outcome = await service.refresh(presented_token=refresh)
        assert isinstance(outcome, RefreshWinner)
        renewed = _decode(settings, outcome.access_token)
        assert renewed.sid == login_claims.sid  # inherits the session
        assert renewed.jti != login_claims.jti  # per-token unique jti
        # authenticated_at forwarded — the refresh is not an authentication.
        assert renewed.authenticated_at == login_claims.authenticated_at


class TestGracePath:
    async def test_loser_within_window_gets_access_only(self, service, session_factory):
        _access, refresh = await _login(service)
        winner = await service.refresh(presented_token=refresh)
        assert isinstance(winner, RefreshWinner)
        snapshot = await _session_rows(session_factory)
        snapshot_hash = snapshot[0].token_hash
        snapshot_prev = snapshot[0].previous_token_hash
        snapshot_rotated = snapshot[0].rotated_at

        # The SAME old token again, inside the grace window → loser.
        loser = await service.refresh(presented_token=refresh)

        assert isinstance(loser, RefreshGrace)
        rows = await _session_rows(session_factory)
        # Grace path: NO write — token_hash/previous/rotated untouched.
        assert rows[0].token_hash == snapshot_hash
        assert rows[0].previous_token_hash == snapshot_prev
        assert rows[0].rotated_at == snapshot_rotated
        # And the loser's access still works (same session).
        assert _decode(service._settings, loser.access_token).sid is not None

    async def test_grace_outside_window_is_401(self, service, clock):
        _access, refresh = await _login(service)
        await service.refresh(presented_token=refresh)
        clock.advance(seconds=31)  # beyond MESH_REFRESH_ROTATION_GRACE_SECONDS
        with pytest.raises(UnauthorizedError):
            await service.refresh(presented_token=refresh)

    async def test_grace_never_returns_refresh_plaintext(self, service):
        _access, refresh = await _login(service)
        await service.refresh(presented_token=refresh)
        loser = await service.refresh(presented_token=refresh)
        assert isinstance(loser, RefreshGrace)
        assert not hasattr(loser, "refresh_token")

    async def test_unknown_token_is_401(self, service):
        with pytest.raises(UnauthorizedError):
            await service.refresh(presented_token="mesh_rft_never-issued")


class TestHardPredicates:
    async def test_revoked_session_rejected_on_both_paths(self, service, session_factory):
        _access, refresh = await _login(service)
        winner = await service.refresh(presented_token=refresh)
        assert isinstance(winner, RefreshWinner)
        # Revoke the live session.
        await service.logout(refresh_token=winner.refresh_token)
        # Winner path (new credential) — rejected.
        with pytest.raises(UnauthorizedError):
            await service.refresh(presented_token=winner.refresh_token)
        # Grace path (old credential, still within the window) — rejected too.
        with pytest.raises(UnauthorizedError):
            await service.refresh(presented_token=refresh)

    async def test_expired_session_not_resurrected(self, service, settings, clock, session_factory):
        _access, refresh = await _login(service)
        # Past the refresh lifetime: neither path may mint ANYTHING.
        clock.advance(days=15)
        rows_before = await _session_rows(session_factory)
        hashes_before = [r.token_hash for r in rows_before]
        prev_before = [r.previous_token_hash for r in rows_before]

        with pytest.raises(UnauthorizedError):
            await service.refresh(presented_token=refresh)

        rows_after = await _session_rows(session_factory)
        # No new token_hash written, no new row — the session stays dead.
        assert [r.token_hash for r in rows_after] == hashes_before
        assert [r.previous_token_hash for r in rows_after] == prev_before
        assert len(rows_after) == len(rows_before)

    async def test_expired_session_grace_path_rejected(self, service, clock, session_factory):
        _access, refresh = await _login(service)
        winner = await service.refresh(presented_token=refresh)
        assert isinstance(winner, RefreshWinner)
        # Advance past session expiry but present the OLD (previous) hash.
        clock.advance(days=15)
        with pytest.raises(UnauthorizedError):
            await service.refresh(presented_token=refresh)
        rows = await _session_rows(session_factory)
        assert len(rows) == 1  # no resurrection row


class TestRenewalScope:
    async def _cli_session(self, session_factory, *, granted_scopes, role):
        """Seed a user + workspace + member + live cli session row."""
        from mesh.auth.security import generate_refresh_token, hash_token

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        refresh = generate_refresh_token()
        async with session_factory() as session, session.begin():
            user = User(email=f"{uuid.uuid4().hex[:10]}@corp.dev", display_name="cli")
            ws = Workspace(name="cli-ws", slug=f"cli-ws-{uuid.uuid4().hex[:6]}")
            session.add_all([user, ws])
            await session.flush()
            member = Member(
                workspace_id=ws.id,
                user_id=user.id,
                member_type="human",
                role=role,
                status="active",
            )
            session.add(member)
            row = Session(
                user_id=user.id,
                token_hash=hash_token(refresh),
                type="cli",
                workspace_id=ws.id,
                granted_scopes=granted_scopes,
                authenticated_at=now,
                expires_at=now + timedelta(days=14),
                last_active_at=now,
            )
            session.add(row)
            await session.flush()
            sid = row.id
        return refresh, sid

    async def test_cli_renewal_intersects_current_role(self, service, session_factory, settings):
        # granted at approval included project:manage; the holder is now a plain
        # member — renewal must NARROW to the role intersection (only ever ∩).
        refresh, sid = await self._cli_session(
            session_factory,
            granted_scopes=["issue:read", "issue:write", "project:manage"],
            role="member",
        )
        outcome = await service.refresh(presented_token=refresh)
        assert isinstance(outcome, RefreshWinner)
        renewed = _decode(settings, outcome.access_token)
        assert renewed.sid == sid
        assert renewed.workspace_id is not None
        assert renewed.scopes == frozenset({"issue:read", "issue:write"})
        assert "project:manage" not in renewed.scopes

    async def test_web_renewal_stays_role_based(self, service, session_factory, settings):
        access, refresh = await _login(service)
        outcome = await service.refresh(presented_token=refresh)
        assert isinstance(outcome, RefreshWinner)
        renewed = _decode(settings, outcome.access_token)
        assert renewed.scopes == frozenset()  # empty claim ⇒ role-based
        assert renewed.workspace_id is None

    async def test_cli_renewal_after_membership_loss_is_401(self, service, session_factory):
        refresh, _sid = await self._cli_session(
            session_factory, granted_scopes=["issue:read"], role="member"
        )
        # Remove the membership the session was anchored to.
        async with session_factory() as session, session.begin():
            member = await session.scalar(select(Member).where(Member.member_type == "human"))
            member.status = "removed"
        with pytest.raises(UnauthorizedError):
            await service.refresh(presented_token=refresh)
