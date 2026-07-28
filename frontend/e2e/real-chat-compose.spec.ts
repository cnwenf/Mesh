/**
 * Compose 真栈 /chat 全链路冒烟(H5 复验)。
 *
 * 前置:`docker compose up --build -d`(postgres+redis+minio+api+worker+gateway+frontend)。
 * 前端由 nginx 在 :3001 同源承载并反代 /api→api、/ws→gateway,故浏览器对 API/WS
 * 全程同源,**无需 --disable-web-security**(这正是 H5 的验收点:一键部署形态可用真实 /chat)。
 *
 * 走通:同源注册/登录 → 建工作区/agent → /chat 新建会话 → 发送 → 流式 agent 回复渲染。
 */
import { expect, test } from '@playwright/test';

const AUTH_KEY = 'mesh.auth.v1';

test('compose same-origin /chat: send renders a streaming agent reply', async ({ page, request }) => {
  const suffix = Math.random().toString(36).slice(2, 8);
  const email = `chat-compose-${suffix}@example.com`;
  const base = process.env.PLAYWRIGHT_COMPOSE_BASE ?? 'http://127.0.0.1:3001';

  // 同源注册/登录(经 nginx 反代到 api)。
  const reg = await request.post(`${base}/api/v1/auth/register`, {
    data: { email, password: 'Compose-Chat-123', display_name: 'Compose Chat' },
  });
  expect([200, 201, 409]).toContain(reg.status());
  const login = await request.post(`${base}/api/v1/auth/login`, {
    data: { email, password: 'Compose-Chat-123' },
  });
  expect(login.status()).toBe(200);
  const token = (await login.json()).data.access_token as string;
  const auth = { Authorization: `Bearer ${token}` };

  const ws = await request.post(`${base}/api/v1/workspaces`, {
    headers: auth,
    data: { name: `Compose Chat ${suffix}`, slug: `cc-${suffix}` },
  });
  expect(ws.status()).toBe(201);
  const wsId = (await ws.json()).data.id as string;
  const ag = await request.post(`${base}/api/v1/workspaces/${wsId}/agents`, {
    headers: auth,
    data: { name: `compose-bot-${suffix}` },
  });
  expect(ag.status()).toBe(201);
  const agentId = (await ag.json()).data.id as string;

  // 注入登录态后打开 /chat(同源,无 CORS)。
  await page.addInitScript(
    ([key, tok]) => {
      localStorage.setItem(key, JSON.stringify({ state: { token: tok, refreshToken: null }, version: 0 }));
    },
    [AUTH_KEY, token] as const,
  );
  await page.goto(`${base}/chat`);
  await expect(page.getByTestId('chat-page')).toBeVisible({ timeout: 15_000 });

  // 新建会话(选 agent)→ 发送 → 等待流式 agent 回复渲染出非空正文。
  await page.getByTestId('chat-new-session').click();
  await expect(page.getByTestId('chat-new-session-create')).toBeVisible();
  await page.selectOption('[data-testid="chat-new-session-agent"]', agentId);
  await page.getByTestId('chat-new-session-create').click();
  await expect(page.getByTestId('chat-composer-input')).toBeVisible({ timeout: 10_000 });

  await page.getByTestId('chat-composer-input').fill('你好,请用一句话介绍你自己');
  await page.getByTestId('chat-composer-send').click();

  // 流式回复:至少两条消息(用户 + agent),且 agent 正文非空。
  await page.waitForFunction(
    () => {
      const bodies = Array.from(document.querySelectorAll('[data-testid^="chat-body-"]'));
      return bodies.length >= 2 && (bodies[bodies.length - 1].textContent ?? '').trim().length > 0;
    },
    undefined,
    { timeout: 30_000 },
  );
  // 生成结束后 composer 回到可交互空闲态:空输入下发送键按设计 disabled,
  // 重新输入即启用 —— 仅当流已结束(stream.isStreaming=false)时才会启用,
  // 故该断言同时验证了终态解除生成锁。
  await page.getByTestId('chat-composer-input').fill('再来一句');
  await expect(page.getByTestId('chat-composer-send')).toBeEnabled({ timeout: 15_000 });
  await page.screenshot({ path: '/tmp/chat-compose-smoke.png' });
});
