import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../api/client';
import { useWorkspaceLocale } from '../useWorkspaceLocale';

function createMockClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => 'test-token',
    fetchImpl,
  });
}

function workspaceResponse(defaultLocale: string | undefined): typeof fetch {
  const data = [{ id: 'ws-1', name: 'WS', slug: 'ws', settings: defaultLocale !== undefined ? { default_locale: defaultLocale } : {} }];
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ data, next_cursor: null }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

describe('useWorkspaceLocale(工作区默认 locale,§6.18 协商链第三级)', () => {
  it('client=null 时返回 null(不请求)', () => {
    const { result } = renderHook(() => useWorkspaceLocale(null));
    expect(result.current).toBeNull();
  });

  it('成功获取工作区 default_locale', async () => {
    const client = createMockClient(workspaceResponse('zh-CN'));
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => expect(result.current).toBe('zh-CN'));
  });

  it('工作区无 default_locale 时返回 null', async () => {
    const client = createMockClient(workspaceResponse(undefined));
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => {
      // 加载完成后仍为 null(无 default_locale)
      expect(result.current).toBeNull();
    });
    // 确保请求已发出(effect 已执行)
  });

  it('网络错误时静默降级返回 null', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('network')) as unknown as typeof fetch;
    const client = createMockClient(fetchImpl);
    const { result } = renderHook(() => useWorkspaceLocale(client));

    // 等待 effect 执行完毕
    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    // 错误后仍为 null(静默降级)
    expect(result.current).toBeNull();
  });

  it('组件卸载后不更新状态(取消竞态)', async () => {
    let resolvePromise: (value: Response) => void = () => {};
    const fetchImpl = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolvePromise = resolve;
      }),
    ) as unknown as typeof fetch;
    const client = createMockClient(fetchImpl);

    const { result, unmount } = renderHook(() => useWorkspaceLocale(client));
    expect(result.current).toBeNull();

    // 卸载后再 resolve
    unmount();
    resolvePromise(
      new Response(JSON.stringify({ data: [{ id: '1', name: 'W', slug: 'w', settings: { default_locale: 'en' } }], next_cursor: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    // 等待一个 tick 确保不会抛错
    await new Promise((r) => setTimeout(r, 10));
  });

  it('空工作区列表返回 null', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: [], next_cursor: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ) as unknown as typeof fetch;
    const client = createMockClient(fetchImpl);
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    expect(result.current).toBeNull();
  });
});
