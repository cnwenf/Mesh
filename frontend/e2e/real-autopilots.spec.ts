/**
 * Autopilot 模块真实后端浏览器走查(autopilot.md §4 验收):
 *   ① 真实注册/登录 → /autopilots 列表(空态);
 *   ② 编辑器创建规则(触发器/动作/护栏四段)→ 保存并启用 → 详情页;
 *   ③ 手动 test-run → 运行时间线出现运行行(worker executor 真实执行
 *      send_notification 动作至 succeeded);
 *   ④ 运行详情页(触发快照 / 尝试 / 产物);
 *   ⑤ Webhook 配置页创建凭据(明文仅显示一次,whk_/whs_);
 *   ⑥ kill switch 二次确认暂停全部 → 恢复。
 *
 * 前置:后端栈运行中(api + worker + gateway + postgres + redis + minio,
 * MESH_AUTH_MODE=dev);dev server 由 playwright.autopilots.config.ts 拉起。
 * 每步截图存证 e2e/evidence/autopilots(随 PR 提交;字节互异)。
 */
import { expect, test } from '@playwright/test';
import { dismissOnboarding, injectSession } from './helpers';
import type { Page } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE_DIR = process.env.AUTOPILOTS_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'autopilots');
mkdirSync(EVIDENCE_DIR, { recursive: true });

const API_BASE = process.env.AUTOPILOTS_API_BASE ?? 'http://127.0.0.1:8160';

const RUN_SUFFIX = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
let WORKSPACE_ID = '';
let WORKSPACE_SLUG = '';
let TOKEN = '';

async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
): Promise<{ status: number; data: T }> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json = (await resp.json().catch(() => ({}))) as { data: T };
  if (resp.status >= 400)
    throw new Error(`${method} ${path} → ${resp.status}: ${JSON.stringify(json)}`);
  return { status: resp.status, data: json.data };
}

async function bootstrapWorld(): Promise<void> {
  const email = `ap-walkthrough-${RUN_SUFFIX}@example.com`;
  const password = `Ap1!${randomBytes(24).toString('base64url')}`;
  const register = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, display_name: 'AP Walkthrough' }),
  });
  if (register.status !== 201)
    throw new Error(`register failed: ${register.status} ${await register.text()}`);
  const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  TOKEN = ((await login.json()) as { data: { access_token: string } }).data.access_token;
  WORKSPACE_SLUG = `ap-walk-${RUN_SUFFIX}`;
  const ws = await api<{ id: string }>('POST', '/api/v1/workspaces', {
    name: 'AP Walkthrough WS',
    slug: WORKSPACE_SLUG,
  });
  WORKSPACE_ID = ws.data.id;
  // provision an agent member so the rule editor's executor pickers have an
  // entry (the id itself is never referenced by the walkthrough)
  await api('POST', `/api/v1/workspaces/${WORKSPACE_ID}/agents`, {
    name: `ap-agent-${RUN_SUFFIX}`,
  });
}

async function cleanupWorld(): Promise<void> {
  if (TOKEN === '' || WORKSPACE_ID === '') return;

  // Rotation invalidates every token that could have appeared during a failed
  // assertion while discarding the replacement plaintext without rendering it.
  const secrets = await api<Array<{ id: string; status: string }>>(
    'GET',
    `/api/v1/workspaces/${WORKSPACE_ID}/webhook-secrets`,
  );
  for (const secret of secrets.data) {
    if (secret.status === 'active') {
      await api('POST', `/api/v1/workspaces/${WORKSPACE_ID}/webhook-secrets/${secret.id}/rotate`);
    }
  }
  await api('DELETE', `/api/v1/workspaces/${WORKSPACE_ID}`, { confirm_slug: WORKSPACE_SLUG });
  await api('POST', '/api/v1/auth/logout-all', {});
  TOKEN = '';
}

async function loginReal(page: Page): Promise<void> {
  // dev-auth 栈无表单登录:会话经 authStore 持久化键注入(MES-107 起登录页无 dev 入口)
  await injectSession(page, TOKEN);
  await page.goto('/');
}

test.describe.configure({ mode: 'serial' });

test.afterAll(async () => {
  await cleanupWorld();
});

