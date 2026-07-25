import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import {
  createWorkspace,
  deleteWorkspace,
  fetchAllWorkspaceSummaries,
  fetchWorkspaceDefaultLocale,
  fetchWorkspaces,
  getWorkspace,
  getWorkspaceBySlug,
  listWorkspaces,
  restoreWorkspace,
  updateWorkspace,
} from '../workspace';
import type { WorkspaceDetail, WorkspaceSummary } from '../workspace';

function createMockFetch(status: number, body: unknown): typeof fetch {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

/** 按调用次序返回不同响应的 fetch 桩 */
function sequenceFetch(...responses: Array<{ status: number; body: unknown }>): typeof fetch {
  const impl = vi.fn();
  responses.forEach((response) => {
    impl.mockImplementationOnce(() =>
      Promise.resolve(
        new Response(JSON.stringify(response.body), {
          status: response.status,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
  });
  return impl as unknown as typeof fetch;
}

function createClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => 'test-token',
    fetchImpl,
  });
}

const SUMMARY: WorkspaceSummary = {
  id: 'ws-1',
  name: 'Test WS',
  slug: 'test-ws',
  logo_url: null,
  my_role: 'owner',
  created_at: '2026-07-24T10:00:00Z',
};

const DETAIL: WorkspaceDetail = {
  ...SUMMARY,
  timezone: 'UTC',
  settings: { default_locale: 'zh-CN' },
  updated_at: '2026-07-24T11:00:00Z',
};

function calledUrl(fetchImpl: typeof fetch, call = 0): string {
  return ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[call] as [string])[0];
}

function calledInit(fetchImpl: typeof fetch, call = 0): RequestInit {
  return ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[call] as [string, RequestInit])[1];
}

