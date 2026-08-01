/**
 * usePaletteContext — GET /users/me 解析当前身份与活跃工作区:
 * 成功三元组落地、无成员身份降级(workspace 字段 null)、token 在而 me 失败降级
 * (各字段 null 且 resolved=true)、模块级缓存共享、卸载后解析不落地。
 *
 * 匿名守卫(回归:OAuth 回调往返被打断 + 登录页持久化偏好被摧毁):
 * 无 token 绝不发 GET /users/me(匿名 401 会触发 MES-106 全局兜底);匿名降级
 * resolved=true;SPA 登录(token 出现)补取;登出(token→null)重置并清缓存;
 * 账号切换(token 变更)按 token 键控缓存失效重取。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetApiClient } from '../../api/instance';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import { useAuthStore } from '../../state/authStore';
import { resetPaletteContextCache, usePaletteContext } from '../usePaletteContext';

/** 冲刷微任务链(loadMe → fetchMe → setContext),真实时钟下确定性落地 */
async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

interface MeBodyOptions {
  memberships?: unknown[];
  userId?: string;
}

function meBody(options: MeBodyOptions = {}): unknown {
  return {
    data: {
      user: { id: options.userId ?? 'u-1', email: 'u@c.com', display_name: 'U' },
      memberships: options.memberships ?? [
        {
          workspace_id: 'ws-1',
          workspace_name: 'WS',
          workspace_slug: 'ws',
          role: 'member',
          status: 'active',
          joined_at: null,
        },
      ],
    },
  };
}

/** 全局 fetch 桩:/users/me 按用例回包,其余请求空信封 */
function stubMeFetch(impl: (url: string) => Response): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return impl(url);
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch,
  );
}

/** 置登录态 token(守卫前提:有 token 才发 GET /users/me) */
function setToken(token: string | null): void {
  act(() => {
    useAuthStore.setState({ token });
  });
}

/** 统计 /users/me 请求次数 */
function countMeCalls(fetchImpl: ReturnType<typeof vi.fn>): number {
  return fetchImpl.mock.calls.filter((call) => String(call[0]).includes('/users/me')).length;
}

