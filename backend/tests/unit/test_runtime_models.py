"""Database-level runtime constraint tests (runtime.md §2, README §6.4/§6.10/§6.11).

Real PostgreSQL, raw SQL: the STRICT string-array scheduling fields (R3),
the capability-grants snapshot shape (R4), the per-execution attempt-number
uniqueness (audit chain, T4), the partial-unique idempotency backstop
(README §6.5), the approvals single-pending-per-subject index (README §6.10)
and the cross-tenant composite FK rejections (README §6.2) must all be
enforced by the database itself — service code is the first line, these
constraints the last.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.unit


async def _seed_world(db_session) -> dict:
    """Two workspaces + one agent/member per workspace."""
    ws1, ws2, user, m1, m2, a1, a2 = (uuid.uuid4() for _ in range(7))
    await db_session.execute(
        text(
            "INSERT INTO workspaces (id, name, slug) VALUES "
            "(:w1, 'WS1', :s1), (:w2, 'WS2', :s2)"
        ),
        {"w1": ws1, "s1": f"rt1-{ws1.hex[:8]}", "w2": ws2, "s2": f"rt2-{ws2.hex[:8]}"},
    )
    await db_session.execute(
        text("INSERT INTO users (id, email, display_name) VALUES (:u, :e, 'RT Owner')"),
        {"u": user, "e": f"rt-{user.hex[:8]}@corp.com"},
    )
    await db_session.execute(
        text(
            "INSERT INTO agents (id, workspace_id, name, owner_user_id) VALUES "
            "(:a1, :w1, 'Agent One', :u), (:a2, :w2, 'Agent Two', :u)"
        ),
        {"a1": a1, "a2": a2, "w1": ws1, "w2": ws2, "u": user},
    )
    await db_session.execute(
        text(
            "INSERT INTO members (id, workspace_id, member_type, user_id) VALUES "
            "(:m1, :w1, 'human', :u), (:m2, :w2, 'human', :u)"
        ),
        {"m1": m1, "m2": m2, "w1": ws1, "w2": ws2, "u": user},
    )
    await db_session.commit()
    return {"ws1": ws1, "ws2": ws2, "user": user, "m1": m1, "m2": m2, "a1": a1, "a2": a2}


async def _seed_runtime(db_session, *, runtime_id, ws, **overrides) -> None:
    cols = {"id": runtime_id, "ws": ws, "name": "build-01"}
    extra_cols, extra_vals = [], []
    for col, val in overrides.items():
        extra_cols.append(f", {col}")
        extra_vals.append(f", :{col}")
        cols[col] = val
    await db_session.execute(
        text(
            "INSERT INTO runtimes (id, workspace_id, name"
            + "".join(extra_cols)
            + ") VALUES (:id, :ws, :name"
            + "".join(extra_vals)
            + ")"
        ),
        cols,
    )
    await db_session.commit()


async def _seed_execution(db_session, *, execution_id, ws, agent=None, **overrides) -> None:
    cols = {"id": execution_id, "ws": ws, "agent": agent}
    extra_cols, extra_vals = [], []
    for col, val in overrides.items():
        extra_cols.append(f", {col}")
        extra_vals.append(f", :{col}")
        cols[col] = val
    await db_session.execute(
        text(
            "INSERT INTO task_executions (id, workspace_id, agent_id"
            + "".join(extra_cols)
            + ") VALUES (:id, :ws, :agent"
            + "".join(extra_vals)
            + ")"
        ),
        cols,
    )
    await db_session.commit()


async def _seed_attempt(
    db_session, *, attempt_id, ws, execution, number, runtime=None, **overrides
) -> None:
    cols = {"id": attempt_id, "ws": ws, "execution": execution, "number": number, "runtime": runtime}
    extra_cols, extra_vals = [], []
    for col, val in overrides.items():
        extra_cols.append(f", {col}")
        extra_vals.append(f", :{col}")
        cols[col] = val
    await db_session.execute(
        text(
            "INSERT INTO execution_attempts "
            "(id, workspace_id, execution_id, attempt_number, runtime_id"
            + "".join(extra_cols)
            + ") VALUES (:id, :ws, :execution, :number, :runtime"
            + "".join(extra_vals)
            + ")"
        ),
        cols,
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# runtimes: status / kind enums + strict string-array capabilities (R3)
# ---------------------------------------------------------------------------


async def test_runtime_valid_row_defaults(db_session):
    ids = await _seed_world(db_session)
    rt = uuid.uuid4()
    await _seed_runtime(db_session, runtime_id=rt, ws=ids["ws1"])
    row = (
        await db_session.execute(
            text(
                "SELECT status, kind, max_concurrent, current_load, "
                "capabilities, labels FROM runtimes WHERE id = :r"
            ),
            {"r": rt},
        )
    ).one()
    assert row.status == "pending"
    assert row.kind == "self_hosted"
    assert row.max_concurrent == 1
    assert row.current_load == 0
    assert row.capabilities == []
    assert row.labels == {}


async def test_runtime_invalid_status_rejected(db_session):
    ids = await _seed_world(db_session)
    with pytest.raises(IntegrityError):
        await _seed_runtime(
            db_session, runtime_id=uuid.uuid4(), ws=ids["ws1"], status="flying"
        )


async def test_runtime_non_string_capability_rejected_t28(db_session):
    """R3: capabilities must be a STRICT string array — an object entry would
    make the claim ``<@`` match miss forever, so the schema refuses it."""
    ids = await _seed_world(db_session)
    with pytest.raises(IntegrityError):
        await _seed_runtime(
            db_session,
            runtime_id=uuid.uuid4(),
            ws=ids["ws1"],
            capabilities='[{"capability": "gpu"}]' ,
        )


async def test_runtime_non_string_label_value_rejected(db_session):
    ids = await _seed_world(db_session)
    with pytest.raises(IntegrityError):
        await _seed_runtime(
            db_session,
            runtime_id=uuid.uuid4(),
            ws=ids["ws1"],
            labels='{"gpu": true}',
        )


# ---------------------------------------------------------------------------
# task_executions: strict scheduling fields + snapshot grants (R3/R4)
# ---------------------------------------------------------------------------


async def test_execution_required_capabilities_must_be_string_array_t28(db_session):
    ids = await _seed_world(db_session)
    with pytest.raises(IntegrityError):
        await _seed_execution(
            db_session,
            execution_id=uuid.uuid4(),
            ws=ids["ws1"],
            agent=ids["a1"],
            required_capabilities='[{"capability": "python", "permission": "write"}]',
        )


async def test_execution_required_capabilities_string_array_accepted(db_session):
    ids = await _seed_world(db_session)
    ex = uuid.uuid4()
    await _seed_execution(
        db_session,
        execution_id=ex,
        ws=ids["ws1"],
        agent=ids["a1"],
        required_capabilities='["python", "version_control"]',
    )
    row = (
        await db_session.execute(
            text("SELECT required_capabilities FROM task_executions WHERE id = :e"),
            {"e": ex},
        )
    ).one()
    assert row.required_capabilities == ["python", "version_control"]


async def test_execution_capability_grants_missing_permission_rejected_r4(db_session):
    """R4: snapshot grants REQUIRE a valid permission string."""
    ids = await _seed_world(db_session)
    with pytest.raises(IntegrityError):
        await _seed_execution(
            db_session,
            execution_id=uuid.uuid4(),
            ws=ids["ws1"],
            agent=ids["a1"],
            config_snapshot='{"capability_grants": [{"capability": "exec:shell"}]}',
        )


async def test_execution_capability_grants_invalid_permission_rejected_r4(db_session):
    ids = await _seed_world(db_session)
    with pytest.raises(IntegrityError):
        await _seed_execution(
            db_session,
            execution_id=uuid.uuid4(),
            ws=ids["ws1"],
            agent=ids["a1"],
            config_snapshot=(
                '{"capability_grants": [{"capability": "exec:shell", "permission": "sudo"}]}'
            ),
        )


async def test_execution_capability_grants_valid_shape_accepted(db_session):
    ids = await _seed_world(db_session)
    ex = uuid.uuid4()
    await _seed_execution(
        db_session,
        execution_id=ex,
        ws=ids["ws1"],
        agent=ids["a1"],
        config_snapshot=(
            '{"capability_grants": [{"capability": "exec:shell", '
            '"permission": "confirm_required"}]}'
        ),
    )
    row = (
        await db_session.execute(
            text("SELECT config_snapshot FROM task_executions WHERE id = :e"), {"e": ex}
        )
    ).one()
    assert row.config_snapshot["capability_grants"][0]["permission"] == "confirm_required"


async def test_execution_trigger_enum_includes_integration(db_session):
    ids = await _seed_world(db_session)
    ex = uuid.uuid4()
    await _seed_execution(
        db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"], trigger="integration"
    )
    with pytest.raises(IntegrityError):
        await _seed_execution(
            db_session,
            execution_id=uuid.uuid4(),
            ws=ids["ws1"],
            agent=ids["a1"],
            trigger="telepathy",
        )


async def test_execution_idempotency_key_unique_but_nullable(db_session):
    """README §6.5: the same trigger never enqueues twice; NULL never conflicts."""
    ids = await _seed_world(db_session)
    key = f"idem-{uuid.uuid4().hex}"
    await _seed_execution(
        db_session, execution_id=uuid.uuid4(), ws=ids["ws1"], agent=ids["a1"], idempotency_key=key
    )
    with pytest.raises(IntegrityError):
        await _seed_execution(
            db_session,
            execution_id=uuid.uuid4(),
            ws=ids["ws1"],
            agent=ids["a1"],
            idempotency_key=key,
        )
    await db_session.rollback()
    # Two NULLs are fine (partial index).
    await _seed_execution(db_session, execution_id=uuid.uuid4(), ws=ids["ws1"], agent=ids["a1"])
    await _seed_execution(db_session, execution_id=uuid.uuid4(), ws=ids["ws1"], agent=ids["a1"])


async def test_execution_cross_tenant_agent_rejected(db_session):
    """README §6.2: executor must live in the SAME workspace."""
    ids = await _seed_world(db_session)
    with pytest.raises(IntegrityError):
        await _seed_execution(
            db_session, execution_id=uuid.uuid4(), ws=ids["ws1"], agent=ids["a2"]
        )


# ---------------------------------------------------------------------------
# execution_attempts: audit chain uniqueness + tenant FKs
# ---------------------------------------------------------------------------


async def test_attempt_number_unique_per_execution_t4(db_session):
    """Requeue INSERTs attempt #N+1; reusing a number must fail (audit chain)."""
    ids = await _seed_world(db_session)
    rt = uuid.uuid4()
    await _seed_runtime(db_session, runtime_id=rt, ws=ids["ws1"], status="online")
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    await _seed_attempt(
        db_session, attempt_id=uuid.uuid4(), ws=ids["ws1"], execution=ex, number=1, runtime=rt
    )
    with pytest.raises(IntegrityError):
        await _seed_attempt(
            db_session, attempt_id=uuid.uuid4(), ws=ids["ws1"], execution=ex, number=1, runtime=rt
        )
    await db_session.rollback()
    await _seed_attempt(
        db_session, attempt_id=uuid.uuid4(), ws=ids["ws1"], execution=ex, number=2, runtime=rt
    )


