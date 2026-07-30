/**
 * issuesSort 纯助手单测:URL 解析 / 循环切换 / 各列比较 / 稳定不可变排序。
 */
import { describe, expect, it } from 'vitest';
import {
  compareIssueField,
  nextIssueSort,
  parseIssueSort,
  sortIssues,
} from '../issuesSort';
import type { IssueSortField, IssueSortState } from '../issuesSort';
import type { IssuePriority, IssueSummary } from '../types';

function issue(overrides: Partial<IssueSummary> = {}): IssueSummary {
  return {
    id: 'id',
    workspace_id: 'ws',
    project_id: null,
    project: null,
    identifier_namespace_key: 'WS',
    number: 1,
    identifier: 'WS-1',
    title: 'Title',
    description: null,
    status: null,
    status_id: 'st',
    state_category: 'todo',
    priority: 'none',
    assignee: null,
    assignee_id: null,
    reporter: null,
    reporter_id: null,
    estimate: null,
    estimate_unit: null,
    due_date: null,
    start_date: null,
    milestone_id: null,
    cycle_id: null,
    parent_id: null,
    position: 0,
    completed_at: null,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('parseIssueSort', () => {
  it('合法字段 + 默认升序', () => {
    expect(parseIssueSort('title', null)).toEqual({ field: 'title', order: 'asc' });
  });

  it('desc 顺序', () => {
    expect(parseIssueSort('priority', 'desc')).toEqual({ field: 'priority', order: 'desc' });
  });

  it('null / 非法字段回退 null', () => {
    expect(parseIssueSort(null, 'asc')).toBeNull();
    expect(parseIssueSort('bogus', 'asc')).toBeNull();
  });
});

describe('nextIssueSort', () => {
  it('循环:无 → 升 → 降 → 无', () => {
    const asc = nextIssueSort(null, 'title');
    expect(asc).toEqual({ field: 'title', order: 'asc' });
    const desc = nextIssueSort(asc, 'title');
    expect(desc).toEqual({ field: 'title', order: 'desc' });
    expect(nextIssueSort(desc, 'title')).toBeNull();
  });

  it('切换列从升序重新开始', () => {
    const current: IssueSortState = { field: 'title', order: 'desc' };
    expect(nextIssueSort(current, 'due')).toEqual({ field: 'due', order: 'asc' });
  });
});

describe('compareIssueField', () => {
  it('identifier 数值感知比较', () => {
    const a = issue({ identifier: 'WS-2' });
    const b = issue({ identifier: 'WS-10' });
    expect(compareIssueField(a, b, 'identifier')).toBeLessThan(0);
  });

  it('title 字典比较', () => {
    const a = issue({ title: 'Apple' });
    const b = issue({ title: 'Banana' });
    expect(compareIssueField(a, b, 'title')).toBeLessThan(0);
    expect(compareIssueField(b, a, 'title')).toBeGreaterThan(0);
    expect(compareIssueField(a, issue({ title: 'Apple' }), 'title')).toBe(0);
  });

  it('priority 按等级序(urgent 最前)', () => {
    const urgent = issue({ priority: 'urgent' });
    const none = issue({ priority: 'none' });
    expect(compareIssueField(urgent, none, 'priority')).toBeLessThan(0);
  });

  it('due 空值恒排末尾,非法日期视同空值', () => {
    const dated = issue({ due_date: '2026-08-15' });
    const undated = issue({ due_date: null });
    const invalid = issue({ due_date: 'not-a-date' });
    expect(compareIssueField(dated, undated, 'due')).toBeLessThan(0);
    expect(compareIssueField(undated, dated, 'due')).toBeGreaterThan(0);
    expect(compareIssueField(invalid, dated, 'due')).toBeGreaterThan(0);
    expect(compareIssueField(undated, invalid, 'due')).toBe(0);
  });

  it('覆盖全部列分支', () => {
    const fields: readonly IssueSortField[] = ['identifier', 'title', 'priority', 'due'];
    for (const field of fields) {
      expect(typeof compareIssueField(issue(), issue(), field)).toBe('number');
    }
  });
});

describe('sortIssues', () => {
  const a = issue({ id: 'a', title: 'Banana', priority: 'low' as IssuePriority });
  const b = issue({ id: 'b', title: 'Apple', priority: 'urgent' as IssuePriority });
  const c = issue({ id: 'c', title: 'Cherry', priority: 'high' as IssuePriority });

  it('null 排序态原序拷贝且不修改入参', () => {
    const input = [a, b, c];
    const result = sortIssues(input, null);
    expect(result.map((i) => i.id)).toEqual(['a', 'b', 'c']);
    expect(result).not.toBe(input);
  });

  it('title 升 / 降序', () => {
    expect(sortIssues([a, b, c], { field: 'title', order: 'asc' }).map((i) => i.id)).toEqual([
      'b',
      'a',
      'c',
    ]);
    expect(sortIssues([a, b, c], { field: 'title', order: 'desc' }).map((i) => i.id)).toEqual([
      'c',
      'a',
      'b',
    ]);
  });

  it('priority 升序(urgent 最前)', () => {
    expect(sortIssues([a, b, c], { field: 'priority', order: 'asc' }).map((i) => i.id)).toEqual([
      'b',
      'c',
      'a',
    ]);
  });
});
