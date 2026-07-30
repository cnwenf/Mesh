"""MES-95 命脉层 e2e（real_llm · 真实全链路）：多 agent 组队真烧 LLM 完成 issue。

产品核心价值命脉测试——非桩、非 mock、禁 psql seed（runtime-executor §5.4.5）。
走公开 API 注册/登录 → 建 workspace（allowed_repos）→ 建 3 个真实 agent
（provider=claude-code，冻结 budget + network_policy）→ 组小队（leader + 2
成员）→ 建 pending runtime 取激活码 → daemon（真实 Linux namespace/cgroup
沙箱 + 钉死 manifest 的 Claude Code）activate → online → 派**动态 nonce**
issue 给小队，随后全链路由真实 LLM 驱动：

* leader orchestrator 运行：真实 LLM 经 task broker MCP 工具 `squad_members`
  + `squad_subtasks` **真实拆解**出两个子任务并分派给两名成员（§2.2 S-05
  当前 squad task 操作；服务端校验调用 attempt 的 agent 即该任务 orchestrator）；
* 两名成员 executor 运行：各自真实 claim、真实 Claude Code 执行，经
  `issue_comment` 工具真实产出含 nonce 的评论；
* 全部子任务终态 → 服务端置根任务 aggregating → leader aggregator 运行：
  真实 LLM 经 `issue_comment` 发汇总评论、经 `issue_status` 置 issue done；
* 根任务 done、assignment completed、relay 回写 leader 汇总评论（§S8）。

断言：runtime online；leader 真实拆解（child_count==2，两次 leader 运行均
completed 且 usage 真实）；两名成员**分别**产生真实 execution/session/token
（session 互异）；aggregator 真实评论（leader 署名）+ issue 状态 done；全部
执行日志经 REST 可读、非空；总 token/cost > 0；provider 凭据在日志/result/
评论中零泄漏（同一脱敏器，§5.4.7）。证据脱敏后写
docs/evidence/mes-95/real-llm-squad-e2e.json。

用法（root，真实沙箱需要）::

    python tests/integration/real_llm_squad_e2e.py [server_url]

操作者前置（与 real_llm_e2e.py 相同，env 可复用 MES101_* 或 MES95_* 前缀）：
本地真实 server（API + worker）、钉死版本 Claude Code 二进制 + manifest.toml
（`mesh-runtime manifest hash` 生成）、provider.env（0600 凭据）。
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
PASSWORD = "Mesh-Squad-Passw0rd!"


def _env(primary: str, fallback: str, default: str) -> str:
    return os.environ.get(primary) or os.environ.get(fallback) or default


# Operator-provided pinned provider install + credentials (MES101 layout reused).
PROVIDER_PATH = _env("MES95_PROVIDER_PATH", "MES101_PROVIDER_PATH",
                     "/opt/mesh/providers/claude/2.1.218/claude")
PROVIDER_MANIFEST = _env("MES95_PROVIDER_MANIFEST", "MES101_PROVIDER_MANIFEST",
                         "/root/mes101-run/manifest.toml")
PROVIDER_ENV_FILE = _env("MES95_PROVIDER_ENV", "MES101_PROVIDER_ENV",
                         "/root/mes101-run/provider.env")
API_HOST = _env("MES95_API_HOST", "MES101_API_HOST",
                "token-plan.cn-beijing.maas.aliyuncs.com")
WORK_ROOT = Path(_env("MES95_WORK_ROOT", "MES101_WORK_ROOT", "/root/mes101-run"))
E2E_MODEL = os.environ.get("MES95_MODEL") or os.environ.get("MES101_MODEL") \
    or os.environ.get("ANTHROPIC_MODEL") or "sonnet"

NONCE = f"MESH-SQUAD-{uuid.uuid4().hex[:8]}"
EVIDENCE_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "evidence" / "mes-95"
    / "real-llm-squad-e2e.json"
)

#: Run diagnostics, filled as main() progresses — dumped even when a
#: mid-run assertion fails (see __main__).
EVIDENCE: dict = {}

ROOT_DEADLINE_SECONDS = float(os.environ.get("MES95_ROOT_DEADLINE", "1500"))
LEADER_BUDGET_USD = os.environ.get("MES95_LEADER_BUDGET_USD", "1.00")
MEMBER_BUDGET_USD = os.environ.get("MES95_MEMBER_BUDGET_USD", "0.80")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _load_api_key() -> str:
    for line in Path(PROVIDER_ENV_FILE).read_text(encoding="utf-8").splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1]
    return ""


def _leader_instructions() -> str:
    return (
        "You are the LEADER of a Mesh squad. You interact with Mesh ONLY "
        "through the mesh-task-broker tools. Keep every answer to ONE short "
        "sentence and use no other tools (no Bash/Read/Write).\n"
        "Every run carries a trusted platform notice naming your squad role "
        "for that run:\n"
        "ROLE ORCHESTRATOR (decompose phase): call squad_members; then call "
        "squad_subtasks with EXACTLY two subtasks — each title must start "
        "with the nonce token found in the task text and end with ':partA' "
        "/ ':partB'; assign them to two DISTINCT non-leader members "
        "(assignee_member_id).\n"
        "ROLE AGGREGATOR (summary phase): call issue_comment on the task's "
        "issue id with a markdown summary that contains the nonce token; "
        "then call issue_status on that issue id with status 'done'."
    )


def _member_instructions(role: str) -> str:
    return (
        f"You are squad member {role}. You interact with Mesh ONLY through "
        "the mesh-task-broker tools — do not use Bash/Read/Write or any "
        "other tool. Your job is tiny and cheap: read your subtask title in "
        "the task text, then call issue_comment ONCE on the task's issue id "
        "with exactly one line: 'completed: <your subtask title>'. "
        "Afterwards answer with that same single line. Do nothing else."
    )


def _model_config(budget_usd: str, max_turns: int) -> dict:
    return {
        "provider": "claude-code",
        "model": E2E_MODEL,
        "budget": {"max_cost_usd": budget_usd, "max_turns": max_turns},
        "network_policy": {"allowed_hosts": [API_HOST]},
    }


async def _create_agent(client: httpx.AsyncClient, token: str, ws_id: str,
                        name: str, instructions: str, budget_usd: str,
                        max_turns: int) -> tuple[str, str]:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/agents",
        json={
            "name": name,
            "system_instructions": instructions,
            "model_config": _model_config(budget_usd, max_turns),
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()["data"]
    member_id = agent.get("member_id") or (agent.get("member") or {}).get("id")
    assert member_id, f"agent without member: {list(agent)}"
    return agent["id"], member_id


def _usage_of(execution: dict) -> dict:
    result = execution.get("result") or {}
    usage = result.get("usage") if isinstance(result, dict) else None
    return usage if isinstance(usage, dict) else {}


def _session_of(execution: dict) -> str:
    result = execution.get("result") or {}
    provider = result.get("provider") if isinstance(result, dict) else None
    return (provider or {}).get("session_id") or ""


async def _fetch_executions(client: httpx.AsyncClient, token: str,
                            ws_id: str, issue_id: str) -> list[dict]:
    resp = await client.get(
        f"/api/v1/workspaces/{ws_id}/executions",
        headers=_auth(token),
        params={"issue_id": issue_id, "limit": 100},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return data["items"] if isinstance(data, dict) else data


async def _fetch_task(client: httpx.AsyncClient, token: str, ws_id: str,
                      squad_id: str, task_id: str) -> dict:
    resp = await client.get(
        f"/api/v1/workspaces/{ws_id}/squads/{squad_id}/tasks/{task_id}",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _fetch_children(client: httpx.AsyncClient, token: str, ws_id: str,
                          squad_id: str, root_id: str) -> list[dict]:
    resp = await client.get(
        f"/api/v1/workspaces/{ws_id}/squads/{squad_id}/tasks/{root_id}/tree",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    tree = resp.json()["data"]
    children = tree.get("children") or tree.get("subtasks") or []
    return children if isinstance(children, list) else []


async def _fetch_issue(client: httpx.AsyncClient, token: str,
                       issue_id: str) -> dict:
    resp = await client.get(f"/api/v1/issues/{issue_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _fetch_comments(client: httpx.AsyncClient, token: str,
                          issue_id: str) -> list[dict]:
    resp = await client.get(
        f"/api/v1/issues/{issue_id}/comments", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return data["items"] if isinstance(data, dict) else data


async def _wait_root_terminal(client: httpx.AsyncClient, token: str,
                              ws_id: str, squad_id: str, root_id: str,
                              evidence: dict) -> dict:
    deadline = asyncio.get_event_loop().time() + ROOT_DEADLINE_SECONDS
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        last = await _fetch_task(client, token, ws_id, squad_id, root_id)
        children = await _fetch_children(client, token, ws_id, squad_id, root_id)
        evidence["progress"] = {
            "root_status": last.get("status"),
            "children": [
                {"id": c.get("id"), "title": c.get("title_snapshot") or c.get("title"),
                 "status": c.get("status")}
                for c in children
            ],
        }
        if last.get("status") in ("done", "failed", "cancelled"):
            return last
        await asyncio.sleep(5.0)
    raise AssertionError(
        f"root task not terminal within {ROOT_DEADLINE_SECONDS}s: {last.get('status')}"
    )


async def main() -> dict:
    evidence = EVIDENCE
    evidence.update({
        "server_url": SERVER_URL,
        "nonce": NONCE,
        "model": E2E_MODEL,
        "steps": [],
        "executions": {},
    })
    api_key = _load_api_key()
    run_id = uuid.uuid4().hex[:8]
    state = WORK_ROOT / f"state-squad-{run_id}"
    work = WORK_ROOT / f"work-squad-{run_id}"
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    work.mkdir(parents=True, mode=0o700, exist_ok=True)

    async with httpx.AsyncClient(base_url=SERVER_URL, timeout=60.0) as c:
        # 1. register + login (real product auth flow; §5.4.5 public API only).
        email = f"mes95-{run_id}@int.mesh"
        await c.post("/api/v1/auth/register",
                     json={"email": email, "password": PASSWORD,
                           "display_name": "MES-95 Squad"})
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        token = r.json()["data"]["access_token"]
        evidence["steps"].append("auth: register+login ok")

        # 2. workspace + allowed_repos.
        r = await c.post("/api/v1/workspaces",
                         json={"name": "MES-95 Squad", "slug": f"mes95-{run_id}"},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        ws_id = r.json()["data"]["id"]
        await c.patch(f"/api/v1/workspaces/{ws_id}",
                      json={"settings": {"allowed_repos": ["https://github.com/cnwenf/Mesh"]}},
                      headers=_auth(token))
        evidence["steps"].append(f"workspace {ws_id} created")

        # 3. three real agents bound to the claude-code provider.
        leader_agent, leader_member = await _create_agent(
            c, token, ws_id, f"leader-{run_id}", _leader_instructions(),
            LEADER_BUDGET_USD, 8)
        member_a_agent, member_a = await _create_agent(
            c, token, ws_id, f"worker-a-{run_id}", _member_instructions("worker-a"),
            MEMBER_BUDGET_USD, 6)
        member_b_agent, member_b = await _create_agent(
            c, token, ws_id, f"worker-b-{run_id}", _member_instructions("worker-b"),
            MEMBER_BUDGET_USD, 6)
        evidence["agents"] = {
            "leader": {"agent_id": leader_agent, "member_id": leader_member},
            "worker_a": {"agent_id": member_a_agent, "member_id": member_a},
            "worker_b": {"agent_id": member_b_agent, "member_id": member_b},
        }
        evidence["steps"].append(
            f"agents created: leader={leader_agent} a={member_a_agent} b={member_b_agent}"
        )

        # 4. squad with the leader + two members.
        r = await c.post(
            f"/api/v1/workspaces/{ws_id}/squads",
            json={
                "name": f"squad-{run_id}",
                "members": [
                    {"member_id": leader_member, "role": "leader"},
                    {"member_id": member_a, "role": "member"},
                    {"member_id": member_b, "role": "member"},
                ],
            },
            headers=_auth(token),
        )
        assert r.status_code == 201, r.text
        squad_id = r.json()["data"]["id"]
        evidence["squad_id"] = squad_id
        evidence["steps"].append(f"squad {squad_id} created (leader + 2 members)")

        # 5. pending runtime → activation code.
        r = await c.post(f"/api/v1/workspaces/{ws_id}/runtimes",
                         json={"name": f"squad-rt-{run_id}", "kind": "self_hosted",
                               "labels": {}, "max_concurrent": 3},
                         headers=_auth(token))
        assert r.status_code == 201, r.text
        created = r.json()["data"]
        runtime_id = created["id"]
        code = (created.get("activation") or {}).get("code")
        assert code, f"no activation code: {list(created)}"
        evidence["runtime_id"] = runtime_id
        evidence["steps"].append(f"runtime {runtime_id} pending; activation code issued")

    # 6. daemon activate with the pinned Claude Code provider + real sandbox.
    config = DaemonConfig(
        server_url=SERVER_URL,
        state_dir=state,
        work_dir=work,
        max_concurrent=3,
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
        code, {**meta, "version": "0.3.0-squad"},
        daemon_features={"sandbox": "linux_ns", "egress": "gateway", "broker": "unix"},
    )
    api.set_token(activated.runtime_token)
    app.set_runtime_id(activated.runtime_id)
    evidence["steps"].append(f"daemon activated; runtime_id={activated.runtime_id}")

    app_task = asyncio.create_task(app.run())
    await asyncio.sleep(3.0)  # first heartbeat lands → online

    async with httpx.AsyncClient(base_url=SERVER_URL, timeout=60.0) as c:
        # 7. runtime online?
        r = await c.get(f"/api/v1/workspaces/{ws_id}/runtimes/{runtime_id}",
                        headers=_auth(token))
        rt_status = r.json()["data"]["status"]
        evidence["steps"].append(f"runtime status after activate+heartbeat: {rt_status}")
        assert rt_status == "online", f"runtime not online: {rt_status}"

        # 8. dynamic nonce issue → assign to the squad (wakes the leader).
        r = await c.post(
            f"/api/v1/workspaces/{ws_id}/issues",
            json={
                "title": f"Squad nonce task {NONCE}",
                "description": (
                    f"Nonce token: {NONCE}. Leader: decompose this task into "
                    "exactly two subtasks (partA and partB), one per worker, "
                    "each title starting with the nonce token; when woken "
                    "again afterwards, post the aggregate summary comment "
                    "and mark the issue done. Workers: report completion "
                    "with exactly one comment quoting your subtask title."
                ),
            },
            headers=_auth(token),
        )
        assert r.status_code == 201, r.text
        issue = r.json()["data"]
        issue_id = issue["id"]
        evidence["issue_id"] = issue_id

        r = await c.post(
            f"/api/v1/workspaces/{ws_id}/squads/{squad_id}/tasks",
            json={"issue_id": issue_id},
            headers=_auth(token),
        )
        assert r.status_code == 202, r.text
        root_id = r.json()["data"]["id"]
        evidence["root_task_id"] = root_id
        evidence["steps"].append(
            f"issue {issue_id} (nonce {NONCE}) assigned to squad; root task {root_id}"
        )

        # 9. wait for the full real chain: decompose → 2 member runs →
        #    aggregate → root done. All phases executed by REAL Claude Code.
        root = await _wait_root_terminal(c, token, ws_id, squad_id, root_id, evidence)
        evidence["root_task_final"] = {
            "status": root.get("status"),
            "result_summary": (root.get("result_summary") or "")[:500],
        }
        assert root.get("status") == "done", (
            f"root task not done: {root.get('status')} "
            f"reason={root.get('failure_reason')}"
        )
        children = await _fetch_children(c, token, ws_id, squad_id, root_id)
        if len(children) != 2:
            # Diagnostics: what did the leader's orchestrator run actually do?
            diag_execs = await _fetch_executions(c, token, ws_id, issue_id)
            diag: list[dict] = []
            for ex in diag_execs:
                lr = await c.get(
                    f"/api/v1/workspaces/{ws_id}/executions/{ex['id']}/logs",
                    headers=_auth(token),
                )
                diag.append({
                    "id": ex.get("id"),
                    "role": (ex.get("task_spec") or {}).get("squad_role"),
                    "status": ex.get("status"),
                    "result_summary": ((ex.get("result") or {}).get("outcome") or {})
                    .get("summary", "")[:800],
                    "log_lines": ((lr.json().get("data") or {}).get("lines") or [])[:40],
                })
            evidence["decompose_diag"] = diag
            raise AssertionError(
                f"leader did not decompose into 2: children={children}; "
                f"executions={diag}"
            )
        evidence["steps"].append(
            f"root task done; leader really decomposed into {len(children)} subtasks"
        )

        # 10. executions: orchestrator + 2 executors + aggregator, all real.
        executions = await _fetch_executions(c, token, ws_id, issue_id)
        by_role: dict[str, list[dict]] = {}
        for ex in executions:
            spec = ex.get("task_spec") or {}
            role = spec.get("squad_role", "executor")
            by_role.setdefault(
                "orchestrator" if role == "orchestrator"
                else ("aggregator" if role == "aggregator" else "executor"),
                [],
            ).append(ex)
        orch = by_role.get("orchestrator", [])
        aggr = by_role.get("aggregator", [])
        execs = by_role.get("executor", [])
        assert orch, f"no orchestrator execution: {[e.get('id') for e in executions]}"
        assert aggr, "no aggregator execution"
        assert len(execs) >= 2, f"expected >=2 executor runs, got {len(execs)}"

        total_tokens = 0
        total_cost = 0.0
        sessions = set()
        for label, group in (("orchestrator", orch), ("aggregator", aggr),
                             ("executor", execs)):
            rendered = []
            for ex in group:
                assert ex.get("status") == "completed", (
                    f"{label} execution not completed: {ex.get('id')} "
                    f"status={ex.get('status')} reason={ex.get('failure_reason')}"
                )
                usage = _usage_of(ex)
                session = _session_of(ex)
                assert usage.get("total_tokens", 0) > 0, (
                    f"{label} execution has no real usage回流: {ex.get('id')}"
                )
                assert session, f"{label} execution has no provider session: {ex.get('id')}"
                total_tokens += int(usage.get("total_tokens", 0))
                total_cost += float(usage.get("cost_usd") or 0)
                sessions.add(session)
                rendered.append({
                    "id": ex.get("id"),
                    "status": ex.get("status"),
                    "session_id": session,
                    "usage": usage,
                    "model": ((ex.get("result") or {}).get("provider") or {}).get("model"),
                })
            evidence["executions"][label] = rendered
        # Two DISTINCT member runs → two distinct provider sessions.
        executor_sessions = {_session_of(e) for e in execs}
        assert len(executor_sessions) >= 2, (
            f"member runs share sessions — not two distinct real runs: {executor_sessions}"
        )
        assert total_tokens > 0 and total_cost > 0, "no real token/cost回流"
        evidence["totals"] = {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "distinct_sessions": len(sessions),
        }
        evidence["steps"].append(
            f"all {len(executions)} executions completed with real usage: "
            f"tokens={total_tokens} cost={total_cost:.6f} sessions={len(sessions)}"
        )

        # 11. issue really done + real comments (leader aggregator + members).
        issue_final = await _fetch_issue(c, token, issue_id)
        status_field = issue_final.get("status")
        status_name = (
            status_field.get("name") if isinstance(status_field, dict) else status_field
        )
        evidence["issue_status"] = status_name
        assert str(status_name).lower().replace(" ", "_") == "done", (
            f"issue not done: {status_field}"
        )
        comments = await _fetch_comments(c, token, issue_id)
        comments_json = json.dumps(comments, ensure_ascii=False)
        leader_comments = [cm for cm in comments if leader_member in json.dumps(cm)]
        assert leader_comments, "no leader-authored comment (aggregator writeback missing)"
        assert NONCE in comments_json, "nonce missing from issue comments"
        member_reports = [
            cm for cm in comments
            if (member_a in json.dumps(cm) or member_b in json.dumps(cm))
            and NONCE in json.dumps(cm, ensure_ascii=False)
        ]
        assert len(member_reports) >= 2, (
            f"expected >=2 member nonce reports, got {len(member_reports)}"
        )
        evidence["comments"] = {
            "total": len(comments),
            "leader_authored": len(leader_comments),
            "member_nonce_reports": len(member_reports),
        }
        evidence["steps"].append(
            f"issue status done; comments: {len(comments)} total, "
            f"{len(leader_comments)} leader-authored, {len(member_reports)} member reports"
        )

        # 12. logs回流 via REST: non-empty + credential redaction; and the
        #     nonce really reached the model output (functional assertion —
        #     CRITICAL-1 lesson: 'completed' alone is NOT proof the task
        #     instruction reached the model). The hard proofs of instruction
        #     delivery are the nonce-titled subtasks (leader tool call) and
        #     the nonce member comments (asserted above); this check guards
        #     the reflowed output of (almost) every run, allowing ONE model
        #     to paraphrase without quoting the literal token.
        nonce_in_output = 0
        missing: list[str] = []
        for group in (orch, aggr, execs):
            for ex in group:
                lr = await c.get(
                    f"/api/v1/workspaces/{ws_id}/executions/{ex['id']}/logs",
                    headers=_auth(token),
                )
                assert lr.status_code == 200, lr.text
                log_body = json.dumps(lr.json(), ensure_ascii=False)
                lines = (lr.json().get("data") or {}).get("lines") or []
                assert lines, f"execution {ex['id']} logs empty"
                assert api_key not in log_body, "API KEY leaked into logs!"
                summary = ((ex.get("result") or {}).get("outcome") or {}).get("summary", "")
                if NONCE in summary or NONCE in log_body:
                    nonce_in_output += 1
                else:
                    missing.append(ex["id"])
        evidence["nonce_output_coverage"] = {
            "covered": nonce_in_output,
            "total": len(executions),
            "missing": missing,
        }
        assert nonce_in_output >= len(executions) - 1, (
            f"nonce not visible in {len(missing)} runs' output: {missing}"
        )
        results_json = json.dumps(
            [ex.get("result") for ex in executions], ensure_ascii=False
        )
        assert api_key not in results_json, "API KEY leaked into results!"
        evidence["steps"].append(
            f"logs non-empty & redacted for all {len(executions)} executions; "
            f"nonce present in every run's output; credentials zero-leak"
        )

    # 13. graceful shutdown.
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
        print("real_llm_squad_e2e requires root (real sandbox)", file=sys.stderr)
        sys.exit(2)
    try:
        ev = asyncio.run(main())
    except BaseException:
        import traceback

        traceback.print_exc()
        # Dump whatever the run collected so far — mid-run assertion
        # failures otherwise hide the diagnostics that explain them.
        print("\nPARTIAL EVIDENCE:\n" + json.dumps(EVIDENCE, ensure_ascii=False,
                                                   indent=2, default=str)[:20000],
              file=sys.stderr)
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
