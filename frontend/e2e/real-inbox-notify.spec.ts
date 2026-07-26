/**
 * 收件箱通知 UI 真实走查(MES-58,双成员):owner 建区建 issue,bob 经 API
 * 发表评论 → outbox fan-out(worker)→ owner 收件箱经 WebSocket 实时出现
 * 通知行与铃铛徽标 → 标记已读后徽标消除。截图存证。
 *
 * 前置:真实后端栈运行中(含 worker),dev server 由 playwright.mes58.config.ts
 * 拉起。与 real-comments-inbox.spec.ts 同批运行。
 */
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const OWNER_EMAIL = `inbox-owner-${RUN}@corp.example`;
const BOB_EMAIL = `inbox-bob-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `ibx${RUN}`;
const EVIDENCE_DIR = process.env.MES58_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'comments-inbox');
// REAL_* are passed to the playwright runner; VITE_* only reach the vite
// webServer process, so the request fixture must read the runner env.
const API =
  process.env.REAL_API_BASE_URL ?? process.env.VITE_MESH_API_BASE_URL ?? 'http://127.0.0.1:8000';

async function apiRegister(request: APIRequestContext, email: string, name: string) {
  await request.post(`${API}/api/v1/auth/register`, {
    data: { email, password: PASSWORD, display_name: name },
  });
  const login = await request.post(`${API}/api/v1/auth/login`, {
    data: { email, password: PASSWORD },
  });
  const body = await login.json();
  return String(body.data.access_token);
}

async function uiLogin(page: Page, email: string): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

test('inbox 通知实时送达 + 标记已读 + 铃铛徽标(双成员,真实后端)', async ({ page, request }) => {
  // ---- 数据准备(API 层,快且确定)---------------------------------------
  const ownerToken = await apiRegister(request, OWNER_EMAIL, 'Inbox Owner');
  const wsResp = await request.post(`${API}/api/v1/workspaces`, {
    headers: { Authorization: `Bearer ${ownerToken}` },
    data: { name: 'Inbox Walkthrough', slug: SLUG },
  });
  const workspaceId = String((await wsResp.json()).data.id);
  const issueResp = await request.post(`${API}/api/v1/workspaces/${workspaceId}/issues`, {
    headers: { Authorization: `Bearer ${ownerToken}` },
    data: { title: 'Inbox 通知走查' },
  });
  const issueId = String((await issueResp.json()).data.id);

  // 邀请 bob 并兑换
  const invResp = await request.post(`${API}/api/v1/workspaces/${workspaceId}/invitations`, {
    headers: { Authorization: `Bearer ${ownerToken}` },
    data: { emails: [BOB_EMAIL], role: 'member' },
  });
  const inviteToken = String((await invResp.json()).data[0].invite_link).split('/').pop();
  const bobToken = await apiRegister(request, BOB_EMAIL, 'Inbox Bob');
  await request.post(`${API}/api/v1/invitations/accept`, {
    headers: { Authorization: `Bearer ${bobToken}` },
    data: { token: inviteToken },
  });

  // ---- owner 登录 UI,先看空收件箱 --------------------------------------
  await uiLogin(page, OWNER_EMAIL);
  await page.goto('/inbox');
  await expect(page.getByTestId('inbox-page')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/07-inbox-empty.png` });

  // ---- bob 经 API 发表评论 → fan-out → owner 收件箱实时出现 -------------
  const commentResp = await request.post(`${API}/api/v1/issues/${issueId}/comments`, {
    headers: { Authorization: `Bearer ${bobToken}` },
    data: { body_markdown: '@owner 这个我来处理,稍后给结论。' },
  });
  expect(commentResp.status()).toBe(201);

  // 实时推送(WS)或轮询兜底:等待通知行出现
  const row = page.locator('[data-testid^="inbox-row-"]').first();
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('inbox-badge')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/08-inbox-notified.png` });

  // 铃铛下拉预览
  await page.getByTestId('inbox-bell').click();
  await expect(page.getByTestId('inbox-dropdown')).toBeVisible();
  await expect(page.locator('[data-testid^="inbox-bell-item-"]').first()).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/09-bell-dropdown.png` });
  await page.keyboard.press('Escape');

  // ---- 标记已读:未读圆点与徽标消除 --------------------------------------
  const unreadDot = page.locator('[data-testid^="inbox-unread-dot-"]').first();
  await expect(unreadDot).toBeVisible();
  const markRead = page.locator('[data-testid^="inbox-mark-read-"]').first();
  await markRead.click();
  await expect(page.locator('[data-testid^="inbox-unread-dot-"]')).toHaveCount(0, {
    timeout: 10_000,
  });
  await expect(page.getByTestId('inbox-badge')).toHaveCount(0, { timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/10-inbox-read.png` });
});
