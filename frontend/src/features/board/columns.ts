/**
 * 看板列派生(纯函数,kanban.md §2.4 分组映射表)。
 *
 * 定义层切片不接真实 issue 数据:列结构完全由视图配置(group_by +
 * board_settings.columns / collapsed_columns / wip)派生,卡片计数恒为 0,
 * 投影查询落地后计数由执行响应填充。
 */
import type { BoardCard, BoardGroup } from './projection';
import type { BoardColumn, View, WipLimit } from './types';

/** 7 个固定状态类别(issue.md §2.2;看板默认列)。 */
export const STATE_CATEGORY_KEYS: readonly string[] = [
  'backlog',
  'todo',
  'in_progress',
  'in_review',
  'blocked',
  'done',
  'cancelled',
];

/** 5 档优先级(issue.md §2.2,可排序序)。 */
export const PRIORITY_KEYS: readonly string[] = ['urgent', 'high', 'medium', 'low', 'none'];

/** 列 key 的 i18n 标签键(类别与优先级)。 */
export function columnLabelKey(groupBy: string | null, key: string): string {
  if (groupBy === 'priority' || groupBy === null && PRIORITY_KEYS.includes(key)) {
    return `board.priority.${key}`;
  }
  return `board.category.${key}`;
}

function wipFor(settings: View['board_settings'], key: string): WipLimit | null {
  const rule = settings.wip?.[key];
  if (rule === undefined) return null;
  return { limit: rule.limit, enforcement: rule.enforcement };
}

/**
 * 按视图配置派生看板列:
 * - group_by = state_category / null(默认)→ 7 个固定类别列;
 *   board_settings.columns 存在时按其排序/筛选列集合;
 * - group_by = priority → 5 档优先级列;
 * - group_by = status/assignee/project/label → 列来自动态实体,
 *   定义层切片以占位列呈现(数据随投影查询增量落地)。
 */
export function columnsForView(view: View): readonly BoardColumn[] {
  const settings = view.board_settings;
  const collapsed = new Set(settings.collapsed_columns ?? []);
  const groupBy = view.group_by;

  if (groupBy === null || groupBy === 'state_category') {
    const configured = settings.columns;
    const known = new Set(STATE_CATEGORY_KEYS);
    const keys =
      configured !== undefined && configured.length > 0
        ? configured.filter((key) => known.has(key))
        : STATE_CATEGORY_KEYS;
    return keys.map((key) => ({
      key,
      label: columnLabelKey('state_category', key),
      collapsed: collapsed.has(key),
      wip: wipFor(settings, key),
      count: 0,
      placeholder: false,
    }));
  }

  if (groupBy === 'priority') {
    return PRIORITY_KEYS.map((key) => ({
      key,
      label: columnLabelKey('priority', key),
      collapsed: collapsed.has(key),
      wip: wipFor(settings, key),
      count: 0,
      placeholder: false,
    }));
  }

  // status / assignee / project / label:列来行动态实体(状态行/成员/项目/
  // 标签),定义层切片无实体数据 → 单个占位列,投影增量落地后替换为真实列。
  const configured = settings.columns ?? [];
  const keys = configured.length > 0 ? [...configured] : ['__dynamic__'];
  return keys.map((key) => ({
    key,
    label: columnLabelKey(groupBy, key),
    collapsed: collapsed.has(key),
    wip: wipFor(settings, key),
    count: 0,
    placeholder: true,
  }));
}

/** 视图是否可切换为看板渲染(layout board;list 复用 shell,timeline/table 预留)。 */
export function isRenderableLayout(layout: View['layout']): boolean {
  return layout === 'board' || layout === 'list';
}

export interface DerivedBoard {
  readonly columns: readonly BoardColumn[];
  readonly cardsByKey: Readonly<Record<string, readonly BoardCard[]>>;
}

/**
 * 把投影响应(分组 + 卡片)与视图列骨架合并为可渲染看板(投影层):
 * - group_by = state_category/priority(含默认)→ 固定列骨架 + 各组 count/wip/卡片;
 * - group_by = status/assignee/project → 列直接来自投影响应的动态分组。
 */
export function deriveColumns(view: View, groups: readonly BoardGroup[]): DerivedBoard {
  const cardsByKey: Record<string, readonly BoardCard[]> = {};
  for (const group of groups) {
    cardsByKey[group.key] = group.data;
  }
  const collapsed = new Set(view.board_settings.collapsed_columns ?? []);
  const groupBy = view.group_by;

  if (groupBy === null || groupBy === 'state_category' || groupBy === 'priority') {
    const columns = columnsForView(view).map((column) => {
      const group = groups.find((item) => item.key === column.key);
      return {
        ...column,
        count: group?.count ?? 0,
        wip: group?.wip ?? column.wip,
      };
    });
    return { columns, cardsByKey };
  }

  const columns: BoardColumn[] = groups.map((group) => ({
    key: group.key,
    label: group.label,
    collapsed: collapsed.has(group.key),
    wip: group.wip,
    count: group.count,
    placeholder: false,
  }));
  return { columns, cardsByKey };
}
