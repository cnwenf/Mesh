"""DeviceCodeService — state machine, lock-order consumption, brute-force
protection (auth.md §2.4.2 / §3.1.1 / §5.5 ①–⑤, quantified).

Deterministic clock; real PostgreSQL. The real-parallel e2e (consume ↔ member
removal racing on the roster row lock) lives in tests/e2e/test_device_auth_e2e.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.auth import jwt as jwt_mod
from mesh.auth.device_codes import (
    FAILED_ATTEMPTS_INVALIDATE_THRESHOLD,
    AccessDeniedError,
    AuthorizationPendingError,
    ConsumedGrant,
    DeviceCodeService,
    ExpiredTokenError,
    InvalidGrantError,
)
from mesh.auth.service import AuthService
from mesh.config import load_settings
from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member
from mesh.db.models.user import DeviceAuthorization, Session
from mesh.db.models.workspace import Workspace
from mesh.errors import ForbiddenError, UnauthorizedError, ValidationError

pytestmark = pytest.mark.unit

EMAIL = "device@corp.com"
PASSWORD = "a-strong-passw0rd"
PEPPER = "unit-test-device-code-pepper-0123456789"


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
        jwt_secret="device-test-signing-secret",
        device_code_pepper=PEPPER,
    )


@pytest.fixture
def clock():
    return Clock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def auth_service(session_factory, settings, clock):
    return AuthService(session_factory, settings, clock=clock)


@pytest.fixture
def device_service(session_factory, settings, auth_service, clock):
    return DeviceCodeService(session_factory, settings, auth_service, clock=clock)


async def _approver(auth_service, session_factory):
    """Register + web-login the approver; return (user_id, sid, access)."""
    await auth_service.register(email=EMAIL, password=PASSWORD, display_name="Dev")
    tokens = await auth_service.login(email=EMAIL, password=PASSWORD)
    claims = jwt_mod.decode_access_token(
        tokens.access_token,
        secret=auth_service._settings.jwt_secret,
        algorithm=auth_service._settings.jwt_algorithm,
    )
    return claims.subject, claims.sid, tokens.access_token


async def _workspace_for(session_factory, user_id, slug=None):
    async with session_factory() as session, session.begin():
        ws = Workspace(name="Dev WS", slug=slug or f"dev-{uuid.uuid4().hex[:8]}")
        session.add(ws)
        await session.flush()
        session.add(
            Member(
                workspace_id=ws.id,
                user_id=user_id,
                member_type="human",
                role="member",
                status="active",
            )
        )
        await session.flush()
        return ws.id, ws.slug


async def _authz_rows(session_factory) -> list[DeviceAuthorization]:
    async with session_factory() as session:
        return list((await session.execute(select(DeviceAuthorization))).scalars().all())


async def _approve(device_service, user_code, workspace_id, user_id, sid):
    return await device_service.approve(
        user_code=user_code,
        workspace_id=workspace_id,
        approver_user_id=user_id,
        approver_sid=sid,
    )


class TestIssuance:
    async def test_create_code_shape_and_hash_only_storage(self, device_service, session_factory):
        issued = await device_service.create_code(
            client_id="mesh-cli", scopes=["issue:read", "issue:write"], ip_address="10.0.0.1"
        )
        assert issued["expires_in"] == 900
        assert issued["interval"] == 5
        assert issued["verification_uri"] == "/device"
        assert issued["user_code"] in issued["verification_uri_complete"]
        assert len(issued["device_code"]) >= 43  # ≥128bit base64url

        rows = await _authz_rows(session_factory)
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "pending"
        assert row.requested_scopes == ["issue:read", "issue:write"]
        # ONLY HMAC hashes at rest — plaintext codes nowhere in the row.
        assert issued["device_code"] not in (row.device_code_hash, row.user_code_hash)
        assert issued["user_code"] not in (row.device_code_hash, row.user_code_hash)

        audits = await _audits(session_factory, "auth.device_code_issued")
        assert len(audits) == 1
        assert audits[0].metadata_["client_id"] == "mesh-cli"

    async def test_missing_pepper_fails_closed(self, session_factory, auth_service):
        no_pepper = load_settings(
            database_url="postgresql+asyncpg://u:p@h/db",
            redis_url="redis://h/0",
            auth_mode="dev",
            device_code_pepper=None,
        )
        service = DeviceCodeService(session_factory, no_pepper, auth_service)
        with pytest.raises(ValidationError) as exc:
            await service.create_code(client_id="mesh-cli", scopes=[])
        assert exc.value.details["reason"] == "device_code_pepper_not_configured"


class TestPollBranches:
    async def test_pending_denied_expired_invalidated_unknown(self, device_service, session_factory, clock):
        issued = await device_service.create_code(client_id="mesh-cli", scopes=["issue:read"])

        # pending → authorization_pending
        with pytest.raises(AuthorizationPendingError):
            await device_service.exchange(device_code=issued["device_code"])

        # unknown → invalid_grant
        with pytest.raises(InvalidGrantError):
            await device_service.exchange(device_code="mesh-never-issued")

        # expired (past TTL) → expired_token
        clock.advance(minutes=16)
        with pytest.raises(ExpiredTokenError):
            await device_service.exchange(device_code=issued["device_code"])
        rows = await _authz_rows(session_factory)
        assert rows[0].status == "expired"  # terminal

    async def test_denied_maps_to_access_denied(self, device_service, session_factory, auth_service):
        user_id, sid, _ = await _approver(auth_service, session_factory)
        ws_id, _ = await _workspace_for(session_factory, user_id)
        issued = await device_service.create_code(client_id="mesh-cli", scopes=["issue:read"])
        await _approve(device_service, issued["user_code"], ws_id, user_id, sid)
        # consume so we can create a second grant to deny
        await device_service.exchange(device_code=issued["device_code"])

        issued2 = await device_service.create_code(client_id="mesh-cli", scopes=["issue:read"])
        await device_service.deny(
            user_code=issued2["user_code"], denier_user_id=user_id, denier_sid=sid
        )
        with pytest.raises(AccessDeniedError):
            await device_service.exchange(device_code=issued2["device_code"])


class TestApprove:
    async def test_approve_intersects_scopes_and_snapshots_auth_time(
        self, device_service, session_factory, auth_service, clock
    ):
        user_id, sid, _ = await _approver(auth_service, session_factory)
        ws_id, _ = await _workspace_for(session_factory, user_id)
        issued = await device_service.create_code(
            client_id="mesh-cli",
            scopes=["issue:read", "issue:write", "project:manage"],  # manage ∉ member role
        )
        result = await _approve(device_service, issued["user_code"], ws_id, user_id, sid)
        assert result["status"] == "approved"
        # Server-enforced intersection — project:manage stripped for a member.
        assert result["granted_scopes"] == ["issue:read", "issue:write"]

        rows = await _authz_rows(session_factory)
        row = rows[-1]
        assert row.granted_scopes == ["issue:read", "issue:write"]
        assert row.workspace_id == ws_id
        # authenticated_at snapshot == the approver session's value (R6-H3).
        async with session_factory() as session:
            web = await session.get(Session, sid)
            assert row.approved_authenticated_at == web.authenticated_at

    async def test_approve_non_member_workspace_forbidden(
        self, device_service, session_factory, auth_service
    ):
        user_id, sid, _ = await _approver(auth_service, session_factory)
        # A workspace the approver does NOT belong to.
        async with session_factory() as session, session.begin():
            foreign = Workspace(name="Foreign", slug=f"f-{uuid.uuid4().hex[:8]}")
            session.add(foreign)
            await session.flush()
            foreign_id = foreign.id
        issued = await device_service.create_code(client_id="mesh-cli", scopes=["issue:read"])
        with pytest.raises(ForbiddenError):
            await _approve(device_service, issued["user_code"], foreign_id, user_id, sid)

    async def test_approve_with_revoked_session_401_and_row_untouched(
        self, device_service, session_factory, auth_service
    ):
        user_id, sid, _ = await _approver(auth_service, session_factory)
        ws_id, _ = await _workspace_for(session_factory, user_id)
        issued = await device_service.create_code(client_id="mesh-cli", scopes=["issue:read"])
        # Revoke the approver's web session — even inside the access TTL window
        # it must not approve (R7-H1 invariant).
        async with session_factory() as session, session.begin():
            web = await session.get(Session, sid)
            web.revoked_at = datetime.now(UTC)
        with pytest.raises(UnauthorizedError):
            await _approve(device_service, issued["user_code"], ws_id, user_id, sid)
        rows = await _authz_rows(session_factory)
        assert rows[-1].status == "pending"  # untouched

    async def test_approve_deny_race_first_wins(self, device_service, session_factory, auth_service):
        user_id, sid, _ = await _approver(auth_service, session_factory)
        ws_id, _ = await _workspace_for(session_factory, user_id)
        issued = await device_service.create_code(client_id="mesh-cli", scopes=["issue:read"])
        first = await _approve(device_service, issued["user_code"], ws_id, user_id, sid)
        assert first["status"] == "approved"
        # The loser transition does NOT overwrite — it echoes current state.
        second = await device_service.deny(
            user_code=issued["user_code"], denier_user_id=user_id, denier_sid=sid
        )
        assert second["status"] == "approved"
        rows = await _authz_rows(session_factory)
        assert rows[-1].status == "approved"


class TestConsume:
    async def test_consume_mints_bound_cli_session_once(
        self, device_service, session_factory, auth_service, settings
    ):
        user_id, sid, _ = await _approver(auth_service, session_factory)
        ws_id, slug = await _workspace_for(session_factory, user_id)
        issued = await device_service.create_code(
            client_id="mesh-cli", scopes=["issue:read", "issue:write"]
        )
        await _approve(device_service, issued["user_code"], ws_id, user_id, sid)
        grant = await device_service.exchange(device_code=issued["device_code"])

        assert isinstance(grant, ConsumedGrant)
        assert grant.workspace_id == ws_id
        assert grant.workspace_slug == slug
        assert grant.scopes == ["issue:read", "issue:write"]
        assert grant.tokens.refresh_token.startswith("mesh_rft_")
        # Access JWT carries sid + workspace + scope claims.
        claims = jwt_mod.decode_access_token(
            grant.tokens.access_token,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        assert claims.workspace_id == ws_id
        assert claims.scopes == frozenset({"issue:read", "issue:write"})
        assert claims.sid is not None

        # cli session row is bound + inherits authenticated_at (R6-H3).
        async with session_factory() as session:
            cli = await session.scalar(select(Session).where(Session.type == "cli"))
            web = await session.get(Session, sid)
        assert cli.workspace_id == ws_id
        assert cli.granted_scopes == ["issue:read", "issue:write"]
        assert cli.device_authorization_id is not None
        assert cli.authenticated_at == web.authenticated_at  # inherited, NOT mint time

        # Second consumption → invalid_grant; still exactly ONE cli session.
        with pytest.raises(InvalidGrantError):
            await device_service.exchange(device_code=issued["device_code"])
        async with session_factory() as session:
            count = len(
                list(
                    (
                        await session.execute(select(Session).where(Session.type == "cli"))
                    ).scalars()
                )
            )
        assert count == 1
        rows = await _authz_rows(session_factory)
        assert rows[0].status == "consumed"

    async def test_consume_after_member_removal_invalidates(
        self, device_service, session_factory, auth_service
    ):
        user_id, sid, _ = await _approver(auth_service, session_factory)
        ws_id, _ = await _workspace_for(session_factory, user_id)
        issued = await device_service.create_code(client_id="mesh-cli", scopes=["issue:read"])
        await _approve(device_service, issued["user_code"], ws_id, user_id, sid)
        # Remove the member between approval and consumption.
        async with session_factory() as session, session.begin():
            member = await session.scalar(
                select(Member).where(Member.workspace_id == ws_id, Member.user_id == user_id)
            )
            member.status = "removed"
        with pytest.raises(AccessDeniedError):
            await device_service.exchange(device_code=issued["device_code"])
        rows = await _authz_rows(session_factory)
        assert rows[0].status == "invalidated"
        audits = await _audits(session_factory, "auth.device_invalidated")
        assert len(audits) == 1
        async with session_factory() as session:
            cli_count = len(
                list(
                    (
                        await session.execute(select(Session).where(Session.type == "cli"))
                    ).scalars()
                )
            )
        assert cli_count == 0  # no session minted


class TestBruteForceAndSweep:
    async def test_violations_over_threshold_invalidate(self, device_service, session_factory):
        issued = await device_service.create_code(client_id="mesh-cli", scopes=[])
        for _ in range(FAILED_ATTEMPTS_INVALIDATE_THRESHOLD + 1):
            await device_service.register_poll_violation(device_code=issued["device_code"])
        rows = await _authz_rows(session_factory)
        assert rows[0].status == "invalidated"
        assert rows[0].failed_attempts == FAILED_ATTEMPTS_INVALIDATE_THRESHOLD + 1
        audits = await _audits(session_factory, "auth.device_invalidated")
        assert audits[0].metadata_["reason"] == "poll_violations_exceeded"
        # The invalidated code is now a dead grant on poll.
        with pytest.raises(InvalidGrantError):
            await device_service.exchange(device_code=issued["device_code"])

    async def test_sweep_expires_pending_and_approved_only(
        self, device_service, session_factory, auth_service, clock
    ):
        user_id, sid, _ = await _approver(auth_service, session_factory)
        ws_id, _ = await _workspace_for(session_factory, user_id)
        await device_service.create_code(client_id="mesh-cli", scopes=[])
        b = await device_service.create_code(client_id="mesh-cli", scopes=[])
        await _approve(device_service, b["user_code"], ws_id, user_id, sid)
        clock.advance(minutes=16)
        swept = await device_service.sweep_expired()
        assert swept == 2
        rows = {r.user_code_hash: r.status for r in await _authz_rows(session_factory)}
        assert set(rows.values()) == {"expired"}


async def _audits(session_factory, action: str) -> list[AuditLog]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(select(AuditLog).where(AuditLog.action == action))
            ).scalars()
        )
