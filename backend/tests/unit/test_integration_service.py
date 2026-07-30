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


def make_service(session_factory) -> IntegrationService:
    return IntegrationService(session_factory, TEST_SIGNING_SECRET)


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
    )
    assert counts[world["integ_slack"]] == 2, "only events within the window"
    assert counts[world["integ_github"]] == 1
    assert await service.event_counts_since(workspace_id=world["ws"], integration_ids=[], since=now) == {}


async def test_event_ledger_filterable(session_factory):
    world = await seed_world(session_factory)
    service = make_service(session_factory)
    from datetime import UTC, datetime

    from mesh.db.models.integration import IntegrationEvent
    from mesh.db.tenant import set_tenant_context

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
