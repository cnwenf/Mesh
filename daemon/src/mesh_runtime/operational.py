"""Daemon-local operational safety gate.

An ``isolated`` runtime must stop claiming locally; relying only on the
server-side heartbeat projection leaves a race where another claim can be
requested before the heartbeat is processed.  Safety incidents are persisted
as fixed reason codes (never exception text) and survive daemon restarts.

Recovery is deliberately narrow: a fresh process must complete startup
reconciliation, pass the complete doctor check set, and have no in-flight
attempt before it may remove the isolation marker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mesh_runtime.inventory import Inventory

_SCHEMA_VERSION = 1
_SECURITY_PROBE_PREFIX = "isolation fixture probe failed:"
_SAFE_REASON_CODES = frozenset(
    {
        "cleanup_failed",
        "provider_isolation_failed",
        "runtime_auth_failed",
        "sandbox_security_failed",
        "usage_invariant_failed",
    }
)


class OperationalGuard:
    """Shared heartbeat/claim gate for one daemon process."""

    def __init__(self, state_path: Path, inventory: Inventory) -> None:
        self._path = Path(state_path)
        self._inventory = inventory
        self._reason_codes = self._load()
        if self._inventory.security_isolation_failed():
            self.isolate("provider_isolation_failed")

    def isolate(self, reason_code: str, *, detail: str | None = None) -> None:
        """Latch isolation using a fixed safe code.

        ``detail`` is accepted so callers can log locally, but is intentionally
        never written to disk or sent in heartbeat diagnostics.
        """
        del detail
        safe_code = reason_code if reason_code in _SAFE_REASON_CODES else "sandbox_security_failed"
        if safe_code in self._reason_codes:
            return
        self._reason_codes.add(safe_code)
        self._persist()

    def claim_allowed(self) -> bool:
        return not self._reason_codes and self._inventory.healthy()

    @property
    def isolated(self) -> bool:
        return bool(self._reason_codes)

    def report(self) -> tuple[str, list[dict]]:
        if self._reason_codes:
            diagnostics = [
                {
                    "reason_code": code,
                    "missing_capabilities": [],
                    "affected_task_types": ["all"],
                }
                for code in sorted(self._reason_codes)
            ]
            return "isolated", diagnostics
        if not self._inventory.healthy():
            return "degraded", self._inventory.operational_diagnostics()
        return "online", []

    def recover_after_checks(self, *, checks_ok: bool, inflight: int) -> bool:
        """Clear a latched incident only after a clean, idle startup audit."""
        if not self._reason_codes:
            return True
        if not checks_ok or inflight != 0 or not self._inventory.healthy():
            return False
        self._reason_codes.clear()
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        return True

    def _load(self) -> set[str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return set()
        except OSError:
            return {"sandbox_security_failed"}
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return {"sandbox_security_failed"}
        if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
            return {"sandbox_security_failed"}
        codes = payload.get("reason_codes")
        if not isinstance(codes, list):
            return {"sandbox_security_failed"}
        safe = {code for code in codes if isinstance(code, str) and code in _SAFE_REASON_CODES}
        return safe or ({"sandbox_security_failed"} if codes else set())

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {
            "reason_codes": sorted(self._reason_codes),
            "schema_version": _SCHEMA_VERSION,
        }
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
