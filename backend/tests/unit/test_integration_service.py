"""IntegrationService unit tests (integrations.md §3.1 / §6.16).

Covers: kind validation, config plaintext-secret rejection (§5.4 scan),
secret ciphertext round-trip with no echo, rotation, bindings CRUD
(provider normalization from kind, scope XOR, 409 binding_conflict, hard
delete releasing the global external-identity slot), event ledger.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.integration import Integration, IntegrationBinding
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError
from mesh.integrations.service import IntegrationService, assert_config_non_secret
from tests.unit.integrations_support import TEST_SIGNING_SECRET, seed_world

pytestmark = pytest.mark.unit


async def _valid_dingtalk_credentials(_config, _secret):
    return "healthy", None


def make_service(session_factory, *, verifier=None) -> IntegrationService:
    return IntegrationService(
        session_factory,
        TEST_SIGNING_SECRET,
        dingtalk_credential_verifier=verifier or _valid_dingtalk_credentials,
    )


# ---------------------------------------------------------------------------
# Config secret scan (§6.16 / §5.4)
# ---------------------------------------------------------------------------


def test_config_non_secret_accepts_refs():
    assert_config_non_secret(
        {
            "app_id": "cli_123",
            "signing_secret_ref": "gAAAAAB-ciphertext",
            "callback_base": "https://mesh.example.com",
        }
    )


def test_config_rejects_plaintext_secret_keys():
    with pytest.raises(BusinessRuleError) as excinfo:
        assert_config_non_secret({"signing_secret": "super-plain"})
    assert excinfo.value.code == "invalid_request"
    with pytest.raises(BusinessRuleError):
        assert_config_non_secret({"webhook_token": "plain"})
    with pytest.raises(BusinessRuleError):
        assert_config_non_secret({"api_password": "plain"})


# ---------------------------------------------------------------------------
# Integrations CRUD
# ---------------------------------------------------------------------------


async def test_create_integration_encrypts_secret_and_never_echoes(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    async with session_factory() as session:
        from mesh.db.models.member import Member

        member = await session.get(Member, world["member"])
    result = await service.create_integration(
        workspace_id=world["ws"],
        creator=member,
        kind="im_slack",
        name="slack-2",
        config={"team_id": "T_NEW"},
        secret="bot-secret-xyz",
    )
    rendered = result["integration"]
    assert rendered["has_secret"] is True
    assert "bot-secret-xyz" not in str(rendered), "plaintext must never be echoed"
    assert "secret" not in {k for k in rendered if k != "has_secret"}
    # Ciphertext round-trip.
    async with session_factory() as session:
        row = await session.scalar(select(Integration).where(Integration.name == "slack-2"))
        assert row is not None
        assert row.secret_ref != "bot-secret-xyz"
        assert service.decrypt_integration_secret(row) == "bot-secret-xyz"


async def test_create_integration_rejects_bad_kind_and_secret_config(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    async with session_factory() as session:
        from mesh.db.models.member import Member

        member = await session.get(Member, world["member"])
    with pytest.raises(BusinessRuleError):
        await service.create_integration(
            workspace_id=world["ws"], creator=member, kind="carrier_pigeon", name="x"
        )
    with pytest.raises(BusinessRuleError):
        await service.create_integration(
            workspace_id=world["ws"],
            creator=member,
            kind="im_slack",
            name="y",
            config={"signing_secret": "plain!"},
        )


async def test_create_integration_duplicate_name_conflict(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    async with session_factory() as session:
        from mesh.db.models.member import Member

        member = await session.get(Member, world["member"])
    await service.create_integration(
        workspace_id=world["ws"], creator=member, kind="im_slack", name="dup-name"
    )
    with pytest.raises(ConflictError):
        await service.create_integration(
            workspace_id=world["ws"], creator=member, kind="im_slack", name="dup-name"
        )


async def test_update_and_soft_delete_integration(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    updated = await service.update_integration(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        status="disabled",
    )
    assert updated["status"] == "disabled"
    await service.delete_integration(workspace_id=world["ws"], integration_id=world["integ_slack"])
    with pytest.raises(NotFoundError):
        await service.get_integration(workspace_id=world["ws"], integration_id=world["integ_slack"])
    # Soft-deleted rows remain (bindings/events preserved, §5.4).
    async with session_factory() as session:
        row = await session.get(Integration, world["integ_slack"])
        assert row is not None and row.deleted_at is not None


async def test_rotate_secret_invalidates_old_ciphertext(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    async with session_factory() as session:
        row = await session.get(Integration, world["integ_slack"])
        row.secret_ref = None
    result = await service.rotate_secret(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        secret="fresh-secret",
    )
    assert result["rotated"] is True
    integration = await service.get_integration(workspace_id=world["ws"], integration_id=world["integ_slack"])
    assert service.decrypt_integration_secret(integration) == "fresh-secret"


# ---------------------------------------------------------------------------
# Bindings CRUD
# ---------------------------------------------------------------------------


async def test_create_binding_normalizes_provider_and_tenant(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    rendered = await service.create_binding(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        external_ref="C_ROOM",
    )
    assert rendered["provider"] == "slack"
    assert rendered["provider_tenant_key"] == "T_TEST"  # from config.team_id
    assert rendered["scope"] == "workspace"


async def test_create_binding_scope_xor_enforced(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    # project scope without project id → CHECK violation surfaces as 4xx.
    with pytest.raises(BusinessRuleError):
        await service.create_binding(
            workspace_id=world["ws"],
            integration_id=world["integ_slack"],
            external_ref="C_XOR",
            scope="project",
            project_id=None,
        )


async def test_create_binding_global_conflict(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    await service.create_binding(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        external_ref="C_TAKEN",
    )
    with pytest.raises(ConflictError) as excinfo:
        await service.create_binding(
            workspace_id=world["ws"],
            integration_id=world["integ_slack"],
            external_ref="C_TAKEN",
        )
    assert excinfo.value.code == "binding_conflict"


async def test_delete_binding_releases_external_slot(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    rendered = await service.create_binding(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        external_ref="C_RELEASE",
    )
    await service.delete_binding(
        workspace_id=world["ws"],
        binding_id=uuid.UUID(rendered["id"]),
    )
    # Hard delete → the slot is free again (disabled would still occupy it).
    again = await service.create_binding(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        external_ref="C_RELEASE",
    )
    assert again["external_ref"] == "C_RELEASE"
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(IntegrationBinding).where(IntegrationBinding.external_ref == "C_RELEASE")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


async def test_update_binding_status_and_match_config(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    rendered = await service.create_binding(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        external_ref="C_UPD",
    )
    updated = await service.update_binding(
        workspace_id=world["ws"],
        binding_id=uuid.UUID(rendered["id"]),
        match_config={"trigger_on": ["keyword"], "keyword_include": ["urgent"]},
        status="disabled",
    )
    assert updated["status"] == "disabled"
    assert updated["match_config"]["keyword_include"] == ["urgent"]
    with pytest.raises(BusinessRuleError):
        await service.update_binding(
            workspace_id=world["ws"],
            binding_id=uuid.UUID(rendered["id"]),
            match_config={"branch_pattern": "(unclosed"},
        )


# ---------------------------------------------------------------------------
# Connector health drive (§2.2 / §3.1 :test / §4.1 badge)
# ---------------------------------------------------------------------------


async def test_test_connection_healthy_clears_last_error_and_stamps_success(
    session_factory,
):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    async with session_factory() as session:
        from mesh.db.models.member import Member

        member = await session.get(Member, world["member"])
    # webhook_outbound has no platform credentials → healthy without HTTP.
    created = await service.create_integration(
        workspace_id=world["ws"],
        creator=member,
        kind="webhook_outbound",
        name="outbound-health",
    )
    integration_id = uuid.UUID(created["integration"]["id"])
    # Seed a prior failure so we can prove healthy clears it.
    integration = await service.get_integration(workspace_id=world["ws"], integration_id=integration_id)
    await service.record_health(
        workspace_id=world["ws"],
        integration=integration,
        health_state="auth_failed",
        last_error="stale",
    )
    result = await service.test_connection(workspace_id=world["ws"], integration_id=integration_id)
    assert result["health_state"] == "healthy"
    async with session_factory() as session:
        row = await session.get(Integration, integration_id)
        assert row.health_state == "healthy"
        assert row.last_error is None, "healthy clears last_error"
        assert row.last_success_at is not None, "healthy stamps last_success_at"


async def test_test_connection_auth_failed_persists_last_error(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    async with session_factory() as session:
        from mesh.db.models.member import Member

        member = await session.get(Member, world["member"])
    # im_slack WITHOUT a secret → auth_failed missing_credentials (no HTTP).
    created = await service.create_integration(
        workspace_id=world["ws"],
        creator=member,
        kind="im_slack",
        name="slack-noauth",
        config={"team_id": "T_NA"},
    )
    integration_id = uuid.UUID(created["integration"]["id"])
    result = await service.test_connection(workspace_id=world["ws"], integration_id=integration_id)
    assert result == {"health_state": "auth_failed", "detail": "missing_credentials"}
    async with session_factory() as session:
        row = await session.get(Integration, integration_id)
        assert row.health_state == "auth_failed"
        assert row.last_error == "missing_credentials"
        assert row.last_success_at is None


# ---------------------------------------------------------------------------
# Renderers — secret redaction + §4.1 columns
# ---------------------------------------------------------------------------


def test_redacted_config_masks_every_ref_value():
    from mesh.integrations.service import _redacted_config

    assert _redacted_config(
        {
            "app_id": "cli_plain",
            "signing_secret_ref": "gAAAAAB-ciphertext",
            "webhook_token_ref": "another-cipher",
            "empty_ref": "",
        }
    ) == {
        "app_id": "cli_plain",
        "signing_secret_ref": "***",
        "webhook_token_ref": "***",
        "empty_ref": "",  # empty value carries no material → left as-is
    }
    assert _redacted_config(None) == {}


def test_render_integration_events_7d_only_when_provided():
    from mesh.integrations.service import render_integration

    integration = Integration(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="im_slack",
        name="s",
        config={"team_id": "T"},
        created_by=uuid.uuid4(),
    )
    without = render_integration(integration)
    assert "events_7d" not in without, "field omitted unless computed"
    with_count = render_integration(integration, events_7d=17)
    assert with_count["events_7d"] == 17


async def test_event_counts_since_counts_per_integration(session_factory):
    from datetime import UTC, datetime, timedelta

    from mesh.db.models.integration import IntegrationEvent
    from mesh.db.tenant import set_tenant_context

    world = await seed_world(session_factory)
    service = make_service(session_factory)
    now = datetime.now(UTC)
    from mesh.db.models.member import Member

    async with session_factory() as session:
        viewer = await session.get(Member, world["member"])
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        # Two recent events on the slack integration, one old, one on github.
        for i in range(2):
            session.add(
                IntegrationEvent(
                    workspace_id=world["ws"],
                    integration_id=world["integ_slack"],
                    external_event_id=f"rc-{i}",
                    event_type="message",
                    payload={},
                    signature_status="valid",
                    process_status="received",
                    received_at=now - timedelta(days=1),
                )
            )
        session.add(
            IntegrationEvent(
                workspace_id=world["ws"],
                integration_id=world["integ_slack"],
                external_event_id="old",
                event_type="message",
                payload={},
                signature_status="valid",
                process_status="received",
                received_at=now - timedelta(days=30),
            )
        )
        session.add(
            IntegrationEvent(
                workspace_id=world["ws"],
                integration_id=world["integ_github"],
                external_event_id="gh-1",
                event_type="push",
                payload={},
                signature_status="valid",
                process_status="received",
                received_at=now - timedelta(days=1),
            )
        )
    counts = await service.event_counts_since(
        workspace_id=world["ws"],
        integration_ids=[world["integ_slack"], world["integ_github"]],
        since=now - timedelta(days=7),
        viewer=viewer,
    )
    assert counts[world["integ_slack"]] == 2, "only events within the window"
    assert counts[world["integ_github"]] == 1
    assert (
        await service.event_counts_since(
            workspace_id=world["ws"], integration_ids=[], since=now, viewer=viewer
        )
        == {}
    )


async def test_event_ledger_filterable(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    from datetime import UTC, datetime

    from mesh.db.models.integration import IntegrationEvent
    from mesh.db.models.member import Member
    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session:
        viewer = await session.get(Member, world["member"])

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, world["ws"])
        for i, (sig, proc) in enumerate(
            [
                ("valid", "dispatched"),
                ("invalid", "rejected"),
                ("valid", "deduped"),
            ]
        ):
            session.add(
                IntegrationEvent(
                    workspace_id=world["ws"],
                    integration_id=world["integ_slack"],
                    external_event_id=f"evt-{i}",
                    event_type="message",
                    payload={},
                    signature_status=sig,
                    process_status=proc,
                    received_at=datetime.now(UTC),
                )
            )
    page = await service.list_events(
        workspace_id=world["ws"],
        integration_id=world["integ_slack"],
        viewer=viewer,
        process_status="rejected",
    )
    assert len(page.items) == 1
    assert page.items[0].process_status == "rejected"


# ---------------------------------------------------------------------------
# GitLab self-hosted instance_url SSRF guard at config WRITE time
# (README §6.16, security HIGH-1)
# ---------------------------------------------------------------------------


async def _create_gitlab(session_factory, world, *, config, name_suffix="g"):
    from mesh.db.models.member import Member

    service = make_service(session_factory)
    async with session_factory() as session:
        member = await session.get(Member, world["member"])
    return await service.create_integration(
        workspace_id=world["ws"],
        creator=member,
        kind="vcs_gitlab",
        name=f"gitlab-{name_suffix}-{uuid.uuid4().hex[:6]}",
        config=config,
        secret="glpat-xxx",
    )


@pytest.mark.parametrize(
    "instance_url",
    [
        "https://127.0.0.1",
        "https://10.0.0.8",
        "https://169.254.169.254",  # cloud metadata
        "https://[::1]",
        "https://localhost",
    ],
)
async def test_create_gitlab_rejects_forbidden_instance_url(session_factory, instance_url):
    # Arrange
    world = await seed_world(session_factory)
    # Act / Assert — refused at the service boundary...
    with pytest.raises(BusinessRuleError) as excinfo:
        await _create_gitlab(session_factory, world, config={"instance_url": instance_url})
    assert excinfo.value.code == "ssrf_blocked"
    # ...and nothing was persisted.
    async with session_factory() as session:
        rows = (
            (await session.execute(select(Integration).where(Integration.workspace_id == world["ws"])))
            .scalars()
            .all()
        )
    assert all((row.config or {}).get("instance_url") != instance_url for row in rows)


async def test_create_gitlab_rejects_non_https_instance_url(session_factory):
    # Arrange
    world = await seed_world(session_factory)
    # Act / Assert
    from mesh.errors import ValidationError

    with pytest.raises(ValidationError) as excinfo:
        await _create_gitlab(
            session_factory, world, config={"instance_url": "http://gitlab.corp.example.com"}
        )
    assert excinfo.value.code == "invalid_url_scheme"


async def test_create_gitlab_accepts_public_https_instance_url(session_factory):
    # Arrange
    world = await seed_world(session_factory)
    # Act
    result = await _create_gitlab(
        session_factory, world, config={"instance_url": "https://gitlab.corp.example.com"}
    )
    # Assert — persisted verbatim (non-secret config is not redacted).
    assert result["integration"]["config"]["instance_url"] == "https://gitlab.corp.example.com"


async def test_update_gitlab_config_rejects_forbidden_instance_url(session_factory):
    # Arrange — an integration with a clean self-hosted config.
    world = await seed_world(session_factory)
    created = await _create_gitlab(
        session_factory, world, config={"instance_url": "https://gitlab.corp.example.com"}
    )
    integration_id = uuid.UUID(created["integration"]["id"])
    service = make_service(session_factory)
    # Act / Assert — switching to an intranet/metadata target is refused.
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.update_integration(
            workspace_id=world["ws"],
            integration_id=integration_id,
            config={"instance_url": "https://169.254.169.254"},
        )
    assert excinfo.value.code == "ssrf_blocked"
    # The stored config is unchanged.
    async with session_factory() as session:
        row = await session.get(Integration, integration_id)
    assert (row.config or {}).get("instance_url") == "https://gitlab.corp.example.com"


# ---------------------------------------------------------------------------
# Per-kind config defaults at every config write (R1, integrations.md
# §2.7:295 / §2.10:649 / §3.2:826 — 「serial_conversation(钉钉默认)」)
# ---------------------------------------------------------------------------


async def _member(session_factory, world):
    from mesh.db.models.member import Member

    async with session_factory() as session:
        return await session.get(Member, world["member"])


async def test_create_dingtalk_defaults_inbound_queue_to_serial(session_factory):
    """An API-created DingTalk integration without an explicit dispatch-mode
    choice materializes the Spec default — serial_conversation. The queue
    module's code-level fallback is parallel (the feishu/slack baseline),
    so the DingTalk default must be made explicit at creation or real API
    integrations would silently parallel direct-dispatch."""
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="dt-default",
        config={"app_key": "dingapp0771", "corp_id": "dingcorp0771"},
        secret="dt-default-secret",
    )
    async with session_factory() as session:
        row = await session.scalar(select(Integration).where(Integration.name == "dt-default"))
    assert row.config["inbound_queue"] == "serial_conversation"
    assert row.config["receive_mode"] == "stream"
    assert row.config["verbosity"] == "final_only"


async def test_create_dingtalk_explicit_inbound_queue_wins_over_default(session_factory):
    """An explicit client choice is never overridden by the Spec default."""
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="dt-parallel",
        config={
            "app_key": "dingapp0772",
            "corp_id": "dingcorp0772",
            "inbound_queue": "parallel",
        },
        secret="dt-parallel-secret",
    )
    async with session_factory() as session:
        row = await session.scalar(select(Integration).where(Integration.name == "dt-parallel"))
    assert row.config["inbound_queue"] == "parallel"


async def test_dingtalk_app_key_cannot_be_claimed_by_another_workspace(session_factory):
    first_world = await seed_world(session_factory)
    second_world = await seed_world(session_factory)
    service = make_service(session_factory)
    config = {
        "app_key": "dingSHAREDAPP001",
        "corp_id": "dingcorpOWNER",
        "robot_code": "dingrobotOWNER",
        "receive_mode": "stream",
    }
    await service.create_integration(
        workspace_id=first_world["ws"],
        creator=await _member(session_factory, first_world),
        kind="im_dingtalk",
        name="owner-app",
        config=config,
        secret="shared-app-secret",
    )

    with pytest.raises(ConflictError) as excinfo:
        await service.create_integration(
            workspace_id=second_world["ws"],
            creator=await _member(session_factory, second_world),
            kind="im_dingtalk",
            name="foreign-app",
            config={**config, "corp_id": "dingcorpFOREIGN"},
            secret="different-secret",
        )
    assert excinfo.value.code == "dingtalk_app_key_conflict"


async def test_invalid_first_claim_cannot_reserve_dingtalk_app_key(session_factory):
    attacker = await seed_world(session_factory)
    owner = await seed_world(session_factory)

    async def verify(_config, secret):
        return (
            ("healthy", None)
            if secret == "real-owner-secret"
            else (
                "auth_failed",
                "errcode_40089",
            )
        )

    service = make_service(session_factory, verifier=verify)
    config = {
        "app_key": "dingPROOFOFPOSSESSION",
        "corp_id": "dingcorpOWNER",
        "robot_code": "dingrobotOWNER",
        "receive_mode": "stream",
    }
    with pytest.raises(BusinessRuleError) as invalid:
        await service.create_integration(
            workspace_id=attacker["ws"],
            creator=await _member(session_factory, attacker),
            kind="im_dingtalk",
            name="fake-first-claim",
            config=config,
            secret="fake-secret",
        )
    assert invalid.value.code == "dingtalk_credentials_invalid"

    claimed = await service.create_integration(
        workspace_id=owner["ws"],
        creator=await _member(session_factory, owner),
        kind="im_dingtalk",
        name="verified-owner",
        config=config,
        secret="real-owner-secret",
    )
    assert claimed["integration"]["workspace_id"] == str(owner["ws"])


async def test_dingtalk_shared_app_requires_same_secret_and_unique_route(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    base = {
        "app_key": "dingSHAREDAPP002",
        "corp_id": "dingcorpONE",
        "robot_code": "dingrobotONE",
        "receive_mode": "stream",
    }
    await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="shared-one",
        config=base,
        secret="one-secret",
    )

    with pytest.raises(ConflictError) as secret_error:
        await service.create_integration(
            workspace_id=world["ws"],
            creator=await _member(session_factory, world),
            kind="im_dingtalk",
            name="shared-wrong-secret",
            config={**base, "corp_id": "dingcorpTWO", "robot_code": "dingrobotTWO"},
            secret="wrong-secret",
        )
    assert secret_error.value.code == "dingtalk_app_credential_conflict"

    with pytest.raises(ConflictError) as route_error:
        await service.create_integration(
            workspace_id=world["ws"],
            creator=await _member(session_factory, world),
            kind="im_dingtalk",
            name="shared-duplicate-route",
            config=base,
            secret="one-secret",
        )
    assert route_error.value.code == "dingtalk_route_conflict"

    allowed = await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="shared-two",
        config={**base, "corp_id": "dingcorpTWO", "robot_code": "dingrobotTWO"},
        secret="one-secret",
    )
    assert allowed["integration"]["name"] == "shared-two"


async def test_dingtalk_shared_stream_app_requires_one_reconnect_policy(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    base = {
        "app_key": "dingSHAREDRECONNECT",
        "receive_mode": "stream",
        "stream_reconnect": {
            "base_seconds": 3,
            "max_seconds": 120,
            "heartbeat_timeout_seconds": 45,
        },
    }
    await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="reconnect-policy-owner",
        config={**base, "corp_id": "dingcorpONE", "robot_code": "dingrobotONE"},
        secret="shared-secret",
    )

    with pytest.raises(ConflictError) as excinfo:
        await service.create_integration(
            workspace_id=world["ws"],
            creator=await _member(session_factory, world),
            kind="im_dingtalk",
            name="reconnect-policy-conflict",
            config={
                **base,
                "corp_id": "dingcorpTWO",
                "robot_code": "dingrobotTWO",
                "stream_reconnect": {
                    "base_seconds": 4,
                    "max_seconds": 120,
                    "heartbeat_timeout_seconds": 45,
                },
            },
            secret="shared-secret",
        )
    assert excinfo.value.code == "dingtalk_stream_config_conflict"


async def test_dingtalk_shared_app_rotate_and_reconnect_are_group_scoped(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    base = {
        "app_key": "dingSHAREDAPP003",
        "receive_mode": "stream",
    }
    first = await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="group-first",
        config={**base, "corp_id": "dingcorpONE", "robot_code": "dingrobotONE"},
        secret="old-shared-secret",
    )
    second = await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="group-second",
        config={**base, "corp_id": "dingcorpTWO", "robot_code": "dingrobotTWO"},
        secret="old-shared-secret",
    )
    first_id = uuid.UUID(first["integration"]["id"])
    second_id = uuid.UUID(second["integration"]["id"])

    await service.rotate_secret(
        workspace_id=world["ws"], integration_id=second_id, secret="new-shared-secret"
    )
    async with session_factory() as session:
        rows = [await session.get(Integration, item_id) for item_id in (first_id, second_id)]
    assert [service.decrypt_integration_secret(row) for row in rows] == [
        "new-shared-secret",
        "new-shared-secret",
    ]

    await service.request_stream_reconnect(workspace_id=world["ws"], integration_id=second_id)
    async with session_factory() as session:
        rows = [await session.get(Integration, item_id) for item_id in (first_id, second_id)]
    reconnect_ids = {(row.stream_state or {}).get("reconnect_request_id") for row in rows}
    assert len(reconnect_ids) == 1
    assert None not in reconnect_ids
    assert all((row.stream_state or {}).get("state") == "reconnecting" for row in rows)


async def test_create_slack_carries_no_inbound_queue_default(session_factory):
    """The parallel baseline for feishu/slack stays the queue module's
    code-level fallback — no key is materialized for non-DingTalk kinds."""
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_slack",
        name="slack-no-default",
        config={"team_id": "T_NODEFAULT"},
    )
    async with session_factory() as session:
        row = await session.scalar(select(Integration).where(Integration.name == "slack-no-default"))
    assert "inbound_queue" not in (row.config or {})


async def test_update_dingtalk_wholesale_config_rematerializes_serial_default(
    session_factory,
):
    """A wholesale config replacement (update) must not silently drop a
    DingTalk integration back onto the parallel code fallback — the Spec
    default is re-materialized at every config write."""
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    created = await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="dt-update",
        config={
            "app_key": "dingapp0773",
            "corp_id": "dingcorp0773",
            "inbound_queue": "parallel",
        },
        secret="dt-update-secret",
    )
    integration_id = uuid.UUID(created["integration"]["id"])
    await service.update_integration(
        workspace_id=world["ws"],
        integration_id=integration_id,
        config={
            "app_key": "dingapp0773",
            "corp_id": "dingcorp0773",
        },  # no inbound_queue this time
    )
    async with session_factory() as session:
        row = await session.get(Integration, integration_id)
    assert row.config["inbound_queue"] == "serial_conversation"


async def test_api_created_dingtalk_default_config_enqueues_serial_pending(
    session_factory,
):
    """End-to-end behavioral pin (unit level): an integration created
    through the real service path with NO inbound_queue enqueues an
    inbound text message as a SERIAL pending item (awaiting the queue
    dispatcher) — never a parallel optimistic direct dispatch."""
    from mesh.integrations.connectors import VerifiedEnvelope
    from mesh.integrations.ingest import ingest_verified_event
    from tests.unit.integrations_support import (
        DINGTALK_CONVERSATION_ID,
        NOW,
        dingtalk_message_payload,
        make_dingtalk_binding,
    )

    world = await seed_world(session_factory)
    service = make_service(session_factory)
    created = await service.create_integration(
        workspace_id=world["ws"],
        creator=await _member(session_factory, world),
        kind="im_dingtalk",
        name="dt-behavior",
        config={
            "app_key": "dingappkey0001",
            "corp_id": "dingcorp0001",
        },  # matches the fixture payload corp
        secret="dt-behavior-secret",
    )
    integration_id = uuid.UUID(created["integration"]["id"])
    await make_dingtalk_binding(session_factory, world={**world, "integ_dingtalk": integration_id})

    from mesh.integrations.dingtalk import normalize_message_payload

    envelope = normalize_message_payload(dingtalk_message_payload(), max_chars=4000, channel="http")
    assert isinstance(envelope, VerifiedEnvelope)
    async with session_factory() as session:
        integration = await session.get(Integration, integration_id)
    async with session_factory() as session, session.begin():
        result = await ingest_verified_event(session, integration=integration, envelope=envelope, now=NOW)
    assert result.process_status == "dispatched"

    from mesh.db.models.integration import IntegrationMessageQueue

    async with session_factory() as session:
        item = (await session.execute(select(IntegrationMessageQueue))).scalar_one()
    assert item.dispatch_mode == "serial_conversation"  # the Spec default
    assert item.state == "pending"  # awaiting the dispatcher — NOT direct dispatch
    assert item.conversation_key == f"dingtalk:dingcorp0001:{DINGTALK_CONVERSATION_ID}"
