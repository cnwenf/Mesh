from __future__ import annotations

import json

from mesh_runtime.inventory import Inventory, ProviderStatus
from mesh_runtime.operational import OperationalGuard


def _inventory(*, available: bool = True, reason: str | None = None) -> Inventory:
    return Inventory(
        [
            ProviderStatus(
                name="claude-code",
                available=available,
                version="2.1.218" if available else None,
                binary_sha256="sha256" if available else None,
                capabilities=("coding_cli.claude",),
                reason=reason,
            )
        ]
    )


def test_security_probe_failure_starts_isolated_and_blocks_claim(tmp_path):
    guard = OperationalGuard(
        tmp_path / "operational-state.json",
        _inventory(available=False, reason="isolation fixture probe failed: hostile hook fired"),
    )

    state, diagnostics = guard.report()

    assert state == "isolated"
    assert guard.claim_allowed() is False
    assert diagnostics == [
        {
            "reason_code": "provider_isolation_failed",
            "missing_capabilities": [],
            "affected_task_types": ["all"],
        }
    ]


def test_runtime_incident_is_persisted_without_sensitive_detail(tmp_path):
    path = tmp_path / "operational-state.json"
    guard = OperationalGuard(path, _inventory())

    guard.isolate("cleanup_failed", detail="secret=/srv/private/token")

    assert guard.report()[0] == "isolated"
    assert guard.claim_allowed() is False
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"reason_codes": ["cleanup_failed"], "schema_version": 1}
    assert "secret" not in path.read_text(encoding="utf-8")
    assert path.stat().st_mode & 0o777 == 0o600

    restarted = OperationalGuard(path, _inventory())
    assert restarted.report()[0] == "isolated"
    assert restarted.claim_allowed() is False


def test_corrupt_persisted_state_fails_closed(tmp_path):
    path = tmp_path / "operational-state.json"
    path.write_text("not-json", encoding="utf-8")

    guard = OperationalGuard(path, _inventory())

    assert guard.report()[0] == "isolated"
    assert guard.claim_allowed() is False


def test_recovery_requires_verified_checks_and_no_inflight(tmp_path):
    path = tmp_path / "operational-state.json"
    guard = OperationalGuard(path, _inventory())
    guard.isolate("usage_invariant_failed")

    assert guard.recover_after_checks(checks_ok=False, inflight=0) is False
    assert guard.recover_after_checks(checks_ok=True, inflight=1) is False
    assert path.exists()

    assert guard.recover_after_checks(checks_ok=True, inflight=0) is True
    assert guard.report() == ("online", [])
    assert guard.claim_allowed() is True
    assert not path.exists()


def test_degraded_inventory_is_not_misreported_as_isolated(tmp_path):
    guard = OperationalGuard(
        tmp_path / "operational-state.json",
        _inventory(available=False, reason="binary missing at /srv/private/provider"),
    )

    state, diagnostics = guard.report()

    assert state == "degraded"
    assert guard.claim_allowed() is False
    assert diagnostics[0]["reason_code"] == "provider_unavailable"
    assert "/srv/private" not in str(diagnostics)
