/**
 * usePaletteData —— 面板数据编排层单测:空态唯一数据流(§4.2.1)、默认 favorites
 * 提供器、favorites 失败降级、query 路径(经 useEntitySearch 防抖)、noteLocalChange
 * 触发 recents 重读、settledToken 落地播报。
 *
 * 全局 fetch 桩驱动(api 实例调用时读 global fetch);空态走真实微任务,query 防抖
 * 走假时钟 + advanceTimersByTimeAsync 冲刷(绝不与 RTL async 查询混用)。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetApiClient } from '../../api/instance';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import type { SearchItem } from '../../api/search';
import { listRecents, pushRecent, setRecentsScope } from '../recents';
import { usePaletteData } from '../usePaletteData';

function issue(id: string, title: string): SearchItem {
  return {
    type: 'issue',
    id,
    title,
    context: {
      identifier: `WEB-${id}`,
      project: null,
      status: { id: 's', name: 'Todo', category: 'todo' },
    },
    icon: 'issue',
    url: `/issues/${id}`,
  };
}

function favoritesBody(): unknown {
  return {
    data: [
      {
        target_type: 'issue',
        target_id: 'fav-1',
        title: 'Fav issue',
        url: '/issues/fav-1',
        created_at: '2026-02-01T00:00:00Z',
      },
    ],
    next_cursor: null,
  };
}

/** 路由全局 fetch:/favorites 走给定实现,其余空集 */
function stubFetch(favoritesImpl: () => Response): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/favorites')) return favoritesImpl();
      if (url.includes('/search')) {
        return fakeResponse({ body: { data: [issue('1', 'Crash')], next_cursor: null } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  setRecentsScope({ userId: 'u-1', workspaceId: 'ws-1' });
  resetApiClient();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  resetApiClient();
});

describe('usePaletteData 空态唯一数据流(§4.2.1)', () => {
  it('默认提供器拉取 favorites 并组装收藏区(未注入 provider 时)', async () => {
    stubFetch(() => fakeResponse({ body: favoritesBody() }));
    const { result } = renderHook(() =>
      usePaletteData({ workspaceId: 'ws-1', userId: 'u-1', query: '', enabled: true }),
    );
    await waitFor(() => {
      expect(result.current.sections.some((s) => s.key === 'favorites')).toBe(true);
    });
    const fav = result.current.sections.find((s) => s.key === 'favorites');
    expect(fav?.options[0]?.title).toBe('Fav issue');
    expect(result.current.isSearching).toBe(false);
  });

  it('真实 favorites 仅含 target id 时解析标题与规范深链,不渲染 UUID 死行', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/favorites')) {
        return fakeResponse({
          body: {
            data: [
              {
                id: 'favorite-row-1',
                workspace_id: 'ws-1',
                member_id: 'member-1',
                target_type: 'issue',
                target_id: 'fav-1',
                created_at: '2026-02-01T00:00:00Z',
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (url.includes('/issues/fav-1')) {
        return fakeResponse({
          body: {
            data: { id: 'fav-1', identifier: 'WEB-7', title: 'Resolved favorite' },
          },
        });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    });
    vi.stubGlobal('fetch', fetchImpl as typeof fetch);
    const { result } = renderHook(() =>
      usePaletteData({
        workspaceId: 'ws-1',
        workspaceSlug: 'acme',
        userId: 'u-1',
        query: '',
        enabled: true,
      }),
    );

    await waitFor(() => {
      const favorite = result.current.sections.find((section) => section.key === 'favorites');
      expect(favorite?.options[0]).toMatchObject({
        title: 'Resolved favorite',
        url: '/w/acme/issues/by-identifier/WEB-7',
      });
    });
    expect(
      result.current.sections.some((section) =>
        section.options.some((option) => option.title === 'fav-1'),
      ),
    ).toBe(false);
  });

  it('打开空态时 403/404 对象 recent 会从 UI 与 localStorage 同步剪枝', async () => {
    pushRecent({
      kind: 'object',
      type: 'issue',
      id: 'private-1',
      title: 'Revoked private issue',
      url: '/w/acme/issues/private-1',
      at: 9,
    });
    const detailCalls = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/favorites')) {
          return fakeResponse({ body: { data: [], next_cursor: null } });
        }
        if (url.includes('/issues/private-1')) {
          detailCalls();
          return fakeResponse({
            status: 403,
            body: { error: { code: 'forbidden', message: 'forbidden' } },
          });
        }
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }) as typeof fetch,
    );

    const { result } = renderHook(() =>
      usePaletteData({
        workspaceId: 'ws-1',
        workspaceSlug: 'acme',
        userId: 'u-1',
        query: '',
        enabled: true,
      }),
    );
    await waitFor(() => expect(detailCalls).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(
        result.current.sections.some((section) =>
          section.options.some((option) => option.title === 'Revoked private issue'),
        ),
      ).toBe(false);
    });
    expect(listRecents()).toEqual([]);
  });

  it('核验期间新增的跨标签 recent 不会被旧快照清理', async () => {
    pushRecent({
      kind: 'object',
      type: 'issue',
      id: 'private-1',
      title: 'Revoked issue',
      url: '/w/acme/issues/private-1',
      at: 9,
    });
    let resolvePrivate: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/favorites')) {
          return Promise.resolve(fakeResponse({ body: { data: [], next_cursor: null } }));
        }
        if (url.includes('/issues/private-1')) {
          return new Promise<Response>((resolve) => {
            resolvePrivate = resolve;
          });
        }
        return Promise.resolve(
          fakeResponse({ body: { data: { id: 'new-2', identifier: 'WEB-2', title: 'New' } } }),
        );
      }) as typeof fetch,
    );

    renderHook(() =>
      usePaletteData({
        workspaceId: 'ws-1',
        workspaceSlug: 'acme',
        userId: 'u-1',
        query: '',
        enabled: true,
      }),
    );
    await waitFor(() => expect(resolvePrivate).toBeDefined());

    pushRecent({
      kind: 'object',
      type: 'issue',
      id: 'new-2',
      title: 'Concurrent recent',
      url: '/w/acme/issues/new-2',
      at: 10,
    });
    resolvePrivate?.(
      fakeResponse({
        status: 403,
        body: { error: { code: 'forbidden', message: 'forbidden' } },
      }),
    );

    await waitFor(() => {
      expect(listRecents().map((entry) => entry.id)).toEqual(['new-2']);
    });
  });

  it('favorites 提供器失败 → 空态降级(收藏区不出现,不崩溃)', async () => {
    stubFetch(() =>
      fakeResponse({ status: 500, body: { error: { code: 'server', message: 'x' } } }),
    );
    const { result } = renderHook(() =>
      usePaletteData({ workspaceId: 'ws-1', userId: 'u-1', query: '', enabled: true }),
    );
    // 失败后稳定:无收藏区,flatCount 0,且无未捕获错误
    await waitFor(() => expect(result.current.error).toBeNull());
    expect(result.current.sections.some((s) => s.key === 'favorites')).toBe(false);
  });

  it('enabled=false / workspaceId=null → 不请求,空结果', async () => {
    type Props = { workspaceId: string | null; enabled: boolean };
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl as typeof fetch);
    const initial: Props = { workspaceId: null, enabled: true };
    const { result, rerender } = renderHook(
      (props: Props) =>
        usePaletteData({
          workspaceId: props.workspaceId,
          userId: 'u-1',
          query: '',
          enabled: props.enabled,
        }),
      { initialProps: initial },
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result.current.sections).toHaveLength(0);

    const disabled: Props = { workspaceId: 'ws-1', enabled: false };
    rerender(disabled);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('query 路径:实体经防抖补入分组(§11.4 本地命令不阻塞)', async () => {
    vi.useFakeTimers();
    stubFetch(() => fakeResponse({ body: favoritesBody() }));
    const { result } = renderHook(() =>
      usePaletteData({ workspaceId: 'ws-1', userId: 'u-1', query: 'Crash', enabled: true }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(150);
    });
    expect(result.current.sections.some((s) => s.key === 'issues')).toBe(true);
    expect(result.current.entityCount).toBe(1);
  });

  it('noteLocalChange 触发 recents/计数重读(空态重算)', async () => {
    stubFetch(() => fakeResponse({ body: { data: [], next_cursor: null } }));
    const { result } = renderHook(() =>
      usePaletteData({ workspaceId: 'ws-1', userId: 'u-1', query: '', enabled: true }),
    );
    await waitFor(() => expect(result.current.error).toBeNull());
    // object 类 recent 不依赖命令注册表(命令类 recent 失效即剔除)
    pushRecent({
      kind: 'object',
      type: 'issue',
      id: 'r1',
      title: 'Recent issue',
      url: '/issues/r1',
      at: 9,
    });
    act(() => {
      result.current.noteLocalChange();
    });
    await waitFor(() => {
      expect(result.current.sections.some((s) => s.key === 'recents')).toBe(true);
    });
  });

  it('落地后 settledToken 前进(供 live region 播报)', async () => {
    stubFetch(() => fakeResponse({ body: favoritesBody() }));
    const { result } = renderHook(() =>
      usePaletteData({ workspaceId: 'ws-1', userId: 'u-1', query: '', enabled: true }),
    );
    await waitFor(() => expect(result.current.settledToken).toBeGreaterThan(0));
  });
});
