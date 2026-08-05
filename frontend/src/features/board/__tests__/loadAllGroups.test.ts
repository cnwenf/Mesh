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

const CARD_A = {
  id: 'a',
  identifier: 'A',
  title: 'A',
  state_category: 'todo',
  status: null,
  status_id: 's',
  priority: 'none',
  assignee: null,
  assignee_id: null,
  project_id: null,
  position: 1,
  version: 1,
  updated_at: '',
};
const CARD_B = {
  id: 'b',
  identifier: 'B',
  title: 'B',
  state_category: 'todo',
  status: null,
  status_id: 's',
  priority: 'none',
  assignee: null,
  assignee_id: null,
  project_id: null,
  position: 2,
  version: 1,
  updated_at: '',
};
const CARD_C = {
  id: 'c',
  identifier: 'C',
  title: 'C',
  state_category: 'done',
  status: null,
  status_id: 's',
  priority: 'none',
  assignee: null,
  assignee_id: null,
  project_id: null,
  position: 1,
  version: 1,
  updated_at: '',
};

const PAGE1 = {
  layout: 'board',
  group_by: 'state_category',
  column_target_status: {},
  groups: [{ key: 'todo', label: 'Todo', count: 2, wip: null, data: [CARD_A] }],
  next_cursor: 'cur1',
};
const PAGE2 = {
  layout: 'board',
  group_by: 'state_category',
  column_target_status: {},
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
    expect(result.next_cursor).toBeNull();
  });

  it('整体游标页面重叠时按 cell + issue id 去重', async () => {
    const client = groupedClient([
      PAGE1,
      {
        ...PAGE2,
        groups: [
          { key: 'todo', label: 'Todo', count: 2, wip: null, data: [CARD_A, CARD_B] },
          { key: 'done', label: 'Done', count: 1, wip: null, data: [CARD_C] },
        ],
      },
    ]);

    const result = await loadAllGroups(client as never, 'v1');

    expect(
      result.groups.find((group) => group.key === 'todo')?.data.map((item) => item.id),
    ).toEqual(['a', 'b']);
    expect(result.next_cursor).toBeNull();
  });

  it('多页按 lane + group cell 合并二维数据', async () => {
    const skeleton = {
      layout: 'board',
      group_by: 'state_category',
      sub_group_by: 'priority',
      column_target_status: {},
      columns: [
        { key: 'todo', label: 'Todo', count: 2, wip: null },
        { key: 'done', label: 'Done', count: 1, wip: null },
      ],
    };
    const client = groupedClient([
      {
        ...skeleton,
        lanes: [
          {
            key: 'high',
            label: 'High',
            count: 2,
            groups: [
              { key: 'todo', count: 2, data: [CARD_A] },
              { key: 'done', count: 0, data: [] },
            ],
          },
          {
            key: 'low',
            label: 'Low',
            count: 1,
            groups: [
              { key: 'todo', count: 0, data: [] },
              { key: 'done', count: 1, data: [] },
            ],
          },
        ],
        next_cursor: 'cur-two-d',
      },
      {
        ...skeleton,
        lanes: [
          {
            key: 'high',
            label: 'High',
            count: 2,
            groups: [
              { key: 'todo', count: 2, data: [CARD_B] },
              { key: 'done', count: 0, data: [] },
            ],
          },
          {
            key: 'low',
            label: 'Low',
            count: 1,
            groups: [
              { key: 'todo', count: 0, data: [] },
              { key: 'done', count: 1, data: [CARD_C] },
            ],
          },
        ],
        next_cursor: null,
      },
    ]);

    const result = await loadAllGroups(client as never, 'v1');

    expect(result.groups).toEqual([]);
    expect(result.lanes[0]?.groups[0]?.data.map((item) => item.id)).toEqual(['a', 'b']);
    expect(result.lanes[1]?.groups[1]?.data.map((item) => item.id)).toEqual(['c']);
    expect(result.columns.map((column) => column.key)).toEqual(['todo', 'done']);
    expect(result.next_cursor).toBeNull();
  });

  it('二维页面重叠时仅在同一 lane + group cell 内去重', async () => {
    const skeleton = {
      layout: 'board',
      group_by: 'state_category',
      sub_group_by: 'priority',
      column_target_status: {},
      columns: [{ key: 'todo', label: 'Todo', count: 2, wip: null }],
    };
    const lane = (data: readonly (typeof CARD_A)[], nextCursor: string | null) => ({
      ...skeleton,
      lanes: [
        {
          key: 'high',
          label: 'High',
          count: 2,
          groups: [{ key: 'todo', count: 2, data }],
        },
      ],
      next_cursor: nextCursor,
    });
    const client = groupedClient([lane([CARD_A], 'next'), lane([CARD_A, CARD_B], null)]);

    const result = await loadAllGroups(client as never, 'v1');

    expect(result.lanes[0]?.groups[0]?.data.map((item) => item.id)).toEqual(['a', 'b']);
    expect(result.next_cursor).toBeNull();
  });

  it('后续页出现新泳道或既有泳道的新分组时保留动态投影', async () => {
    const skeleton = {
      layout: 'board',
      group_by: 'state_category',
      sub_group_by: 'priority',
      column_target_status: {},
      columns: [
        { key: 'todo', label: 'Todo', count: 1, wip: null },
        { key: 'done', label: 'Done', count: 2, wip: null },
      ],
    };
    const client = groupedClient([
      {
        ...skeleton,
        lanes: [
          {
            key: 'high',
            label: 'High',
            count: 1,
            groups: [{ key: 'todo', count: 1, data: [CARD_A] }],
          },
        ],
        next_cursor: 'cur-dynamic',
      },
      {
        ...skeleton,
        lanes: [
          {
            key: 'high',
            label: 'High',
            count: 1,
            groups: [{ key: 'done', count: 1, data: [CARD_C] }],
          },
          {
            key: 'low',
            label: 'Low',
            count: 1,
            groups: [{ key: 'done', count: 1, data: [CARD_B] }],
          },
        ],
        next_cursor: null,
      },
    ]);

    const result = await loadAllGroups(client as never, 'v1');
    const byLane = new Map(result.lanes.map((lane) => [lane.key, lane]));

    expect(byLane.get('high')?.groups.map((group) => group.key)).toEqual(['todo', 'done']);
    expect(byLane.get('high')?.groups[1]?.data.map((item) => item.id)).toEqual(['c']);
    expect(byLane.get('low')?.groups[0]?.data.map((item) => item.id)).toEqual(['b']);
  });
});
