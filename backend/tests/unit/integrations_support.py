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


def github_request(
    secret: str, payload: dict, *, event: str, delivery: str | None = None
) -> tuple[bytes, dict]:
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


# ---------------------------------------------------------------------------
# DingTalk (MES-87) — im_dingtalk integration seeding + signature helpers
# ---------------------------------------------------------------------------

DINGTALK_APP_SECRET = "dingtalk-test-app-secret-000000000000"
DINGTALK_CORP_ID = "dingcorp0001"
DINGTALK_APP_KEY = "dingappkey0001"
DINGTALK_ROBOT_CODE = "dingappkey0001"
DINGTALK_CONVERSATION_ID = "cid6EUvB2O8qVF2RYQtHTKEsg=="


def dingtalk_sign(app_secret: str, timestamp_ms: str | int) -> str:
    """Base64(HMAC_SHA256(app_secret, timestamp + "\\n" + app_secret))."""
    import base64 as _b64

    material = f"{timestamp_ms}\n{app_secret}".encode()
    return _b64.b64encode(
        hmac.new(app_secret.encode(), material, hashlib.sha256).digest()
    ).decode()


def dingtalk_request(payload: dict, *, ts_ms: str | None = None, secret: str = DINGTALK_APP_SECRET):
    body = json.dumps(payload).encode()
    ts = ts_ms or str(int(NOW.timestamp() * 1000))
    headers = {
        "timestamp": ts,
        "sign": dingtalk_sign(secret, ts),
        "content-type": "application/json",
    }
    return body, headers


def dingtalk_message_payload(
    *,
    text: str = "帮我查下昨晚的报警",
    msg_id: str | None = None,
    conversation_id: str = DINGTALK_CONVERSATION_ID,
    conversation_type: str = "2",
    msgtype: str = "text",
    staff_id: str | None = "014728255240768602",
    sender_id: str | None = "$:LWCP_v1:$6GYsn+zrv5WZ77xc2v4zsyXfBv1MhAv9",
    is_in_at_list: bool = True,
    corp_id: str = DINGTALK_CORP_ID,
    robot_code: str = DINGTALK_ROBOT_CODE,
) -> dict:
    return {
        "msgId": msg_id or f"msg{uuid.uuid4().hex[:20]}==",
        "conversationId": conversation_id,
        "conversationType": conversation_type,
        "chatbotCorpId": corp_id,
        "robotCode": robot_code,
        "msgtype": msgtype,
        "senderStaffId": staff_id,
        "senderId": sender_id,
        "senderNick": "值班人",
        "isInAtList": is_in_at_list,
        "text": {"content": f" {text}" if conversation_type == "2" else text},
        "sessionWebhookExpiredTime": 1753890000000,
    }


async def seed_dingtalk_world(
    session_factory,
    *,
    inbound_queue: str = "serial_conversation",
    ack_template: str | None = None,
    receive_mode: str = "http",
    status: str = "active",
    config_extra: dict | None = None,
) -> dict:
    """Workspace + admin member + agent + one im_dingtalk integration with a
    ciphertext app_secret_ref (the pipeline decrypts the REAL secret)."""
    ids = {k: uuid.uuid4() for k in ("ws", "user", "member", "agent")}
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=ids["ws"], name="DT WS", slug=f"dt-{ids['ws'].hex[:10]}"))
        session.add(User(
            id=ids["user"], email=f"dt-{ids['user'].hex[:8]}@mesh.test",
            display_name="DT Admin", password_hash="unused-in-tests",
        ))
        await session.flush()
        session.add(Agent(
            id=ids["agent"], workspace_id=ids["ws"], name="DT On-Call Agent",
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
        config = {
            "app_key": DINGTALK_APP_KEY,
            "corp_id": DINGTALK_CORP_ID,
            "robot_code": DINGTALK_ROBOT_CODE,
            "receive_mode": receive_mode,
            "inbound_queue": inbound_queue,
            "app_secret_ref": encrypt(DINGTALK_APP_SECRET),
        }
        if ack_template is not None:
            config["ack_template"] = ack_template
        if config_extra:
            config.update(config_extra)
        integration = Integration(
            id=uuid.uuid4(), workspace_id=ids["ws"], kind="im_dingtalk",
            name="dingtalk-main", config=config, created_by=ids["member"],
            status=status,
        )
        session.add(integration)
    ids["integ_dingtalk"] = integration.id
    ids["agent_member_id"] = agent_member_id
    return ids


async def make_dingtalk_binding(
    session_factory,
    *,
    world: dict,
    external_ref: str = DINGTALK_CONVERSATION_ID,
    scope: str = "workspace",
    project_id: uuid.UUID | None = None,
    match_config: dict | None = None,
    bound_agent: bool = True,
) -> IntegrationBinding:
    async with session_factory() as session, session.begin():
        binding = IntegrationBinding(
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            provider="dingtalk",
            provider_tenant_key=DINGTALK_CORP_ID,
            scope=scope,
            project_id=project_id,
            external_ref=external_ref,
            match_config=match_config or {},
            bound_agent_id=world["agent"] if bound_agent else None,
        )
        session.add(binding)
    return binding