test('autopilot 模块真实走查 + 截图存证(§4)', async ({ page }) => {
  await bootstrapWorld();

  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));

  await loginReal(page);

  // ① 列表页:空态 + 侧边导航入口
  await page.goto('/autopilots');
  await expect(page.getByTestId('autopilots-page')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('autopilot-create')).toBeVisible();
  await dismissOnboarding(page);
  await page.screenshot({ path: `${EVIDENCE_DIR}/01-autopilots-list-empty.png` });

  // ② 编辑器:四段折叠区块,创建定时规则(send_notification 动作)
  await page.getByTestId('autopilot-create').click();
  await expect(page.getByTestId('autopilot-editor')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId('autopilot-editor-name').fill(`每日进展汇总-${RUN_SUFFIX}`);
  // 触发器段默认展开(schedule + cron + 时区)
  await expect(page.getByTestId('autopilot-editor-cron')).toBeVisible();
  await page.getByTestId('autopilot-editor-cron').fill('0 9 * * 1-5');
  await page.screenshot({ path: `${EVIDENCE_DIR}/02-editor-trigger.png` });
  // 动作段:改为发通知(无需 executor)
  await page.getByTestId('autopilot-section-actions-toggle').click();
  await page.getByTestId('autopilot-action-type-0').selectOption('send_notification');
  await page.getByTestId('autopilot-editor-action-message').fill('每日汇总完成');
  // 护栏段:默认值预填可见
  await page.getByTestId('autopilot-section-guardrails-toggle').click();
  await expect(page.getByTestId('autopilot-editor-rate-max')).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/03-editor-guardrails.png` });
  // 保存并启用 → 跳转详情页
  await page.getByTestId('autopilot-editor-save').click();
  await expect(page.getByTestId('autopilot-detail-name')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('autopilot-detail-name')).toContainText(
    `每日进展汇总-${RUN_SUFFIX}`,
  );
  await page.screenshot({ path: `${EVIDENCE_DIR}/04-rule-detail.png` });

  // ③ 手动 test-run → 跳转运行详情页;worker executor 真实执行 send_notification
  //    动作至 succeeded
  await page.getByTestId('autopilot-detail-test-run').click();
  await page.getByTestId('autopilot-test-submit').click();
  await expect(page.getByTestId('autopilot-run-snapshot')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('autopilot-run-status')).toContainText(/succeeded|成功/i, {
    timeout: 30_000,
  });
  await page.screenshot({ path: `${EVIDENCE_DIR}/05-run-detail-succeeded.png` });

  // ④ 规则详情页:运行时间线出现该运行行
  await page.goto('/autopilots');
  await page.locator('tr[data-testid^="autopilot-row-"]').first().click();
  await expect(page.getByTestId('autopilot-runs-table')).toBeVisible({ timeout: 30_000 });
  // Run history is an accessible timeline button list rather than a table row;
  // keep the stable test id independent from the presentation element.
  const firstRunRow = page.locator('[data-testid^="autopilot-run-row-"]').first();
  await expect(firstRunRow).toContainText(/succeeded|成功/i, { timeout: 30_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/06-runs-timeline.png` });

  // ⑤ Webhook 配置页:创建凭据,明文仅显示一次
  await page.goto('/webhooks');
  await expect(page.getByTestId('webhook-create-secret')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId('webhook-label-input').fill('e2e-alert');
  await page.getByTestId('webhook-create-secret').click();
  const fresh = page.getByTestId('webhook-fresh-credential');
  await expect(fresh).toBeVisible({ timeout: 30_000 });
  await expect(fresh).toContainText('whk_');
  await expect(fresh).toContainText('whs_');
  await fresh.getByRole('button').click();
  await expect(fresh).not.toBeVisible();
  await expect(page.getByTestId('webhook-secrets-table')).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/07-webhook-credential-dismissed.png` });

  // ⑥ kill switch:二次确认暂停全部 → 规则 paused → 恢复
  await page.goto('/autopilots');
  await expect(page.getByTestId('autopilot-kill-switch-button')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId('autopilot-kill-switch-button').click();
  await page.getByTestId('autopilot-kill-reason').fill('走查:紧急止血');
  await page.getByTestId('autopilot-kill-confirm').click();
  await expect(page.locator('tr[data-testid^="autopilot-row-"]').first()).toContainText(
    /paused|已暂停/i,
    { timeout: 30_000 },
  );
  await page.screenshot({ path: `${EVIDENCE_DIR}/08-kill-switch-engaged.png` });
  await page.getByTestId('autopilot-kill-switch-button').click();
  await page.getByTestId('autopilot-kill-confirm').click();
  await expect(page.locator('tr[data-testid^="autopilot-row-"]').first()).toContainText(
    /active|已启用/i,
    { timeout: 30_000 },
  );
  await page.screenshot({ path: `${EVIDENCE_DIR}/09-kill-switch-restored.png` });

  expect(errors).toEqual([]);
});