async def test_attempt_status_enum_includes_cancelling_and_reclaimed(db_session):
    ids = await _seed_world(db_session)
    rt = uuid.uuid4()
    await _seed_runtime(db_session, runtime_id=rt, ws=ids["ws1"], status="online")
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    for i, status in enumerate(("cancelling", "reclaimed"), start=1):
        await _seed_attempt(
            db_session,
            attempt_id=uuid.uuid4(),
            ws=ids["ws1"],
            execution=ex,
            number=i,
            runtime=rt,
            status=status,
        )
    with pytest.raises(IntegrityError):
        await _seed_attempt(
            db_session,
            attempt_id=uuid.uuid4(),
            ws=ids["ws1"],
            execution=ex,
            number=3,
            runtime=rt,
            status="sleeping",
        )


async def test_attempt_cross_tenant_runtime_rejected(db_session):
    ids = await _seed_world(db_session)
    rt2 = uuid.uuid4()
    await _seed_runtime(db_session, runtime_id=rt2, ws=ids["ws2"], status="online")
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    with pytest.raises(IntegrityError):
        await _seed_attempt(
            db_session, attempt_id=uuid.uuid4(), ws=ids["ws1"], execution=ex, number=1, runtime=rt2
        )


# ---------------------------------------------------------------------------
# approvals: single pending per subject + subject-shape CHECK (README §6.10)
# ---------------------------------------------------------------------------


