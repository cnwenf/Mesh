/**
 * 看板实时增量合并测试(纯函数,kanban.md §3.5):单卡插入/移动/移除、防回退、
 * view.updated → 整板重拉、view.presence 不动分组、归属本地重判。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import type { BoardCard, BoardGroup } from '../projection';
import {
  applyBoardFrame,
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
    { key: 'in_progress', label: 'In Progress', count: 0, wip: { limit: 2, enforcement: 'block' }, data: [] },
  ];
}

function frame(event: string, payload: Record<string, unknown>): RealtimeEventFrame {
  return { op: 'event', channel: 'workspace:w:issues', seq: 1, event, payload };
}

const CTX = { groupBy: 'state_category', belongs: () => true };

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
      frame('issue.moved', { id: 'i1', changes: { state_category: 'in_progress', status_id: 'st_ip' }, updated_at: '2026-07-26T11:00:00Z' }),
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
      frame('issue.updated', { id: 'i1', changes: { priority: 'urgent' }, updated_at: '2026-07-26T11:00:00Z' }),
      CTX,
    );
    const todo = result.groups.find((g) => g.key === 'todo');
    expect(todo?.data[0]?.priority).toBe('urgent');
    expect(todo?.count).toBe(1);
  });

  it('不再 belongs → 从视图移除', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.updated', { id: 'i1', changes: { priority: 'low' }, updated_at: '2026-07-26T11:00:00Z' }),
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
      frame('issue.updated', { id: 'i1', changes: { priority: 'low' }, updated_at: '2026-07-26T09:00:00Z' }),
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
    expect(cardBelongsToView(card({ priority: 'low', state_category: 'done' }), filters)).toBe(false);
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
    const notIn = { operator: 'AND', conditions: [{ field: 'priority', op: 'not_in', value: ['low'] }] };
    expect(cardBelongsToView(card({ priority: 'high' }), notIn)).toBe(true);
    expect(cardBelongsToView(card({ priority: 'low' }), notIn)).toBe(false);
    const unknown = { operator: 'AND', conditions: [{ field: 'milestone_id', op: 'eq', value: 'x' }] };
    expect(cardBelongsToView(card(), unknown)).toBe(true);
    const label = { operator: 'AND', conditions: [{ field: 'label', op: 'in', value: ['l'] }] };
    expect(cardBelongsToView(card(), label)).toBe(true);
    const custom = { operator: 'AND', conditions: [{ field_kind: 'custom_field', op: 'eq', value: 'x' }] };
    expect(cardBelongsToView(card(), custom)).toBe(true);
    const nonObject = { operator: 'AND', conditions: ['oops'] };
    expect(cardBelongsToView(card(), nonObject)).toBe(true);
  });
});

describe('rebucketGroups(按 group_by 本地重分桶,§4.2)', () => {
  const g = (key: string, data: BoardCard[], label = key): BoardGroup => ({
    key, label, count: data.length, wip: null, data,
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
    const a = card({ id: 'a', status: { id: 'st1', name: 'In Review', category: 'in_review' }, status_id: 'st1' });
    const groups = [g('todo', [a], 'Todo')];
    const byStatus = rebucketGroups(groups, 'status');
    expect(byStatus.find((x) => x.key === 'st1')?.label).toBe('In Review');
  });
});
