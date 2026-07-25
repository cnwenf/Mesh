import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { fetchWorkspaceById, fetchWorkspaceDefaultLocale, fetchWorkspaces } from '../workspace';
import type { WorkspaceDetail, WorkspaceListItem } from '../workspace';

function createMockFetch(responses: Array<{ status: number; body: unknown }>): typeof fetch {
  let callIndex = 0;
  return vi.fn().mockImplementation(() => {
    const response = responses[callIndex] ?? responses[responses.length - 1];
    callIndex += 1;
    return Promise.resolve(
      new Response(JSON.stringify(response.body), {
        status: response.status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }) as unknown as typeof fetch;
}

function createClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => 'test-token',
    fetchImpl,
  });
}

describe('workspace API(workspace.md 工作区列表与设置)', () => {
  it('fetchWorkspaces 返回列表(不含 settings,list_view)', async () => {
    const workspaces: WorkspaceListItem[] = [
      { id: 'ws-1', name: 'Test WS', slug: 'test-ws', logo_url: null, my_role: 'admin', created_at: '2026-01-01T00:00:00Z' },
    ];
    const fetchImpl = createMockFetch([{ status: 200, body: { data: workspaces, next_cursor: null } }]);
    const client = createClient(fetchImpl);

    const result = await fetchWorkspaces(client);

    expect(result).toEqual(workspaces);
    const [url] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain('/api/v1/workspaces');
  });

  it('fetchWorkspaceById 返回完整工作区(含 settings)', async () => {
    const detail: WorkspaceDetail = {
      id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, timezone: 'UTC',
      settings: { default_locale: 'zh-CN' }, my_role: 'admin',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    };
    const fetchImpl = createMockFetch([{ status: 200, body: { data: detail } }]);
    const client = createClient(fetchImpl);

    const result = await fetchWorkspaceById(client, 'ws-1');

    expect(result.settings.default_locale).toBe('zh-CN');
    const [url] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain('/api/v1/workspaces/ws-1');
  });

  it('fetchWorkspaceDefaultLocale 两步获取:列表→单对象(含 settings)', async () => {
    const listResponse = {
      status: 200,
      body: { data: [{ id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, my_role: 'admin', created_at: '' }], next_cursor: null },
    };
    const detailResponse = {
      status: 200,
      body: { data: { id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, timezone: 'UTC', settings: { default_locale: 'zh-CN' }, my_role: 'admin', created_at: '', updated_at: '' } },
    };
    const fetchImpl = createMockFetch([listResponse, detailResponse]);
    const client = createClient(fetchImpl);

    const locale = await fetchWorkspaceDefaultLocale(client);

    expect(locale).toBe('zh-CN');
    // 验证两次调用:列表 + 单对象
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const [url1] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    const [url2] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[1] as [string];
    expect(url1).toContain('/api/v1/workspaces');
    expect(url2).toContain('/api/v1/workspaces/ws-1');
  });

  it('fetchWorkspaceDefaultLocale 无工作区时返回 null', async () => {
    const fetchImpl = createMockFetch([{ status: 200, body: { data: [], next_cursor: null } }]);
    const client = createClient(fetchImpl);

    const locale = await fetchWorkspaceDefaultLocale(client);

    expect(locale).toBeNull();
    // 仅一次调用(列表为空,不发第二次请求)
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('fetchWorkspaceDefaultLocale settings 无 default_locale 时返回 null', async () => {
    const listResponse = {
      status: 200,
      body: { data: [{ id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, my_role: 'admin', created_at: '' }], next_cursor: null },
    };
    const detailResponse = {
      status: 200,
      body: { data: { id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, timezone: 'UTC', settings: {}, my_role: 'admin', created_at: '', updated_at: '' } },
    };
    const fetchImpl = createMockFetch([listResponse, detailResponse]);
    const client = createClient(fetchImpl);

    const locale = await fetchWorkspaceDefaultLocale(client);

    expect(locale).toBeNull();
  });

  it('fetchWorkspaceDefaultLocale default_locale 为空字符串时返回 null', async () => {
    const listResponse = {
      status: 200,
      body: { data: [{ id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, my_role: 'admin', created_at: '' }], next_cursor: null },
    };
    const detailResponse = {
      status: 200,
      body: { data: { id: 'ws-1', name: 'WS', slug: 'ws', logo_url: null, timezone: 'UTC', settings: { default_locale: '' }, my_role: 'admin', created_at: '', updated_at: '' } },
    };
    const fetchImpl = createMockFetch([listResponse, detailResponse]);
    const client = createClient(fetchImpl);

    const locale = await fetchWorkspaceDefaultLocale(client);

    expect(locale).toBeNull();
  });

  it('网络错误时抛出 MeshApiError', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('network')) as unknown as typeof fetch;
    const client = createClient(fetchImpl);

    await expect(fetchWorkspaceDefaultLocale(client)).rejects.toMatchObject({
      status: 0,
      code: 'network',
    });
  });
});
