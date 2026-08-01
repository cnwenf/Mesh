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
import { pushRecent, setRecentsScope } from '../recents';
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

  it('favorites 提供器失败 → 空态降级(收藏区不出现,不崩溃)', async () => {
    stubFetch(() => fakeResponse({ status: 500, body: { error: { code: 'server', message: 'x' } } }));
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
        usePaletteData({ workspaceId: props.workspaceId, userId: 'u-1', query: '', enabled: props.enabled }),
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
    pushRecent({ kind: 'object', type: 'issue', id: 'r1', title: 'Recent issue', url: '/issues/r1', at: 9 });
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
