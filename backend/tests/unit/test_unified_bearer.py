"""Unified Bearer dependency — representative endpoint integration (auth.md
§5.2 review H7): every regular ``/api/v1`` route accepts session JWT /
``mesh_pat_`` / ``mesh_agt_`` uniformly (effective permissions = scopes ∩
role), while ``mesh_rt_`` / ``mesh_rft_`` are rejected on regular routes.

Representative set per the spec: issue read, issue write, comment write,
identity introspection (/me) — each exercised with a human PAT AND an agent
credential, plus scope-narrowing and misrouted-prefix negatives.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import redis.asyncio as aioredis

from mesh.api.app import create_app
from mesh.auth.tokens import TokenService
from mesh.config import load_settings
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.workspace.service import WorkspaceService

pytestmark = pytest.mark.unit

PASSWORD = "a-strong-passw0rd"


@pytest.fixture
def app(db_url, redis_url):
    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="unified-bearer-test-signing-secret",
        session_cookie_secure=False,
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await app.state.redis.aclose()
    await app.state.engine.dispose()


@pytest.fixture(autouse=True)
async def _flush_redis(redis_url):
    c = aioredis.from_url(redis_url, decode_responses=True)
    await c.flushdb()
    yield
    await c.flushdb()
    await c.aclose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


FULL_SCOPES = ["issue:read", "issue:write", "comment:write"]


async def _seed(session_factory, *, scopes=None):
    """Owner user + workspace + agent member; returns (user, ws_id, agent)."""
    async with session_factory() as session, session.begin():
        user = User(email=f"ub-{uuid.uuid4().hex[:8]}@corp.dev", display_name="UB")
        session.add(user)
        await session.flush()
        user_id = user.id
    ws = await WorkspaceService(session_factory).create_workspace(
        user=User(id=user_id, email="x@x.io", display_name="UB"),
        name="UB WS",
        slug=f"ub-{uuid.uuid4().hex[:10]}",
    )
    workspace_id = ws["id"]
    from sqlalchemy import select

    from mesh.db.models.agent import Agent

    async with session_factory() as session, session.begin():
        agent_row = Agent(workspace_id=workspace_id, name="ub-agent", owner_user_id=user_id)
        session.add(agent_row)
        await session.flush()
        agent_member = Member(
            workspace_id=workspace_id,
            member_type="agent",
            agent_id=agent_row.id,
            role="member",
            status="active",
            display_override="ub-agent",
        )
        session.add(agent_member)
        await session.flush()
        agent_id = agent_member.id
        owner_member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.user_id == user_id,
                Member.status == "active",
            )
        )
        owner_member_id = owner_member.id
    token_service = TokenService(session_factory)
    return user_id, workspace_id, owner_member_id, agent_id, token_service


async def _tokens(token_service, session_factory, *, owner_member_id, agent_id, workspace_id, scopes):
    from sqlalchemy import select

    from mesh.db.models.member import Member

    async with session_factory() as session:
        owner = await session.scalar(select(Member).where(Member.id == owner_member_id))
        agent = await session.scalar(select(Member).where(Member.id == agent_id))
    pat = await token_service.create_token(
        actor=owner, workspace_id=workspace_id, name="ub-pat", scopes=list(scopes)
    )
    agt = await token_service.create_token(
        actor=owner,
        workspace_id=workspace_id,
        name="ub-agent-token",
        scopes=list(scopes),
        owner_member_id=agent.id,
    )
    return pat["token"], agt["token"]


class TestRepresentativeEndpoints:
    @pytest.mark.parametrize("credential", ["pat", "agent"])
    async def test_issue_read_write_comment_me(
        self, client, session_factory, credential
    ):
        user_id, ws_id, owner_id, agent_id, ts = await _seed(session_factory)
        pat_token, agent_token = await _tokens(
            ts,
            session_factory,
            owner_member_id=owner_id,
            agent_id=agent_id,
            workspace_id=ws_id,
            scopes=FULL_SCOPES,
        )
        bearer = _auth(pat_token if credential == "pat" else agent_token)

        # READ — issue list.
        listed = await client.get(f"/api/v1/workspaces/{ws_id}/issues", headers=bearer)
        assert listed.status_code == 200, listed.text

        # WRITE — issue create.
        created = await client.post(
            f"/api/v1/workspaces/{ws_id}/issues",
            json={"title": f"via {credential} token"},
            headers=bearer,
        )
        assert created.status_code == 201, created.text
        issue_id = created.json()["data"]["id"]

        # COMMENT — write on the created issue.
        comment = await client.post(
            f"/api/v1/issues/{issue_id}/comments",
            json={"body_markdown": f"comment via {credential}"},
            headers=bearer,
        )
        assert comment.status_code == 201, comment.text

        # INTROSPECTION — /me resolves for both credential kinds.
        me = await client.get("/api/v1/me", headers=bearer)
        assert me.status_code == 200, me.text
        if credential == "agent":
            assert me.json()["data"]["member_type"] == "agent"
        else:
            assert me.json()["data"]["email"].endswith("@corp.dev")

    async def test_scope_narrowing_denies_uncovered_permission(
        self, client, session_factory
    ):
        user_id, ws_id, owner_id, agent_id, ts = await _seed(session_factory)
        pat_token, _agt = await _tokens(
            ts,
            session_factory,
            owner_member_id=owner_id,
            agent_id=agent_id,
            workspace_id=ws_id,
            scopes=["issue:read"],  # read only — no issue:write
        )
        bearer = _auth(pat_token)
        ok = await client.get(f"/api/v1/workspaces/{ws_id}/issues", headers=bearer)
        assert ok.status_code == 200
        denied = await client.post(
            f"/api/v1/workspaces/{ws_id}/issues",
            json={"title": "nope"},
            headers=bearer,
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["details"]["required_scope"] == "issue:write"

    async def test_pat_cannot_reach_foreign_workspace(self, client, session_factory):
        user_id, ws_id, owner_id, agent_id, ts = await _seed(session_factory)
        _u2, ws2, _o2, _a2, _ts2 = await _seed(session_factory)
        pat_token, _agt = await _tokens(
            ts,
            session_factory,
            owner_member_id=owner_id,
            agent_id=agent_id,
            workspace_id=ws_id,
            scopes=FULL_SCOPES,
        )
        resp = await client.get(f"/api/v1/workspaces/{ws2}/issues", headers=_auth(pat_token))
        assert resp.status_code == 404  # uniform invisible-workspace 404

    async def test_refresh_and_runtime_prefixes_rejected_on_regular_routes(
        self, client, session_factory
    ):
        user_id, ws_id, owner_id, agent_id, ts = await _seed(session_factory)
        # mesh_rft_ belongs ONLY to /auth/refresh; mesh_rt_ ONLY to the daemon
        # namespace — both must be 401 on regular routes.
        rft = await client.get(f"/api/v1/workspaces/{ws_id}/issues", headers=_auth("mesh_rft_x"))
        assert rft.status_code == 401
        rt = await client.get("/api/v1/me", headers=_auth("mesh_rt_x"))
        assert rt.status_code == 401

    async def test_type_semantics_prefix_holder_mismatch_rejected(
        self, client, session_factory
    ):
        # §2.5.1 R2-H2: an agent-issued credential forged under the mesh_pat_
        # prefix (or vice versa) must not authenticate. TokenService issues
        # prefixes by holder type, so simulate the mismatch at the dependency
        # level: a mesh_pat_-prefixed plaintext whose hash maps to an AGENT
        # holder row is rejected.
        from mesh.auth import security
        from mesh.db.models.api_token import ApiToken

        user_id, ws_id, owner_id, agent_id, ts = await _seed(session_factory)
        forged = "mesh_pat_" + security.generate_token()
        async with session_factory() as session, session.begin():
            session.add(
                ApiToken(
                    workspace_id=ws_id,
                    owner_member_id=agent_id,  # agent holder, human prefix — mismatch
                    name="forged",
                    token_hash=security.hash_token(forged),
                    prefix=forged[:12],
                    scopes=["issue:read"],
                )
            )
        resp = await client.get(f"/api/v1/workspaces/{ws_id}/issues", headers=_auth(forged))
        assert resp.status_code == 401
        assert resp.json()["error"]["details"]["reason"] == "credential_type_mismatch"
