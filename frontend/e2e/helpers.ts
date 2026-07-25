import type { Page } from '@playwright/test';

export const MOCK_BASE = 'http://127.0.0.1:8901';

/** 开发态 token(与后端 v0.1.0 dev 鉴权同形:mesh-dev:<workspace-uuid>) */
export const DEV_TOKEN = 'mesh-dev:00000000-0000-0000-0000-000000000001';

/** 重置 mock 服务端内存态(数据/幂等键/频道 seq),保证用例隔离 */
export async function resetMockServer(): Promise<void> {
  const res = await fetch(`${MOCK_BASE}/api/v1/demo/reset`, { method: 'POST' });
  if (!res.ok) throw new Error(`mock reset failed: ${res.status}`);
}

/** 经 HTTP 注入一帧实时事件(mock 按频道分配 seq 并广播给已鉴权订阅者) */
export async function emit(
  channel: string,
  event: string,
  payload: Record<string, unknown>,
): Promise<{ seq: number }> {
  const res = await fetch(`${MOCK_BASE}/api/v1/demo/emit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel, event, payload }),
  });
  if (!res.ok) throw new Error(`mock emit failed: ${res.status}`);
  const body = (await res.json()) as { data: { seq: number } };
  return body.data;
}

/** 占位登录:粘帖 token → 写入 authStore → 跳转首页(真实 auth 在阶段 2) */
export async function login(page: Page, token: string = DEV_TOKEN): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-token').fill(token);
  await page.getByTestId('login-submit').click();
  await page.waitForURL('**/');
}

/** 等待首页骨架演示区就绪 */
export async function gotoHomeReady(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByTestId('demo-issue-list').waitFor({ state: 'visible' });
}
