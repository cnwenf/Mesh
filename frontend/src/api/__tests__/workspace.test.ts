import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { fetchWorkspaceDefaultLocale, fetchWorkspaces } from '../workspace';
import type { WorkspaceSummary } from '../workspace';

function createMockFetch(status: number, body: unknown): typeof fetch {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

function createClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => 'test-token',
    fetchImpl,
  });
}

describe('workspace API(workspace.md 工作区列表与设置)', () => {
  it('fetchWorkspaces 返回工作区列表', async () => {
    const workspaces: WorkspaceSummary[] = [
      { id: 'ws-1', name: 'Test WS', slug: 'test-ws', settings: { default_locale: 'zh-CN' } },
    ];
    const fetchImpl = createMockFetch(200, { data: workspaces, next_cursor: null });
    const client = createClient(fetchImpl);

    const result = await fetchWorkspaces(client);

    expect(result).toEqual(workspaces);
    const [url] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain('/api/v1/workspaces');
  });

  it('fetchWorkspaceDefaultLocale 返回首个工作区的 default_locale', async () => {
    const workspaces: WorkspaceSummary[] = [
      { id: 'ws-1', name: 'WS1', slug: 'ws1', settings: { default_locale: 'zh-CN' } },
      { id: 'ws-2', name: 'WS2', slug: 'ws2', settings: { default_locale: 'en' } },
    ];
    const fetchImpl = createMockFetch(200, { data: workspaces, next_cursor: null });
    const client = createClient(fetchImpl);

    const locale = await fetchWorkspaceDefaultLocale(client);

    expect(locale).toBe('zh-CN');
  });

  it('fetchWorkspaceDefaultLocale 无工作区时返回 null', async () => {
    const fetchImpl = createMockFetch(200, { data: [], next_cursor: null });
    const client = createClient(fetchImpl);

    const locale = await fetchWorkspaceDefaultLocale(client);

    expect(locale).toBeNull();
  });

  it('fetchWorkspaceDefaultLocale settings 无 default_locale 时返回 null', async () => {
    const workspaces: WorkspaceSummary[] = [
      { id: 'ws-1', name: 'WS1', slug: 'ws1', settings: {} },
    ];
    const fetchImpl = createMockFetch(200, { data: workspaces, next_cursor: null });
    const client = createClient(fetchImpl);

    const locale = await fetchWorkspaceDefaultLocale(client);

    expect(locale).toBeNull();
  });

  it('fetchWorkspaceDefaultLocale default_locale 为空字符串时返回 null', async () => {
    const workspaces: WorkspaceSummary[] = [
      { id: 'ws-1', name: 'WS1', slug: 'ws1', settings: { default_locale: '' } },
    ];
    const fetchImpl = createMockFetch(200, { data: workspaces, next_cursor: null });
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
