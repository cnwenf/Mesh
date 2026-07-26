/**
 * comment-inbox 真实后端浏览器走查(MES-58):注册/登录 → 建区 → 建 issue →
 * 发表评论 → 表情回应 → 回复 → 解决线程 → 打开收件箱 → 标已读。每步截图存证。
 *
 * 前置:真实后端栈运行中(MESH_AUTH_MODE=dev,8000/8081);dev server 由
 * playwright.mes58.config.ts 拉起并指向真实后端。本 spec 由编排器在真实后端
 * 就绪后运行(本任务环境无后端,不在本地执行)。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `comments-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `cmt${RUN}`;
const EVIDENCE_DIR = process.env.MES58_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'comments-inbox');

test.describe.configure({ mode: 'serial' });

async function registerAndLogin(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('Comments Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  // 注册后进入「查收验证邮件」过渡态(dev 模式);会话已建立,点「继续」进入主壳。
  await page.getByTestId('register-continue').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function createWorkspace(page: Page): Promise<void> {
  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('Comments Walkthrough');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(800);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.waitForTimeout(500);
  await page.getByTestId('ws-wizard-skip').click();
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 15_000 });
}

async function createIssue(page: Page): Promise<void> {
  await page.goto('/issues');
  await page.getByTestId('issue-open-create').click();
  await page.getByTestId('issue-create-title').fill('Login redirect bug');
  await page.getByRole('button', { name: /Create|创建/ }).first().click();
  // 列表出现新建 issue 行
  await expect(page.locator('[data-testid^="issue-row-"]').first()).toBeVisible({ timeout: 15_000 });
}

test('comment-inbox 真实走查 + 截图存证', async ({ page }) => {
  await registerAndLogin(page);
  await createWorkspace(page);
  await createIssue(page);

  // 进入 issue 详情(评论区随详情页挂载)
  await page.locator('[data-testid^="issue-row-"] a').first().click();
  await expect(page.getByTestId('comments-panel')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/01-comments-empty.png` });

  // ---- 发表评论 -----------------------------------------------------------
  await page.getByTestId('composer-input').fill('已定位问题,详见日志。');
  await page.getByTestId('composer-submit').click();
  const firstCard = page.locator('[data-testid^="comment-card-"]').first();
  await expect(firstCard).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/02-comment-posted.png` });

  // ---- 表情回应 -----------------------------------------------------------
  await firstCard.getByTestId('reaction-add').click();
  await firstCard.getByTestId('reaction-pick-👍').click();
  await expect(firstCard.getByTestId('reaction-👍')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/03-reaction-added.png` });

  // ---- 回复(单层折叠)----------------------------------------------------
  await firstCard.getByTestId(/comment-reply-/).click();
  // 回复输入框出现(对该线程根)
  const replyInputs = page.getByTestId('composer-input');
  await replyInputs.last().fill('同意,我来跟进。');
  await page.getByTestId('composer-submit').last().click();
  // 折叠开关渲染于线程容器内(评论卡片下方,§4.1「有回复的评论下方」)
  const firstThread = page.locator('[data-testid^="thread-"]').first();
  await expect(firstThread.getByTestId(/thread-toggle-/)).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/04-reply-posted.png` });

  // ---- 解决线程 -----------------------------------------------------------
  await firstCard.getByTestId(/comment-resolve-/).click();
  await expect(firstCard.getByTestId('comment-resolved-tag')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/05-thread-resolved.png` });

  // ---- 收件箱 -------------------------------------------------------------
  await page.goto('/inbox');
  await expect(page.getByTestId('inbox-page')).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/06-inbox.png` });

  // 若有通知行则标已读(单用户自评论可能被自我抑制,故行存在才操作,保持确定性)。
  const firstRow = page.locator('[data-testid^="inbox-row-"]').first();
  if (await firstRow.isVisible().catch(() => false)) {
    const markRead = firstRow.getByTestId(/inbox-mark-read-/);
    if (await markRead.isVisible().catch(() => false)) {
      await markRead.click();
      await page.screenshot({ path: `${EVIDENCE_DIR}/07-inbox-marked-read.png` });
    }
  }
});
