/**
 * PJ-H1 修复真实浏览器复验(project.md §3.4 / §4.2):项目设置页 lead 选择器对
 * 非 lead/admin 只读(禁用 + 提示);普通项目成员经浏览器直调 API 自指派 lead 返回
 * 403 且服务端 lead 不变;现 lead 改派成功后,新 lead 的选择器解锁并可置空。
 *
 * 前置:真实后端栈运行中(docker compose up postgres redis api worker gateway,
 * MESH_AUTH_MODE=dev),api 镜像已含 PJ-H1 修复;dev server 由
 * playwright.real.config.ts 拉起并指向 8000/8081。
 */
import { expect, test } from '@playwright/test';
import type { Browser, Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const OWNER_EMAIL = `lead-owner-${RUN}@corp.example`;
const MEMBER_EMAIL = `lead-member-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `lgate${RUN}`;
const KEY = `LG${RUN.slice(-2)}`;
const EVIDENCE_DIR = process.env.MES41_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'lead-gate');
const API_BASE = 'http://127.0.0.1:8000';

test.describe.configure({ mode: 'serial' });

let pageOwner: Page;
let pageMember: Page;
let projectId = '';
let memberId = '';

async function registerAndLogin(page: Page, name: string, email: string): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill(name);
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function authHeaders(page: Page): Promise<Record<string, string>> {
  return page.evaluate(() => {
    const raw = localStorage.getItem('mesh.auth.v1');
    const parsed = raw === null ? {} : (JSON.parse(raw) as { state?: { token?: string } });
    return {
      Authorization: `Bearer ${parsed.state?.token ?? ''}`,
      'Content-Type': 'application/json',
    };
  });
}

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  pageOwner = await (await browser.newContext()).newPage();
  pageMember = await (await browser.newContext()).newPage();

  // ① Owner 注册 → 建区 → 建项目
  await registerAndLogin(pageOwner, 'Lead Owner', OWNER_EMAIL);
  await pageOwner.getByTestId('ws-switcher-button').click();
  await pageOwner.getByTestId('ws-switcher-create').click();
  await pageOwner.getByTestId('ws-wizard-name-input').fill('Lead Gate');
  await pageOwner.getByTestId('ws-wizard-next').click();
  await pageOwner.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await pageOwner.waitForTimeout(800);
  await pageOwner.getByTestId('ws-wizard-next-slug').click();
  await pageOwner.waitForTimeout(500);
  await pageOwner.getByTestId('ws-wizard-skip').click();
  await expect(pageOwner).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 15_000 });

  await pageOwner.goto('/projects');
  await pageOwner.getByTestId('new-project-button').click();
  await pageOwner.getByLabel('Name').fill('Lead Gate Project');
  await pageOwner.getByLabel(/Key/).fill(KEY);
  await pageOwner.getByTestId('create-project-submit').click();
  await expect(pageOwner).toHaveURL(/\/projects\/[0-9a-f-]+$/, { timeout: 15_000 });
  projectId = (pageOwner.url().match(/\/projects\/([0-9a-f-]+)$/) ?? [])[1];
  expect(projectId).toBeTruthy();

  // ② 邀请链接(max_uses=1)→ Member 注册并接受邀请入区
  await pageOwner.goto(`/w/${SLUG}/settings`);
  await expect(pageOwner.getByTestId('invitation-create')).toBeVisible({ timeout: 30_000 });
  await pageOwner.getByTestId('invite-max-uses').fill('1');
  await pageOwner.getByTestId('invite-submit').click();
  const linkUrl = pageOwner.getByTestId('invite-link-url');
  await expect(linkUrl).toBeVisible({ timeout: 30_000 });
  const invitePath = new URL((await linkUrl.textContent()) ?? '').pathname;

  await pageMember.goto(invitePath);
  await expect(pageMember.getByTestId('invite-preview')).toBeVisible({ timeout: 30_000 });
  await pageMember.getByTestId('invite-login').click();
  await expect(pageMember).toHaveURL(/\/login\?next=/);
  await pageMember.getByTestId('login-mode-register').click();
  await pageMember.getByTestId('login-display-name').fill('Lead Member');
  await pageMember.getByTestId('login-email').fill(MEMBER_EMAIL);
  await pageMember.getByTestId('login-password').fill(PASSWORD);
  await pageMember.getByTestId('login-account-submit').click();
  await expect(pageMember.getByTestId('invite-accept')).toBeVisible({ timeout: 30_000 });
  await pageMember.getByTestId('invite-accept').click();
  await expect(pageMember.getByTestId('invite-accepted')).toBeVisible({ timeout: 30_000 });
  await pageMember.getByTestId('invite-enter').click();

  // ③ 取得 Member 的统一成员 id,并经真实 API 加入项目(脚手架,与本修复点无关)
  const ownerHeaders = await authHeaders(pageOwner);
  const me = (await (
    await pageOwner.request.get(`${API_BASE}/api/v1/users/me`, { headers: ownerHeaders })
  ).json()) as { data: { memberships: { workspace_id: string }[] } };
  const wsId = me.data.memberships[0].workspace_id;
  const roster = (await (
    await pageOwner.request.get(`${API_BASE}/api/v1/workspaces/${wsId}/members`, {
      headers: ownerHeaders,
    })
  ).json()) as { data: { id: string; display_name: string }[] };
  memberId = roster.data.find((entry) => entry.display_name === 'Lead Member')?.id ?? '';
  expect(memberId).toBeTruthy();
  const addResp = await pageOwner.request.post(
    `${API_BASE}/api/v1/projects/${projectId}/members`,
    { headers: ownerHeaders, data: { member_id: memberId, role: 'member' } },
  );
  expect(addResp.status()).toBe(201);
});

test('① 普通成员的 lead 选择器只读,API 自指派 403 且落库不变(PJ-H1)', async () => {
  await pageMember.goto(`/projects/${projectId}/settings`);
  const leadSelect = pageMember.getByTestId('settings-lead');
  await expect(leadSelect).toBeVisible({ timeout: 15_000 });
  await expect(leadSelect).toBeDisabled();
  await expect(pageMember.getByTestId('settings-lead-hint')).toBeVisible();
  await pageMember.screenshot({ path: `${EVIDENCE_DIR}/01-member-lead-readonly.png` });

  // 绕过 UI 直调真实 API 自指派 → 403 forbidden(后端权威校验)
  const headers = await authHeaders(pageMember);
  const patchResp = await pageMember.request.patch(`${API_BASE}/api/v1/projects/${projectId}`, {
    headers,
    data: { lead_member_id: memberId },
  });
  expect(patchResp.status()).toBe(403);
  expect(((await patchResp.json()) as { error: { code: string } }).error.code).toBe('forbidden');

  // 服务端 lead 未被篡改
  const detailResp = await pageMember.request.get(`${API_BASE}/api/v1/projects/${projectId}`, {
    headers,
  });
  const detail = (await detailResp.json()) as { data: { lead_member_id: string | null } };
  expect(detail.data.lead_member_id).toBeNull();

  // 提权环闭合:自指派失败后仍不能删除项目
  const deleteResp = await pageMember.request.delete(`${API_BASE}/api/v1/projects/${projectId}`, {
    headers,
  });
  expect(deleteResp.status()).toBe(403);
});

test('② 现 lead 经 UI 改派成功,新 lead 的选择器解锁并可置空(PJ-H1)', async () => {
  await pageOwner.goto(`/projects/${projectId}/settings`);
  const leadSelect = pageOwner.getByTestId('settings-lead');
  await expect(leadSelect).toBeVisible({ timeout: 15_000 });
  await expect(leadSelect).toBeEnabled();
  await expect(pageOwner.getByTestId('settings-lead-hint')).toHaveCount(0);

  await leadSelect.selectOption(memberId);
  await pageOwner.getByTestId('settings-save').click();
  await expect(pageOwner.getByText(/saved|已保存/).first()).toBeVisible({ timeout: 15_000 });
  await pageOwner.screenshot({ path: `${EVIDENCE_DIR}/02-owner-reassigned-lead.png` });

  // 新 lead(原普通成员)的设置页:选择器解锁
  await pageMember.goto(`/projects/${projectId}/settings`);
  await expect(pageMember.getByTestId('settings-lead')).toBeEnabled({ timeout: 15_000 });
  await expect(pageMember.getByTestId('settings-lead-hint')).toHaveCount(0);
  await expect(pageMember.getByTestId('settings-lead')).toHaveValue(memberId);
  await pageMember.screenshot({ path: `${EVIDENCE_DIR}/03-new-lead-unlocked.png` });

  // 新 lead 置空负责人 → 成功(现 lead 允许置空)
  await pageMember.getByTestId('settings-lead').selectOption('');
  await pageMember.getByTestId('settings-save').click();
  await expect(pageMember.getByText(/saved|已保存/).first()).toBeVisible({ timeout: 15_000 });
  await pageMember.reload();
  await expect(pageMember.getByTestId('settings-lead')).toHaveValue('', { timeout: 15_000 });
});
