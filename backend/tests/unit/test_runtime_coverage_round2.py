"""Coverage round 2: redaction guard + attachment blocking (H2), approval
permission edges (H4), route filters (H3/F9), refetch cap freeze, logs
robustness branches, checkout update paths, reaper approval-expiry edges."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from mesh.db.models.member import Member
from mesh.db.models.runtime import (
    Approval,
    RuntimeCredential,
    TaskExecution,
    TaskLogSegment,
)
from mesh.errors import BusinessRuleError, ForbiddenError, ValidationError
from mesh.runtime.claim import claim_execution
from mesh.runtime.credentials import encrypt_credential_value
from mesh.runtime.redaction import (
    assert_no_workspace_secrets,
    mime_is_textual,
    scan_bytes_for_secrets,
    scan_text_for_secrets,
)
from mesh.runtime.service import RuntimeService

from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    make_execution,
    make_runtime,
    make_settings,
    seed_world,
)

pytestmark = pytest.mark.unit


def _service(session_factory, **overrides) -> RuntimeService:
    return RuntimeService(session_factory, make_settings(**overrides))


@pytest_asyncio.fixture
async def app_client_factory(db_url, redis_url):
    """Yields a factory: suffix → (client, owner_jwt, ws_id, agent_id)."""
    from mesh.api.app import create_app
    from mesh.config import load_settings

    from tests.unit.test_runtime_routes import _settings_kwargs, _world

    app = create_app(load_settings(**_settings_kwargs(db_url, redis_url)))
    try:
        await app.state.storage.ensure_bucket()
    except Exception:  # noqa: BLE001
        pass
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        async def factory(suffix: str):
            token, ws_id, agent_id = await _world(client, suffix)
            return client, token, ws_id, agent_id

        yield factory


async def _claim_one(session_factory, world):
    from mesh.db.models.agent import Agent

    runtime = await make_runtime(session_factory, world["ws_id"])
    async with session_factory() as session:
        agent_id = (
            await session.execute(
                select(Agent.id).where(Agent.workspace_id == world["ws_id"]).limit(1)
            )
        ).scalar_one()
    await make_execution(session_factory, world["ws_id"], agent_id)
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    return runtime, result


async def _add_credential(session_factory, world, value="top-secret-xyz", name="LEAK") -> None:
    async with session_factory() as session, session.begin():
        session.add(
            RuntimeCredential(
                workspace_id=world["ws_id"],
                name=name,
                encrypted_value=encrypt_credential_value(value, TEST_JWT_SECRET),
                redact_in_logs=True,
            )
        )


# ---------------------------------------------------------------------------
# redaction.py (H2 guard)
# ---------------------------------------------------------------------------


async def test_scan_text_counts_hits_and_clean(session_factory):
    world = await seed_world(session_factory)
    await _add_credential(session_factory, world, value="sekrit-777")
    async with session_factory() as session:
        assert (
            await scan_text_for_secrets(
                session,
                workspace_id=world["ws_id"],
                content="key=sekrit-777 and sekrit-777 again",
                signing_secret=TEST_JWT_SECRET,
            )
            == 2
        )
        assert (
            await scan_text_for_secrets(
                session,
                workspace_id=world["ws_id"],
                content="clean",
                signing_secret=TEST_JWT_SECRET,
            )
            == 0
        )
        assert (
            await scan_text_for_secrets(
                session,
                workspace_id=world["ws_id"],
                content="",
                signing_secret=TEST_JWT_SECRET,
            )
            == 0
        )


async def test_scan_bytes_and_mime_detection(session_factory):
    world = await seed_world(session_factory)
    await _add_credential(session_factory, world, value="bin-secret-9")
    async with session_factory() as session:
        hits = await scan_bytes_for_secrets(
            session,
            workspace_id=world["ws_id"],
            data=b"prefix bin-secret-9 suffix",
            signing_secret=TEST_JWT_SECRET,
        )
        assert hits == 1
        assert (
            await scan_bytes_for_secrets(
                session,
                workspace_id=world["ws_id"],
                data=b"",
                signing_secret=TEST_JWT_SECRET,
            )
            == 0
        )
    assert mime_is_textual("text/plain")
    assert mime_is_textual("application/json")
    assert not mime_is_textual("image/png")
    assert not mime_is_textual(None)


async def test_assert_no_workspace_secrets_blocks_and_passes(session_factory):
    world = await seed_world(session_factory)
    await _add_credential(session_factory, world, value="chan-secret-3")
    async with session_factory() as session:
        with pytest.raises(BusinessRuleError) as exc:
            await assert_no_workspace_secrets(
                session,
                workspace_id=world["ws_id"],
                content="comment with chan-secret-3",
                signing_secret=TEST_JWT_SECRET,
                channel="comment",
            )
        assert exc.value.code == "secret_detected"
        assert exc.value.details["channel"] == "comment"
        await assert_no_workspace_secrets(
            session,
            workspace_id=world["ws_id"],
            content="a normal comment",
            signing_secret=TEST_JWT_SECRET,
            channel="comment",
        )


async def test_attachment_text_with_secret_is_blocked(session_factory, object_storage):
    """H2 attachment channel: textual upload carrying a workspace secret is
    blocked (scan_status=infected, gate never opens) + audited."""
    from mesh.attachment.processing import process_blob
    from mesh.db.models.attachment import AttachmentBlob

    world = await seed_world(session_factory)
    await _add_credential(session_factory, world, value="attach-secret-42")
    settings = make_settings(
        attachment_scan_skip_text=False,
        storage_bucket=object_storage._config.bucket,  # noqa: SLF001
    )
    payload = b"report\npassword=attach-secret-42\nend\n"
    storage_key = f"test-secret-{uuid.uuid4().hex}"
    await object_storage.put_bytes(storage_key, payload, content_type="text/plain")

    blob = AttachmentBlob(
        workspace_id=world["ws_id"],
        content_hash=f"staging:{uuid.uuid4().hex}",
        storage_provider="s3",
        storage_bucket=object_storage._config.bucket,  # noqa: SLF001
        storage_key=storage_key,
        file_size=len(payload),
        mime_type="application/octet-stream",
        scan_status="pending",
    )
    async with session_factory() as session, session.begin():
        session.add(blob)
        await session.flush()
        await process_blob(session, blob, storage=object_storage, settings=settings)
    async with session_factory() as session:
        stored = await session.get(AttachmentBlob, blob.id)
    assert stored.scan_status == "infected"
    assert stored.scan_detail["secret_scan_hits"] == 1


# ---------------------------------------------------------------------------
# approvals: decide permission edges (H4) + non-awaiting decide
# ---------------------------------------------------------------------------


async def _seed_member(session_factory, world, role="member"):
    from mesh.db.models.user import User

    user_id, member_id = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            User(
                id=user_id,
                email=f"cov-{user_id.hex[:8]}@example.com",
                display_name="Cov",
                password_hash="x",
            )
        )
        await session.flush()
        session.add(
            Member(
                id=member_id,
                workspace_id=world["ws_id"],
                member_type="human",
                user_id=user_id,
                role=role,
                status="active",
            )
        )
        await session.flush()
        member = await session.get(Member, member_id)
        session.expunge(member)
    return member


async def _seed_pending_approval(session_factory, ws_id, member_id, execution_id, expires_past=False):
    approval_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO approvals (id, workspace_id, subject_type, subject_execution_id, "
                "requested_by_member_id, action_summary, expires_at, status) "
                "VALUES (:id, :ws, 'tool_call', :e, :m, '{}'::jsonb, "
                "now() + (:delta || ' seconds')::interval, 'pending')"
            ),
            {
                "id": approval_id,
                "ws": ws_id,
                "e": execution_id,
                "m": member_id,
                "delta": "-60" if expires_past else "3600",
            },
        )
    return approval_id


async def test_decide_execution_not_awaiting_leaves_it(session_factory):
    """Deciding a pending approval whose execution moved on (not
    awaiting_approval) returns the decision without touching the execution."""
    from mesh.runtime.approvals import decide_approval

    world = await seed_world(session_factory)
    execution = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="running"
    )
    approval_id = await _seed_pending_approval(
        session_factory, world["ws_id"], world["member_id"], execution.id
    )
    data = await decide_approval(
        session_factory,
        approval_id=approval_id,
        workspace_id=world["ws_id"],
        member=await _seed_member(session_factory, world, role="admin"),
        approve=True,
    )
    assert data["status"] == "approved"
    assert data["execution_status"] is None
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution.id)
    assert stored.status == "running"  # untouched


async def test_issue_creator_may_decide_h4(app_client_factory, session_factory):
    """H4: the trigger (issue reporter) can decide even with plain member
    role; an unrelated member cannot."""
    from mesh.runtime.approvals import decide_approval

    app_client, owner_token, ws_id, agent_id = await app_client_factory("covh4")
    auth_owner = {"Authorization": f"Bearer {owner_token}"}

    async def register_and_link(tag: str):
        email = f"{tag}-{uuid.uuid4().hex[:6]}@example.com"
        reg = await app_client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "Cov-User-12345", "display_name": tag},
        )
        assert reg.status_code == 201, reg.text
        login = await app_client.post(
            "/api/v1/auth/login", json={"email": email, "password": "Cov-User-12345"}
        )
        jwt = login.json()["data"]["access_token"]
        member_id = uuid.uuid4()
        async with session_factory() as session, session.begin():
            user_id = (
                await session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO members (id, workspace_id, member_type, user_id, role, status) "
                    "VALUES (:m, :ws, 'human', :u, 'member', 'active')"
                ),
                {"m": member_id, "ws": uuid.UUID(ws_id), "u": user_id},
            )
        return member_id

    creator_member = await register_and_link("creator")
    stranger_member = await register_and_link("stranger")

    # Owner creates the project; the issue carries the creator as reporter
    # (the persistent trigger signal, H4).
    project = (
        await app_client.post(
            f"/api/v1/workspaces/{ws_id}/projects",
            json={"name": "Cov Proj", "key": "COVX"},
            headers=auth_owner,
        )
    ).json()["data"]
    issue_resp = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={
            "project_id": project["id"],
            "title": "cov issue",
            "reporter_id": str(creator_member),
        },
        headers=auth_owner,
    )
    assert issue_resp.status_code == 201, issue_resp.text
    issue = issue_resp.json()["data"]

    execution = await make_execution(
        session_factory,
        uuid.UUID(ws_id),
        uuid.UUID(agent_id),
        status="running",
        issue_id=uuid.UUID(issue["id"]),
    )
    approval_id = await _seed_pending_approval(
        session_factory, uuid.UUID(ws_id), creator_member, execution.id
    )

    async def member_obj(mid):
        async with session_factory() as session:
            member = await session.get(Member, mid)
            session.expunge(member)
        return member

    # Unrelated member → 403.
    with pytest.raises(ForbiddenError):
        await decide_approval(
            session_factory,
            approval_id=approval_id,
            workspace_id=uuid.UUID(ws_id),
            member=await member_obj(stranger_member),
            approve=True,
        )
    # Issue creator (dispatcher) → allowed.
    data = await decide_approval(
        session_factory,
        approval_id=approval_id,
        workspace_id=uuid.UUID(ws_id),
        member=await member_obj(creator_member),
        approve=False,
        comment="nope",
    )
    assert data["status"] == "rejected"


# ---------------------------------------------------------------------------
# routes: labels filter (H3) + approvals list status filter (F9)
# ---------------------------------------------------------------------------


async def test_labels_filter_and_malformed(app_client_factory):
    app_client, token, ws_id, _agent = await app_client_factory("covlbl")
    auth = {"Authorization": f"Bearer {token}"}
    created = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes",
        json={"name": "lbl-rt", "kind": "self_hosted", "labels": {"gpu": "true", "zone": "a"}},
        headers=auth,
    )
    assert created.status_code == 201
    # Containment match.
    hit = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes?labels=gpu:true", headers=auth
    )
    assert [r["name"] for r in hit.json()["data"]] == ["lbl-rt"]
    # Multi-key match.
    hit2 = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes?labels=gpu:true,zone:a", headers=auth
    )
    assert len(hit2.json()["data"]) == 1
    # Mismatch → empty.
    miss = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes?labels=gpu:false", headers=auth
    )
    assert miss.json()["data"] == []
    # Malformed → 400.
    bad = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes?labels=novalue", headers=auth
    )
    assert bad.status_code == 400


async def test_approvals_list_status_filter(app_client_factory):
    app_client, token, ws_id, _agent = await app_client_factory("covappr")
    auth = {"Authorization": f"Bearer {token}"}
    mine = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/approvals?role=mine", headers=auth
    )
    assert mine.json()["data"] == []
    approved = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/approvals?status=approved", headers=auth
    )
    assert approved.status_code == 200
    assert approved.json()["data"] == []


# ---------------------------------------------------------------------------
# daemon refetch cap → freeze (route path)
# ---------------------------------------------------------------------------


async def test_refetch_cap_freezes_execution(app_client_factory, session_factory):
    app_client, token, ws_id, agent_id = await app_client_factory("covcap")
    auth = {"Authorization": f"Bearer {token}"}
    cred = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/credentials",
        json={"name": "CAP_KEY", "kind": "env", "value": "cap-value-1", "env_name": "CAP_KEY"},
        headers=auth,
    )
    cred_id = cred.json()["data"]["id"]
    rt = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes",
        json={"name": "cap-rt", "kind": "self_hosted", "labels": {}, "max_concurrent": 1},
        headers=auth,
    )
    activated = await app_client.post(
        "/api/v1/daemon/runtimes:activate",
        json={
            "activation_code": rt.json()["data"]["activation"]["code"],
            "metadata": {"capabilities": ["python"]},
        },
    )
    daemon_token = activated.json()["data"]["runtime_token"]
    dh = {"Authorization": f"Bearer {daemon_token}"}
    rid = rt.json()["data"]["id"]

    from tests.unit.test_runtime_routes import _enqueue_direct

    await _enqueue_direct(
        session_factory, ws_id, agent_id, task_spec={"credential_ids": [cred_id]}
    )
    claimed = await app_client.post(
        f"/api/v1/daemon/runtimes/{rid}/executions:claim", json={}, headers=dh
    )
    attempt_id = claimed.json()["data"]["attempt"]["id"]

    # Three refetches succeed (cap = 3)...
    for _ in range(3):
        ok = await app_client.post(
            f"/api/v1/daemon/attempts/{attempt_id}/credentials:refetch",
            json={"lease_seq": 1},
            headers=dh,
        )
        assert ok.status_code == 200, ok.text
    # ...the fourth exceeds the cap → freeze + 409 credential_refetch_limit.
    over = await app_client.post(
        f"/api/v1/daemon/attempts/{attempt_id}/credentials:refetch",
        json={"lease_seq": 1},
        headers=dh,
    )
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "credential_refetch_limit"
    # Freeze revoked the envelopes: next refetch sees envelope_revoked.
    after = await app_client.post(
        f"/api/v1/daemon/attempts/{attempt_id}/credentials:refetch",
        json={"lease_seq": 1},
        headers=dh,
    )
    assert after.status_code == 409
    assert after.json()["error"]["code"] == "envelope_revoked"


# ---------------------------------------------------------------------------
# logs robustness branches + checkout update paths
# ---------------------------------------------------------------------------


async def test_log_read_skips_corrupt_segment_and_respects_max_lines(
    session_factory, object_storage
):
    from mesh.runtime.logs import append_log_lines, read_execution_logs

    world = await seed_world(session_factory)
    runtime, result = await _claim_one(session_factory, world)
    attempt_id = uuid.UUID(result.attempt["id"])
    execution_id = uuid.UUID(result.execution["id"])

    first = await append_log_lines(
        session_factory,
        object_storage,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        stream="stdout",
        start_offset=0,
        lines=["one", "two", "three"],
        signing_secret=TEST_JWT_SECRET,
    )
    # Corrupt segment after the good one: the reader must skip it.
    bad_ref = f"logs/{world['ws_id']}/{attempt_id.hex}/corrupt.json"
    await object_storage.put_bytes(bad_ref, b"\xff\xfe not json", content_type="text/plain")
    async with session_factory() as session, session.begin():
        session.add(
            TaskLogSegment(
                workspace_id=world["ws_id"],
                attempt_id=attempt_id,
                start_offset=first["accepted_end_offset"],
                end_offset=first["accepted_end_offset"] + 10,
                storage_ref=bad_ref,
                line_count=1,
                sealed=True,
            )
        )
    page = await read_execution_logs(
        session_factory,
        object_storage,
        workspace_id=world["ws_id"],
        execution_id=execution_id,
        offset=0,
    )
    assert [item["line"] for item in page["lines"]] == ["one", "two", "three"]
    limited = await read_execution_logs(
        session_factory,
        object_storage,
        workspace_id=world["ws_id"],
        execution_id=execution_id,
        offset=0,
        max_lines=2,
    )
    assert len(limited["lines"]) == 2
    assert limited["next_offset"] == len("one\n") + len("two\n")


async def test_checkout_update_paths_and_no_storage(session_factory):
    from sqlalchemy import update

    from mesh.db.models.workspace import Workspace
    from mesh.runtime.checkout import report_checkout

    world = await seed_world(session_factory)
    repo = "https://code.example/team/upd.git"
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Workspace)
            .where(Workspace.id == world["ws_id"])
            .values(settings={"allowed_repos": [repo]})
        )
    runtime, result = await _claim_one(session_factory, world)
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE task_executions SET config_snapshot = :s WHERE id = :e"),
            {
                "s": json.dumps({"repo": {"url": repo, "base_ref": "main"}}),
                "e": uuid.UUID(result.execution["id"]),
            },
        )
    attempt_id = uuid.UUID(result.attempt["id"])
    await report_checkout(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        status="cloning",
        repo_url=repo,
        storage=None,
    )
    data = await report_checkout(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        status="ready",
        repo_url=repo,
        commit_sha="c0ffee1",
        local_path="/tmp/wt",
        storage=None,
    )
    assert data["status"] == "ready"
    nodiff = await report_checkout(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        status="diff_ready",
        repo_url=repo,
        diff="+1 -0",
        storage=None,
    )
    assert nodiff["diff_ref"] is None
    rec = await report_checkout(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        status="recycled",
        repo_url=repo,
        storage=None,
    )
    assert rec["status"] == "recycled"


# ---------------------------------------------------------------------------
# reaper approval-expiry edges
# ---------------------------------------------------------------------------


async def test_reaper_expires_approval_without_execution_and_non_awaiting(session_factory):
    from mesh.runtime.reaper import run_reaper_pass

    world = await seed_world(session_factory)
    # (a) autopilot_action subject: no execution link at all.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO approvals (id, workspace_id, subject_type, subject_run_id, "
                "requested_by_member_id, action_summary, expires_at, status) "
                "VALUES (:a, :ws, 'autopilot_action', :r, :m, '{}'::jsonb, "
                "now() - interval '1 minute', 'pending')"
            ),
            {"a": uuid.uuid4(), "ws": world["ws_id"], "r": uuid.uuid4(), "m": world["member_id"]},
        )
    # (b) tool_call on an execution that is NOT awaiting_approval (moved on).
    execution = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="running"
    )
    await _seed_pending_approval(
        session_factory, world["ws_id"], world["member_id"], execution.id, expires_past=True
    )

    counts = await run_reaper_pass(session_factory)
    assert counts["approvals_expired"] == 2
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution.id)
        approvals = (await session.execute(select(Approval))).scalars().all()
    assert stored.status == "running"  # never awaited → not cancelled
    assert {a.status for a in approvals} == {"expired"}


# ---------------------------------------------------------------------------
# service: heartbeat inflight validation + credential expiry
# ---------------------------------------------------------------------------


async def test_heartbeat_rejects_malformed_inflight(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], created_by=world["member_id"])
    with pytest.raises(ValidationError) as exc:
        await service.heartbeat(
            runtime=runtime,
            current_load=0,
            health="healthy",
            metrics={},
            inflight=["not-a-uuid"],
        )
    assert exc.value.code == "invalid_request"


async def test_create_credential_with_expiry(session_factory):
    world = await seed_world(session_factory)
    service = _service(session_factory)
    data = await service.create_credential(
        workspace_id=world["ws_id"],
        name="EXP_KEY",
        kind="env",
        scope="execution",
        value="v",
        env_name="EXP_KEY",
        redact_in_logs=True,
        expires_in_seconds=3600,
    )
    assert data["expires_at"] is not None
    with pytest.raises(BusinessRuleError):
        await service.create_credential(
            workspace_id=world["ws_id"],
            name="BAD",
            kind="env",
            scope="execution",
            value="v",
            env_name="PYTHONPATH",
            redact_in_logs=True,
            expires_in_seconds=None,
        )


# ---------------------------------------------------------------------------
# request_tool_approval error paths (approvals.py branch coverage)
# ---------------------------------------------------------------------------


async def test_request_approval_error_paths(session_factory):
    from mesh.errors import NotFoundError as NFE
    from mesh.runtime.approvals import request_tool_approval
    from mesh.runtime.attempts import transition_attempt

    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], max_concurrent=2)
    from mesh.db.models.agent import Agent

    async with session_factory() as session:
        agent_id = (
            await session.execute(
                select(Agent.id).where(Agent.workspace_id == world["ws_id"]).limit(1)
            )
        ).scalar_one()
    exec_a = await make_execution(session_factory, world["ws_id"], agent_id)
    exec_b = await make_execution(session_factory, world["ws_id"], agent_id)
    result_a = await claim_execution(
        session_factory, runtime=runtime, lease_seconds=120,
        signing_secret=TEST_JWT_SECRET, envelope_ttl=timedelta(hours=2),
    )
    result_b = await claim_execution(
        session_factory, runtime=runtime, lease_seconds=120,
        signing_secret=TEST_JWT_SECRET, envelope_ttl=timedelta(hours=2),
    )
    assert result_a is not None and result_b is not None
    attempt_a = uuid.UUID(result_a.attempt["id"])
    attempt_b = uuid.UUID(result_b.attempt["id"])

    # (1) Unknown execution → 404.
    with pytest.raises(NFE):
        await request_tool_approval(
            session_factory, execution_id=uuid.uuid4(), runtime=runtime,
            attempt_id=attempt_a, lease_seq=1, action_summary={}, resume_context={},
            approval_ttl=timedelta(hours=1),
        )

    # Bring A to running (execution follows).
    await transition_attempt(
        session_factory, attempt_id=attempt_a, runtime=runtime, lease_seq=1,
        new_status="running",
    )

    # (2) Attempt belongs to a DIFFERENT execution → 422.
    with pytest.raises(BusinessRuleError) as exc:
        await request_tool_approval(
            session_factory, execution_id=exec_a.id, runtime=runtime,
            attempt_id=attempt_b, lease_seq=1, action_summary={}, resume_context={},
            approval_ttl=timedelta(hours=1),
        )
    assert exc.value.code == "invalid_state_transition"

    # (3) Execution running but attempt NOT running → 422 (SQL forces the
    # split state the normal flow can never produce).
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE task_executions SET status = 'running' WHERE id = :e"),
            {"e": exec_b.id},
        )
    with pytest.raises(BusinessRuleError) as exc:
        await request_tool_approval(
            session_factory, execution_id=exec_b.id, runtime=runtime,
            attempt_id=attempt_b, lease_seq=1, action_summary={}, resume_context={},
            approval_ttl=timedelta(hours=1),
        )
    assert exc.value.code == "invalid_state_transition"

    # Happy request on A → pending approval.
    first = await request_tool_approval(
        session_factory, execution_id=exec_a.id, runtime=runtime,
        attempt_id=attempt_a, lease_seq=1, action_summary={"a": 1}, resume_context={},
        approval_ttl=timedelta(hours=1),
    )
    assert first["status"] == "pending"

    # (4) Re-request while pending (execution forced back to running) →
    # returns the EXISTING pending approval.
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE task_executions SET status = 'running' WHERE id = :e"),
            {"e": exec_a.id},
        )
        await session.execute(
            text(
                "UPDATE execution_attempts SET status = 'running' WHERE id = :a"
            ),
            {"a": attempt_a},
        )
    again = await request_tool_approval(
        session_factory, execution_id=exec_a.id, runtime=runtime,
        attempt_id=attempt_a, lease_seq=1, action_summary={"b": 2}, resume_context={},
        approval_ttl=timedelta(hours=1),
    )
    assert again["id"] == first["id"]

    # (5) No agent roster member → 422 approval_requester_missing.
    exec_c = await make_execution(session_factory, world["ws_id"], agent_id)
    result_c = await claim_execution(
        session_factory, runtime=runtime, lease_seconds=120,
        signing_secret=TEST_JWT_SECRET, envelope_ttl=timedelta(hours=2),
    )
    assert result_c is not None
    attempt_c = uuid.UUID(result_c.attempt["id"])
    await transition_attempt(
        session_factory, attempt_id=attempt_c, runtime=runtime, lease_seq=1,
        new_status="running",
    )
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE task_executions SET agent_id = NULL WHERE id = :e"),
            {"e": exec_c.id},
        )
    with pytest.raises(BusinessRuleError) as exc:
        await request_tool_approval(
            session_factory, execution_id=exec_c.id, runtime=runtime,
            attempt_id=attempt_c, lease_seq=1, action_summary={}, resume_context={},
            approval_ttl=timedelta(hours=1),
        )
    assert exc.value.code == "approval_requester_missing"


async def test_decide_unknown_approval_404(session_factory):
    from mesh.errors import NotFoundError
    from mesh.runtime.approvals import decide_approval

    world = await seed_world(session_factory)
    member = await _seed_member(session_factory, world, role="admin")
    with pytest.raises(NotFoundError):
        await decide_approval(
            session_factory,
            approval_id=uuid.uuid4(),
            workspace_id=world["ws_id"],
            member=member,
            approve=True,
        )
