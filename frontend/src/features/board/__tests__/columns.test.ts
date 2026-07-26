/**
 * 看板列派生逻辑测试(kanban.md §2.4 映射表,纯函数)。
 */
import { describe, expect, it } from 'vitest';
import { computeDropPosition } from '../BoardColumns';
import {
  PRIORITY_KEYS,
  STATE_CATEGORY_KEYS,
  columnLabelKey,
  columnsForView,
  deriveColumns,
  isRenderableLayout,
} from '../columns';
import type { BoardCard, BoardGroup } from '../projection';
import type { View } from '../types';

function makeView(overrides: Partial<View> = {}): View {
  return {
    id: 'v1',
    workspace_id: 'ws1',
    project_id: null,
    owner_member_id: 'm1',
    name: 'Board',
    layout: 'board',
    visibility: 'private',
    filters: {},
    group_by: null,
    sub_group_by: null,
    sort: [],
    display_fields: [],
    board_settings: {},
    position: 1,
    is_default: false,
    created_at: '2026-07-26T00:00:00Z',
    updated_at: '2026-07-26T00:00:00Z',
    ...overrides,
  };
}

describe('columnsForView', () => {
  it('group_by 为 null 时默认 7 个状态类别列', () => {
    const columns = columnsForView(makeView());
    expect(columns.map((c) => c.key)).toEqual([...STATE_CATEGORY_KEYS]);
    expect(columns.every((c) => c.count === 0 && !c.placeholder)).toBe(true);
  });

  it('group_by=state_category 与 null 等价', () => {
    expect(columnsForView(makeView({ group_by: 'state_category' })).map((c) => c.key)).toEqual([
      ...STATE_CATEGORY_KEYS,
    ]);
  });

  it('board_settings.columns 重排并筛选状态类别列', () => {
    const columns = columnsForView(
      makeView({ board_settings: { columns: ['done', 'todo', 'unknown_key'] } }),
    );
    expect(columns.map((c) => c.key)).toEqual(['done', 'todo']);
  });

  it('collapsed_columns 与 wip 映射到列', () => {
    const columns = columnsForView(
      makeView({
        board_settings: {
          collapsed_columns: ['done'],
          wip: { in_progress: { limit: 5, enforcement: 'block' } },
        },
      }),
    );
    const done = columns.find((c) => c.key === 'done');
    const inProgress = columns.find((c) => c.key === 'in_progress');
    expect(done?.collapsed).toBe(true);
    expect(inProgress?.wip).toEqual({ limit: 5, enforcement: 'block' });
    expect(columns.find((c) => c.key === 'todo')?.wip).toBeNull();
  });

  it('group_by=priority 呈现 5 档优先级列', () => {
    const columns = columnsForView(makeView({ group_by: 'priority' }));
    expect(columns.map((c) => c.key)).toEqual([...PRIORITY_KEYS]);
    expect(columns[0]?.label).toBe('board.priority.urgent');
  });

  it('动态分组(status/assignee/project/label)为占位列', () => {
    for (const groupBy of ['status', 'assignee', 'project', 'label'] as const) {
      const columns = columnsForView(makeView({ group_by: groupBy }));
      expect(columns).toHaveLength(1);
      expect(columns[0]?.placeholder).toBe(true);
      expect(columns[0]?.key).toBe('__dynamic__');
    }
  });

  it('动态分组携带 board_settings.columns 时按其派生占位列', () => {
    const columns = columnsForView(
      makeView({ group_by: 'status', board_settings: { columns: ['st1', 'st2'] } }),
    );
    expect(columns.map((c) => c.key)).toEqual(['st1', 'st2']);
    expect(columns.every((c) => c.placeholder)).toBe(true);
  });
});

describe('columnLabelKey', () => {
  it('类别与优先级分别映射不同 i18n 键空间', () => {
    expect(columnLabelKey('state_category', 'todo')).toBe('board.category.todo');
    expect(columnLabelKey('priority', 'urgent')).toBe('board.priority.urgent');
  });
});

describe('isRenderableLayout', () => {
  it('board 与 list 可渲染;timeline/table 预留', () => {
    expect(isRenderableLayout('board')).toBe(true);
    expect(isRenderableLayout('list')).toBe(true);
    expect(isRenderableLayout('timeline')).toBe(false);
    expect(isRenderableLayout('table')).toBe(false);
  });
});

function makeCard(overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    id: 'i1', identifier: 'WEB-1', title: 'C', state_category: 'todo',
    status: { id: 'st', name: 'Todo', category: 'todo' }, status_id: 'st', priority: 'high',
    assignee: null, assignee_id: null, project_id: null, position: 1, version: 1,
    updated_at: '', ...overrides,
  };
}

describe('computeDropPosition(浮点中点法,§4.3)', () => {
  it('空列 = 1;列底 = 末张+1;列顶 = 首张-1;中间 = 相邻中点', () => {
    const cards = [makeCard({ id: 'a', position: 2 }), makeCard({ id: 'b', position: 4 })];
    expect(computeDropPosition([], null)).toBe(1);
    expect(computeDropPosition(cards, null)).toBe(5);
    expect(computeDropPosition(cards, 0)).toBe(1);
    expect(computeDropPosition(cards, 1)).toBe(3);
    expect(computeDropPosition(cards, 99)).toBe(5);
  });
});

describe('deriveColumns(投影分组 → 列,§3.2)', () => {
  it('固定分组(state_category):骨架列 + 各组 count/卡片', () => {
    const v = makeView({ group_by: 'state_category' });
    const groups: BoardGroup[] = [
      { key: 'todo', label: 'Todo', count: 1, wip: { limit: 2, enforcement: 'warn' }, data: [makeCard()] },
    ];
    const { columns, cardsByKey } = deriveColumns(v, groups);
    expect(columns.map((c) => c.key)).toEqual([...STATE_CATEGORY_KEYS]);
    const todo = columns.find((c) => c.key === 'todo');
    expect(todo?.count).toBe(1);
    expect(todo?.wip).toEqual({ limit: 2, enforcement: 'warn' });
    expect(cardsByKey.todo).toHaveLength(1);
  });

  it('动态分组(project):列直接来自投影分组', () => {
    const v = makeView({ group_by: 'project' });
    const groups: BoardGroup[] = [
      { key: 'p1', label: 'Proj', count: 1, wip: null, data: [makeCard({ project_id: 'p1' })] },
    ];
    const { columns } = deriveColumns(v, groups);
    expect(columns).toHaveLength(1);
    expect(columns[0]).toMatchObject({ key: 'p1', label: 'Proj', count: 1 });
  });

  it('priority 分组呈现 5 档', () => {
    const v = makeView({ group_by: 'priority' });
    const { columns } = deriveColumns(v, []);
    expect(columns.map((c) => c.key)).toEqual([...PRIORITY_KEYS]);
  });
});
