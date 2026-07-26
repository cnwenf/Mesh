/**
 * 看板列派生逻辑测试(kanban.md §2.4 映射表,纯函数)。
 */
import { describe, expect, it } from 'vitest';
import {
  PRIORITY_KEYS,
  STATE_CATEGORY_KEYS,
  columnLabelKey,
  columnsForView,
  isRenderableLayout,
} from '../columns';
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
