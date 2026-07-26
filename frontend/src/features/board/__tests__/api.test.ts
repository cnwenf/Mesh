/**
 * 看板视图 API 契约层测试:路径/方法/请求体与 kanban.md §3.1 一致,包络解包正确。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  createView,
  deleteView,
  duplicateView,
  getView,
  listViews,
  reorderViews,
  setViewWip,
  updateView,
  viewChannel,
  workspaceViewsChannel,
} from '../api';

function makeClient() {
  const request = vi.fn(async () => ({}));
  const list = vi.fn(async (): Promise<{ data: unknown[]; next_cursor: string | null }> => ({
    data: [],
    next_cursor: null,
  }));
  const client = { request, list } as unknown as MeshApiClient;
  return { client, request, list };
}

describe('视图频道名(§3.5/§6.7)', () => {
  it('派生详情频道与列表频道', () => {
    expect(viewChannel('v1')).toBe('view:v1');
    expect(workspaceViewsChannel('ws1')).toBe('workspace:ws1:views');
  });
});

describe('视图 API 路径与包络', () => {
  it('listViews 命中工作区路径并透传查询', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 'v1' }], next_cursor: 'c1' });
    const page = await listViews(client, 'ws1', { projectId: 'p1', limit: 10, cursor: 'prev' });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws1/views', {
      query: { project_id: 'p1', limit: 10, cursor: 'prev' },
    });
    expect(page.data).toEqual([{ id: 'v1' }]);
    expect(page.nextCursor).toBe('c1');
  });

  it('createView POST 工作区路径并携带请求体', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'v1' });
    const view = await createView(client, 'ws1', { name: 'Board', layout: 'board' });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws1/views', {
      body: { name: 'Board', layout: 'board' },
    });
    expect(view.id).toBe('v1');
  });

  it('getView 命中无工作区前缀路径', async () => {
    const { client, request } = makeClient();
    await getView(client, 'v1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/views/v1');
  });

  it('updateView 携带 If-Match(§6.14 乐观并发)', async () => {
    const { client, request } = makeClient();
    await updateView(client, 'v1', { name: 'X' }, { ifMatch: '2026-07-26T00:00:00Z' });
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/views/v1', {
      body: { name: 'X' },
      ifMatch: '2026-07-26T00:00:00Z',
    });
  });

  it('updateView 未提供 If-Match 时不携带该键', async () => {
    const { client, request } = makeClient();
    await updateView(client, 'v1', { name: 'X' });
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/views/v1', { body: { name: 'X' } });
  });

  it('deleteView / duplicateView / wip / reorder 路径', async () => {
    const { client, request } = makeClient();
    await deleteView(client, 'v1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/views/v1');

    await duplicateView(client, 'v1');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/views/v1/duplicate');

    await setViewWip(client, 'v1', { group_key: 'todo', limit: null });
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/views/v1/wip', {
      body: { group_key: 'todo', limit: null },
    });

    await reorderViews(client, 'ws1', ['v2', 'v1']);
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/workspaces/ws1/views/reorder', {
      body: { view_ids: ['v2', 'v1'] },
    });
  });
});
