import type { Page } from '@playwright/test';

export const MOCK_BASE = 'http://127.0.0.1:8901';

/** mock 契约登录凭证(auth/login 签发同形:mesh-dev:<workspace-id>) */
export const MOCK_TOKEN = 'mesh-dev:ws-1';

/** authStore 持久化键(zustand persist,与 src/state/authStore.ts 一致) */
const AUTH_STORAGE_KEY = 'mesh.auth.v1';

/** 重置 mock 服务端内存态(数据/幂等键/频道 seq),保证用例隔离 */
export async function resetMockServer(): Promise<void> {
  const res = await fetch(`${MOCK_BASE}/api/v1/mock/reset`, { method: 'POST' });
  if (!res.ok) throw new Error(`mock reset failed: ${res.status}`);
}

/** 经 HTTP 注入一帧实时事件(mock 按频道分配 seq 并广播给已鉴权订阅者) */
export async function emit(
  channel: string,
  event: string,
  payload: Record<string, unknown>,
): Promise<{ seq: number }> {
  const res = await fetch(`${MOCK_BASE}/api/v1/mock/emit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel, event, payload }),
  });
  if (!res.ok) throw new Error(`mock emit failed: ${res.status}`);
  const body = (await res.json()) as { data: { seq: number } };
  return body.data;
}

/** 真实邮箱/密码登录(mock 契约账号 jane@corp.com;像真人一样操作登录页) */
export async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-email').fill('jane@corp.com');
  await page.getByTestId('login-password').fill('secret123');
  await page.getByTestId('login-account-submit').click();
  await page.waitForURL('**/');
}

/**
 * 会话注入(dev-auth 真实栈联调用):把 access token 直接写入 authStore 持久化键,
 * 后续导航即登录态(RequireAuth 通过、请求带 Bearer、WS 首帧鉴权可用)。
 * 必须在首个 page.goto 之前调用(addInitScript 于文档加载前执行)。
 */
export async function injectSession(page: Page, token: string): Promise<void> {
  await page.addInitScript(
    ([key, value]: [string, string]) => {
      window.localStorage.setItem(key, value);
    },
    [AUTH_STORAGE_KEY, JSON.stringify({ state: { token }, version: 0 })] as [string, string],
  );
}

/** 等待真实首页(工作区列表)就绪 */
export async function gotoHomeReady(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByTestId('home-workspace-list').waitFor({ state: 'visible' });
}