async def _seed_approval(
    db_session, *, approval_id, ws, execution, member, status="pending", **overrides
) -> None:
    cols = {
        "id": approval_id,
        "ws": ws,
        "execution": execution,
        "member": member,
        "status": status,
    }
    extra_cols, extra_vals = [], []
    for col, val in overrides.items():
        extra_cols.append(f", {col}")
        extra_vals.append(f", :{col}")
        cols[col] = val
    await db_session.execute(
        text(
            "INSERT INTO approvals (id, workspace_id, subject_type, subject_execution_id, "
            "requested_by_member_id, action_summary, expires_at, status"
            + "".join(extra_cols)
            + ") VALUES (:id, :ws, 'tool_call', :execution, :member, "
            "'{\"action\": \"exec:shell\"}'::jsonb, now() + interval '1 hour', :status"
            + "".join(extra_vals)
            + ")"
        ),
        cols,
    )
    await db_session.commit()


async def test_approvals_single_pending_per_execution(db_session):
    ids = await _seed_world(db_session)
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    await _seed_approval(
        db_session, approval_id=uuid.uuid4(), ws=ids["ws1"], execution=ex, member=ids["m1"]
    )
    # Second pending approval for the SAME subject is refused...
    with pytest.raises(IntegrityError):
        await _seed_approval(
            db_session, approval_id=uuid.uuid4(), ws=ids["ws1"], execution=ex, member=ids["m1"]
        )


