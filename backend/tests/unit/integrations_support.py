"""Shared fixtures for integrations-module tests (real PostgreSQL/Redis).

Mirrors ``autopilot_support.py``: seed helpers + signature construction
for all four platform schemes so the ingestion pipeline is exercised with
REAL signatures (nothing on the contract path is mocked).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

from mesh.auth.security import encrypt_secret
from mesh.db.models.agent import Agent
from mesh.db.models.integration import Integration, IntegrationBinding
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace

TEST_SIGNING_SECRET = "integrations-test-signing-secret-00000000"

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


def encrypt(plaintext: str) -> str:
    return encrypt_secret(plaintext, TEST_SIGNING_SECRET)


async def seed_world(session_factory) -> dict:
    """Workspace + admin member + agent (+ roster row) + integrations.

    Creates one integration per provider kind with ciphertext ``*_ref``
    config entries so the ingestion pipeline can decrypt real secrets.
    """
    ids = {k: uuid.uuid4() for k in ("ws", "user", "member", "agent")}
    secrets = {
        "feishu_encrypt_key": "fek-" + uuid.uuid4().hex,
        "feishu_verification_token": "fvt-" + uuid.uuid4().hex,
        "slack_signing_secret": "sss-" + uuid.uuid4().hex,
        "github_webhook_secret": "gws-" + uuid.uuid4().hex,
        "gitlab_webhook_token": "gwt-" + uuid.uuid4().hex,
    }
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=ids["ws"], name="INTG WS", slug=f"intg-{ids['ws'].hex[:10]}"))
        session.add(User(
            id=ids["user"], email=f"intg-{ids['user'].hex[:8]}@mesh.test",
            display_name="INTG Admin", password_hash="unused-in-tests",
        ))
        await session.flush()
        session.add(Agent(
            id=ids["agent"], workspace_id=ids["ws"], name="On-Call Agent",
            owner_user_id=ids["user"], lifecycle_status="active",
        ))
        await session.flush()
        session.add(Member(
            id=ids["member"], workspace_id=ids["ws"], member_type="human",
            user_id=ids["user"], role="admin", status="active",
        ))
        agent_member_id = uuid.uuid4()
        session.add(Member(
            id=agent_member_id, workspace_id=ids["ws"], member_type="agent",
            agent_id=ids["agent"], role="member", status="active",
        ))
        await session.flush()
        integrations = {
            "feishu": Integration(
                id=uuid.uuid4(), workspace_id=ids["ws"], kind="im_feishu",
                name="feishu-main",
                config={
                    "app_id": "cli_test",
                    "tenant_key": "tk-test",
                    "encrypt_key_ref": encrypt(secrets["feishu_encrypt_key"]),
                    "verification_token_ref": encrypt(secrets["feishu_verification_token"]),
                },
                created_by=ids["member"],
            ),
            "slack": Integration(
                id=uuid.uuid4(), workspace_id=ids["ws"], kind="im_slack",
                name="slack-main",
                config={
                    "team_id": "T_TEST",
                    "bot_user_id": "U_BOT",
                    "signing_secret_ref": encrypt(secrets["slack_signing_secret"]),
                },
                created_by=ids["member"],
            ),
            "github": Integration(
                id=uuid.uuid4(), workspace_id=ids["ws"], kind="vcs_github",
                name="github-main",
                config={
                    "installation_id": "1234567",
                    "webhook_secret_ref": encrypt(secrets["github_webhook_secret"]),
                },
                created_by=ids["member"],
            ),
            "gitlab": Integration(
                id=uuid.uuid4(), workspace_id=ids["ws"], kind="vcs_gitlab",
                name="gitlab-main",
                config={
                    "instance_url": "https://gitlab.com",
                    "webhook_token_ref": encrypt(secrets["gitlab_webhook_token"]),
                },
                created_by=ids["member"],
            ),
        }
        for integration in integrations.values():
            session.add(integration)
    ids.update({f"integ_{name}": i.id for name, i in integrations.items()})
    ids["agent_member_id"] = agent_member_id
    return {**ids, "secrets": secrets}


async def make_binding(
    session_factory,
    *,
    world: dict,
    provider: str,
    external_ref: str,
    provider_tenant_key: str = "",
    scope: str = "workspace",
    project_id: uuid.UUID | None = None,
    match_config: dict | None = None,
    bound_agent: bool = True,
    status: str = "active",
) -> IntegrationBinding:
    integration_key = {
        "feishu": "integ_feishu", "slack": "integ_slack",
        "github": "integ_github", "gitlab": "integ_gitlab",
    }[provider]
    async with session_factory() as session, session.begin():
        binding = IntegrationBinding(
            workspace_id=world["ws"],
            integration_id=world[integration_key],
            provider=provider,
            provider_tenant_key=provider_tenant_key,
            scope=scope,
            project_id=project_id,
            external_ref=external_ref,
            match_config=match_config or {},
            bound_agent_id=world["agent"] if bound_agent else None,
            status=status,
        )
        session.add(binding)
    return binding


# ---------------------------------------------------------------------------
# Platform request construction (real signature algorithms)
# ---------------------------------------------------------------------------


def slack_request(secret: str, payload: dict, *, ts: str | None = None) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = ts or str(int(NOW.timestamp()))
    sig = hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    headers = {
        "x-slack-signature": f"v0={sig}",
        "x-slack-request-timestamp": ts,
        "content-type": "application/json",
    }
    return body, headers


def feishu_request(encrypt_key: str, payload: dict, *, ts: str | None = None) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = ts or str(int(NOW.timestamp()))
    nonce = "nonce-1"
    sig = hashlib.sha256(f"{ts}{nonce}{encrypt_key}".encode() + body).hexdigest()
    headers = {
        "timestamp": ts, "nonce": nonce, "x-lark-signature": sig,
        "content-type": "application/json",
    }
    return body, headers


def github_request(secret: str, payload: dict, *, event: str, delivery: str | None = None) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "x-hub-signature-256": f"sha256={sig}",
        "x-github-event": event,
        "x-github-delivery": delivery or f"del-{uuid.uuid4().hex[:12]}",
        "content-type": "application/json",
    }
    return body, headers


def gitlab_request(token: str, payload: dict, *, event: str) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    headers = {
        "x-gitlab-token": token,
        "x-gitlab-event": event,
        "content-type": "application/json",
    }
    return body, headers
