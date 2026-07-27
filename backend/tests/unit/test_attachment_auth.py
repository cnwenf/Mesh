"""Attachment principal resolution / workspace gating tests (§5.3 / README §6.1)."""

from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request

from mesh.attachment.auth import Caller, authenticate, gate_workspace
from mesh.auth.tokens import ResolvedToken
from mesh.errors import ForbiddenError, NotFoundError, UnauthorizedError

pytestmark = pytest.mark.unit


def _request_with_bearer(app, token: str) -> Request:
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "app": app,
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


@pytest.fixture
def app(attachment_settings_kwargs):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    return create_app(load_settings(**attachment_settings_kwargs))


async def test_authenticate_rejects_garbage_token(app):
    request = _request_with_bearer(app, "not-a-real-credential")
    with pytest.raises(UnauthorizedError):
        await authenticate(request, app.state.session_factory)


async def test_authenticate_accepts_session_jwt(app):
    from mesh.auth import jwt as jwt_mod

    # Seed a user row the JWT can resolve to.
    from mesh.db.models.user import User

    async with app.state.session_factory() as session, session.begin():
        user = User(email="jwt-auth@mesh.test", display_name="JWT", password_hash="x")
        session.add(user)
    token, _jti = jwt_mod.encode_access_token(
        subject=user.id,
        secret=app.state.settings.jwt_secret,
        algorithm=app.state.settings.jwt_algorithm,
        ttl=app.state.settings.access_token_ttl,
    )
    caller = await authenticate(_request_with_bearer(app, token), app.state.session_factory)
    assert caller.user is not None and caller.token is None
    assert caller.is_token is False


async def test_authenticate_falls_back_to_pat(app):
    from mesh.db.models.member import Member
    from mesh.db.models.user import User
    from mesh.db.models.workspace import Workspace

    async with app.state.session_factory() as session, session.begin():
        workspace = Workspace(name="PAT WS", slug=f"pat-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    async with app.state.session_factory() as session, session.begin():
        user = User(email="pat-auth@mesh.test", display_name="PAT", password_hash="x")
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, user_id=user.id,
            member_type="human", role="member", status="active",
        )
        session.add(member)

    from mesh.auth.tokens import TokenService

    service = TokenService(app.state.session_factory)
    created = await service.create_token(
        actor=member, workspace_id=workspace.id, name="test-pat", scopes=["issue:read"]
    )
    raw_token = created["token"]

    caller = await authenticate(_request_with_bearer(app, raw_token), app.state.session_factory)
    assert caller.token is not None and caller.user is None
    assert caller.is_token is True

    # Gating: the PAT pins the workspace; membership resolves to the owner row.
    async with app.state.session_factory() as session:
        resolved_member = await gate_workspace(session, caller, workspace.id)
        assert resolved_member.id == member.id

    # A foreign workspace is a uniform 404.
    async with app.state.session_factory() as session:
        with pytest.raises(NotFoundError):
            await gate_workspace(session, caller, uuid.uuid4())

    # Permission enforcement against the token's role.
    async with app.state.session_factory() as session:
        with pytest.raises(ForbiddenError):
            await gate_workspace(
                session, caller, workspace.id, permission="workspace:settings"
            )
    _ = ResolvedToken  # imported for clarity; the real one comes from resolve_pat


async def test_gate_workspace_jwt_path(app):
    from mesh.db.models.member import Member
    from mesh.db.models.user import User
    from mesh.db.models.workspace import Workspace

    async with app.state.session_factory() as session, session.begin():
        workspace = Workspace(name="JWT WS", slug=f"jwt-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    async with app.state.session_factory() as session, session.begin():
        user = User(email="gate-jwt@mesh.test", display_name="G", password_hash="x")
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, user_id=user.id,
            member_type="human", role="guest", status="active",
        )
        session.add(member)

    caller = Caller(user=user, token=None)
    async with app.state.session_factory() as session:
        resolved = await gate_workspace(session, caller, workspace.id)
        assert resolved.id == member.id
    # Guest fails the issue:write gate.
    async with app.state.session_factory() as session:
        with pytest.raises(ForbiddenError):
            await gate_workspace(session, caller, workspace.id, permission="issue:write")
    # Unknown workspace → 404.
    async with app.state.session_factory() as session:
        with pytest.raises(NotFoundError):
            await gate_workspace(session, caller, uuid.uuid4())
