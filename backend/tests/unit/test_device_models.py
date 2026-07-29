"""Model-level DDL behaviour for the device-code increment (auth.md §2.4.2).

Asserts the database enforces the spec invariants the service layer relies on:
the device_authorizations state-machine CHECK, the PARTIAL unique index over
active user codes only (terminal rows may reuse a hash — the 20-bit code space
must not be exhausted by history), the sessions CHECK binding a cli session to
a workspace, and the single-consumption UNIQUE linking a device authorization
to at most one session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.user import DeviceAuthorization, Session, User
from mesh.db.models.workspace import Workspace

pytestmark = pytest.mark.unit


async def _user(session_factory, *, name: str = "u") -> uuid.UUID:
    async with session_factory() as session, session.begin():
        user = User(email=f"{name}-{uuid.uuid4().hex[:8]}@corp.dev", display_name=name)
        session.add(user)
        await session.flush()
        return user.id


async def _workspace(session_factory) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}", slug=f"ws-{uuid.uuid4().hex[:8]}")
        session.add(ws)
        await session.flush()
        return ws.id


def _authz(**overrides) -> dict:
    now = datetime.now(UTC)
    values = {
        "device_code_hash": uuid.uuid4().hex,
        "user_code_hash": uuid.uuid4().hex,
        "expires_at": now + timedelta(minutes=15),
    }
    values.update(overrides)
    return values


class TestDeviceAuthorizationConstraints:
    async def test_state_check_rejects_unknown_status(self, session_factory):
        async with session_factory() as session, session.begin():
            session.add(DeviceAuthorization(**_authz(status="bogus")))
            with pytest.raises(IntegrityError):
                await session.flush()

    async def test_all_terminal_statuses_accepted(self, session_factory):
        for status in ("pending", "approved", "denied", "consumed", "expired", "invalidated"):
            async with session_factory() as session, session.begin():
                session.add(DeviceAuthorization(**_authz(status=status)))
                await session.flush()

    @pytest.mark.parametrize("terminal", ["consumed", "expired", "invalidated"])
    async def test_terminal_state_cannot_transition(self, session_factory, terminal):
        """B2: the BEFORE UPDATE trigger rejects ANY status change out of a
        terminal state — resurrection (consumed → approved ⇒ double redemption)
        is impossible even for paths that omit the from-state predicate or for
        direct operational writes."""
        async with session_factory() as session, session.begin():
            grant = DeviceAuthorization(**_authz(status=terminal))
            session.add(grant)
            await session.flush()
            grant_id = grant.id
        with pytest.raises(IntegrityError, match="terminal"):
            async with session_factory() as session, session.begin():
                row = await session.get(DeviceAuthorization, grant_id)
                row.status = "approved"
        # The row is untouched.
        async with session_factory() as session:
            assert (await session.get(DeviceAuthorization, grant_id)).status == terminal

    async def test_non_terminal_transition_still_allowed(self, session_factory):
        """The trigger only guards terminal states: pending → approved is the
        happy-path transition the service layer performs conditionally."""
        async with session_factory() as session, session.begin():
            grant = DeviceAuthorization(**_authz(status="pending"))
            session.add(grant)
            await session.flush()
            grant_id = grant.id
        async with session_factory() as session, session.begin():
            row = await session.get(DeviceAuthorization, grant_id)
            row.status = "approved"
        async with session_factory() as session:
            assert (await session.get(DeviceAuthorization, grant_id)).status == "approved"

    async def test_terminal_row_non_status_update_allowed(self, session_factory):
        """A terminal row may still receive writes that keep the status (the
        trigger guards the state machine, not the whole row)."""
        async with session_factory() as session, session.begin():
            grant = DeviceAuthorization(**_authz(status="consumed"))
            session.add(grant)
            await session.flush()
            grant_id = grant.id
        async with session_factory() as session, session.begin():
            result = await session.execute(
                text(
                    "UPDATE device_authorizations SET user_code_hash = :h WHERE id = :id"
                ),
                {"h": uuid.uuid4().hex, "id": grant_id},
            )
            assert result.rowcount == 1

    async def test_partial_unique_blocks_two_active_same_user_code(self, session_factory):
        shared = uuid.uuid4().hex
        async with session_factory() as session, session.begin():
            session.add(DeviceAuthorization(**_authz(user_code_hash=shared, status="pending")))
            await session.flush()
        async with session_factory() as session, session.begin():
            session.add(DeviceAuthorization(**_authz(user_code_hash=shared, status="approved")))
            with pytest.raises(IntegrityError):
                await session.flush()

    async def test_partial_unique_allows_terminal_hash_reuse(self, session_factory):
        # auth.md §2.4.2 (R2-M3): the 20-bit code space is bounded — terminal
        # rows MUST release their hash or issuance eventually exhausts.
        shared = uuid.uuid4().hex
        async with session_factory() as session, session.begin():
            session.add(DeviceAuthorization(**_authz(user_code_hash=shared, status="consumed")))
            await session.flush()
        async with session_factory() as session, session.begin():
            session.add(DeviceAuthorization(**_authz(user_code_hash=shared, status="pending")))
            await session.flush()  # must not raise

    async def test_device_code_hash_unique_across_all_history(self, session_factory):
        shared = uuid.uuid4().hex
        async with session_factory() as session, session.begin():
            session.add(DeviceAuthorization(**_authz(device_code_hash=shared, status="consumed")))
            await session.flush()
        async with session_factory() as session, session.begin():
            session.add(DeviceAuthorization(**_authz(device_code_hash=shared, status="pending")))
            with pytest.raises(IntegrityError):
                await session.flush()


class TestSessionDeviceColumns:
    async def test_cli_session_requires_workspace(self, session_factory):
        user_id = await _user(session_factory)
        async with session_factory() as session, session.begin():
            session.add(
                Session(
                    user_id=user_id,
                    token_hash=uuid.uuid4().hex,
                    type="cli",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()

    async def test_web_session_workspace_may_be_null(self, session_factory):
        user_id = await _user(session_factory)
        async with session_factory() as session, session.begin():
            session.add(
                Session(
                    user_id=user_id,
                    token_hash=uuid.uuid4().hex,
                    type="web",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            await session.flush()

    async def test_cli_session_with_workspace_and_granted_scopes(self, session_factory):
        user_id = await _user(session_factory)
        workspace_id = await _workspace(session_factory)
        async with session_factory() as session, session.begin():
            session.add(
                Session(
                    user_id=user_id,
                    token_hash=uuid.uuid4().hex,
                    type="cli",
                    workspace_id=workspace_id,
                    granted_scopes=["issue:read", "issue:write"],
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            await session.flush()
        async with session_factory() as session:
            row = await session.scalar(select(Session).where(Session.type == "cli"))
            assert row.workspace_id == workspace_id
            assert row.granted_scopes == ["issue:read", "issue:write"]
            assert row.previous_token_hash is None
            assert row.rotated_at is None
            assert row.device_authorization_id is None

    async def test_device_authorization_id_unique(self, session_factory):
        # auth.md §2.4: single consumption → at most one session per grant.
        user_id = await _user(session_factory)
        workspace_id = await _workspace(session_factory)
        async with session_factory() as session, session.begin():
            authz = DeviceAuthorization(**_authz(status="consumed"))
            session.add(authz)
            await session.flush()
            authz_id = authz.id
        expires = datetime.now(UTC) + timedelta(days=1)
        async with session_factory() as session, session.begin():
            session.add(
                Session(
                    user_id=user_id,
                    token_hash=uuid.uuid4().hex,
                    type="cli",
                    workspace_id=workspace_id,
                    device_authorization_id=authz_id,
                    expires_at=expires,
                )
            )
            await session.flush()
        async with session_factory() as session, session.begin():
            session.add(
                Session(
                    user_id=user_id,
                    token_hash=uuid.uuid4().hex,
                    type="cli",
                    workspace_id=workspace_id,
                    device_authorization_id=authz_id,
                    expires_at=expires,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()

    async def test_device_auth_delete_sets_session_fk_null(self, session_factory):
        user_id = await _user(session_factory)
        workspace_id = await _workspace(session_factory)
        async with session_factory() as session, session.begin():
            authz = DeviceAuthorization(**_authz(status="consumed"))
            session.add(authz)
            await session.flush()
            authz_id = authz.id
            session.add(
                Session(
                    user_id=user_id,
                    token_hash=uuid.uuid4().hex,
                    type="cli",
                    workspace_id=workspace_id,
                    device_authorization_id=authz_id,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            await session.flush()
        async with session_factory() as session, session.begin():
            await session.delete(await session.get(DeviceAuthorization, authz_id))
        async with session_factory() as session:
            row = await session.scalar(select(Session).where(Session.type == "cli"))
            assert row.device_authorization_id is None
