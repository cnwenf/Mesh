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

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * 列表(短项,无 settings)→ detail(全量,含 settings)两步响应桩。
 * 列表响应不含 settings(后端 v0.4.0 契约),default_locale 必须经 detail 读取。
 */
function workspaceResponse(defaultLocale: string | undefined): typeof fetch {
  const list = {
    data: [{ id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, my_role: 'owner', created_at: '2026-07-25T00:00:00Z' }],
    next_cursor: null,
  };
  const detail = {
    data: {
      id: 'ws-1',
      name: 'WS',
      slug: 'ws',
      logo_url: null,
      timezone: 'UTC',
      settings: defaultLocale !== undefined ? { default_locale: defaultLocale } : {},
      my_role: 'owner',
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T00:00:00Z',
    },
  };
  const impl = vi.fn();
  impl.mockResolvedValueOnce(jsonResponse(200, list));
  impl.mockResolvedValueOnce(jsonResponse(200, detail));
  return impl as unknown as typeof fetch;
}

describe('useWorkspaceLocale(工作区默认 locale,§6.18 协商链第三级)', () => {
  it('client=null 时返回 null(不请求)', () => {
    const { result } = renderHook(() => useWorkspaceLocale(null));
    expect(result.current).toBeNull();
  });

  it('成功获取工作区 default_locale(经列表→detail 两步)', async () => {
    const client = createMockClient(workspaceResponse('zh-CN'));
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => expect(result.current).toBe('zh-CN'));
  });

  it('工作区无 default_locale 时返回 null', async () => {
    const fetchImpl = workspaceResponse(undefined);
    const client = createMockClient(fetchImpl);
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => {
      // 两步请求均已发出且加载完成(仍为 null = 无 default_locale)
      expect(fetchImpl).toHaveBeenCalledTimes(2);
    });
    expect(result.current).toBeNull();
  });

  it('detail 读取失败时静默降级返回 null', async () => {
    const impl = vi.fn();
    impl.mockResolvedValueOnce(
      jsonResponse(200, {
        data: [{ id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, my_role: 'owner', created_at: '2026-07-25T00:00:00Z' }],
        next_cursor: null,
      }),
    );
    impl.mockResolvedValueOnce(
      jsonResponse(404, { error: { code: 'not_found', message: 'workspace not found' } }),
    );
    const client = createMockClient(impl as unknown as typeof fetch);
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => expect(impl).toHaveBeenCalledTimes(2));
    expect(result.current).toBeNull();
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
    resolvePromise(jsonResponse(200, { data: [], next_cursor: null }));

    // 等待一个 tick 确保不会抛错
    await new Promise((r) => setTimeout(r, 10));
  });

  it('空工作区列表返回 null(不发起 detail 请求)', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(200, { data: [], next_cursor: null }));
    const client = createMockClient(fetchImpl as unknown as typeof fetch);
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    expect(result.current).toBeNull();
    // 无工作区则不读 detail(仅一次请求)
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