beforeEach(() => {
  window.history.replaceState({}, '', '/');
  resetPaletteContextCache();
  resetApiClient();
  useAuthStore.setState({ token: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetPaletteContextCache();
  resetApiClient();
  useAuthStore.setState({ token: null });
});

describe('usePaletteContext', () => {
  it('当前 /w/{slug} 路由优先于 memberships 顺序,路由切换复用缓存并重解析', async () => {
    const memberships = [
      {
        workspace_id: 'ws-a',
        workspace_name: 'A',
        workspace_slug: 'a',
        role: 'member',
        status: 'active',
        joined_at: '2026-01-01T00:00:00Z',
      },
      {
        workspace_id: 'ws-b',
        workspace_name: 'B',
        workspace_slug: 'b',
        role: 'admin',
        status: 'active',
        joined_at: '2026-02-01T00:00:00Z',
      },
    ];
    const fetchImpl = vi.fn(async () => fakeResponse({ body: meBody({ memberships }) }));
    vi.stubGlobal('fetch', fetchImpl);
    window.history.replaceState({}, '', '/w/b/issues');
    setToken('token-a');

    const { result, rerender } = renderHook(() => usePaletteContext(window.location.pathname));
    await flush();
    expect(result.current).toMatchObject({
      workspaceId: 'ws-b',
      workspaceSlug: 'b',
      role: 'admin',
    });

    window.history.pushState({}, '', '/w/a/board');
    rerender();
    await flush();
    expect(result.current).toMatchObject({
      workspaceId: 'ws-a',
      workspaceSlug: 'a',
      role: 'member',
    });
    expect(countMeCalls(fetchImpl)).toBe(1);
  });

  it('首帧未解析(EMPTY_CONTEXT);me 落地后三元组齐备', async () => {
    stubMeFetch(() => fakeResponse({ body: meBody() }));
    setToken('token-a');
    const { result } = renderHook(() => usePaletteContext());
    // 首帧:resolved=false 且各字段 null
    expect(result.current).toEqual({
      userId: null,
      workspaceId: null,
      workspaceSlug: null,
      role: null,
      resolved: false,
    });
    await flush();
    expect(result.current).toEqual({
      userId: 'u-1',
      workspaceId: 'ws-1',
      workspaceSlug: 'ws',
      role: 'member',
      resolved: true,
    });
  });

  it('匿名(无 token):绝不发 GET /users/me,直接降级 resolved=true(防 401 兜底破坏公开页)', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse({ body: meBody() }));
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => usePaletteContext());
    await flush();
    expect(countMeCalls(fetchImpl)).toBe(0);
    expect(result.current).toEqual({
      userId: null,
      workspaceId: null,
      workspaceSlug: null,
      role: null,
      resolved: true,
    });
  });

  it('SPA 登录(token 出现):匿名降级后经 token 依赖补取,三元组落地', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse({ body: meBody() }));
    vi.stubGlobal('fetch', fetchImpl);
    const { result } = renderHook(() => usePaletteContext());
    await flush();
    expect(countMeCalls(fetchImpl)).toBe(0);
    expect(result.current.resolved).toBe(true);
    // 登录:setToken → 效果重跑 → 补取
    setToken('token-a');
    await flush();
    expect(countMeCalls(fetchImpl)).toBe(1);
    expect(result.current).toEqual({
      userId: 'u-1',
      workspaceId: 'ws-1',
      workspaceSlug: 'ws',
      role: 'member',
      resolved: true,
    });
  });

  it('登出(token→null):上下文重置为匿名降级并清缓存(再次登录发起新请求)', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse({ body: meBody() }));
    vi.stubGlobal('fetch', fetchImpl);
    setToken('token-a');
    const { result } = renderHook(() => usePaletteContext());
    await flush();
    expect(result.current.userId).toBe('u-1');
    // 登出
    setToken(null);
    await flush();
    expect(result.current).toEqual({
      userId: null,
      workspaceId: null,
      workspaceSlug: null,
      role: null,
      resolved: true,
    });
    // 再次登录:缓存已被登出清除 → 发起新请求(不复用旧账号结果)
    setToken('token-b');
    await flush();
    expect(countMeCalls(fetchImpl)).toBe(2);
    expect(result.current.userId).toBe('u-1');
  });

  it('账号切换(token 变更):缓存按 token 键控失效,发起新请求(防串用)', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse({ body: meBody({ userId: 'u-1' }) }));
    vi.stubGlobal('fetch', fetchImpl);
    setToken('token-a');
    const { result } = renderHook(() => usePaletteContext());
    await flush();
    expect(result.current.userId).toBe('u-1');
    // 切换到另一账号(token-b):缓存失效 → 重新请求
    fetchImpl.mockImplementation(async () => fakeResponse({ body: meBody({ userId: 'u-2' }) }));
    setToken('token-b');
    await flush();
    expect(countMeCalls(fetchImpl)).toBe(2);
    expect(result.current.userId).toBe('u-2');
  });

  it('token 在而 me 请求失败(401 失效/离线):各字段 null 且 resolved=true(降级仅本地命令)', async () => {
    stubMeFetch(() =>
      fakeResponse({ status: 401, body: { error: { code: 'unauthorized', message: 'x' } } }),
    );
    setToken('token-a');
    const { result } = renderHook(() => usePaletteContext());
    await flush();
    expect(result.current).toEqual({
      userId: null,
      workspaceId: null,
      workspaceSlug: null,
      role: null,
      resolved: true,
    });
  });

  it('无成员身份:workspace 字段 null,userId 保留(单归属口径降级)', async () => {
    stubMeFetch(() => fakeResponse({ body: meBody({ memberships: [] }) }));
    setToken('token-a');
    const { result } = renderHook(() => usePaletteContext());
    await flush();
    expect(result.current).toEqual({
      userId: 'u-1',
      workspaceId: null,
      workspaceSlug: null,
      role: null,
      resolved: true,
    });
  });

  it('模块级缓存:多个消费者共享一次 GET /users/me;reset 后重新请求', async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL) => fakeResponse({ body: meBody() }));
    vi.stubGlobal('fetch', fetchImpl);
    setToken('token-a');
    const first = renderHook(() => usePaletteContext());
    const second = renderHook(() => usePaletteContext());
    await flush();
    expect(first.result.current.userId).toBe('u-1');
    expect(second.result.current.userId).toBe('u-1');
    expect(countMeCalls(fetchImpl)).toBe(1);
    // 清缓存后重新解析发起新请求(测试场景)
    resetPaletteContextCache();
    const third = renderHook(() => usePaletteContext());
    await flush();
    expect(countMeCalls(fetchImpl)).toBe(2);
    expect(third.result.current.resolved).toBe(true);
  });

  it('卸载后 me 才落地:不触发状态更新(无 setState-after-unmount)', async () => {
    let respond: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      respond = resolve;
    });
    stubMeFetch(() => pending as unknown as Response);
    setToken('token-a');
    const { result, unmount } = renderHook(() => usePaletteContext());
    unmount();
    // 卸载后解析:不抛错,结果不再落地
    await act(async () => {
      respond(fakeResponse({ body: meBody() }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(result.current.resolved).toBe(false);
  });
});
