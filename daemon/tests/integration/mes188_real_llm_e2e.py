"""MES-188 real daemon/provider acceptance wrapper.

The underlying MES-101 journey is the canonical production-shaped provider
test: public auth/API setup, real daemon activation, real namespace/cgroup
sandbox, pinned Claude Code binary, live model call, logs, usage and result
reflow.  This wrapper gives MES-188 its own evidence artifact and adds the
observability assertions needed by the agent/runtime UI increment without
copying that security-sensitive harness.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import real_llm_e2e

EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "evidence" / "mes-188" / "real-daemon-provider.json"
)


def _assert_mes188_contract(evidence: dict) -> None:
    result = evidence.get("result") or {}
    execution = evidence.get("execution") or {}
    usage = result.get("usage") or {}
    provider = result.get("provider") or {}
    assert evidence.get("verdict") == "PASS"
    assert execution.get("status") == "completed"
    assert evidence.get("marker_found") is True
    assert provider.get("name") == "claude-code"
    assert provider.get("version")
    assert provider.get("model")
    assert provider.get("session_recorded") is True
    assert usage.get("total_tokens", 0) > 0
    assert isinstance(usage.get("cost_usd"), str)
    assert evidence.get("log_excerpt")
    evidence["mes188_assertions"] = {
        "real_daemon": True,
        "real_provider": True,
        "pinned_provider_version": provider["version"],
        "attempt_terminal": execution["status"],
        "usage_reflowed": True,
        "logs_reflowed": True,
        "result_schema_version": result.get("schema_version"),
    }


if __name__ == "__main__":
    evidence = asyncio.run(real_llm_e2e.main())
    _assert_mes188_contract(evidence)
    secret = real_llm_e2e._load_api_key()
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2)
    if secret:
        rendered = rendered.replace(secret, "***")
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print(f"\nVERDICT: PASS — MES-188 evidence at {EVIDENCE_PATH}")
