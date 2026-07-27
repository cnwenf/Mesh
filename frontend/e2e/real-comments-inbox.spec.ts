/**
 * comment-inbox 真实后端浏览器走查(MES-58):注册/登录 → 建区 → 建 issue →
 * 发表评论 → 表情回应 → 回复 → 解决线程 → 打开收件箱 → 标已读。每步截图存证。
 *
 * 前置:真实后端栈运行中(MESH_AUTH_MODE=dev,8000/8081);dev server 由
 * playwright.mes58.config.ts 拉起并指向真实后端。本 spec 由编排器在真实后端
 * 就绪后运行(本任务环境无后端,不在本地执行)。
 */
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `comments-${RUN}@corp.example`;
const PEER_EMAIL = `comments-peer-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `cmt${RUN}`;
const EVIDENCE_DIR = process.env.MES58_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'comments-inbox');
// REAL_* are passed to the playwright runner; the API fixture reads the runner
// env (VITE_* only reaches the vite webServer process).
const API =
  process.env.REAL_API_BASE_URL ?? process.env.VITE_MESH_API_BASE_URL ?? 'http://127.0.0.1:8000';

async function apiRegister(request: APIRequestContext, email: string, name: string): Promise<string> {
  await request.post(`${API}/api/v1/auth/register`, {
    data: { email, password: PASSWORD, display_name: name },
  });
  const login = await request.post(`${API}/api/v1/auth/login`, {
    data: { email, password: PASSWORD },
  });
  return String((await login.json()).data.access_token);
}

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

test('comment-inbox 真实走查 + 截图存证', async ({ page, request }) => {
  // ---- 数据准备(API 层,快且确定)---------------------------------------
  // owner 已在 registerAndLogin 注册;这里取 token 建区/建 issue/邀同伴。
  await registerAndLogin(page);
  const ownerToken = (await request.post(`${API}/api/v1/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  }).then((r) => r.json())).data.access_token as string;
  const wsId = (await request.post(`${API}/api/v1/workspaces`, {
    headers: { Authorization: `Bearer ${ownerToken}` },
    data: { name: 'Comments Walkthrough', slug: SLUG },
  }).then((r) => r.json())).data.id as string;
  const ISSUE_TITLE = 'Login redirect bug';
  const issueId = (await request.post(`${API}/api/v1/workspaces/${wsId}/issues`, {
    headers: { Authorization: `Bearer ${ownerToken}` },
    data: { title: ISSUE_TITLE },
  }).then((r) => r.json())).data.id as string;
  // 同伴加入并经 API 评论 → owner 收到 comment_created 通知(跨成员,不被自我抑制),
  // 使 owner 收件箱在打开时**非空**,06-inbox 存证与 real-inbox-notify 的空态
  // 07-inbox-empty 视觉不同(存证唯一性 #1)。
  const inv = await request.post(`${API}/api/v1/workspaces/${wsId}/invitations`, {
    headers: { Authorization: `Bearer ${ownerToken}` },
    data: { emails: [PEER_EMAIL], role: 'member' },
  });
  const inviteToken = String((await inv.json()).data[0].invite_link).split('/').pop();
  const peerToken = await apiRegister(request, PEER_EMAIL, 'Comments Peer');
  await request.post(`${API}/api/v1/invitations/accept`, {
    headers: { Authorization: `Bearer ${peerToken}` },
    data: { token: inviteToken },
  });
  const peerComment = await request.post(`${API}/api/v1/issues/${issueId}/comments`, {
    headers: { Authorization: `Bearer ${peerToken}` },
    data: { body_markdown: '@owner 我来跟进这个重定向问题。' },
  });
  expect(peerComment.status()).toBe(201);

  // ---- UI:进入 issue 详情(按标题定位,与 API 建的 issue 一致)---------
  await page.goto('/issues');
  await page.locator(`[data-testid^="issue-row-"]`).filter({ hasText: ISSUE_TITLE }).first()
    .locator('a').first().click();
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

  // ---- 收件箱(非空:含同伴评论的通知)---------------------------------
  await page.goto('/inbox');
  await expect(page.getByTestId('inbox-page')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('[data-testid^="inbox-row-"]').first()).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/06-inbox.png` });
  // 注:标已读存证由 real-inbox-notify 的 10-inbox-read 提供;本 spec 占用 01-06,
  // 与 notify 的 07-10 槽位不重叠,保证两 spec 任意执行顺序存证均唯一。
});
