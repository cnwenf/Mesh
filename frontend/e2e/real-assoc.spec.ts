/**
 * 真实后端 label-property issue 关联层浏览器走查(MES-32 §4 验收):
 * 注册/登录 → 建区 → (经 API 预置标签/枚举字段/issue)→ issue 详情页
 * 标签 picker(联想 + 选中 + chip + 移除)→ 自定义字段面板(单选 / 数值 /
 * 布尔控件设值,刷新后持久化)→ 每步截图存证 e2e/evidence/assoc。
 *
 * 前置:真实后端栈运行中(MESH_AUTH_MODE=dev,API 8132 / gateway 8182,
 * 库 mesh_ui_mes32);dev server 由 playwright.mes32.config.ts 拉起。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `assoc-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `as${RUN}`;
const API = process.env.MES32_API_BASE ?? 'http://127.0.0.1:8132';
const EVIDENCE_DIR = process.env.MES32_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'assoc');

test.describe.configure({ mode: 'serial' });

async function registerAndLogin(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('Assoc Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await page.getByTestId('register-continue').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function createWorkspace(page: Page): Promise<void> {
  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('Assoc Walkthrough');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(800);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.waitForTimeout(500);
  await page.getByTestId('ws-wizard-skip').click();
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 15_000 });
}

interface Seed {
  workspaceId: string;
  issueId: string;
  bugLabelId: string;
  severityFieldId: string;
  majorOptionId: string;
  usersFieldId: string;
  docsFieldId: string;
}

/** 经 REST 预置关联层走查所需实体(设置页 UI 已由 MES-42 走查覆盖)。 */
async function seedViaApi(token: string): Promise<Seed> {
  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  };
  const call = async (method: string, path: string, body?: unknown) => {
    const res = await fetch(`${API}/api/v1${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${method} ${path} → ${res.status}: ${await res.text()}`);
    return (await res.json()) as { data: Record<string, unknown> };
  };
  const list = async (path: string) => {
    const res = await fetch(`${API}/api/v1${path}`, { headers });
    if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
    return (await res.json()) as { data: Record<string, unknown>[] };
  };

  const workspaces = await list('/workspaces');
  const workspaceId = workspaces.data.find((w) => w.slug === SLUG)?.id as string;
  if (!workspaceId) throw new Error(`workspace ${SLUG} not found via API`);

  const bug = await call('POST', `/workspaces/${workspaceId}/labels`, {
    name: 'bug',
    color: '#e5484d',
  });
  await call('POST', `/workspaces/${workspaceId}/labels`, {
    name: 'feature',
    color: '#30a46c',
  });
  const severity = (
    await call('POST', `/workspaces/${workspaceId}/custom-fields`, {
      name: 'Severity',
      field_key: 'severity',
      type: 'single_select',
      options: [
        { name: 'Minor', color: '#888888', position: 0 },
        { name: 'Major', color: '#f5a623', position: 1 },
      ],
    })
  ).data;
  const users = await call('POST', `/workspaces/${workspaceId}/custom-fields`, {
    name: 'Affected users',
    field_key: 'affected_users',
    type: 'number',
    config: { min: 0, max: 1_000_000 },
  });
  const docs = await call('POST', `/workspaces/${workspaceId}/custom-fields`, {
    name: 'Needs docs',
    field_key: 'needs_docs',
    type: 'boolean',
  });
  const issue = await call('POST', `/workspaces/${workspaceId}/issues`, {
    title: 'Assoc walkthrough issue',
  });
  const options = severity.options as { id: string; name: string }[];
  return {
    workspaceId,
    issueId: issue.data.id as string,
    bugLabelId: bug.data.id as string,
    severityFieldId: severity.id as string,
    majorOptionId: options.find((o) => o.name === 'Major')?.id as string,
    usersFieldId: users.data.id as string,
    docsFieldId: docs.data.id as string,
  };
}

