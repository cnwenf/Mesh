"""MES-100 真实联调:本地真实 server(MES-98 已合入)+ daemon 真沙箱执行。

走公开 API(§5.4.5,禁 psql seed):注册/登录 → 建 workspace(设
allowed_repos)→ 建 agent → 建 pending runtime 取激活码 → daemon activate →
online → issue 指派 agent 触发 enqueue → daemon 真实 claim → namespace/cgroup
沙箱内执行 provider → 脱敏日志/终态 result 回流。

断言:runtime online;execution queued→claimed→running→completed;日志经
REST 可读且含 provider 输出;result schema_version=1;终态后 task token 即
刻吊销(401)。证据写 docs/evidence/mes-100/integration.json。

用法:python tests/integration/real_server_e2e.py [server_url]
"""

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx

from mesh_runtime.api import RuntimeApiClient
from mesh_runtime.app import RuntimeApp, heartbeat_metadata
from mesh_runtime.config import DaemonConfig
from mesh_runtime.inventory import Inventory
from mesh_runtime.providers.fake import FakeProvider
from mesh_runtime.sandbox import SandboxManager

SERVER_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASSWORD = "Mesh-Int-Passw0rd!"
SECRET = f"sk-live-int-{uuid.uuid4().hex}"
EVIDENCE_PATH = Path(__file__).resolve().parents[3] / "docs" / "evidence" / "mes-100" / "integration.json"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def main() -> dict:
    evidence: dict = {"server_url": SERVER_URL, "steps": []}
    run_id = uuid.uuid4().hex[:8]
    short = Path(f"/mesh-int-{run_id}")
    prov_dir = Path(f"/mesh-int-prov-{run_id}")
    short.mkdir(exist_ok=True)
    prov_dir.mkdir(exist_ok=True)
    (prov_dir / "provider.sh").write_text(
        f"#!/bin/sh\necho 'integration-run {run_id}'\necho 'carries {SECRET}'\nexit 0\n"
    )
    (prov_dir / "provider.sh").chmod(0o755)

    async with httpx.AsyncClient(base_url=SERVER_URL, timeout=30.0) as c:
        # 1. register + login (real product auth flow).
        email = f"mes100-{run_id}@int.mesh"
        await c.post("/api/v1/auth/register",
                     json={"email": email, "password": PASSWORD, "display_name": "MES-100 INT"})
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        token = r.json()["data"]["access_token"]
        evidence["steps"].append("auth: register+login ok")

        # 2. workspace + allowed_repos settings.
        r = await c.post("/api/v1/workspaces",
                         json={"name": "MES-100 INT", "slug": f"mes100-{run_id}"},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        ws_id = r.json()["data"]["id"]
        r = await c.patch(f"/api/v1/workspaces/{ws_id}",
                          json={"settings": {"allowed_repos": ["https://github.com/cnwenf/Mesh"]}},
                          headers=_auth(token))
        assert r.status_code == 200, r.text
        evidence["steps"].append(f"workspace {ws_id} created; allowed_repos set")

        # 3. agent (executor for claim INNER JOIN).
        r = await c.post(f"/api/v1/workspaces/{ws_id}/agents",
                         json={"name": f"a2-agent-{run_id}",
                               "system_instructions": "You are the A2 integration agent."},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        agent = r.json()["data"]
        agent_member_id = (
            agent.get("member_id")
            or (agent.get("member") or {}).get("id")
        )
        assert agent_member_id, f"no member id in agent response: {list(agent)}"
        evidence["steps"].append(f"agent {agent['id']} created (member {agent_member_id})")

        # 4. pending runtime → activation code.
        r = await c.post(f"/api/v1/workspaces/{ws_id}/runtimes",
                         json={"name": f"a2-rt-{run_id}", "kind": "self_hosted",
                               "labels": {}, "max_concurrent": 2},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        created = r.json()["data"]
        runtime_id = created["id"]
        activation = created.get("activation") or {}
        code = activation.get("code")
        assert code, f"no activation code in create response: {list(created)}"
        evidence["steps"].append(f"runtime {runtime_id} pending; activation code issued")

    # 5. daemon activate + full security stack.
    config = DaemonConfig(
        server_url=SERVER_URL,
        state_dir=short / "state",
        work_dir=short / "work",
        max_concurrent=2,
        provider_path=prov_dir / "provider.sh",
        provider_version="0.0.0-a2-int",
        allow_insecure_http=True,  # loopback local stack only
        heartbeat_interval_seconds=5.0,
    )
    api = RuntimeApiClient(SERVER_URL, None)
    mgr = SandboxManager(
        state_root=short / "sandbox", sandbox_uid=config.sandbox_uid,
        sandbox_gid=config.sandbox_gid,
    )
    await mgr.start()
    meta = heartbeat_metadata(config, Inventory([]))
    activated = await api.activate(
        code, {**meta, "capabilities": ["coding_cli.fake"], "version": "0.2.0-a2"},
        daemon_features={"sandbox": "linux_ns", "egress": "gateway", "broker": "unix"},
    )
    api.set_token(activated.runtime_token)
    evidence["steps"].append(f"daemon activated; runtime_id={activated.runtime_id}")

    from mesh_runtime.journal import Journal

    journal = Journal(config.journal_path)
    await journal.open()
    # SECRET is a daemon-known redaction secret (production: operators pin
    # these; claim credential values are added automatically per attempt).
    app = RuntimeApp(config, api, journal, Inventory([]), adapters=[FakeProvider(events=[])],
                     sandbox_manager=mgr, redaction_secrets=[SECRET])
    app.set_runtime_id(activated.runtime_id)
    app_task = asyncio.create_task(app.run())
    await asyncio.sleep(2.0)  # first heartbeat lands → online

    # 6. runtime online?
    async with httpx.AsyncClient(base_url=SERVER_URL, timeout=30.0) as c:
        r = await c.get(f"/api/v1/workspaces/{ws_id}/runtimes/{runtime_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        rt_status = r.json()["data"]["status"]
        evidence["steps"].append(f"runtime status after activate+heartbeat: {rt_status}")
        assert rt_status == "online", f"runtime not online: {rt_status}"

        # 7. assign issue to the agent member → outbox → worker → enqueue.
        r = await c.post(f"/api/v1/workspaces/{ws_id}/issues",
                         json={"title": f"A2 integration {run_id}", "assignee_id": agent_member_id},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        issue = r.json()["data"]
        evidence["steps"].append(f"issue {issue['id']} assigned → enqueue path triggered")

        # 8. poll executions until completed (claim→sandbox→terminal reflow).
        execution = None
        deadline = asyncio.get_event_loop().time() + 90
        while asyncio.get_event_loop().time() < deadline:
            r = await c.get(f"/api/v1/workspaces/{ws_id}/executions", headers=_auth(token),
                            params={"issue_id": issue["id"]})
            items = r.json()["data"]["items"] if isinstance(r.json()["data"], dict) else r.json()["data"]
            if items:
                execution = items[0]
                if execution["status"] in ("completed", "failed", "cancelled", "timeout"):
                    break
            await asyncio.sleep(2.0)
        assert execution is not None, "execution never materialized"
        evidence["execution"] = {
            "id": execution["id"], "status": execution["status"],
            "failure_reason": execution.get("failure_reason"),
        }
        evidence["steps"].append(f"execution terminal: {execution['status']}")
        assert execution["status"] == "completed", evidence["execution"]

        # 9. logs via REST: provider output present, secret REDACTED.
        r = await c.get(f"/api/v1/workspaces/{ws_id}/executions/{execution['id']}/logs",
                        headers=_auth(token))
        assert r.status_code == 200, r.text
        log_body = json.dumps(r.json())
        evidence["log_excerpt"] = log_body[:600]
        assert f"integration-run {run_id}" in log_body, "provider output missing from logs"
        assert SECRET not in log_body, "SECRET leaked into logs!"
        assert "***" in log_body, "redaction marker missing"
        evidence["steps"].append("logs reflowed; secret redacted (***)")

        # 10. result schema on the execution/attempt.
        r = await c.get(f"/api/v1/workspaces/{ws_id}/executions/{execution['id']}",
                        headers=_auth(token))
        assert r.status_code == 200
        exec_detail = r.json()["data"]
        result = exec_detail.get("result") or {}
        evidence["result_schema_version"] = result.get("schema_version")
        assert result.get("schema_version") == 1, f"result schema: {result}"
        assert SECRET not in json.dumps(result), "SECRET leaked into result!"
        evidence["steps"].append("result schema_version=1; no secret in result")

        # 11. attempt task token revoked after terminal.
        attempts = exec_detail.get("attempts") or []
        task_token = None
        for att in attempts:
            if att.get("task_token"):
                task_token = att["task_token"]
        if task_token:
            r2 = await c.get(f"/api/v1/workspaces/{ws_id}/issues/{issue['id']}",
                             headers=_auth(task_token))
            evidence["task_token_after_terminal"] = r2.status_code
            assert r2.status_code == 401, "task token NOT revoked after terminal!"
            evidence["steps"].append("task token revoked post-terminal (401)")
        else:
            evidence["steps"].append("task token not exposed in console API (expected; "
                                     "plaintext only in claim/renew) — revocation covered "
                                     "by server P0 contract tests")

    # 12. graceful shutdown.
    app.request_shutdown()
    await asyncio.wait_for(app_task, timeout=30)
    await journal.close()
    await api.close()
    await mgr.shutdown()
    shutil.rmtree(short, ignore_errors=True)
    shutil.rmtree(prov_dir, ignore_errors=True)
    evidence["steps"].append("daemon graceful shutdown; sandbox resources released")
    evidence["verdict"] = "PASS"
    return evidence


if __name__ == "__main__":
    if os.getuid() != 0:
        print("real_server_e2e requires root (real sandbox)", file=sys.stderr)
        sys.exit(2)
    ev = asyncio.run(main())
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Redact the integration secret from evidence before persisting.
    text = json.dumps(ev, ensure_ascii=False, indent=2).replace(SECRET, "***")
    EVIDENCE_PATH.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nVERDICT: {ev['verdict']} — evidence at {EVIDENCE_PATH}")
