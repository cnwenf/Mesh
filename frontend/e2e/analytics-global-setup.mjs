/**
 * 统计报表真实后端联调全局准备(analytics.md §5 / T33 UI 走查):
 * 1) 以受限 mesh_app 角色(RLS 生效)在目标库上拉起真实 uvicorn API;
 * 2) 经真实 REST 注册 owner + 普通成员 m1(邀请兑换入册)、建公私两个项目、
 *    建 workspace 可见 agent;
 * 3) 经 psql 播种确定性统计源数据(done issue + 状态留痕 → cycle time /
 *    velocity / burndown;公私项目上的执行 + attempts + autopilot token →
 *    agent 统计的可见性差异;在途执行 → workload);
 * 4) 上下文(tokens / workspace / project / agent)写入 .analytics-context.json。
 * teardown 关闭 API 子进程。
 */
import { spawn, execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BACKEND = resolve(ROOT, '..', 'backend');
const API_PORT = Number(process.env.MESH_E2E_API_PORT ?? 8123);
const API_BASE = `http://127.0.0.1:${API_PORT}`;
const WEB_PORT = Number(process.env.MESH_E2E_WEB_PORT ?? 5231);
const CONTEXT_FILE = resolve(ROOT, 'e2e', '.analytics-context.json');
const PASSWORD = 'a-strong-passw0rd';

const DB_URL =
  process.env.MESH_E2E_DB_URL ?? 'postgresql+asyncpg://mesh:mesh@127.0.0.1:54399/mesh_test';
const APP_DB_URL = DB_URL.replace('mesh:mesh@', 'mesh_app:mesh_app@');
const REDIS_URL = process.env.MESH_E2E_REDIS_URL ?? 'redis://127.0.0.1:6399/3';
// psql 直连参数(与 DB_URL 同源)。
const PGHOST = process.env.MESH_E2E_PGHOST ?? '127.0.0.1';
const PGPORT = process.env.MESH_E2E_PGPORT ?? '54399';
const PGUSER = process.env.MESH_E2E_PGUSER ?? 'mesh';
const PGPASSWORD = process.env.MESH_E2E_PGPASSWORD ?? 'mesh';
const PGDATABASE = process.env.MESH_E2E_PGDATABASE ?? 'mesh_test';

async function waitReady(timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${API_BASE}/healthz`);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('API did not become ready');
}

async function api(method, path, token, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(`API ${method} ${path} -> ${res.status}: ${text}`);
  return json;
}

async function registerAndLogin(email, name) {
  await api('POST', '/api/v1/auth/register', null, { email, password: PASSWORD, display_name: name });
  const login = await api('POST', '/api/v1/auth/login', null, { email, password: PASSWORD });
  return login.data.access_token;
}

function psql(sql) {
  return execFileSync('psql', ['-h', PGHOST, '-p', PGPORT, '-U', PGUSER, '-d', PGDATABASE,
    '-v', 'ON_ERROR_STOP=1', '-tA', '-c', sql], {
    env: { ...process.env, PGPASSWORD },
    encoding: 'utf8',
  }).trim();
}

function iso(daysAgo, hour = 12) {
  const d = new Date(Date.now() - daysAgo * 86_400_000);
  d.setUTCHours(hour, 0, 0, 0);
  return d.toISOString();
}

function dateOnly(daysOffset) {
  const d = new Date(Date.now() + daysOffset * 86_400_000);
  return d.toISOString().slice(0, 10);
}

function seedStatsData({ workspaceId, pubProjectId, privProjectId, agentId, ownerMemberId, m1MemberId }) {
  const ws = workspaceId;
  // 状态:复用工作区初始化播种的 todo/done 状态。
  const todoStatus = psql(
    `SELECT id FROM issue_statuses WHERE workspace_id='${ws}' AND category='todo' LIMIT 1`,
  );
  const doneStatus = psql(
    `SELECT id FROM issue_statuses WHERE workspace_id='${ws}' AND category='done' LIMIT 1`,
  );
  const agentMember = psql(
    `SELECT id FROM members WHERE workspace_id='${ws}' AND agent_id='${agentId}' LIMIT 1`,
  );
  const cycleStart = dateOnly(-6);
  const cycleEnd = dateOnly(0);

  psql(`
    INSERT INTO cycles (workspace_id, name, project_id, starts_at, ends_at, state)
    VALUES ('${ws}', 'Sprint PW', '${pubProjectId}', '${cycleStart}', '${cycleEnd}', 'active');
  `);
  const cycleId = psql(
    `SELECT id FROM cycles WHERE workspace_id='${ws}' AND name='Sprint PW' LIMIT 1`,
  );

  // done issue(挂 pub 项目 + 周期,estimate 3,带 in_progress 留痕 → 三类指标取数)
  psql(`
    INSERT INTO issues (workspace_id, title, identifier_namespace_key, number, identifier,
                        status_id, state_category, project_id, cycle_id, estimate,
                        estimate_unit, completed_at, created_at, updated_at)
    VALUES ('${ws}', 'pw done issue', 'PWP', 1, 'PWP-1', '${doneStatus}', 'done',
            '${pubProjectId}', '${cycleId}', 3, 'points',
            '${iso(3)}', '${iso(6)}', now());
  `);
  const doneIssue = psql(`SELECT id FROM issues WHERE workspace_id='${ws}' AND identifier='PWP-1'`);
  psql(`
    INSERT INTO issue_activity (workspace_id, issue_id, actor_member_id, field,
                                old_value, new_value, created_at)
    VALUES ('${ws}', '${doneIssue}', '${ownerMemberId}', 'state_category',
            '"todo"', '"in_progress"', '${iso(5)}');
  `);

  // 两条 open issue:m1 指派(pub + priv)→ workload 可见性差异
  psql(`
    INSERT INTO issues (workspace_id, title, identifier_namespace_key, number, identifier,
                        status_id, state_category, project_id, assignee_id, created_at, updated_at)
    VALUES ('${ws}', 'pw open pub', 'PWP', 2, 'PWP-2', '${todoStatus}', 'todo',
            '${pubProjectId}', '${m1MemberId}', now(), now()),
           ('${ws}', 'pw open priv', 'PWP', 3, 'PWP-3', '${todoStatus}', 'todo',
            '${privProjectId}', '${m1MemberId}', now(), now());
  `);
  const openPub = psql(`SELECT id FROM issues WHERE workspace_id='${ws}' AND identifier='PWP-2'`);
  const openPriv = psql(`SELECT id FROM issues WHERE workspace_id='${ws}' AND identifier='PWP-3'`);

  // 执行矩阵:pub 上 completed / priv 上 completed / 无 issue queued
  psql(`
    INSERT INTO task_executions (workspace_id, agent_id, issue_id, trigger, status,
                                 queued_at, finished_at, timeout_seconds)
    VALUES ('${ws}', '${agentId}', '${openPub}', 'assign', 'completed',
            '${iso(2)}', '${iso(2)}', 1800),
           ('${ws}', '${agentId}', '${openPriv}', 'assign', 'completed',
            '${iso(2)}', '${iso(2)}', 1800),
           ('${ws}', '${agentId}', NULL, 'manual', 'queued',
            '${iso(1)}', NULL, 1800);
  `);
  const execPub = psql(
    `SELECT id FROM task_executions WHERE workspace_id='${ws}' AND issue_id='${openPub}' LIMIT 1`,
  );
  // attempts + autopilot token(pub 执行:1 次 attempt + 100/50 token)
  psql(`
    INSERT INTO execution_attempts (workspace_id, execution_id, attempt_number, status,
                                    started_at, finished_at)
    VALUES ('${ws}', '${execPub}', 1, 'completed', '${iso(2)}', '${iso(2)}');
    INSERT INTO autopilots (workspace_id, name, trigger_type, created_by)
    VALUES ('${ws}', 'PW Autopilot', 'issue_created', '${ownerMemberId}');
  `);
  const autopilotId = psql(
    `SELECT id FROM autopilots WHERE workspace_id='${ws}' AND name='PW Autopilot' LIMIT 1`,
  );
  psql(`
    INSERT INTO autopilot_runs (workspace_id, autopilot_id, trigger_type, execution_id,
                                status, started_at, prompt_tokens, completion_tokens)
    VALUES ('${ws}', '${autopilotId}', 'issue_created', '${execPub}', 'succeeded',
            '${iso(2)}', 100, 50);
  `);
  // agent 成员行存在性(供名册深链)
  if (agentMember === '') throw new Error('agent member row missing');
}

export default async function globalSetup() {
  const child = spawn(
    'python3',
    ['-m', 'uvicorn', 'mesh.api.app:create_app', '--factory', '--host', '127.0.0.1',
      '--port', String(API_PORT), '--log-level', 'warning'],
    {
      cwd: BACKEND,
      env: {
        ...process.env,
        PYTHONPATH: resolve(BACKEND, 'src'),
        MESH_DATABASE_URL: DB_URL,
        MESH_APP_DATABASE_URL: APP_DB_URL,
        MESH_REDIS_URL: REDIS_URL,
        MESH_AUTH_MODE: 'dev',
      },
      stdio: 'ignore',
    },
  );

  try {
    await waitReady();

    const stamp = Date.now();
    const ownerEmail = `pw-owner-${stamp}@corp.com`;
    const m1Email = `pw-m1-${stamp}@corp.com`;
    const ownerToken = await registerAndLogin(ownerEmail, 'PW Owner');
    const m1Token = await registerAndLogin(m1Email, 'PW Member');

    const slug = `pw-${Math.random().toString(36).slice(2, 10)}`;
    const ws = (await api('POST', '/api/v1/workspaces', ownerToken, { name: 'PW Team', slug })).data;

    // m1 邀请兑换入册(普通成员)
    const inv = (
      await api('POST', `/api/v1/workspaces/${ws.id}/invitations`, ownerToken, {
        emails: [m1Email],
        role: 'member',
      })
    ).data[0];
    const invToken = inv.invite_link.split('/').pop();
    const accepted = await api('POST', '/api/v1/invitations/accept', m1Token, { token: invToken });
    const m1MemberId = accepted.data.member.id;
    const ownerMemberId = psql(
      `SELECT id FROM members WHERE workspace_id='${ws.id}' AND role='owner' LIMIT 1`,
    );

    // 公/私两个项目
    const pub = (
      await api('POST', `/api/v1/workspaces/${ws.id}/projects`, ownerToken, {
        name: 'PW Public', key: `PWP${String(stamp).slice(-4)}`, visibility: 'public',
      })
    ).data;
    const priv = (
      await api('POST', `/api/v1/workspaces/${ws.id}/projects`, ownerToken, {
        name: 'PW Private', key: `PWV${String(stamp).slice(-4)}`, visibility: 'private',
      })
    ).data;

    // workspace 可见 agent(真实 REST 创建入口)
    const agent = (
      await api('POST', `/api/v1/workspaces/${ws.id}/agents`, ownerToken, {
        name: 'PW Agent',
        role_tag: '工程',
        bio: '统计走查 agent',
        visibility: 'workspace',
        system_instructions: '你是工程助手。',
        model_config: { model_tier: 'balanced', temperature: 0.2, max_tokens: 8192 },
        trigger_on_assign: true,
      })
    ).data;

    seedStatsData({
      workspaceId: ws.id,
      pubProjectId: pub.id,
      privProjectId: priv.id,
      agentId: agent.id,
      ownerMemberId,
      m1MemberId,
    });

    writeFileSync(
      CONTEXT_FILE,
      JSON.stringify(
        {
          apiBase: API_BASE,
          webBase: `http://127.0.0.1:${WEB_PORT}`,
          ownerToken,
          m1Token,
          workspaceId: ws.id,
          pubProjectId: pub.id,
          agentId: agent.id,
        },
        null,
        2,
      ),
    );
  } catch (err) {
    child.kill('SIGTERM');
    throw err;
  }

  return () => {
    child.kill('SIGTERM');
  };
}
