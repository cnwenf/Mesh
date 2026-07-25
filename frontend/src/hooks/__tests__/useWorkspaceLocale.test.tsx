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

/** 模拟两步请求:列表 → 单对象(含 settings) */
function twoStepFetch(defaultLocale: string | undefined): typeof fetch {
  let callIndex = 0;
  return vi.fn().mockImplementation(() => {
    callIndex += 1;
    if (callIndex === 1) {
      // 列表响应(不含 settings)
      return Promise.resolve(
        new Response(JSON.stringify({ data: [{ id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, my_role: 'admin', created_at: '' }], next_cursor: null }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }
    // 单对象响应(含 settings)
    const settings = defaultLocale !== undefined ? { default_locale: defaultLocale } : {};
    return Promise.resolve(
      new Response(JSON.stringify({ data: { id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, timezone: 'UTC', settings, my_role: 'admin', created_at: '', updated_at: '' } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }) as unknown as typeof fetch;
}

describe('useWorkspaceLocale(工作区默认 locale,§6.18 协商链第三级)', () => {
  it('client=null 时返回 null(不请求)', () => {
    const { result } = renderHook(() => useWorkspaceLocale(null));
    expect(result.current).toBeNull();
  });

  it('成功获取工作区 default_locale(两步:列表→单对象)', async () => {
    const client = createMockClient(twoStepFetch('zh-CN'));
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => expect(result.current).toBe('zh-CN'));
  });

  it('工作区无 default_locale 时返回 null', async () => {
    const client = createMockClient(twoStepFetch(undefined));
    const { result } = renderHook(() => useWorkspaceLocale(client));

    // 等待两步请求完成
    await new Promise((r) => setTimeout(r, 50));
    expect(result.current).toBeNull();
  });

  it('网络错误时静默降级返回 null', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('network')) as unknown as typeof fetch;
    const client = createMockClient(fetchImpl);
    const { result } = renderHook(() => useWorkspaceLocale(client));

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
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

    unmount();
    resolvePromise(
      new Response(JSON.stringify({ data: [{ id: '1', name: 'W', slug: 'w', logo_url: null, my_role: 'admin', created_at: '' }], next_cursor: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

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
