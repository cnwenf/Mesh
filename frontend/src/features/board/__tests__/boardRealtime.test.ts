/**
 * 看板实时增量合并测试(纯函数,kanban.md §3.5):单卡插入/移动/移除、防回退、
 * view.updated → 整板重拉、view.presence 不动分组、归属本地重判。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import type { BoardCard, BoardGroup, BoardLane, BoardProjectionColumn } from '../projection';
import {
  applyBoardFrame,
  applyBoardLaneFrame,
  cardBelongsToView,
  groupKeyForCard,
  rebucketGroups,
  NONE_KEY,
} from '../boardRealtime';

function card(overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    id: 'i1',
    identifier: 'WEB-1',
    title: 'Card',
    state_category: 'todo',
    status: { id: 'st_todo', name: 'Todo', category: 'todo' },
    status_id: 'st_todo',
    priority: 'high',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position: 1,
    version: 1,
    updated_at: '2026-07-26T10:00:00Z',
    ...overrides,
  };
}

function groups(): BoardGroup[] {
  return [
    { key: 'todo', label: 'Todo', count: 1, wip: null, data: [card()] },
    {
      key: 'in_progress',
      label: 'In Progress',
      count: 0,
      wip: { limit: 2, enforcement: 'block' },
      data: [],
    },
  ];
}

function frame(event: string, payload: Record<string, unknown>): RealtimeEventFrame {
  return { op: 'event', channel: 'workspace:w:issues', seq: 1, event, payload };
}

const CTX = { groupBy: 'state_category', belongs: () => true };

const SWIMLANE_COLUMNS: readonly BoardProjectionColumn[] = [
  { key: 'todo', label: 'Todo', count: 1, wip: null },
  { key: 'done', label: 'Done', count: 0, wip: null },
];

function lanes(): BoardLane[] {
  return [
    {
      key: 'high',
      label: 'High',
      count: 1,
      groups: [
        { key: 'todo', count: 1, data: [card()] },
        { key: 'done', count: 0, data: [] },
      ],
    },
    {
      key: 'low',
      label: 'Low',
      count: 0,
      groups: [
        { key: 'todo', count: 0, data: [] },
        { key: 'done', count: 0, data: [] },
      ],
    },
  ];
}

function singleCell(
  item: BoardCard,
  groupKey: string,
  subGroupKey: string,
): { columns: BoardProjectionColumn[]; lanes: BoardLane[] } {
  return {
    columns: [{ key: groupKey, label: groupKey, count: 1, wip: null }],
    lanes: [
      {
        key: subGroupKey,
        label: subGroupKey,
        count: 1,
        groups: [{ key: groupKey, count: 1, data: [item] }],
      },
    ],
  };
}

const LANE_CTX = {
  groupBy: 'state_category',
  subGroupBy: 'priority',
  belongs: () => true,
};

describe('groupKeyForCard(§2.4)', () => {
  it('按 group_by 派生列 key', () => {
    const c = card({ assignee_id: 'm1', project_id: 'p1', priority: 'urgent' });
    expect(groupKeyForCard(c, 'state_category')).toBe('todo');
    expect(groupKeyForCard(c, 'status')).toBe('st_todo');
    expect(groupKeyForCard(c, 'assignee')).toBe('m1');
    expect(groupKeyForCard(c, 'priority')).toBe('urgent');
    expect(groupKeyForCard(c, 'project')).toBe('p1');
  });

  it('assignee/project 无值 → __none__', () => {
    const c = card();
    expect(groupKeyForCard(c, 'assignee')).toBe(NONE_KEY);
    expect(groupKeyForCard(c, 'project')).toBe(NONE_KEY);
  });
});

describe('applyBoardFrame 单卡增量(§3.5)', () => {
  it('issue.moved 跨列重分桶并调整计数', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.moved', {
        id: 'i1',
        changes: { state_category: 'in_progress', status_id: 'st_ip' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      CTX,
    );
    expect(result.refetch).toBe(false);
    const todo = result.groups.find((g) => g.key === 'todo');
    const ip = result.groups.find((g) => g.key === 'in_progress');
    expect(todo?.data).toHaveLength(0);
    expect(todo?.count).toBe(0);
    expect(ip?.data).toHaveLength(1);
    expect(ip?.count).toBe(1);
    expect(ip?.data[0]?.state_category).toBe('in_progress');
  });

  it('issue.updated 就地合并字段', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'urgent' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      CTX,
    );
    const todo = result.groups.find((g) => g.key === 'todo');
    expect(todo?.data[0]?.priority).toBe('urgent');
    expect(todo?.count).toBe(1);
  });

  it('不再 belongs → 从视图移除', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      { groupBy: 'state_category', belongs: (c) => c.priority === 'high' },
    );
    expect(result.groups.find((g) => g.key === 'todo')?.data).toHaveLength(0);
  });

  it('issue.deleted 移除卡片', () => {
    const result = applyBoardFrame(groups(), frame('issue.deleted', { id: 'i1' }), CTX);
    expect(result.groups.find((g) => g.key === 'todo')?.data).toHaveLength(0);
  });

  it('issue.created 插入对应列', () => {
    const created = card({ id: 'i2', identifier: 'WEB-2', state_category: 'in_progress' });
    const result = applyBoardFrame(groups(), frame('issue.created', { issue: created }), CTX);
    const ip = result.groups.find((g) => g.key === 'in_progress');
    expect(ip?.data.map((c) => c.id)).toContain('i2');
    expect(ip?.count).toBe(1);
  });

  it('created 但已存在或不属于视图 → 不变', () => {
    const base = groups();
    const dup = card({ id: 'i1' });
    const r1 = applyBoardFrame(base, frame('issue.created', { issue: dup }), CTX);
    expect(r1.groups).toBe(base); // 原引用返回(无变化)
    const foreign = card({ id: 'i3', state_category: 'todo' });
    const r2 = applyBoardFrame(base, frame('issue.created', { issue: foreign }), {
      groupBy: 'state_category',
      belongs: () => false,
    });
    expect(r2.groups).toBe(base);
    expect(r2.groups.find((g) => g.key === 'todo')?.data).toHaveLength(1);
  });

  it('防回退:旧 updated_at 丢弃', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low' },
        updated_at: '2026-07-26T09:00:00Z',
      }),
      CTX,
    );
    expect(result.groups.find((g) => g.key === 'todo')?.data[0]?.priority).toBe('high');
  });

  it('view.updated → refetch;view.presence 不动', () => {
    const base = groups();
    const updated = applyBoardFrame(base, frame('view.updated', { id: 'v1' }), CTX);
    expect(updated.refetch).toBe(true);
    const presence = applyBoardFrame(
      base,
      frame('view.presence', { view_id: 'v1', online: 2 }),
      CTX,
    );
    expect(presence.refetch).toBe(false);
    expect(presence.groups).toBe(base);
  });

  it('无关帧返回原引用', () => {
    const base = groups();
    const result = applyBoardFrame(base, frame('comment.created', { id: 'c1' }), CTX);
    expect(result.groups).toBe(base);
  });

  it('缺失载荷保持稳定，无时间戳更新与新分组仍可增量收敛', () => {
    const base = groups();
    for (const invalid of [
      frame('issue.created', { issue: null }),
      frame('issue.created', { issue: 'bad' }),
      frame('issue.created', { issue: { id: 42 } }),
      frame('issue.updated', {}),
      frame('issue.updated', { id: 'missing', changes: { priority: 'low' } }),
    ]) {
      expect(applyBoardFrame(base, invalid, CTX).groups).toBe(base);
    }

    const updated = applyBoardFrame(
      base,
      frame('issue.updated', { id: 'i1', changes: { title: 'No timestamp' } }),
      CTX,
    );
    expect(updated.groups[0]?.data[0]?.title).toBe('No timestamp');

    const dynamic = applyBoardFrame(
      base,
      frame('issue.created', {
        issue: card({ id: 'i2', identifier: 'WEB-2', state_category: 'triage' }),
      }),
      CTX,
    );
    expect(dynamic.groups.at(-1)).toMatchObject({ key: 'triage', label: 'triage', count: 1 });
  });

  it.each(['updated', 'moved', 'project_changed'])(
    'issue.%s 目标卡不在投影内 → 请求该 id 单卡对账',
    (action) => {
      const base = groups();
      const result = applyBoardFrame(base, frame(`issue.${action}`, { id: 'i-enter' }), CTX);
      expect(result.groups).toBe(base);
      expect(result.refetch).toBe(false);
      expect(result.reconcileIssueId).toBe('i-enter');
    },
  );

  it('缺失 deleted/未知动作不请求单卡对账', () => {
    const base = groups();
    expect(
      applyBoardFrame(base, frame('issue.deleted', { id: 'i-missing' }), CTX).reconcileIssueId,
    ).toBeUndefined();
    expect(
      applyBoardFrame(base, frame('issue.archived', { id: 'i-missing' }), CTX).reconcileIssueId,
    ).toBeUndefined();
  });
});

describe('applyBoardLaneFrame 二维 cell 增量(§3.5)', () => {
  it('issue.created 插入对应 cell 并同步 lane/column/cell count', () => {
    const created = card({
      id: 'i2',
      identifier: 'WEB-2',
      state_category: 'done',
      priority: 'low',
    });
    const result = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.created', { issue: created }),
      LANE_CTX,
    );

    expect(result.refetch).toBe(false);
    expect(result.columns.find((column) => column.key === 'done')?.count).toBe(1);
    const low = result.lanes.find((lane) => lane.key === 'low');
    expect(low?.count).toBe(1);
    expect(low?.groups.find((group) => group.key === 'done')).toMatchObject({
      count: 1,
      data: [expect.objectContaining({ id: 'i2' })],
    });
  });

  it('issue.updated 按更新后的两轴重分 cell，不改主列总数', () => {
    const result = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low', title: 'Updated' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      LANE_CTX,
    );

    expect(result.columns.find((column) => column.key === 'todo')?.count).toBe(1);
    expect(result.lanes.find((lane) => lane.key === 'high')?.count).toBe(0);
    const moved = result.lanes
      .find((lane) => lane.key === 'low')
      ?.groups.find((group) => group.key === 'todo')?.data[0];
    expect(moved).toMatchObject({ id: 'i1', priority: 'low', title: 'Updated' });
  });

  it('issue.moved 优先采用 payload.to.group_key/to_sub_group 并更新位置', () => {
    const result = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.moved', {
        id: 'i1',
        to: { group_key: 'done' },
        to_sub_group: 'low',
        position: 8,
        updated_at: '2026-07-26T11:00:00Z',
      }),
      LANE_CTX,
    );

    expect(result.columns.map((column) => [column.key, column.count])).toEqual([
      ['todo', 0],
      ['done', 1],
    ]);
    const moved = result.lanes
      .find((lane) => lane.key === 'low')
      ?.groups.find((group) => group.key === 'done')?.data[0];
    expect(moved).toMatchObject({
      id: 'i1',
      state_category: 'done',
      priority: 'low',
      position: 8,
    });
  });

  it('issue.deleted 从 cell 移除并递减三层 count', () => {
    const result = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.deleted', { id: 'i1' }),
      LANE_CTX,
    );
    expect(result.columns.find((column) => column.key === 'todo')?.count).toBe(0);
    expect(result.lanes.find((lane) => lane.key === 'high')?.count).toBe(0);
    expect(
      result.lanes
        .find((lane) => lane.key === 'high')
        ?.groups.find((group) => group.key === 'todo'),
    ).toMatchObject({ count: 0, data: [] });
  });

  it('issue.project_changed 用 to_project_id 与 status mapping 更新项目泳道', () => {
    const projectColumns: readonly BoardProjectionColumn[] = [
      { key: 'todo', label: 'Todo', count: 1, wip: null },
    ];
    const projectLanes: readonly BoardLane[] = [
      {
        key: '__none__',
        label: 'No project',
        count: 1,
        groups: [{ key: 'todo', count: 1, data: [card()] }],
      },
    ];
    const result = applyBoardLaneFrame(
      projectColumns,
      projectLanes,
      frame('issue.project_changed', {
        id: 'i1',
        to_project_id: 'p2',
        mapped_fields: [
          {
            field: 'status',
            to: { id: 'st2', name: 'Todo 2', category: 'todo' },
          },
        ],
        updated_at: '2026-07-26T11:00:00Z',
      }),
      { groupBy: 'state_category', subGroupBy: 'project', belongs: () => true },
    );
    const target = result.lanes.find((lane) => lane.key === 'p2');
    expect(target?.groups[0]?.data[0]).toMatchObject({
      project_id: 'p2',
      status_id: 'st2',
      state_category: 'todo',
    });
  });

  it('过期/重复/不属于视图的帧保持稳定，只有 view.updated 请求 refetch', () => {
    const baseLanes = lanes();
    const stale = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      baseLanes,
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low' },
        updated_at: '2026-07-26T09:00:00Z',
      }),
      LANE_CTX,
    );
    expect(stale.columns).toBe(SWIMLANE_COLUMNS);
    expect(stale.lanes).toBe(baseLanes);

    const removed = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      baseLanes,
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      { ...LANE_CTX, belongs: () => false },
    );
    expect(removed.columns[0]?.count).toBe(0);

    expect(
      applyBoardLaneFrame(SWIMLANE_COLUMNS, baseLanes, frame('view.updated', {}), LANE_CTX).refetch,
    ).toBe(true);
    expect(
      applyBoardLaneFrame(SWIMLANE_COLUMNS, baseLanes, frame('view.presence', {}), LANE_CTX)
        .refetch,
    ).toBe(false);
  });

  it('异常/重复二维帧保持原引用，未知 issue 动作不触发 refetch', () => {
    const baseLanes = lanes();
    const invalidFrames = [
      frame('comment.created', { id: 'c1' }),
      frame('issue.created', { issue: null }),
      frame('issue.created', { issue: 'bad' }),
      frame('issue.created', { issue: { id: 42 } }),
      frame('issue.created', { issue: card() }),
      frame('issue.updated', {}),
      frame('issue.updated', { id: 'missing' }),
      frame('issue.archived', { id: 'i1' }),
    ];
    for (const invalid of invalidFrames) {
      const result = applyBoardLaneFrame(SWIMLANE_COLUMNS, baseLanes, invalid, LANE_CTX);
      expect(result.columns).toBe(SWIMLANE_COLUMNS);
      expect(result.lanes).toBe(baseLanes);
      expect(result.refetch).toBe(false);
    }

    const foreign = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      baseLanes,
      frame('issue.created', {
        issue: card({ id: 'i2', identifier: 'WEB-2' }),
      }),
      { ...LANE_CTX, belongs: () => false },
    );
    expect(foreign.lanes).toBe(baseLanes);
  });

  it('moved 支持轴字段回退、动态 status/assignee/project cell 与空值轴', () => {
    const base = singleCell(card(), 'todo', 'high');
    const axisFallback = applyBoardLaneFrame(
      base.columns,
      base.lanes,
      frame('issue.moved', {
        id: 'i1',
        to: { state_category: 'done', priority: 'low' },
      }),
      LANE_CTX,
    );
    expect(
      axisFallback.lanes
        .find((lane) => lane.key === 'low')
        ?.groups.find((group) => group.key === 'done')?.data[0],
    ).toMatchObject({ state_category: 'done', priority: 'low' });

    const statusAssignee = singleCell(card(), 'st_todo', '__none__');
    const assigned = applyBoardLaneFrame(
      statusAssignee.columns,
      statusAssignee.lanes,
      frame('issue.moved', {
        id: 'i1',
        to: { group_key: 'st_done' },
        to_sub_group: 'm2',
      }),
      { groupBy: 'status', subGroupBy: 'assignee', belongs: () => true },
    );
    expect(assigned.columns.find((column) => column.key === 'st_done')?.count).toBe(1);
    expect(assigned.lanes.find((lane) => lane.key === 'm2')?.groups[1]?.data[0]).toMatchObject({
      status_id: 'st_done',
      assignee_id: 'm2',
    });

    const projectState = singleCell(
      card({ project_id: 'p1', state_category: 'todo' }),
      'p1',
      'todo',
    );
    const cleared = applyBoardLaneFrame(
      projectState.columns,
      projectState.lanes,
      frame('issue.moved', {
        id: 'i1',
        to: { group_key: '__none__' },
        to_sub_group: 'done',
      }),
      { groupBy: 'project', subGroupBy: 'state_category', belongs: () => true },
    );
    expect(
      cleared.lanes
        .find((lane) => lane.key === 'done')
        ?.groups.find((group) => group.key === '__none__')?.data[0],
    ).toMatchObject({ project_id: null, state_category: 'done' });

    const changesOnly = applyBoardLaneFrame(
      base.columns,
      base.lanes,
      frame('issue.moved', { id: 'i1', changes: { priority: 'low' }, position: 'bad' }),
      LANE_CTX,
    );
    expect(changesOnly.lanes.find((lane) => lane.key === 'low')?.count).toBe(1);
  });

  it('project_changed 对 inbox、缺省 mapping 与精简 status mapping 均 fail closed', () => {
    const project = singleCell(card({ project_id: 'p1' }), 'todo', 'p1');
    const context = {
      groupBy: 'state_category',
      subGroupBy: 'project',
      belongs: () => true,
    };
    const inbox = applyBoardLaneFrame(
      project.columns,
      project.lanes,
      frame('issue.project_changed', { id: 'i1', to_project_id: null }),
      context,
    );
    expect(inbox.lanes.find((lane) => lane.key === '__none__')?.count).toBe(1);

    for (const mapped_fields of [
      [{ field: 'milestone_id', to: null }],
      [{ field: 'status', to: null }],
      [{ field: 'status', to: { name: 'Missing id' } }],
    ]) {
      const result = applyBoardLaneFrame(
        project.columns,
        project.lanes,
        frame('issue.project_changed', { id: 'i1', to_project_id: 'p2', mapped_fields }),
        context,
      );
      expect(result.lanes.find((lane) => lane.key === 'p2')?.count).toBe(1);
    }

    const compact = applyBoardLaneFrame(
      project.columns,
      project.lanes,
      frame('issue.project_changed', {
        id: 'i1',
        to_project_id: 'p2',
        mapped_fields: [{ field: 'status', to: { id: 'st2' } }],
      }),
      context,
    );
    expect(compact.lanes.find((lane) => lane.key === 'p2')?.groups[0]?.data[0]).toMatchObject({
      status_id: 'st2',
      state_category: 'todo',
      status: { id: 'st2', name: 'Todo', category: 'todo' },
    });
  });

  it('重建时保留同 cell 多卡并为新列/泳道补全交叉 cell', () => {
    const sameCell = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.created', {
        issue: card({ id: 'i2', identifier: 'WEB-2' }),
      }),
      LANE_CTX,
    );
    expect(sameCell.lanes[0]?.groups[0]).toMatchObject({ count: 2 });

    const dynamic = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.created', {
        issue: card({
          id: 'i3',
          identifier: 'WEB-3',
          state_category: 'triage',
          priority: 'medium',
        }),
      }),
      LANE_CTX,
    );
    expect(dynamic.columns.at(-1)).toMatchObject({ key: 'triage', count: 1 });
    expect(dynamic.lanes.at(-1)?.groups).toHaveLength(dynamic.columns.length);
  });

  it.each(['updated', 'moved', 'project_changed'])(
    '二维 issue.%s 目标卡不在投影内 → 请求该 id 单卡对账',
    (action) => {
      const baseLanes = lanes();
      const result = applyBoardLaneFrame(
        SWIMLANE_COLUMNS,
        baseLanes,
        frame(`issue.${action}`, { id: 'i-enter' }),
        LANE_CTX,
      );
      expect(result.columns).toBe(SWIMLANE_COLUMNS);
      expect(result.lanes).toBe(baseLanes);
      expect(result.refetch).toBe(false);
      expect(result.reconcileIssueId).toBe('i-enter');
    },
  );

  it('二维缺失 deleted/未知动作不请求单卡对账', () => {
    const baseLanes = lanes();
    expect(
      applyBoardLaneFrame(
        SWIMLANE_COLUMNS,
        baseLanes,
        frame('issue.deleted', { id: 'i-missing' }),
        LANE_CTX,
      ).reconcileIssueId,
    ).toBeUndefined();
    expect(
      applyBoardLaneFrame(
        SWIMLANE_COLUMNS,
        baseLanes,
        frame('issue.archived', { id: 'i-missing' }),
        LANE_CTX,
      ).reconcileIssueId,
    ).toBeUndefined();
  });
});

describe('cardBelongsToView 本地重判(§3.5)', () => {
  it('空 filters → 属于', () => {
    expect(cardBelongsToView(card(), {})).toBe(true);
  });

  it('顶层 AND eq/in 评估', () => {
    const filters = {
      operator: 'AND',
      conditions: [
        { field: 'priority', op: 'in', value: ['high', 'urgent'] },
        { field: 'state_category', op: 'eq', value: 'todo' },
      ],
    };
    expect(cardBelongsToView(card(), filters)).toBe(true);
    expect(cardBelongsToView(card({ priority: 'low' }), filters)).toBe(false);
  });

  it('OR 评估', () => {
    const filters = {
      operator: 'OR',
      conditions: [
        { field: 'priority', op: 'eq', value: 'urgent' },
        { field: 'state_category', op: 'eq', value: 'todo' },
      ],
    };
    expect(cardBelongsToView(card({ priority: 'low' }), filters)).toBe(true);
    expect(cardBelongsToView(card({ priority: 'low', state_category: 'done' }), filters)).toBe(
      false,
    );
  });

  it('嵌套/无法判定 → 保守保留', () => {
    const nested = {
      operator: 'AND',
      conditions: [{ operator: 'OR', conditions: [{ field: 'priority', op: 'eq', value: 'x' }] }],
    };
    expect(cardBelongsToView(card(), nested)).toBe(true);
    const range = { operator: 'AND', conditions: [{ field: 'due_date', op: 'lte', value: 'x' }] };
    expect(cardBelongsToView(card(), range)).toBe(true);
  });

  it('not_in 与不支持字段/label/q/自定义字段 → 保守', () => {
    const notIn = {
      operator: 'AND',
      conditions: [{ field: 'priority', op: 'not_in', value: ['low'] }],
    };
    expect(cardBelongsToView(card({ priority: 'high' }), notIn)).toBe(true);
    expect(cardBelongsToView(card({ priority: 'low' }), notIn)).toBe(false);
    const unknown = {
      operator: 'AND',
      conditions: [{ field: 'milestone_id', op: 'eq', value: 'x' }],
    };
    expect(cardBelongsToView(card(), unknown)).toBe(true);
    const label = { operator: 'AND', conditions: [{ field: 'label', op: 'in', value: ['l'] }] };
    expect(cardBelongsToView(card(), label)).toBe(true);
    const custom = {
      operator: 'AND',
      conditions: [{ field_kind: 'custom_field', op: 'eq', value: 'x' }],
    };
    expect(cardBelongsToView(card(), custom)).toBe(true);
    const nonObject = { operator: 'AND', conditions: ['oops'] };
    expect(cardBelongsToView(card(), nonObject)).toBe(true);
  });
});

describe('rebucketGroups(按 group_by 本地重分桶,§4.2)', () => {
  const g = (key: string, data: BoardCard[], label = key): BoardGroup => ({
    key,
    label,
    count: data.length,
    wip: null,
    data,
  });

  it('按新 group_by 重分桶并保留组标签', () => {
    const a = card({ id: 'a', priority: 'high', state_category: 'todo' });
    const b = card({ id: 'b', priority: 'high', state_category: 'done' });
    const c = card({ id: 'c', priority: 'low', state_category: 'todo' });
    const groups = [g('todo', [a, c], 'Todo'), g('done', [b], 'Done')];
    const byPriority = rebucketGroups(groups, 'priority');
    const high = byPriority.find((x) => x.key === 'high');
    expect(high?.data.map((x) => x.id).sort()).toEqual(['a', 'b']);
    expect(high?.count).toBe(2);
    expect(byPriority.find((x) => x.key === 'low')?.data.map((x) => x.id)).toEqual(['c']);
  });

  it('assignee/project 无值 → __none__ 列与回退标签', () => {
    const a = card({ id: 'a', assignee_id: null });
    const groups = [g('todo', [a], 'Todo')];
    const byAssignee = rebucketGroups(groups, 'assignee');
    const none = byAssignee.find((x) => x.key === NONE_KEY);
    expect(none?.label).toBe('No assignee');
    const byProject = rebucketGroups(groups, 'project');
    expect(byProject.find((x) => x.key === NONE_KEY)?.label).toBe('No project');
  });

  it('status 分组回退到卡片状态名', () => {
    const a = card({
      id: 'a',
      status: { id: 'st1', name: 'In Review', category: 'in_review' },
      status_id: 'st1',
    });
    const groups = [g('todo', [a], 'Todo')];
    const byStatus = rebucketGroups(groups, 'status');
    expect(byStatus.find((x) => x.key === 'st1')?.label).toBe('In Review');
  });
});
