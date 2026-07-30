/**
 * Issue 列表客户端排序纯助手(design-quality.md §7.6:可排序列显示方向与无障碍状态)。
 *
 * 为避免改动列表 API(服务端固定 created_at desc 分页),排序在已加载行上
 * 客户端进行;排序态经 URL ?sort=/?order= 持久化以便分享与刷新保持。
 * 纯函数,无 React/网络依赖,可独立单测。
 */
import type { IssuePriority, IssueSummary } from './types';

/** 可排序列(编号/标题/优先级/截止日)。 */
export type IssueSortField = 'identifier' | 'title' | 'priority' | 'due';

export const ISSUE_SORT_FIELDS: readonly IssueSortField[] = ['identifier', 'title', 'priority', 'due'];

export interface IssueSortState {
  readonly field: IssueSortField;
  readonly order: 'asc' | 'desc';
}

/** 优先级序:urgent 最前,none 最后(与看板 PRIORITY_KEYS 一致)。 */
export const PRIORITY_SORT_RANK: Readonly<Record<IssuePriority, number>> = Object.freeze({
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4,
});

function isSortField(value: string): value is IssueSortField {
  return (ISSUE_SORT_FIELDS as readonly string[]).includes(value);
}

/** 从 URL 参数解析排序态;非法字段回退 null(默认排序)。 */
export function parseIssueSort(
  sortParam: string | null,
  orderParam: string | null,
): IssueSortState | null {
  if (sortParam === null || !isSortField(sortParam)) return null;
  return { field: sortParam, order: orderParam === 'desc' ? 'desc' : 'asc' };
}

/**
 * 点击表头的排序循环:无 → 升序 → 降序 → 无。
 * 切换到不同列时从升序重新开始。
 */
export function nextIssueSort(
  current: IssueSortState | null,
  field: IssueSortField,
): IssueSortState | null {
  if (current === null || current.field !== field) return { field, order: 'asc' };
  if (current.order === 'asc') return { field, order: 'desc' };
  return null;
}

/** 截止日比较键:null 恒排末尾(升序时最大)。 */
function dueKey(issue: IssueSummary): number {
  if (issue.due_date === null) return Number.POSITIVE_INFINITY;
  const time = Date.parse(issue.due_date);
  return Number.isNaN(time) ? Number.POSITIVE_INFINITY : time;
}

/** 单列升序比较(相等返回 0,由调用方保证稳定排序)。 */
export function compareIssueField(a: IssueSummary, b: IssueSummary, field: IssueSortField): number {
  switch (field) {
    case 'identifier':
      return a.identifier.localeCompare(b.identifier, undefined, { numeric: true });
    case 'title':
      return a.title.localeCompare(b.title);
    case 'priority':
      return PRIORITY_SORT_RANK[a.priority] - PRIORITY_SORT_RANK[b.priority];
    case 'due': {
      const diff = dueKey(a) - dueKey(b);
      // 双方均无截止日(∞ − ∞ = NaN)视为相等,保证比较函数全函数(total)。
      return Number.isNaN(diff) ? 0 : diff;
    }
  }
}

/** 按排序态返回新数组;null 时原序拷贝(不修改入参,不可变)。 */
export function sortIssues(
  issues: readonly IssueSummary[],
  sort: IssueSortState | null,
): readonly IssueSummary[] {
  if (sort === null) return [...issues];
  const factor = sort.order === 'asc' ? 1 : -1;
  return [...issues].sort((a, b) => compareIssueField(a, b, sort.field) * factor);
}
