/**
 * loadAllGroups 分页合并单测:遍历 next_cursor 直至末页,按 group key 合并各组 data
 * (整板加载,供本地重分桶使用)。
 */
import { describe, expect, it, vi } from 'vitest';
import { loadAllGroups } from '../BoardPage';

interface StubClient {
  request: ReturnType<typeof vi.fn>;
  grouped: ReturnType<typeof vi.fn>;
}

function groupedClient(pages: unknown[]): StubClient {
  const grouped = vi.fn();
  pages.forEach((p) => grouped.mockResolvedValueOnce(p));
  return { request: vi.fn(), grouped };
}

const CARD_A = { id: 'a', identifier: 'A', title: 'A', state_category: 'todo', status: null, status_id: 's', priority: 'none', assignee: null, assignee_id: null, project_id: null, position: 1, version: 1, updated_at: '' };
const CARD_B = { id: 'b', identifier: 'B', title: 'B', state_category: 'todo', status: null, status_id: 's', priority: 'none', assignee: null, assignee_id: null, project_id: null, position: 2, version: 1, updated_at: '' };
const CARD_C = { id: 'c', identifier: 'C', title: 'C', state_category: 'done', status: null, status_id: 's', priority: 'none', assignee: null, assignee_id: null, project_id: null, position: 1, version: 1, updated_at: '' };

const PAGE1 = {
  layout: 'board', group_by: 'state_category', column_target_status: {},
  groups: [{ key: 'todo', label: 'Todo', count: 2, wip: null, data: [CARD_A] }],
  next_cursor: 'cur1',
};
const PAGE2 = {
  layout: 'board', group_by: 'state_category', column_target_status: {},
  groups: [
    { key: 'todo', label: 'Todo', count: 2, wip: null, data: [CARD_B] },
    { key: 'done', label: 'Done', count: 1, wip: null, data: [CARD_C] },
  ],
  next_cursor: null,
};

describe('loadAllGroups', () => {
  it('单页(next_cursor=null)直接返回', async () => {
    const client = groupedClient([{ ...PAGE1, next_cursor: null }]);
    const result = await loadAllGroups(client as never, 'v1');
    expect(result.groups).toHaveLength(1);
    expect(client.grouped).toHaveBeenCalledTimes(1);
  });

  it('多页按 group key 合并 data,直至 next_cursor=null', async () => {
    const client = groupedClient([PAGE1, PAGE2]);
    const result = await loadAllGroups(client as never, 'v1');
    expect(client.grouped).toHaveBeenCalledTimes(2);
    const byKey = new Map(result.groups.map((g) => [g.key, g]));
    expect(byKey.get('todo')?.data.map((c) => c.id)).toEqual(['a', 'b']);
    expect(byKey.get('done')?.data.map((c) => c.id)).toEqual(['c']);
  });
});
