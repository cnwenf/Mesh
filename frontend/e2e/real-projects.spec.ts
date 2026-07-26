/**
 * 真实后端 project 模块浏览器走查(MES-30 §4 验收):注册/登录 → 建区 → 项目列表 →
 * 新建项目(key 自动建议 + 重复前缀 409)→ 详情(健康度灯点击更新 + 里程碑逾期)→
 * 归档只读 422。每步截图存证,默认写入仓库内 e2e/evidence/projects(随 PR 提交,可复现)。
 *
 * 前置:真实后端栈运行中(docker compose up postgres redis api worker gateway,
 * MESH_AUTH_MODE=dev);dev server 由 playwright.real.config.ts 拉起并指向 8000/8081。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `proj-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `proj${RUN}`;
// 默认提交到仓库内,使截图可复现(可用 MES30_EVIDENCE_DIR 覆盖)
const EVIDENCE_DIR = process.env.MES30_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'projects');

test.describe.configure({ mode: 'serial' });

async function registerAndLogin(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('Proj Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function createWorkspace(page: Page): Promise<void> {
  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('Project Walkthrough');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(800);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.waitForTimeout(500);
  await page.getByTestId('ws-wizard-skip').click();
  // 向导完成后跳入 /w/{slug}
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 15_000 });
}

test('project 模块真实走查 + 截图存证', async ({ page }) => {
  await registerAndLogin(page);
  await createWorkspace(page);

  // 列表页(空态)
  await page.goto('/projects');
  await expect(page.getByTestId('new-project-button')).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/01-projects-empty.png` });

  // 新建项目(中文名无 Latin 字符 → 无自动建议,手动填 key 校验格式)
  await page.getByTestId('new-project-button').click();
  await page.getByLabel('Name').fill('官网改版');
  const keyField = page.getByLabel(/Key/);
  await keyField.fill('WEB');
  await expect(keyField).toHaveValue('WEB');
  await page.getByTestId('create-project-submit').click();
  // §4.3 创建后进入新项目详情
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]+$/, { timeout: 15_000 });
  const projectId = (page.url().match(/\/projects\/([0-9a-f-]+)$/) ?? [])[1];
  expect(projectId).toBeTruthy();
  await expect(page.getByTestId('project-detail-header')).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/02-project-detail.png` });

  // 健康度灯点击更新(§4.2)
  await page.getByTestId('health-light-button').click();
  await expect(page.getByTestId('health-update-form')).toBeVisible();
  await page.getByTestId('health-select').selectOption('at_risk');
  await page.getByTestId('health-update-submit').click();
  await expect(page.getByText(/At risk|有风险/).first()).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/03-health-updated.png` });

  // 里程碑 + 逾期(§4.2 时间线/逾期标红)
  await page.getByTestId('tab-milestones').click();
  await page.getByTestId('create-milestone-button').click();
  await page.getByTestId('milestone-title-input').fill('Beta 发布');
  await page.getByTestId('milestone-target-input').fill('2026-01-15');
  await page.getByTestId('create-milestone-submit').click();
  await expect(page.getByText(/Overdue|已逾期/).first()).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/04-milestone-overdue.png` });

  // 归档 → 只读 422(经详情页设置链接进入设置页)
  await page.goto(`/projects/${projectId}`);
  await expect(page.getByTestId('project-detail-header')).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('settings-link').click();
  await expect(page.getByTestId('settings-name')).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('settings-archive-toggle').click();
  await page.waitForTimeout(800);
  await page.getByTestId('settings-name').fill('归档后改名');
  await page.getByTestId('settings-save').click();
  await expect(page.getByText(/archived|归档/).first()).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/05-archived-readonly.png` });

  // 重复前缀 409 内联(T19)——放最后,避免影响前述导航
  await page.goto('/projects');
  await page.getByTestId('new-project-button').click();
  await page.getByLabel('Name').fill('冲突项目');
  const keyInput = page.getByLabel(/Key/);
  await keyInput.fill('');
  await keyInput.fill('WEB');
  await page.getByTestId('create-project-submit').click();
  await expect(page.getByText(/already taken|已被占用/).first()).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/06-key-conflict.png` });
});
