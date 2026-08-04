/**
 * 看板投影契约层测试:GET /views/{id}/issues(分组整体游标)、POST moves/reorder
 * 的路径/方法/请求体与 kanban.md §3.2 一致,包络解包正确。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  fetchViewIssues,
  moveCard,
  previewMove,
  quickCreateCard,
  reorderCard,
} from '../projection';

function makeClient() {
  const request = vi.fn(async (..._args: unknown[]): Promise<unknown> => ({}));
  const grouped = vi.fn(async (..._args: unknown[]): Promise<Record<string, unknown>> => ({
    groups: [],
    next_cursor: null,
  }));
  const client = { request, grouped } as unknown as MeshApiClient;
  return { client, request, grouped };
}

describe('fetchViewIssues(§3.2 分组整体游标)', () => {
  it('命中视图执行路径并透传游标参数', async () => {
    const { client, grouped } = makeClient();
    grouped.mockResolvedValueOnce({
      layout: 'board',
      group_by: 'state_category',
      column_target_status: { todo: 'st_todo' },
      groups: [{ key: 'todo', label: 'Todo', count: 1, wip: null, data: [{ id: 'i1' }] }],
      next_cursor: 'c1',
    });
    const result = await fetchViewIssues(client, 'v1', { limit: 20, cursor: 'prev' });
    expect(grouped).toHaveBeenCalledWith('/api/v1/views/v1/issues', {
      query: { limit: 20, cursor: 'prev' },
    });
    expect(result.group_by).toBe('state_category');
    expect(result.column_target_status).toEqual({ todo: 'st_todo' });
    expect(result.groups[0]?.key).toBe('todo');
    expect(result.next_cursor).toBe('c1');
  });

  it('缺省元数据回落 board/state_category/空映射', async () => {
    const { client, grouped } = makeClient();
    grouped.mockResolvedValueOnce({ groups: [], next_cursor: null });
    const result = await fetchViewIssues(client, 'v1');
    expect(result.layout).toBe('board');
    expect(result.group_by).toBe('state_category');
    expect(result.column_target_status).toEqual({});
    expect(result.next_cursor).toBeNull();
  });

  it('解析 columns + lanes 二维泳道包络', async () => {
    const { client, grouped } = makeClient();
    grouped.mockResolvedValueOnce({
      layout: 'board',
      group_by: 'state_category',
      sub_group_by: 'priority',
      columns: [{ key: 'todo', label: 'Todo', count: 2, wip: null }],
      lanes: [
        {
          key: 'high',
          label: 'High',
          count: 2,
          groups: [{ key: 'todo', count: 2, data: [{ id: 'i1' }] }],
        },
      ],
      next_cursor: 'two-d-cursor',
    });

    const result = await fetchViewIssues(client, 'v1');

    expect(result.groups).toEqual([]);
    expect(result.sub_group_by).toBe('priority');
    expect(result.columns[0]).toMatchObject({ key: 'todo', count: 2 });
    expect(result.lanes[0]?.groups[0]?.data[0]).toMatchObject({ id: 'i1' });
    expect(result.next_cursor).toBe('two-d-cursor');
  });
});

describe('moveCard / previewMove / reorderCard(§3.2/§4.3)', () => {
  it('moveCard POST moves 并携带拖拽请求体', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'i1', version: 8 });
    const body = { issue_id: 'i1', to_group_key: 'in_progress', position: 2.5, version: 7 };
    const result = await moveCard(client, 'v1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/views/v1/moves', { body });
    expect(result.version).toBe(8);
  });

  it('moveCard 携带目标泳道 key', async () => {
    const { client, request } = makeClient();
    const body = {
      issue_id: 'i1',
      to_group_key: 'done',
      to_sub_group_key: 'urgent',
      position: 2,
    };
    await moveCard(client, 'v1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/views/v1/moves', { body });
  });

  it('previewMove 强制 dry_run=true', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ issue_id: 'i1', mapped_fields: [], cleared_fields: [] });
    await previewMove(client, 'v1', { issue_id: 'i1', to_group_key: 'p2', position: 1 });
    const call = request.mock.calls[0];
    expect(call?.[1]).toBe('/api/v1/views/v1/moves');
    expect((call?.[2] as { body: { dry_run: boolean } }).body.dry_run).toBe(true);
  });

  it('reorderCard POST reorder 仅位置', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'i1', group_key: 'todo', position: 3 });
    const result = await reorderCard(client, 'v1', {
      issue_id: 'i1',
      to_group_key: 'todo',
      position: 3,
    });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/views/v1/reorder', {
      body: { issue_id: 'i1', to_group_key: 'todo', position: 3 },
    });
    expect(result.position).toBe(3);
  });

  it('reorderCard 在 cell 内携带 sub_group_key', async () => {
    const { client, request } = makeClient();
    await reorderCard(client, 'v1', {
      issue_id: 'i1',
      to_group_key: 'todo',
      sub_group_key: 'high',
      position: 3,
    });
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/views/v1/reorder', {
      body: {
        issue_id: 'i1',
        to_group_key: 'todo',
        sub_group_key: 'high',
        position: 3,
      },
    });
  });

  it('quickCreateCard 通过视图 cell 端点继承两轴', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'i-new' });
    const body = { title: 'New card', group_key: 'todo', sub_group_key: 'high' };
    const result = await quickCreateCard(client, 'v1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/views/v1/issues', { body });
    expect(result).toMatchObject({ id: 'i-new' });
  });
});