describe('workspace API(workspace.md §3 工作区全量端点)', () => {
  it('listWorkspaces 返回短项列表与游标', async () => {
    const fetchImpl = createMockFetch(200, { data: [SUMMARY], next_cursor: 'c1' });
    const client = createClient(fetchImpl);

    const result = await listWorkspaces(client, { limit: 10, cursor: 'c0' });

    expect(result).toEqual({ data: [SUMMARY], next_cursor: 'c1' });
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces?limit=10&cursor=c0');
  });

  it('fetchWorkspaces 返回首页数据', async () => {
    const fetchImpl = createMockFetch(200, { data: [SUMMARY], next_cursor: null });
    const client = createClient(fetchImpl);

    await expect(fetchWorkspaces(client)).resolves.toEqual([SUMMARY]);
  });

  it('fetchAllWorkspaceSummaries 自动翻页至末页', async () => {
    const fetchImpl = sequenceFetch(
      { status: 200, body: { data: [SUMMARY], next_cursor: 'c1' } },
      {
        status: 200,
        body: { data: [{ ...SUMMARY, id: 'ws-2', slug: 'ws-2' }], next_cursor: null },
      },
    );
    const client = createClient(fetchImpl);

    const all = await fetchAllWorkspaceSummaries(client);

    expect(all.map((workspace) => workspace.id)).toEqual(['ws-1', 'ws-2']);
    expect(calledUrl(fetchImpl, 1)).toContain('cursor=c1');
  });

  it('getWorkspace 读 UUID detail', async () => {
    const fetchImpl = createMockFetch(200, { data: DETAIL });
    const client = createClient(fetchImpl);

    await expect(getWorkspace(client, 'ws-1')).resolves.toEqual(DETAIL);
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1');
  });

  it('getWorkspaceBySlug 编码 slug 路径', async () => {
    const fetchImpl = createMockFetch(200, { data: DETAIL });
    const client = createClient(fetchImpl);

    await expect(getWorkspaceBySlug(client, 'a b')).resolves.toEqual(DETAIL);
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/by-slug/a%20b');
  });

  it('createWorkspace POST 请求体与 201 解析', async () => {
    const fetchImpl = createMockFetch(201, { data: DETAIL });
    const client = createClient(fetchImpl);

    const result = await createWorkspace(client, { name: 'Acme', slug: 'acme' });

    expect(result).toEqual(DETAIL);
    expect(calledInit(fetchImpl).method).toBe('POST');
    expect(JSON.parse(String(calledInit(fetchImpl).body))).toEqual({ name: 'Acme', slug: 'acme' });
  });

  it('updateWorkspace PATCH 携带浅合并 patch', async () => {
    const updated = { ...DETAIL, settings: { default_locale: 'en' } };
    const fetchImpl = createMockFetch(200, { data: updated });
    const client = createClient(fetchImpl);

    const result = await updateWorkspace(client, 'ws-1', {
      settings: { default_locale: 'en' },
    });

    expect(result.settings.default_locale).toBe('en');
    expect(calledInit(fetchImpl).method).toBe('PATCH');
  });

  it('deleteWorkspace 携带 confirm_slug 请求体', async () => {
    const fetchImpl = createMockFetch(200, { data: { status: 'deleted' } });
    const client = createClient(fetchImpl);

    await expect(deleteWorkspace(client, 'ws-1', 'test-ws')).resolves.toEqual({
      status: 'deleted',
    });
    expect(calledInit(fetchImpl).method).toBe('DELETE');
    expect(JSON.parse(String(calledInit(fetchImpl).body))).toEqual({ confirm_slug: 'test-ws' });
  });

  it('restoreWorkspace POST restore', async () => {
    const fetchImpl = createMockFetch(200, { data: DETAIL });
    const client = createClient(fetchImpl);

    await expect(restoreWorkspace(client, 'ws-1')).resolves.toEqual(DETAIL);
    expect(calledUrl(fetchImpl)).toContain('/api/v1/workspaces/ws-1/restore');
  });

  it('fetchWorkspaceDefaultLocale 经 detail 读取 settings(列表不含 settings)', async () => {
    const fetchImpl = sequenceFetch(
      { status: 200, body: { data: [SUMMARY], next_cursor: null } },
      { status: 200, body: { data: DETAIL } },
    );
    const client = createClient(fetchImpl);

    await expect(fetchWorkspaceDefaultLocale(client)).resolves.toBe('zh-CN');
    expect(calledUrl(fetchImpl, 1)).toContain('/api/v1/workspaces/ws-1');
  });

  it('fetchWorkspaceDefaultLocale 无工作区时返回 null', async () => {
    const fetchImpl = createMockFetch(200, { data: [], next_cursor: null });
    const client = createClient(fetchImpl);

    await expect(fetchWorkspaceDefaultLocale(client)).resolves.toBeNull();
  });

  it('fetchWorkspaceDefaultLocale detail 缺省/空串时返回 null', async () => {
    const noLocale = sequenceFetch(
      { status: 200, body: { data: [SUMMARY], next_cursor: null } },
      { status: 200, body: { data: { ...DETAIL, settings: {} } } },
    );
    await expect(fetchWorkspaceDefaultLocale(createClient(noLocale))).resolves.toBeNull();

    const emptyLocale = sequenceFetch(
      { status: 200, body: { data: [SUMMARY], next_cursor: null } },
      { status: 200, body: { data: { ...DETAIL, settings: { default_locale: '' } } } },
    );
    await expect(fetchWorkspaceDefaultLocale(createClient(emptyLocale))).resolves.toBeNull();
  });

  it('fetchWorkspaceDefaultLocale detail 读取失败时静默降级为 null', async () => {
    const fetchImpl = sequenceFetch(
      { status: 200, body: { data: [SUMMARY], next_cursor: null } },
      { status: 404, body: { error: { code: 'not_found', message: 'workspace not found' } } },
    );
    const client = createClient(fetchImpl);

    await expect(fetchWorkspaceDefaultLocale(client)).resolves.toBeNull();
  });

  it('列表网络错误时抛出 MeshApiError', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('network')) as unknown as typeof fetch;
    const client = createClient(fetchImpl);

    await expect(fetchWorkspaceDefaultLocale(client)).rejects.toMatchObject({
      status: 0,
      code: 'network',
    });
  });

  it('409 slug_taken 透传具名码与 details', async () => {
    const fetchImpl = createMockFetch(409, {
      error: { code: 'slug_taken', message: 'slug taken', details: { slug: 'acme' } },
    });
    const client = createClient(fetchImpl);

    await expect(createWorkspace(client, { name: 'Acme', slug: 'acme' })).rejects.toMatchObject({
      status: 409,
      code: 'slug_taken',
      details: { slug: 'acme' },
    });
  });
});
