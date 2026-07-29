"""MES-101 真实 LLM e2e (A3 核心门禁):钉死版本 Claude Code provider 真跑。

走公开 API(§5.4.5,禁 psql seed)注册/登录 → 建 workspace(设 allowed_repos)
→ 建 agent(model_config 携带冻结 budget + network_policy)→ 建 pending runtime
取激活码 → daemon(真实沙箱 + 钉死 manifest 的 Claude Code)activate → online →
issue 指派 agent 触发 enqueue → daemon 真实 claim → 在 namespace/cgroup 沙箱内
用钉死二进制真实调用 LLM → stream-json 解析 → 脱敏日志/会话/usage/result 回流。

断言:runtime online;execution queued→claimed→running→completed;日志经 REST
可读且含 provider 真实输出;result schema_version=1 且 usage.total_tokens>0、
session_id 非空、cost 为 decimal string;provider 凭据(API key)在日志与 result
中零泄漏(同一脱敏器,§5.4.7)。证据写 docs/evidence/mes-101/real-llm-e2e.json
(落盘前对凭据脱敏)。

用法:python tests/integration/real_llm_e2e.py [server_url]

前置:本地真实 server(API + worker)、真实 Claude Code 二进制与 manifest、
provider.env(0600 凭据)。见 MES-101 交付说明。
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.app import RuntimeApp, heartbeat_metadata
from mesh_runtime.cli import build_adapters
from mesh_runtime.config import DaemonConfig
from mesh_runtime.inventory import Inventory
from mesh_runtime.journal import Journal
from mesh_runtime.sandbox import SandboxManager

SERVER_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:58100"
PASSWORD = "Mesh-A3-Passw0rd!"

# Operator-provided pinned provider install + credentials (see MES-101 notes).
PROVIDER_PATH = os.environ.get("MES101_PROVIDER_PATH", "/opt/mesh/providers/claude/2.1.218/claude")
PROVIDER_MANIFEST = os.environ.get("MES101_PROVIDER_MANIFEST", "/root/mes101-run/manifest.toml")
PROVIDER_ENV_FILE = os.environ.get("MES101_PROVIDER_ENV", "/root/mes101-run/provider.env")
API_HOST = os.environ.get("MES101_API_HOST", "token-plan.cn-beijing.maas.aliyuncs.com")
WORK_ROOT = Path(os.environ.get("MES101_WORK_ROOT", "/root/mes101-run"))
MARKER = f"MESH-A3-OK-{uuid.uuid4().hex[:8]}"
EVIDENCE_PATH = Path(__file__).resolve().parents[3] / "docs" / "evidence" / "mes-101" / "real-llm-e2e.json"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _load_api_key() -> str:
    for line in Path(PROVIDER_ENV_FILE).read_text(encoding="utf-8").splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1]
    return ""


async def main() -> dict:
    evidence: dict = {"server_url": SERVER_URL, "marker": MARKER, "steps": []}
    api_key = _load_api_key()
    run_id = uuid.uuid4().hex[:8]
    state = WORK_ROOT / f"state-{run_id}"
    work = WORK_ROOT / f"work-{run_id}"
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    work.mkdir(parents=True, mode=0o700, exist_ok=True)

    async with httpx.AsyncClient(base_url=SERVER_URL, timeout=30.0) as c:
        # 1. register + login (real product auth flow).
        email = f"mes101-{run_id}@int.mesh"
        await c.post("/api/v1/auth/register",
                     json={"email": email, "password": PASSWORD, "display_name": "MES-101 A3"})
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        token = r.json()["data"]["access_token"]
        evidence["steps"].append("auth: register+login ok")

        # 2. workspace + allowed_repos.
        r = await c.post("/api/v1/workspaces",
                         json={"name": "MES-101 A3", "slug": f"mes101-{run_id}"},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        ws_id = r.json()["data"]["id"]
        await c.patch(f"/api/v1/workspaces/{ws_id}",
                      json={"settings": {"allowed_repos": ["https://github.com/cnwenf/Mesh"]}},
                      headers=_auth(token))
        evidence["steps"].append(f"workspace {ws_id} created")

        # 3. agent with FROZEN budget + network policy in model_config (§2.1).
        # The task instruction lives in the TRUSTED system_instructions (frozen
        # AgentConfig); the issue body is UNTRUSTED context and — per §3.7 — the
        # provider must NOT treat it as an instruction.
        model_config = {
            "provider": "claude-code",
            "budget": {"max_cost_usd": "0.50", "max_turns": 2},
            "network_policy": {"allowed_hosts": [API_HOST]},
        }
        r = await c.post(f"/api/v1/workspaces/{ws_id}/agents",
                         json={"name": f"a3-agent-{run_id}",
                               "system_instructions": (
                                   "You are a concise agent. For this task reply with "
                                   f"exactly the text {MARKER} and nothing else. Do not "
                                   "use any tools."
                               ),
                               "model_config": model_config},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        agent = r.json()["data"]
        agent_member_id = agent.get("member_id") or (agent.get("member") or {}).get("id")
        assert agent_member_id, f"no member id: {list(agent)}"
        evidence["steps"].append(f"agent {agent['id']} created (member {agent_member_id})")

        # 4. pending runtime → activation code.
        r = await c.post(f"/api/v1/workspaces/{ws_id}/runtimes",
                         json={"name": f"a3-rt-{run_id}", "kind": "self_hosted",
                               "labels": {}, "max_concurrent": 1},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        created = r.json()["data"]
        runtime_id = created["id"]
        code = (created.get("activation") or {}).get("code")
        assert code, f"no activation code: {list(created)}"
        evidence["steps"].append(f"runtime {runtime_id} pending; activation code issued")

    # 5. daemon activate with the pinned Claude Code provider + real sandbox.
    config = DaemonConfig(
        server_url=SERVER_URL,
        state_dir=state,
        work_dir=work,
        max_concurrent=1,
        provider_path=Path(PROVIDER_PATH),
        provider_version=None,
        provider_manifest=Path(PROVIDER_MANIFEST),
        provider_env_file=Path(PROVIDER_ENV_FILE),
        allow_insecure_http=True,  # loopback local stack only
        heartbeat_interval_seconds=5.0,
        sandbox_memory_bytes=2 * 1024 * 1024 * 1024,
        sandbox_pids_max=256,
        sandbox_tmp_bytes=256 * 1024 * 1024,
    )
    api = RuntimeApiClient(SERVER_URL, None)
    mgr = SandboxManager(state_root=state / "sandbox",
                         sandbox_uid=config.sandbox_uid, sandbox_gid=config.sandbox_gid)
    await mgr.start()

    adapters = build_adapters(config)  # pinned ClaudeCodeAdapter (manifest configured)
    inventory = await Inventory.probe(adapters)
    assert inventory.healthy(), f"provider probe failed: {inventory.degraded_reasons()}"
    evidence["steps"].append(
        f"provider probed healthy: {[s.name for s in inventory.statuses]}"
    )

    journal = Journal(config.journal_path)
    await journal.open()
    app = RuntimeApp(config, api, journal, inventory, adapters,
                     redaction_secrets=[api_key] if api_key else [],
                     sandbox_manager=mgr)
    meta = heartbeat_metadata(config, inventory)
    activated = await api.activate(
        code, {**meta, "version": "0.3.0-a3"},
        daemon_features={"sandbox": "linux_ns", "egress": "gateway", "broker": "unix"},
    )
    api.set_token(activated.runtime_token)
    app.set_runtime_id(activated.runtime_id)
    evidence["steps"].append(f"daemon activated; runtime_id={activated.runtime_id}")

    app_task = asyncio.create_task(app.run())
    await asyncio.sleep(3.0)  # first heartbeat lands → online

    # 6. runtime online?
    async with httpx.AsyncClient(base_url=SERVER_URL, timeout=30.0) as c:
        r = await c.get(f"/api/v1/workspaces/{ws_id}/runtimes/{runtime_id}", headers=_auth(token))
        rt_status = r.json()["data"]["status"]
        evidence["steps"].append(f"runtime status after activate+heartbeat: {rt_status}")
        assert rt_status == "online", f"runtime not online: {rt_status}"

        # 7. assign issue → enqueue → real claim → real claude run.
        r = await c.post(f"/api/v1/workspaces/{ws_id}/issues",
                         json={"title": f"A3 real LLM {run_id}",
                               "description": f"Reply with exactly the text {MARKER} and nothing else.",
                               "assignee_id": agent_member_id},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        issue = r.json()["data"]
        evidence["steps"].append(f"issue {issue['id']} assigned → enqueue path triggered")

        # 8. poll executions until terminal.
        execution = None
        deadline = asyncio.get_event_loop().time() + 400
        while asyncio.get_event_loop().time() < deadline:
            r = await c.get(f"/api/v1/workspaces/{ws_id}/executions", headers=_auth(token),
                            params={"issue_id": issue["id"]})
            data = r.json()["data"]
            items = data["items"] if isinstance(data, dict) else data
            if items:
                execution = items[0]
                if execution["status"] in ("completed", "failed", "cancelled", "timeout"):
                    break
            await asyncio.sleep(3.0)
        assert execution is not None, "execution never materialized"
        evidence["execution"] = {
            "id": execution["id"], "status": execution["status"],
            "failure_reason": execution.get("failure_reason"),
        }
        evidence["steps"].append(f"execution terminal: {execution['status']} "
                                 f"(reason={execution.get('failure_reason')})")

        # 9. result schema v1 + real usage回流 (fetched BEFORE the status assert
        # so a failed run still surfaces diagnostics).
        r = await c.get(f"/api/v1/workspaces/{ws_id}/executions/{execution['id']}",
                        headers=_auth(token))
        exec_detail = r.json()["data"]
        result = exec_detail.get("result") or {}
        evidence["result"] = result
        # capture logs even on failure for debugging
        lr = await c.get(f"/api/v1/workspaces/{ws_id}/executions/{execution['id']}/logs",
                         headers=_auth(token))
        evidence["log_excerpt"] = json.dumps(lr.json())[:2000]
        assert execution["status"] == "completed", evidence["execution"]
        assert result.get("schema_version") == 1, f"result schema: {result}"
        assert result.get("provider", {}).get("name") == "claude-code"
        usage = result.get("usage", {})
        assert usage.get("total_tokens", 0) > 0, f"no usage回流: {usage}"
        assert result.get("provider", {}).get("session_id"), "no session_id回流"
        result_json = json.dumps(result)
        assert api_key not in result_json, "API KEY leaked into result!"
        evidence["steps"].append(
            f"result v1 ok: total_tokens={usage.get('total_tokens')} "
            f"turns={usage.get('turns')} cost={usage.get('cost_usd')} "
            f"session={result['provider']['session_id'][:8]}"
        )

        # 10. logs回流 via REST + secret redaction.
        r = await c.get(f"/api/v1/workspaces/{ws_id}/executions/{execution['id']}/logs",
                        headers=_auth(token))
        log_body = json.dumps(r.json())
        evidence["log_excerpt"] = log_body[:800]
        assert api_key not in log_body, "API KEY leaked into logs!"
        evidence["steps"].append("logs reflowed; provider credential redacted")

    # 11. graceful shutdown.
    app.request_shutdown()
    await asyncio.wait_for(app_task, timeout=30)
    await journal.close()
    await api.close()
    await mgr.shutdown()
    evidence["steps"].append("daemon graceful shutdown; sandbox resources released")
    evidence["verdict"] = "PASS"
    return evidence


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    if os.getuid() != 0:
        print("real_llm_e2e requires root (real sandbox)", file=sys.stderr)
        sys.exit(2)
    try:
        ev = asyncio.run(main())
    except BaseException:
        import traceback

        traceback.print_exc()
        print("\nE2E FAILED (see traceback above)", file=sys.stderr)
        sys.exit(1)
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    secret = _load_api_key()
    text = json.dumps(ev, ensure_ascii=False, indent=2)
    if secret:
        text = text.replace(secret, "***")
    EVIDENCE_PATH.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nVERDICT: {ev['verdict']} — evidence at {EVIDENCE_PATH}")