async def test_approvals_new_pending_allowed_after_decision(db_session):
    ids = await _seed_world(db_session)
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    await _seed_approval(
        db_session,
        approval_id=uuid.uuid4(),
        ws=ids["ws1"],
        execution=ex,
        member=ids["m1"],
        status="approved",
        decided_by_member_id=ids["m1"],
        decided_at=datetime.now(timezone.utc),
    )
    # ...but after a decision a fresh pending approval may be raised.
    await _seed_approval(
        db_session, approval_id=uuid.uuid4(), ws=ids["ws1"], execution=ex, member=ids["m1"]
    )


async def test_approvals_subject_shape_enforced(db_session):
    """tool_call rows must carry ONLY subject_execution_id."""
    ids = await _seed_world(db_session)
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    with pytest.raises(IntegrityError):
        await _seed_approval(
            db_session,
            approval_id=uuid.uuid4(),
            ws=ids["ws1"],
            execution=ex,
            member=ids["m1"],
            subject_run_id=uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# credentials + execution_credentials composite tenant FKs
# ---------------------------------------------------------------------------


async def test_execution_credential_cross_tenant_credential_rejected(db_session):
    """README §6.2: an attempt can only be injected with SAME-workspace secrets."""
    ids = await _seed_world(db_session)
    rt = uuid.uuid4()
    await _seed_runtime(db_session, runtime_id=rt, ws=ids["ws1"], status="online")
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    att = uuid.uuid4()
    await _seed_attempt(
        db_session, attempt_id=att, ws=ids["ws1"], execution=ex, number=1, runtime=rt
    )
    foreign_cred = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO runtime_credentials (id, workspace_id, name, encrypted_value) "
            "VALUES (:c, :w2, 'foreign-secret', 'gAAAAA-fake')"
        ),
        {"c": foreign_cred, "w2": ids["ws2"]},
    )
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO execution_credentials "
                "(attempt_id, credential_id, workspace_id, envelope_ref) "
                "VALUES (:a, :c, :w1, 'env-1')"
            ),
            {"a": att, "c": foreign_cred, "w1": ids["ws1"]},
        )


async def test_log_segment_offset_unique_per_attempt(db_session):
    """Contiguous offsets are fine; overlapping start offsets are refused."""
    ids = await _seed_world(db_session)
    rt = uuid.uuid4()
    await _seed_runtime(db_session, runtime_id=rt, ws=ids["ws1"], status="online")
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    att = uuid.uuid4()
    await _seed_attempt(
        db_session, attempt_id=att, ws=ids["ws1"], execution=ex, number=1, runtime=rt
    )
    for start, end in ((0, 100), (100, 250)):
        await db_session.execute(
            text(
                "INSERT INTO task_log_segments "
                "(id, workspace_id, attempt_id, start_offset, end_offset, storage_ref) "
                "VALUES (:id, :w, :a, :s, :e, 'seg/1')"
            ),
            {"id": uuid.uuid4(), "w": ids["ws1"], "a": att, "s": start, "e": end},
        )
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO task_log_segments "
                "(id, workspace_id, attempt_id, start_offset, end_offset, storage_ref) "
                "VALUES (:id, :w, :a, 100, 300, 'seg/dup')"
            ),
            {"id": uuid.uuid4(), "w": ids["ws1"], "a": att},
        )


async def test_repo_checkout_one_per_attempt(db_session):
    ids = await _seed_world(db_session)
    rt = uuid.uuid4()
    await _seed_runtime(db_session, runtime_id=rt, ws=ids["ws1"], status="online")
    ex = uuid.uuid4()
    await _seed_execution(db_session, execution_id=ex, ws=ids["ws1"], agent=ids["a1"])
    att = uuid.uuid4()
    await _seed_attempt(
        db_session, attempt_id=att, ws=ids["ws1"], execution=ex, number=1, runtime=rt
    )
    await db_session.execute(
        text(
            "INSERT INTO repo_checkouts "
            "(id, workspace_id, attempt_id, repo_url, base_ref, working_branch) "
            "VALUES (:id, :w, :a, 'https://code.example/team/app.git', 'main', 'agent/x/a1')"
        ),
        {"id": uuid.uuid4(), "w": ids["ws1"], "a": att},
    )
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO repo_checkouts "
                "(id, workspace_id, attempt_id, repo_url, base_ref, working_branch) "
                "VALUES (:id, :w, :a, 'https://code.example/team/app.git', 'main', 'agent/x/a1')"
            ),
            {"id": uuid.uuid4(), "w": ids["ws1"], "a": att},
        )
