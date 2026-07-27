"""Credential fencing + redaction + checkout whitelist tests
(runtime.md §2.2 protocol, §2.2 H1 / README §6.16, T16)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.runtime import ExecutionCredential, RuntimeCredential, TaskExecution
from mesh.errors import BusinessRuleError, ConflictError, ForbiddenError
from mesh.runtime.checkout import (
    assert_public_url,
    is_forbidden_host,
    repo_is_allowed,
    report_checkout,
)
from mesh.runtime.credentials import (
    decrypt_credential_value,
    encrypt_credential_value,
    issue_envelopes,
    redact_text,
    refetch_envelopes,
    revoke_attempt_envelopes,
)

from tests.unit.runtime_support import TEST_JWT_SECRET, seed_world

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# encryption + redaction
# ---------------------------------------------------------------------------


async def test_encrypt_decrypt_roundtrip_and_tamper():
    ciphertext = encrypt_credential_value("s3cret-value", TEST_JWT_SECRET)
    assert "s3cret-value" not in ciphertext
    assert decrypt_credential_value(ciphertext, TEST_JWT_SECRET) == "s3cret-value"
    with pytest.raises(Exception):
        decrypt_credential_value(ciphertext, "another-secret-key-0000000000000000")


def test_redact_text_replaces_all_hits():
    text, hits = redact_text(
        "token=abc123 again abc123 and key=xyz", ["abc123", "xyz", "  "]
    )
    assert text == "token=*** again *** and key=***"
    assert hits == 3


def test_redact_text_ignores_empty_secrets():
    text, hits = redact_text("nothing to redact", ["", "   "])
    assert text == "nothing to redact"
    assert hits == 0


# ---------------------------------------------------------------------------
# envelope lifecycle
# ---------------------------------------------------------------------------


async def _seed_attempt(session_factory, world):
    from tests.unit.runtime_support import make_execution, make_runtime
    from mesh.runtime.claim import claim_execution

    runtime = await make_runtime(session_factory, world["ws_id"])
    cred = RuntimeCredential(
        workspace_id=world["ws_id"],
        name="CI_API_KEY",
        kind="env",
        encrypted_value=encrypt_credential_value("sk-live-123", TEST_JWT_SECRET),
        env_name="CI_API_KEY",
    )
    async with session_factory() as session, session.begin():
        session.add(cred)
        await session.flush()
        cred_id = cred.id
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        task_spec={"credential_ids": [str(cred_id)]},
    )
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    return runtime, uuid.UUID(result.attempt["id"]), cred_id


async def test_issue_envelopes_idempotent_per_binding(session_factory):
    world = await seed_world(session_factory)
    runtime, attempt_id, cred_id = await _seed_attempt(session_factory, world)
    async with session_factory() as session, session.begin():
        first = await issue_envelopes(
            session,
            workspace_id=world["ws_id"],
            attempt_id=attempt_id,
            credential_ids=[cred_id],
            signing_secret=TEST_JWT_SECRET,
            envelope_ttl=timedelta(hours=2),
        )
    # Re-issue on the same binding rotates the envelope, no new row.
    async with session_factory() as session, session.begin():
        second = await issue_envelopes(
            session,
            workspace_id=world["ws_id"],
            attempt_id=attempt_id,
            credential_ids=[cred_id],
            signing_secret=TEST_JWT_SECRET,
            envelope_ttl=timedelta(hours=2),
        )
    assert first[0].value == second[0].value == "sk-live-123"
    assert first[0].envelope != second[0].envelope
    async with session_factory() as session:
        rows = (await session.execute(select(ExecutionCredential))).scalars().all()
    assert len(rows) == 1


async def test_refetch_rotates_and_counts_then_blocks(session_factory):
    world = await seed_world(session_factory)
    runtime, attempt_id, _ = await _seed_attempt(session_factory, world)
    seen_envelopes = set()
    for i in range(3):
        async with session_factory() as session, session.begin():
            delivered = await refetch_envelopes(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt_id,
                signing_secret=TEST_JWT_SECRET,
                envelope_ttl=timedelta(hours=2),
                refetch_limit=3,
            )
        assert delivered[0].value == "sk-live-123"
        seen_envelopes.add(delivered[0].envelope)
    assert len(seen_envelopes) == 3  # fresh envelope every time
    with pytest.raises(ConflictError) as exc:
        async with session_factory() as session, session.begin():
            await refetch_envelopes(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt_id,
                signing_secret=TEST_JWT_SECRET,
                envelope_ttl=timedelta(hours=2),
                refetch_limit=3,
            )
    assert exc.value.code == "credential_refetch_limit"


async def test_terminal_revocation_is_final(session_factory):
    world = await seed_world(session_factory)
    runtime, attempt_id, _ = await _seed_attempt(session_factory, world)
    async with session_factory() as session, session.begin():
        revoked = await revoke_attempt_envelopes(session, attempt_id=attempt_id)
    assert revoked == 1
    with pytest.raises(ConflictError):
        async with session_factory() as session, session.begin():
            await refetch_envelopes(
                session,
                workspace_id=world["ws_id"],
                attempt_id=attempt_id,
                signing_secret=TEST_JWT_SECRET,
                envelope_ttl=timedelta(hours=2),
                refetch_limit=3,
            )


# ---------------------------------------------------------------------------
# checkout whitelist + SSRF (T16)
# ---------------------------------------------------------------------------


def test_repo_allowlist_exact_and_prefix():
    allowed = ["https://code.example/team/app.git", "https://code.example/libs/"]
    assert repo_is_allowed("https://code.example/team/app.git", allowed)
    assert repo_is_allowed("https://code.example/libs/tool.git", allowed)
    assert not repo_is_allowed("https://code.example/other/app.git", allowed)
    assert not repo_is_allowed("https://evil.example/team/app.git", allowed)


@pytest.mark.parametrize(
    "host",
    [
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.9",
        "127.0.0.1",
        "169.254.169.254",
        "metadata.google.internal",
        "localhost",
        "::1",
        "fe80::1",
        "::ffff:10.0.0.1",
    ],
)
def test_forbidden_hosts_rejected(host):
    assert is_forbidden_host(host)


def test_public_hosts_allowed():
    assert not is_forbidden_host("code.example.com")
    assert not is_forbidden_host("8.8.8.8")


def test_assert_public_url_scheme_and_host():
    assert_public_url("https://code.example.com/team/app.git")  # ok
    with pytest.raises(ForbiddenError):
        assert_public_url("file:///etc/passwd")
    with pytest.raises(ForbiddenError):
        assert_public_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(ForbiddenError):
        assert_public_url("https://127.0.0.1/team/app.git")


async def test_t16_checkout_outside_allowlist_403(session_factory):
    world = await seed_world(session_factory)
    from tests.unit.runtime_support import make_execution, make_runtime
    from mesh.runtime.claim import claim_execution

    runtime = await make_runtime(session_factory, world["ws_id"])
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        config_snapshot={"repo": {"url": "https://code.example/secret/app.git", "base_ref": "main"}},
    )
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    with pytest.raises(ForbiddenError) as exc:
        await report_checkout(
            session_factory,
            attempt_id=uuid.UUID(result.attempt["id"]),
            runtime=runtime,
            lease_seq=1,
            status="ready",
        )
    assert exc.value.code == "repo_not_allowed"


async def test_t16_platform_managed_private_address_rejected(session_factory):
    world = await seed_world(session_factory)
    from tests.unit.runtime_support import make_execution, make_runtime
    from mesh.runtime.claim import claim_execution

    runtime = await make_runtime(session_factory, world["ws_id"], kind="platform_managed")
    private_repo = "http://192.168.10.5/internal/app.git"
    # Whitelisted (so the allowlist gate passes) but private → SSRF gate 403.
    from sqlalchemy import update
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        await session.execute(
            update(Workspace)
            .where(Workspace.id == world["ws_id"])
            .values(settings={"allowed_repos": [private_repo]})
        )
    await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        config_snapshot={"repo": {"url": private_repo, "base_ref": "main"}},
    )
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    with pytest.raises(ForbiddenError) as exc:
        await report_checkout(
            session_factory,
            attempt_id=uuid.UUID(result.attempt["id"]),
            runtime=runtime,
            lease_seq=1,
            status="ready",
        )
    assert exc.value.code == "private_address_forbidden"


async def test_checkout_happy_path_and_url_mismatch(session_factory):
    world = await seed_world(session_factory)
    from tests.unit.runtime_support import make_execution, make_runtime
    from mesh.runtime.claim import claim_execution
    from sqlalchemy import update
    from mesh.db.models.workspace import Workspace

    repo = "https://code.example/team/app.git"
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Workspace)
            .where(Workspace.id == world["ws_id"])
            .values(settings={"allowed_repos": [repo]})
        )
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        config_snapshot={"repo": {"url": repo, "base_ref": "main"}},
    )
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    attempt_id = uuid.UUID(result.attempt["id"])

    # Daemon may only checkout the FROZEN repo url.
    with pytest.raises(ForbiddenError):
        await report_checkout(
            session_factory,
            attempt_id=attempt_id,
            runtime=runtime,
            lease_seq=1,
            status="cloning",
            repo_url="https://code.example/team/OTHER.git",
        )

    data = await report_checkout(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        status="ready",
        repo_url=repo,
        commit_sha="c0ffee",
    )
    assert data["status"] == "ready"
    assert data["working_branch"] == f"agent/{execution.id}/a1"
