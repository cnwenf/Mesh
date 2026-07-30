/**
 * 看板「列表」布局的纯逻辑辅助(无 React 依赖,便于独立单测)。
 *
 * 职责:
 * - 列 id 集合与可见列解析(display_fields 选取/重排,未知列过滤);
 * - 表头排序的纯比较器(优先级 rank、时间、文本、状态名、负责人名)与方向轮换;
 * - 单元格内联编辑 → PATCH 请求体构造(title/priority/status_id)。
 *
 * 约束:本文件禁止 import react(纯函数层,保证可被虚拟化等场景复用)。
 */
import type { TranslateFn } from '../../i18n';
import { PRIORITY_ORDER, STATE_CATEGORY_ORDER } from '../issues/types';
import type { IssuePriority, UpdateIssueBody } from '../issues/types';
import type { BoardCard, BoardGroup } from './projection';
import type { View } from './types';

/** 状态分类 → 徽标语义 tone(与 Badge 三元组语义对齐,非数据色)。 */
export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent';

/** 列表布局支持的列 id(规范列序)。 */
export type ColumnId =
  | 'identifier'
  | 'title'
  | 'status'
  | 'priority'
  | 'assignee'
  | 'updated';

/** 全部列 id,按规范列序排列(默认可见集合)。 */
export const ALL_COLUMN_IDS: readonly ColumnId[] = [
  'identifier',
  'title',
  'status',
  'priority',
  'assignee',
  'updated',
];

/** 可排序列(列表当前六列均可排序)。 */
export type SortField = ColumnId;

/** 排序方向:none = 未排序(回到投影默认序)。 */
export type SortDir = 'asc' | 'desc' | 'none';

/** 可内联编辑的单元格字段。 */
export type CellField = 'title' | 'priority' | 'status';

/** 优先级排序 rank:urgent 最高(0),none 最低(4);未知值按 none 处理。 */
export const PRIORITY_RANK: Readonly<Record<string, number>> = Object.freeze({
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4,
});

/** 未知优先级的兜底 rank(与 none 一致,排在最后)。 */
const FALLBACK_PRIORITY_RANK = PRIORITY_RANK.none;

/**
 * 解析可见列。
 * 优先使用 board_settings.display_fields(非空时),否则回退 view.display_fields;
 * 过滤未知列、去重并保留「请求顺序」(display_fields 可重排列);结果为空 → 默认全部六列。
 */
export function resolveVisibleColumns(
  viewDisplayFields: readonly string[],
  settingsDisplayFields: readonly string[] | undefined,
): readonly ColumnId[] {
  const requested =
    settingsDisplayFields !== undefined && settingsDisplayFields.length > 0
      ? settingsDisplayFields
      : viewDisplayFields;
  const known = new Set<string>(ALL_COLUMN_IDS);
  const seen = new Set<ColumnId>();
  const ordered: ColumnId[] = [];
  for (const field of requested) {
    if (!known.has(field)) continue;
    const columnId = field as ColumnId;
    if (seen.has(columnId)) continue;
    seen.add(columnId);
    ordered.push(columnId);
  }
  return ordered.length > 0 ? ordered : ALL_COLUMN_IDS;
}

/** 取卡片的比较键值(负责人/状态空名归一为 '',由调用方决定空值排序)。 */
function fieldValue(field: SortField, card: BoardCard): string {
  switch (field) {
    case 'title':
      return card.title;
    case 'identifier':
      return card.identifier;
    case 'status':
      return card.status?.name ?? '';
    case 'assignee':
      return card.assignee?.name ?? '';
    default:
      return '';
  }
}

/**
 * 升序比较两张卡(纯函数)。
 * - priority:按 PRIORITY_RANK;updated:按 Date.parse;
 * - title/identifier/status:localeCompare;assignee:localeCompare 且空名排最后。
 */
export function compareByField(field: SortField, a: BoardCard, b: BoardCard): number {
  if (field === 'priority') {
    const rankA = PRIORITY_RANK[a.priority] ?? FALLBACK_PRIORITY_RANK;
    const rankB = PRIORITY_RANK[b.priority] ?? FALLBACK_PRIORITY_RANK;
    return rankA - rankB;
  }
  if (field === 'updated') {
    return Date.parse(a.updated_at) - Date.parse(b.updated_at);
  }
  if (field === 'assignee') {
    const nameA = fieldValue('assignee', a);
    const nameB = fieldValue('assignee', b);
    if (nameA === '' && nameB === '') return 0;
    if (nameA === '') return 1; // 空负责人排最后
    if (nameB === '') return -1;
    return nameA.localeCompare(nameB);
  }
  return fieldValue(field, a).localeCompare(fieldValue(field, b));
}

/** 生成带方向的比较器;dir='none' 时恒等(保持投影默认序)。 */
export function compareCards(
  field: SortField,
  dir: SortDir,
): (a: BoardCard, b: BoardCard) => number {
  return (a, b) => {
    if (dir === 'none') return 0;
    const result = compareByField(field, a, b);
    return dir === 'asc' ? result : -result;
  };
}

/** 排序方向轮换:asc → desc → none → asc。 */
export function nextSortDir(current: SortDir): SortDir {
  if (current === 'asc') return 'desc';
  if (current === 'desc') return 'none';
  return 'asc';
}

/**
 * 单元格编辑 → PATCH 请求体(不含 version,调用方叠加乐观锁版本)。
 * title → {title};priority → {priority};status(传 status_id)→ {status_id}。
 */
export function buildCellPatch(field: CellField, value: string): UpdateIssueBody {
  if (field === 'title') return { title: value };
  if (field === 'priority') return { priority: value as IssuePriority };
  return { status_id: value };
}

/** 已知状态分类集合(用于把未知分类归一到稳定兜底键)。 */
const CATEGORY_SET = new Set<string>(STATE_CATEGORY_ORDER);

/** 已知优先级集合。 */
const PRIORITY_SET = new Set<string>(PRIORITY_ORDER);

/** 状态分类 → 徽标语义 tone;未知分类回退 neutral。 */
const CATEGORY_TONE: Readonly<Record<string, StatusTone>> = Object.freeze({
  backlog: 'neutral',
  todo: 'info',
  in_progress: 'warning',
  in_review: 'accent',
  blocked: 'danger',
  done: 'success',
  cancelled: 'neutral',
});

/** 归一状态分类键(未知 → 'todo',保证 i18n 键存在)。 */
export function categoryKey(category: string): string {
  return CATEGORY_SET.has(category) ? category : 'todo';
}

/** 状态分类 → 徽标 tone。 */
export function statusTone(category: string): StatusTone {
  return CATEGORY_TONE[category] ?? 'neutral';
}

/** 优先级展示文案(未知优先级按 none)。 */
export function priorityLabelText(t: TranslateFn, priority: string): string {
  return t(`board.priority.${PRIORITY_SET.has(priority) ? priority : 'none'}`);
}

/**
 * 分组标签翻译。
 * - group.label 本身是 board.(category|priority).* i18n 键 → 直接翻译;
 * - group_by=state_category(或 null)→ 按分类键翻译;
 * - group_by=priority → 按优先级键翻译;
 * - 其余(assignee/project/label/status 等动态实体)→ 原样使用 label。
 */
export function groupLabelText(view: View, group: BoardGroup, t: TranslateFn): string {
  if (/^board\.(category|priority)\./.test(group.label)) return t(group.label);
  const groupBy = view.group_by;
  if (groupBy === 'state_category' || groupBy === null) {
    return t(`board.category.${categoryKey(group.key)}`);
  }
  if (groupBy === 'priority') return priorityLabelText(t, group.key);
  return group.label;
}
