/**
 * 成员名册真实后端 e2e 全局准备:
 * 1) 以受限 mesh_app 角色(RLS 生效)在 mesh_test 库上拉起真实 uvicorn API(8099);
 * 2) 经真实 REST 注册/登录/建工作区/邀请并兑换第二名人类成员;
 * 3) 经真实 REST(POST /agents,唯一创建入口的后端面)创建 agent「代码助手」;
 * 4) 把 owner token / workspace / 第二名成员写入上下文文件供用例登录与断言。
 * teardown 关闭 API 子进程。
 */
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BACKEND = resolve(ROOT, '..', 'backend');
const PYTHON = resolve(BACKEND, '.venv/bin/python');
// 端口同样可经环境变量切换(并行联调避免与其他分支互踩)。
const API_PORT = Number(process.env.MESH_E2E_API_PORT ?? 8099);
const API_BASE = `http://127.0.0.1:${API_PORT}`;
// 允许以环境变量切换目标库(并行开发期避免共享 mesh_test 被其他分支迁移互踩)。
const DB = process.env.MESH_E2E_DB ?? 'mesh_test';
const CONTEXT_FILE = resolve(ROOT, 'e2e', '.members-context.json');
const PASSWORD = 'a-strong-passw0rd';

const DB_URL = `postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/${DB}`;
const APP_DB_URL = `postgresql+asyncpg://mesh_app:mesh_app@127.0.0.1:5432/${DB}`;

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

export default async function globalSetup() {
  const child = spawn(
    PYTHON,
    ['-m', 'uvicorn', 'mesh.api.app:create_app', '--factory', '--host', '127.0.0.1', '--port', String(API_PORT), '--log-level', 'warning'],
    {
      cwd: BACKEND,
      env: {
        ...process.env,
        MESH_DATABASE_URL: DB_URL,
        MESH_APP_DATABASE_URL: APP_DB_URL,
        // 验收 B2:redis 地址可经 MESH_E2E_REDIS_URL 覆写,免硬编码 6379(并行/隔离环境端口各异)。
        MESH_REDIS_URL: process.env.MESH_E2E_REDIS_URL ?? 'redis://127.0.0.1:6379/2',
        MESH_AUTH_MODE: 'dev',
      },
      stdio: 'ignore',
    },
  );

  try {
    await waitReady();

    const ownerEmail = `owner-${Date.now()}@corp.com`;
    const joinerEmail = `joiner-${Date.now()}@corp.com`;
    const ownerToken = await registerAndLogin(ownerEmail, 'Owner');
    const joinerToken = await registerAndLogin(joinerEmail, 'Joiner');

    const slug = `ui-${Math.random().toString(36).slice(2, 10)}`;
    const ws = (await api('POST', '/api/v1/workspaces', ownerToken, { name: 'UI Team', slug })).data;

    // Invite + accept the second human member.
    const inv = (
      await api('POST', `/api/v1/workspaces/${ws.id}/invitations`, ownerToken, {
        emails: [joinerEmail],
        role: 'member',
      })
    ).data[0];
    const invToken = inv.invite_link.split('/').pop();
    const accepted = await api('POST', '/api/v1/invitations/accept', joinerToken, { token: invToken });
    const joinerMemberId = accepted.data.member.id;

    // Create the agent through the real REST surface (agent.md §3.1 — the
    // backend half of the roster's single [+ New Agent] entry).
    await api('POST', `/api/v1/workspaces/${ws.id}/agents`, ownerToken, {
      name: '代码助手',
      role_tag: '工程',
      bio: '协助工程任务',
      visibility: 'workspace',
      system_instructions: '你是工程助手。',
      model_config: { model_tier: 'balanced', temperature: 0.2, max_tokens: 8192 },
      trigger_on_assign: true,
    });

    writeFileSync(
      CONTEXT_FILE,
      JSON.stringify(
        {
          apiBase: API_BASE,
          ownerToken,
          workspaceId: ws.id,
          joinerMemberId,
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
