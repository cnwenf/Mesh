"""MES-96 P2-2 — terminal result contract: server-side schema v1 enforcement.

runtime-executor.md §3.9 / runtime.md §2.6: the daemon ``result`` is a frozen
schema v1 (decimal-string money, non-negative integer tokens/turns, fixed
termination vocabulary) capped at 64 KiB. "The server 422s anything else" — a
contract that was never implemented server-side (``cost_usd:"nan"`` slipped past
``float()`` into ``Numeric(16,6)`` and 500'd; oversized / non-conforming results
were accepted and even stamped ``result_schema_version``).

These regressions drive the real daemon PATCH path (``transition_attempt``) and
the request schema (``AttemptTransitionRequest``); every invalid case must 422
(``BusinessRuleError`` / pydantic ``ValidationError``), and a conforming result
must persist with an honest stamp and an exact ``Decimal`` cost.
"""

from __future__ import annotations

import copy
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from mesh.db.models.agent import Agent
from mesh.db.models.runtime import ExecutionAttempt
from mesh.errors import BusinessRuleError
from mesh.runtime.attempts import transition_attempt
from mesh.runtime.claim import claim_execution
from mesh.runtime.result_schema import validate_result_schema
from mesh.runtime.schemas import MAX_JSON_FIELD_BYTES, AttemptTransitionRequest, HeartbeatRequest
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    make_execution,
    make_runtime,
    seed_world,
    valid_result_v1,
)

pytestmark = pytest.mark.unit


def test_heartbeat_operational_diagnostics_are_structured_and_fail_closed():
    request = HeartbeatRequest(
        current_load=0,
        health="degraded",
        operational_state="degraded",
        diagnostics=[
            {
                "reason_code": "provider_unavailable",
                "missing_capabilities": ["python", "version_control"],
                "affected_task_types": ["provider:primary"],
            }
        ],
    )
    assert request.operational_state == "degraded"
    assert request.diagnostics[0].reason_code == "provider_unavailable"

    # Human-authored/raw failure strings, filesystem paths and arbitrary
    # reason codes never enter the persisted/API diagnostic channel.
    with pytest.raises(ValidationError):
        HeartbeatRequest(
            health="degraded",
            operational_state="degraded",
            diagnostics=[
                {
                    "reason_code": "provider_unavailable",
                    "missing_capabilities": ["/srv/private/provider"],
                    "affected_task_types": ["provider:primary"],
                    "detail": "token=secret",
                }
            ],
        )
    with pytest.raises(ValidationError):
        HeartbeatRequest(
            health="degraded",
            operational_state="isolated",
            diagnostics=[{"reason_code": "arbitrary_daemon_text"}],
        )

    # A daemon may not claim Online while simultaneously reporting degraded.
    with pytest.raises(ValidationError):
        HeartbeatRequest(health="degraded", operational_state="online")


@pytest.mark.parametrize(
    "reason_code",
    [
        "cleanup_failed",
        "provider_isolation_failed",
        "runtime_auth_failed",
        "sandbox_security_failed",
        "usage_invariant_failed",
    ],
)
def test_heartbeat_accepts_every_daemon_isolation_reason(reason_code):
    """The daemon's persisted safety latch must cross the heartbeat boundary."""
    request = HeartbeatRequest(
        health="degraded",
        operational_state="isolated",
        diagnostics=[
            {
                "reason_code": reason_code,
                "missing_capabilities": [],
                "affected_task_types": ["all"],
            }
        ],
    )

    assert request.diagnostics[0].reason_code == reason_code


