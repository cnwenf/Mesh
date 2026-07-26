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
  reorderCard,
} from '../projection';

function makeClient() {
  const request = vi.fn(async (..._args: unknown[]): Promise<unknown> => ({}));
  const grouped = vi.fn(
    async (..._args: unknown[]): Promise<Record<string, unknown>> => ({
      groups: [],
      next_cursor: null,
    }),
  );
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
});