test('label-property issue 关联层真实走查 + 截图存证', async ({ page }) => {
  await registerAndLogin(page);
  await createWorkspace(page);

  // zustand persist 镜像(state/authStore.ts,AUTH_STORAGE_KEY = mesh.auth.v1)。
  const token = await page.evaluate(() => {
    try {
      const raw = localStorage.getItem('mesh.auth.v1');
      const parsed = raw === null ? null : (JSON.parse(raw) as { state?: { token?: string } });
      return parsed?.state?.token ?? '';
    } catch {
      return '';
    }
  });
  expect(token.length).toBeGreaterThan(0);
  const seed = await seedViaApi(token);

  // ---- issue 详情页:标签 picker ------------------------------------------
  await page.goto(`/issues/${seed.issueId}`);
  await page.getByTestId('issue-detail').waitFor({ state: 'visible', timeout: 30_000 });
  // 标签编辑器挂载完毕(搜索框恒渲染;chip 列表空态高度塌缩故等搜索框)。
  await page.getByTestId('issue-label-search').waitFor({ state: 'visible', timeout: 30_000 });
  // 存证固定文件名(每轮覆盖,与 board/labels 等走查同惯例;去重门禁 check-evidence-unique.mjs)。
  await page.screenshot({ path: resolve(EVIDENCE_DIR, '01-detail-loaded.png') });

  // 联想 + 选中:输入 "bug" → 建议项 → chip 出现。
  await page.getByTestId('issue-label-search').fill('bug');
  await expect(page.getByTestId('issue-label-suggest')).toContainText('bug');
  await page.screenshot({ path: resolve(EVIDENCE_DIR, '02-label-suggest.png') });
  await page.getByTestId('issue-label-suggest').getByText('bug').click();
  await expect(page.getByTestId('issue-label-chips')).toContainText('bug', {
    timeout: 15_000,
  });
  await page.screenshot({ path: resolve(EVIDENCE_DIR, '03-label-attached.png') });

  // 持久化:刷新后 chip 仍在。
  await page.reload();
  await page.getByTestId('issue-label-chips').waitFor({ state: 'visible' });
  await expect(page.getByTestId('issue-label-chips')).toContainText('bug');

  // 移除:× 后 chip 消失。
  await page.getByLabel('Remove label bug').click();
  await expect(page.getByTestId('issue-label-chips')).not.toContainText('bug', {
    timeout: 15_000,
  });
  await page.screenshot({ path: resolve(EVIDENCE_DIR, '04-label-removed.png') });

  // ---- 自定义字段面板:按类型设值 -------------------------------------------
  // 单选下拉 → Major。
  const severity = page.getByTestId('issue-field-severity');
  await severity.waitFor({ state: 'visible' });
  await severity.selectOption(seed.majorOptionId);
  await page.waitForTimeout(1_200); // 提交 + 列表回读
  await page.screenshot({ path: resolve(EVIDENCE_DIR, '05-field-select.png') });

  // 数值 → 1500(blur 触发提交)。
  const users = page.getByTestId('issue-field-affected_users');
  await users.fill('1500');
  await users.blur();
  await page.waitForTimeout(1_200);

  // 布尔 → 勾选(受控 checkbox + 异步 PUT 回读,用最终态断言替代 check() 自检)。
  const docs = page.getByTestId('issue-field-needs_docs');
  await docs.click();
  await expect(docs).toBeChecked({ timeout: 15_000 });
  await page.waitForTimeout(1_200);
  await page.screenshot({ path: resolve(EVIDENCE_DIR, '06-fields-filled.png') });

  // 持久化:刷新后三值仍在。
  await page.reload();
  await expect(page.getByTestId('issue-field-severity')).toHaveValue(seed.majorOptionId, {
    timeout: 30_000,
  });
  await expect(page.getByTestId('issue-field-affected_users')).toHaveValue('1500');
  await expect(page.getByTestId('issue-field-needs_docs')).toBeChecked();
  // 刷新后重开标签联想下拉;fullPage 整帧存证(与 02/06 均不同帧:
  // 上方 picker 刷新后仍可用 + 下方字段面板三值回显)。
  await page.getByTestId('issue-label-search').waitFor({ state: 'visible' });
  await page.getByTestId('issue-label-search').fill('bug');
  await expect(page.getByTestId('issue-label-suggest')).toContainText('bug');
  await page.screenshot({ path: resolve(EVIDENCE_DIR, '07-fields-persisted.png'), fullPage: true });
});