async def _complete_with_result(session_factory, result):
    """Drive the real daemon path: claim → running → completed(result)."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    async with session_factory() as session:
        agent_id = (
            await session.execute(
                select(Agent.id).where(Agent.workspace_id == world["ws_id"]).limit(1)
            )
        ).scalar_one()
    await make_execution(session_factory, world["ws_id"], agent_id)
    claim = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert claim is not None
    attempt_id = uuid.UUID(claim.attempt["id"])
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    return await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="completed",
        result=result,
    )


async def _assert_rejects(session_factory, result, *, field):
    """A non-conforming result 422s naming the offending field."""
    with pytest.raises(BusinessRuleError) as exc:
        await _complete_with_result(session_factory, result)
    assert exc.value.code == "invalid_result_schema"
    assert exc.value.details["field"] == field
    # Nothing was persisted — the attempt never went terminal on a bad result.
    async with session_factory() as session:
        attempt = (await session.execute(select(ExecutionAttempt))).scalar_one()
    assert attempt.status == "running"
    assert attempt.result is None
    assert attempt.result_schema_version is None


# --- size cap (§2.6 / L3): the only transition field that lacked the guard ---


def test_result_over_64kib_rejected_at_request_schema():
    oversized = valid_result_v1(summary="x" * (MAX_JSON_FIELD_BYTES + 1))
    with pytest.raises(ValidationError):
        AttemptTransitionRequest(lease_seq=1, status="completed", result=oversized)


def test_result_under_cap_passes_request_schema():
    ok = AttemptTransitionRequest(lease_seq=1, status="completed", result=valid_result_v1())
    assert ok.result is not None
    # None is a legal result (a terminal report without structured output).
    assert AttemptTransitionRequest(lease_seq=1, status="failed", result=None).result is None


# --- schema v1 strict validation (runtime-executor.md §3.9) ---


async def test_cost_usd_nan_rejected(session_factory):
    bad = valid_result_v1(cost_usd="nan")
    await _assert_rejects(session_factory, bad, field="usage.cost_usd")


async def test_cost_usd_inf_rejected(session_factory):
    bad = valid_result_v1(cost_usd="inf")
    await _assert_rejects(session_factory, bad, field="usage.cost_usd")


async def test_cost_usd_float_rejected(session_factory):
    bad = valid_result_v1()
    bad["usage"]["cost_usd"] = 0.144512  # a float, not a decimal string
    await _assert_rejects(session_factory, bad, field="usage.cost_usd")


async def test_cost_usd_negative_sign_rejected(session_factory):
    bad = valid_result_v1(cost_usd="-0.5")
    await _assert_rejects(session_factory, bad, field="usage.cost_usd")


async def test_bool_token_rejected(session_factory):
    bad = valid_result_v1()
    bad["usage"]["input_tokens"] = True  # bool is an int subclass → reject
    await _assert_rejects(session_factory, bad, field="usage.input_tokens")


async def test_total_tokens_mismatch_rejected(session_factory):
    bad = valid_result_v1(input_tokens=10, output_tokens=5)
    bad["usage"]["total_tokens"] = 999  # != 10 + 5
    await _assert_rejects(session_factory, bad, field="usage.total_tokens")


async def test_unknown_termination_rejected(session_factory):
    bad = valid_result_v1(termination="exploded")
    await _assert_rejects(session_factory, bad, field="outcome.termination")


async def test_missing_schema_version_rejected(session_factory):
    bad = valid_result_v1()
    del bad["schema_version"]
    await _assert_rejects(session_factory, bad, field="schema_version")


async def test_empty_result_dict_rejected(session_factory):
    await _assert_rejects(session_factory, {}, field="schema_version")


async def test_valid_result_persisted_stamped_and_decimal_cost(session_factory):
    result = valid_result_v1(
        cost_usd="0.144512",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
        cache_creation_tokens=5,
        turns=3,
        session_id="sess-abc",
    )
    response = await _complete_with_result(session_factory, result)
    assert response["status"] == "completed"
    async with session_factory() as session:
        attempt = (await session.execute(select(ExecutionAttempt))).scalar_one()
    # The stamp is honest: only a validated result carries schema version 1.
    assert attempt.result_schema_version == 1
    # Decimal round-trips exactly — no float noise on the Numeric(16,6) column.
    assert attempt.cost_usd == Decimal("0.144512")
    assert isinstance(attempt.cost_usd, Decimal)
    assert attempt.prompt_tokens == 100
    assert attempt.completion_tokens == 50
    assert attempt.cache_tokens == 15
    assert attempt.num_turns == 3
    assert attempt.provider == "test-provider"
    assert attempt.model == "test-model"
    assert attempt.provider_session_id == "sess-abc"
    assert attempt.result["schema_version"] == 1


# --- direct rule-matrix coverage of the validator's remaining branches ---


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda r: r.update({"schema_version": True}), "schema_version"),  # bool version
        (lambda r: r.update({"schema_version": 2}), "schema_version"),
        (lambda r: r.pop("provider"), "provider"),
        (lambda r: r["provider"].update({"name": 7}), "provider.name"),
        (lambda r: r["provider"].update({"version": None}), "provider.version"),
        (lambda r: r["provider"].update({"model": ["m"]}), "provider.model"),
        (lambda r: r["provider"].update({"session_id": 5}), "provider.session_id"),
        (lambda r: r.pop("usage"), "usage"),
        (lambda r: r["usage"].update({"turns": -1}), "usage.turns"),
        (lambda r: r["usage"].update({"total_tokens": "x"}), "usage.total_tokens"),
        (lambda r: r["usage"].update({"cost_usd": ""}), "usage.cost_usd"),
        (lambda r: r["usage"].update({"cost_usd": "1.2.3"}), "usage.cost_usd"),
        (lambda r: r.pop("outcome"), "outcome"),
        (lambda r: r["outcome"].update({"exit_code": True}), "outcome.exit_code"),
        (lambda r: r["outcome"].update({"exit_code": -1}), "outcome.exit_code"),
        (lambda r: r["outcome"].update({"summary": 3}), "outcome.summary"),
        (lambda r: r.update({"artifacts": ["x"]}), "artifacts"),
        (lambda r: r.update({"redaction": "none"}), "redaction"),
    ],
)
def test_validate_result_schema_rule_matrix(mutate, field):
    result = valid_result_v1()
    mutate(result)
    with pytest.raises(BusinessRuleError) as exc:
        validate_result_schema(result)
    assert exc.value.code == "invalid_result_schema"
    assert exc.value.details["field"] == field


def test_validate_result_schema_rejects_non_dict():
    with pytest.raises(BusinessRuleError) as exc:
        validate_result_schema(["not", "a", "dict"])  # type: ignore[arg-type]
    assert exc.value.details["field"] == "result"


def test_validate_result_schema_accepts_optional_sections_absent():
    # artifacts / redaction are optional server-side.
    minimal = valid_result_v1()
    minimal = copy.deepcopy(minimal)
    minimal.pop("artifacts")
    minimal.pop("redaction")
    validate_result_schema(minimal)  # no raise
