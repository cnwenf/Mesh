/**
 * listCellEdit 纯逻辑单测:可见列解析、排序比较器、方向轮换、PATCH 体构造。
 */
import { describe, expect, it } from 'vitest';
import {
  ALL_COLUMN_IDS,
  PRIORITY_RANK,
  buildCellPatch,
  compareByField,
  compareCards,
  nextSortDir,
  resolveVisibleColumns,
} from '../listCellEdit';
import type { BoardCard } from '../projection';

function card(overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    id: 'x',
    identifier: 'WEB-1',
    title: 'Card',
    state_category: 'todo',
    status: { id: 'st', name: 'Todo', category: 'todo' },
    status_id: 'st',
    priority: 'medium',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position: 1,
    version: 1,
    updated_at: '2026-07-26T10:00:00Z',
    ...overrides,
  };
}

describe('resolveVisibleColumns', () => {
  it('settings 非空时优先 settings,过滤未知列并去重', () => {
    const result = resolveVisibleColumns(['title'], ['title', 'bogus', 'title', 'priority']);
    expect(result).toEqual(['title', 'priority']);
  });

  it('settings 为空数组时回退 view.display_fields', () => {
    const result = resolveVisibleColumns(['assignee', 'updated'], []);
    expect(result).toEqual(['assignee', 'updated']);
  });

  it('settings 为 undefined 时回退 view.display_fields', () => {
    const result = resolveVisibleColumns(['status'], undefined);
    expect(result).toEqual(['status']);
  });

  it('保留请求顺序(可重排列)', () => {
    const result = resolveVisibleColumns(['updated', 'identifier', 'title'], undefined);
    expect(result).toEqual(['updated', 'identifier', 'title']);
  });

  it('结果全为未知列时回退默认全部列', () => {
    const result = resolveVisibleColumns(['nope', 'nada'], undefined);
    expect(result).toEqual(ALL_COLUMN_IDS);
  });

  it('两个来源都为空时回退默认全部列', () => {
    const result = resolveVisibleColumns([], undefined);
    expect(result).toEqual(ALL_COLUMN_IDS);
  });
});

describe('PRIORITY_RANK', () => {
  it('urgent 最高,none 最低', () => {
    expect(PRIORITY_RANK.urgent).toBe(0);
    expect(PRIORITY_RANK.none).toBe(4);
    expect(PRIORITY_RANK.urgent).toBeLessThan(PRIORITY_RANK.high);
  });
});

describe('compareByField', () => {
  it('priority 按 rank 升序', () => {
    const a = card({ priority: 'urgent' });
    const b = card({ priority: 'low' });
    expect(compareByField('priority', a, b)).toBeLessThan(0);
    expect(compareByField('priority', b, a)).toBeGreaterThan(0);
  });

  it('未知优先级按 none(排最后)', () => {
    const a = card({ priority: 'mystery' });
    const b = card({ priority: 'urgent' });
    expect(compareByField('priority', a, b)).toBeGreaterThan(0);
  });

  it('updated 按时间升序', () => {
    const a = card({ updated_at: '2026-07-01T00:00:00Z' });
    const b = card({ updated_at: '2026-07-20T00:00:00Z' });
    expect(compareByField('updated', a, b)).toBeLessThan(0);
  });

  it('title 按 localeCompare', () => {
    const a = card({ title: 'Apple' });
    const b = card({ title: 'Banana' });
    expect(compareByField('title', a, b)).toBeLessThan(0);
  });

  it('identifier 按 localeCompare', () => {
    const a = card({ identifier: 'WEB-1' });
    const b = card({ identifier: 'WEB-2' });
    expect(compareByField('identifier', a, b)).toBeLessThan(0);
  });

  it('status 按状态名', () => {
    const a = card({ status: { id: 's1', name: 'Alpha', category: 'todo' } });
    const b = card({ status: { id: 's2', name: 'Zeta', category: 'done' } });
    expect(compareByField('status', a, b)).toBeLessThan(0);
  });

  it('assignee 空名排最后', () => {
    const unassigned = card({ assignee: null });
    const assigned = card({ assignee: { id: 'm1', name: 'Alice' } });
    expect(compareByField('assignee', unassigned, assigned)).toBeGreaterThan(0);
    expect(compareByField('assignee', assigned, unassigned)).toBeLessThan(0);
    expect(compareByField('assignee', unassigned, card({ assignee: null }))).toBe(0);
  });
});

describe('compareCards', () => {
  const a = card({ title: 'Apple', priority: 'low' });
  const b = card({ title: 'Banana', priority: 'urgent' });

  it("dir='asc' 与 compareByField 一致", () => {
    expect(compareCards('title', 'asc')(a, b)).toBeLessThan(0);
  });

  it("dir='desc' 反转结果", () => {
    expect(compareCards('title', 'desc')(a, b)).toBeGreaterThan(0);
  });

  it("dir='none' 恒等", () => {
    expect(compareCards('title', 'none')(a, b)).toBe(0);
  });

  it('priority desc 把 urgent 排前', () => {
    expect(compareCards('priority', 'desc')(a, b)).toBeLessThan(0);
  });
});

describe('nextSortDir', () => {
  it('asc → desc → none → asc 循环', () => {
    expect(nextSortDir('asc')).toBe('desc');
    expect(nextSortDir('desc')).toBe('none');
    expect(nextSortDir('none')).toBe('asc');
  });
});

describe('buildCellPatch', () => {
  it('title → {title}', () => {
    expect(buildCellPatch('title', 'New')).toEqual({ title: 'New' });
  });

  it('priority → {priority}', () => {
    expect(buildCellPatch('priority', 'high')).toEqual({ priority: 'high' });
  });

  it('status → {status_id}', () => {
    expect(buildCellPatch('status', 'st-9')).toEqual({ status_id: 'st-9' });
  });
});
