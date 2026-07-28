/**
 * Onboarding UI 真人走查(真实全栈:compose API/worker/gateway + Vite 前端)。
 * 清单渲染 / CTA 深链 / 空状态 / 自动完成实时刷新 / dismiss-restore / 庆祝态。
 * 存证截图 → e2e/evidence/onboarding/。
 */
import { rm } from 'node:fs/promises';
import { mkdirSync as mkdir } from 'node:fs';
import { chromium } from 'playwright';

const API = 'http://127.0.0.1:18000';
// compose SPA 前门(nginx 同源代理 /api → api、/ws → gateway,无 CORS)
const UI = 'http://127.0.0.1:13001';
const EMAIL = `walk-${Date.now().toString(36)}@example.com`;
const PASSWORD = 'UI-Walkthrough-123';
const EVIDENCE = new URL('./evidence/onboarding/', import.meta.url).pathname;

const log = (...args) => console.log('[walkthrough]', ...args);

async function api(path, { method = 'GET', token = null, body = null } = {}) {
  const resp = await fetch(`${API}/api/v1${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await resp.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  if (!resp.ok) throw new Error(`API ${method} ${path} → ${resp.status}: ${text.slice(0, 300)}`);
  return data?.data ?? data;
}

mkdir(EVIDENCE, { recursive: true });

// 1. 真实 API 建账号(注册流程属 auth 模块 e2e 覆盖;此处聚焦 onboarding UI)
await api('/auth/register', {
  method: 'POST',
  body: { email: EMAIL, password: PASSWORD, display_name: 'Walk Owner' },
});
log('account registered:', EMAIL);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const shot = async (name) => {
  await page.screenshot({ path: `${EVIDENCE}${name}`, fullPage: false });
  log('screenshot', name);
};

try {
  // 2. 真实登录 UI
  await page.goto(`${UI}/login`, { waitUntil: 'networkidle' });
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await page.waitForURL(/\/(inbox)?$/, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1500);
  log('logged in, url:', page.url());

  // 3. 建工作区(工作区切换器「新建」→ 真实向导 UI)——建区事务即播种清单
  await page.getByTestId('ws-switcher-button').click();
  await page.waitForTimeout(600);
  await page.getByTestId('ws-switcher-create').click();
  await page.waitForTimeout(800);
  if (await page.getByTestId('ws-wizard-name-input').isVisible({ timeout: 8000 }).catch(() => false)) {
    await page.getByTestId('ws-wizard-name-input').fill(`Walk WS ${Date.now().toString(36)}`);
    await page.getByTestId('ws-wizard-next').click();
    await page.waitForTimeout(800);
    const nextSlug = page.getByTestId('ws-wizard-next-slug');
    if (await nextSlug.isVisible({ timeout: 3000 }).catch(() => false)) {
      await nextSlug.click();
      await page.waitForTimeout(800);
    }
    await page.getByTestId('ws-wizard-create').click({ timeout: 8000 });
    await page.waitForTimeout(1200);
    if (await page.getByTestId('ws-wizard-skip').isVisible({ timeout: 4000 }).catch(() => false)) {
      await page.getByTestId('ws-wizard-skip').click();
    }
    await page.waitForTimeout(1500);
    log('workspace created, url:', page.url());
  }

  // 4. 清单渲染:步骤 1 即完成(建区事务内播种),进度 1/5
  await page.goto(`${UI}/inbox`, { waitUntil: 'networkidle' });
  const card = page.getByTestId('onboarding-card');
  await card.waitFor({ timeout: 15000 });
  const progressText = await page.getByTestId('onboarding-progress').textContent();
  log('progress:', progressText?.trim());
  const step1 = page.getByTestId('onboarding-step-create_workspace');
  log('step1 classes:', await step1.getAttribute('class'));
  await shot('01-checklist-fresh.png');

  // 5. CTA 深链跳转验证(不重复造向导:均跳既有入口)
  await page.getByTestId('onboarding-cta-invite_member_or_add_agent').click();
  await page.waitForTimeout(1000);
  log('cta2 →', page.url());
  if (!page.url().includes('/members')) throw new Error('step2 CTA did not deeplink to /members');
  await page.getByTestId('onboarding-cta-create_first_issue').click();
  await page.waitForTimeout(1000);
  log('cta3 →', page.url());
  if (!page.url().includes('/board')) throw new Error('step3 CTA did not deeplink to /board');

  // 6. 看板空状态四要素(插画 + 文案 + 主操作)
  await shot('02-board-empty-state.png');

  // 7. 成员名册唯一 agent 创建入口 → 四步向导(真实建 agent;worker relay 消费 member.added → 步骤 2 实时自动完成)
  await page.goto(`${UI}/members`, { waitUntil: 'networkidle' });
  await page.getByTestId('new-agent-button').click();
  await page.getByTestId('agent-wizard-name').fill('小走');
  await page.getByTestId('agent-wizard-next').click();
  await page.waitForTimeout(500);
  await page.getByTestId('agent-wizard-next').click();
  await page.waitForTimeout(500);
  await page.getByTestId('agent-wizard-next').click();
  await page.waitForTimeout(500);
  await page.getByTestId('agent-wizard-finish').click();
  await page.waitForTimeout(2000);
  log('agent created via roster wizard');

  // 步骤 2 自动完成(实时帧或轮询;等待最多 20s)
  await page.waitForFunction(
    () => {
      const el = document.querySelector('[data-testid="onboarding-auto-badge-invite_member_or_add_agent"]');
      return el !== null;
    },
    { timeout: 20000 },
  );
  log('step2 auto-completed via member.added event chain');
  await shot('03-step2-auto-completed.png');

  // 8. 真实 issue + 真实分派(agent 触发执行)→ 步骤 3/4 经 outbox 事件链自动完成
  const token = await page.evaluate(() => {
    const raw = localStorage.getItem('mesh-auth') ?? '';
    try {
      const parsed = JSON.parse(raw);
      return parsed?.state?.token ?? parsed?.state?.accessToken ?? null;
    } catch {
      return null;
    }
  });
  if (!token) {
    // 兜底:直接登录取 token(API 路径,UI 行为不受影响)
    const login = await api('/auth/login', { method: 'POST', body: { email: EMAIL, password: PASSWORD } });
    var apiToken = login.access_token;
  } else {
    var apiToken = token;
  }
  const me = await api('/users/me', { token: apiToken });
  const wsId = me.memberships[0].workspace_id;
  const members = await api(`/workspaces/${wsId}/members`, { token: apiToken });
  const agentMember = members.find((m) => m.member_type === 'agent');
  const issue = await api(`/workspaces/${wsId}/issues`, {
    method: 'POST',
    token: apiToken,
    body: { title: '走查任务:接入登录' },
  });
  log('issue created:', issue.identifier);
  await page.waitForFunction(
    () => document.querySelector('[data-testid="onboarding-auto-badge-create_first_issue"]') !== null,
    { timeout: 20000 },
  );
  log('step3 auto-completed via issue.created');

  await api(`/issues/${issue.id}`, {
    method: 'PATCH',
    token: apiToken,
    body: { assignee_id: agentMember.id },
  });
  log('assigned to agent (real assign orchestration → execution.queued)');
  await page.waitForFunction(
    () => document.querySelector('[data-testid="onboarding-auto-badge-dispatch_or_mention_agent"]') !== null,
    { timeout: 20000 },
  );
  log('step4 auto-completed via execution.queued (trigger owner = me)');
  await shot('04-steps-auto-progress.png');

  // 9. dismiss → 隐藏 → 帮助菜单 restore → 重现
  await page.getByTestId('onboarding-dismiss').click();
  await page.waitForTimeout(800);
  if (await page.getByTestId('onboarding-card').isVisible({ timeout: 2000 }).catch(() => false)) {
    throw new Error('checklist still visible after dismiss');
  }
  log('dismissed — card hidden');
  await page.getByTestId('open-help').click();
  await page.waitForTimeout(500);
  await page.getByTestId('help-restore-onboarding').click();
  await page.getByTestId('onboarding-card').waitFor({ timeout: 10000 });
  log('restored from help menu — card back');
  // 换页存证:恢复后的清单在收件箱页重现(与 04 看板页区分)
  await page.goto(`${UI}/inbox`, { waitUntil: 'networkidle' });
  await page.getByTestId('onboarding-card').waitFor({ timeout: 10000 });
  await page.waitForTimeout(800);
  await shot('05-dismiss-restore.png');

  // 10. aha 庆祝态渲染(手动完成末步触发;阅读证据链由 T34 e2e 覆盖)
  await api(`/onboarding/steps/see_agent_reply_in_inbox/complete?workspace_id=${wsId}`, {
    method: 'POST',
    token: apiToken,
    body: {},
  });
  await page.getByTestId('onboarding-aha-card').waitFor({ timeout: 20000 });
  log('aha celebration card visible');
  await shot('06-aha-celebration.png');

  // 11. 庆祝卡收起(= dismiss)后 → 成员页管理员重置入口(二次确认)
  await page.getByTestId('onboarding-aha-close').click();
  await page.waitForTimeout(800);
  await page.goto(`${UI}/members`, { waitUntil: 'networkidle' });
  await page.getByTestId('new-agent-button').waitFor({ timeout: 15000 });
  await page.waitForTimeout(800);
  await shot('07-members-admin-reset.png');

  // 走查通过 → 清理失败调试帧
  await rm(`${EVIDENCE}99-failure.png`, { force: true });
  log('WALKTHROUGH PASS — all UI assertions green');
} catch (err) {
  await shot('99-failure.png').catch(() => {});
  console.error('[walkthrough] FAIL:', err.message);
  process.exitCode = 1;
} finally {
  await browser.close();
}
