/**
 * Runtimes 模块真实后端浏览器走查(runtime.md §4 验收):
 *   ① dev-token 登录 → Runtimes 列表页;
 *   ② UI 向导创建 runtime → 断言一次性激活码与可审安装脚本(§4.3);
 *   ③ 经 psql 落库一台 online runtime + 一条 running execution(§2.2 schema)→
 *      列表断言可见(状态点 / 负载 / 心跳新鲜度);
 *   ④ 执行详情页 → 日志面板 + 取消按钮 + 凭证脱敏(值恒 ***)。
 *
 * 前置:后端栈运行中(docker compose up postgres redis api worker gateway,
 * MESH_AUTH_MODE=dev);dev server 由 playwright.runtimes.config.ts 拉起并指向 8000/8081。
 * 每步截图存证 e2e/evidence/runtimes(随 PR 提交;字节互异,见 check-evidence-unique.mjs)。
 */
import { expect, test } from '@playwright/test';
import { dismissOnboarding, injectSession } from './helpers';
import type { Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE_DIR = process.env.RUNTIMES_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'runtimes');

const API_BASE = process.env.VITE_MESH_API_BASE_URL ?? 'http://127.0.0.1:8000';
const PG_CONTAINER = 'mesh-postgres-1';

// Per-run IDs keep repeated real-stack runs tenant-correct; fixed IDs would
// retain the first run's workspace through ON CONFLICT and disappear here.
const RUNTIME_ID = randomUUID();
const EXECUTION_ID = randomUUID();
const ATTEMPT_ID = randomUUID();

// Real register + workspace over the API (the REST surface accepts real JWTs
// only — dev tokens are gateway-only): the walkthrough then runs as a real
// workspace owner. IDs are per-run so the spec is repeatable on a fresh DB.
const RUN_SUFFIX = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
let WORKSPACE_ID = '';
let TOKEN = '';

