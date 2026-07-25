/**
 * 成员名册真实后端 e2e 全局准备:
 * 1) 以受限 mesh_app 角色(RLS 生效)在 mesh_test 库上拉起真实 uvicorn API(8099);
 * 2) 经真实 REST 注册/登录/建工作区/邀请并兑换第二名人类成员;
 * 3) 直接 INSERT 一条 agent 名册行(agents 表尚未落地,以 display_override 提供显示名);
 * 4) 把 owner token / workspace / 第二名成员写入上下文文件供用例登录与断言。
 * teardown 关闭 API 子进程。
 */
import { spawn, execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BACKEND = resolve(ROOT, '..', 'backend');
const PYTHON = resolve(BACKEND, '.venv/bin/python');
const API_PORT = 8099;
const API_BASE = `http://127.0.0.1:${API_PORT}`;
const DB = 'mesh_test';
const CONTEXT_FILE = resolve(ROOT, 'e2e', '.members-context.json');
const PASSWORD = 'a-strong-passw0rd';

const DB_URL = `postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/${DB}`;
const APP_DB_URL = `postgresql+asyncpg://mesh_app:mesh_app@127.0.0.1:5432/${DB}`;
const SEED_DSN = `postgresql://mesh:mesh@127.0.0.1:5432/${DB}`;

/** Directly seed an agent roster row via asyncpg (agents table is deferred). */
function seedAgent(workspaceId) {
  const script =
    'import asyncio, asyncpg, sys\n' +
    'async def main():\n' +
    `    conn = await asyncpg.connect("${SEED_DSN}")\n` +
    '    await conn.execute(\n' +
    '        "INSERT INTO members (workspace_id, member_type, agent_id, role, display_override, joined_at) "\n' +
    "        \"VALUES ($1, 'agent', gen_random_uuid(), 'member', '代码助手', now())\",\n" +
    '        sys.argv[1],\n' +
    '    )\n' +
    '    await conn.close()\n' +
    'asyncio.run(main())\n';
  execFileSync(PYTHON, ['-c', script, workspaceId], { encoding: 'utf8', timeout: 30_000 });
}

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
        MESH_REDIS_URL: 'redis://127.0.0.1:6379/2',
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

    // Insert an agent roster row (agents table deferred; display_override supplies the name).
    seedAgent(ws.id);

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