async function bootstrapWorld(): Promise<void> {
  const email = `rt-walkthrough-${RUN_SUFFIX}@example.com`;
  const password = 'Rt-Walkthrough-12345';
  const register = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, display_name: 'RT Walkthrough' }),
  });
  if (register.status !== 201)
    throw new Error(`register failed: ${register.status} ${await register.text()}`);
  const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  TOKEN = ((await login.json()) as { data: { access_token: string } }).data.access_token;
  const ws = await fetch(`${API_BASE}/api/v1/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ name: 'RT Walkthrough WS', slug: `rt-walk-${RUN_SUFFIX}` }),
  });
  if (ws.status !== 201)
    throw new Error(`workspace create failed: ${ws.status} ${await ws.text()}`);
  WORKSPACE_ID = ((await ws.json()) as { data: { id: string } }).data.id;
}

function psql(sql: string): string {
  // CI (GH service container): direct psql via env. Local compose: docker exec.
  const host = process.env.RUNTIMES_PG_HOST;
  if (host) {
    return execFileSync(
      'psql',
      [
        '-h',
        host,
        '-p',
        process.env.RUNTIMES_PG_PORT ?? '5432',
        '-U',
        process.env.RUNTIMES_PG_USER ?? 'mesh',
        '-d',
        process.env.RUNTIMES_PG_DATABASE ?? 'mesh',
        '-tAc',
        sql,
      ],
      {
        encoding: 'utf8',
        timeout: 30_000,
        env: { ...process.env, PGPASSWORD: process.env.RUNTIMES_PG_PASSWORD ?? 'mesh' },
      },
    );
  }
  return execFileSync(
    'docker',
    ['exec', '-i', PG_CONTAINER, 'psql', '-U', 'mesh', '-d', 'mesh', '-tAc', sql],
    { encoding: 'utf8', timeout: 30_000 },
  );
}

/** §2.2 schema:落库一台 online runtime + 一条 running execution(含 attempt #1)。 */
function seedOnlineRuntimeAndExecution(): void {
  psql(`
INSERT INTO runtimes
  (id, workspace_id, name, kind, status, labels, capabilities,
   hostname, os, cpu_cores, memory_mb, max_concurrent, current_load,
   last_heartbeat_at, heartbeat_interval_seconds, lease_grace_seconds)
VALUES
  ('${RUNTIME_ID}', '${WORKSPACE_ID}', 'e2e-build-01', 'self_hosted', 'online',
   '{"region":"intranet"}'::jsonb, '["version_control","python"]'::jsonb,
   'e2e-node-7', 'linux-x86_64', 8, 32768, 4, 1,
   now(), 15, 45)
ON CONFLICT (id) DO UPDATE SET status = 'online', last_heartbeat_at = now();
`);
  psql(`
INSERT INTO task_executions
  (id, workspace_id, agent_id, issue_id, trigger, status, priority,
   task_spec, label_requirements, required_capabilities, config_snapshot,
   max_attempts, queued_at, timeout_seconds)
VALUES
  ('${EXECUTION_ID}', '${WORKSPACE_ID}', NULL, NULL, 'assign', 'running', 100,
   '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '{}'::jsonb,
   3, now(), 1800)
ON CONFLICT (id) DO UPDATE SET status = 'running';
`);
  psql(`
INSERT INTO execution_attempts
  (id, workspace_id, execution_id, attempt_number, runtime_id,
   claimed_by_runtime_id, status, lease_seq, lease_expires_at,
   claimed_at, started_at, working_branch)
VALUES
  ('${ATTEMPT_ID}', '${WORKSPACE_ID}', '${EXECUTION_ID}', 1, '${RUNTIME_ID}',
   '${RUNTIME_ID}', 'running', 1, now() + interval '5 minutes',
   now(), now(), 'agent/${EXECUTION_ID}/a1')
ON CONFLICT (id) DO UPDATE SET status = 'running', started_at = now();
`);
}

async function loginReal(page: Page): Promise<void> {
  // dev-auth 栈无表单登录:会话经 authStore 持久化键注入(MES-107 起登录页无 dev 入口)
  await injectSession(page, TOKEN);
  await page.goto('/');
}

test.describe.configure({ mode: 'serial' });

test('runtimes 模块真实走查 + 截图存证(§4)', async ({ page }) => {
  await bootstrapWorld();

  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));

  await loginReal(page);

  // ① 列表页(自动化入口 → /runtimes)
  await page.goto('/runtimes');
  await expect(page.getByTestId('new-runtime-button')).toBeVisible({ timeout: 30_000 });
  await dismissOnboarding(page);
  await page.screenshot({ path: `${EVIDENCE_DIR}/01-runtimes-list.png` });

  // ② 向导创建:基本信息 → 激活码 + 安装脚本
  await page.getByTestId('new-runtime-button').click();
  await expect(page.getByTestId('runtime-wizard-basic')).toBeVisible();
  await page.getByTestId('runtime-wizard-name').fill('e2e-wizard-runtime');
  await page.getByTestId('runtime-wizard-max-concurrent').fill('2');
  await page.screenshot({ path: `${EVIDENCE_DIR}/02-wizard-basic.png` });
  await page.getByTestId('runtime-wizard-next').click();

  // 激活码一次性呈现(§4.3:ACT-* 或后端等价格式),安装脚本可审且无 curl|sh
  const activationCode = page.getByTestId('runtime-wizard-activation-code');
  await expect(activationCode).toBeVisible({ timeout: 30_000 });
  await expect(activationCode).not.toHaveText(/^\s*$/);
  const script = (await page.getByTestId('runtime-wizard-install-script').textContent()) ?? '';
  expect(script).toContain('sha256sum -c -');
  expect(script).toContain('--activation-file');
  expect(script).not.toMatch(/curl[^|\n]*\|\s*sh/);
  await page.screenshot({ path: `${EVIDENCE_DIR}/03-wizard-activation-code.png` });

  // ③ 落库一台 online runtime + running execution → 列表可见
  seedOnlineRuntimeAndExecution();
  await page.goto('/runtimes');
  const row = page.getByTestId(`runtime-row-${RUNTIME_ID}`);
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row).toContainText('e2e-build-01');
  await expect(row).toContainText('1/4'); // current_load/max_concurrent
  await page.screenshot({ path: `${EVIDENCE_DIR}/04-runtimes-seeded.png` });

  // ④ 执行详情:日志面板 + 取消按钮 + 元信息
  await page.goto(`/executions/${EXECUTION_ID}`);
  await expect(page.getByTestId('execution-detail-page')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('execution-panel-logs')).toBeVisible();
  await expect(page.getByTestId('execution-cancel-button')).toBeVisible();
  await expect(page.getByTestId('execution-branch')).toContainText(`agent/${EXECUTION_ID}/a1`);
  await page.screenshot({ path: `${EVIDENCE_DIR}/05-execution-detail.png` });

  // 凭证 Tab:值恒 ***(§4.10 红线)——有注入凭证时才断言内容
  await page.getByTestId('execution-tab-credentials').click();
  await expect(page.getByTestId('execution-panel-credentials')).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/06-execution-credentials.png` });

  expect(errors).toEqual([]);
});
