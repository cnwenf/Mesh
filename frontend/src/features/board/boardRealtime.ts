/**
 * 看板实时增量合并(kanban.md §3.5,README §6.7)。
 *
 * 纯函数:绝不修改入参,有变化返回新结构,无变化返回原引用。收到 issue.* 帧后
 * 按当前视图 group_by 在本地重判该卡归属,做单卡 插入/移动/移除(禁整板刷新);
 * 仅 view.updated(投影规则变更)或 resync_required 才整板重拉(§3.5/§6.12)。
 * 防回退以 updated_at 字符串比较为闸(RFC3339 UTC 可直接比较,§6.7 水位)。
 * 复杂嵌套 filters 下允许按 id 轻量 refetch —— cardBelongsToView 无法判定时
 * 保守保留卡片(不静默移除),由上层在需要时重拉对账。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { BoardCard, BoardGroup } from './projection';
import type { WipLimit } from './types';

/** 空分组 key(assignee/project 无值列,对齐后端 §2.4)。 */
export const NONE_KEY = '__none__';

interface FramePayload {
  readonly id?: unknown;
  readonly updated_at?: unknown;
  readonly changes?: unknown;
  readonly issue?: unknown;
  readonly [key: string]: unknown;
}

function payloadOf(frame: RealtimeEventFrame): FramePayload {
  return frame.payload as FramePayload;
}

function actionOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? event : event.slice(dot + 1);
}

function entityOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? '' : event.slice(0, dot);
}

/** 卡片在 group_by 下的列 key(对齐后端 projection.group_key_for,§2.4)。 */
export function groupKeyForCard(card: BoardCard, groupBy: string): string {
  switch (groupBy) {
    case 'status':
      return card.status_id;
    case 'assignee':
      return card.assignee_id ?? NONE_KEY;
    case 'priority':
      return card.priority;
    case 'project':
      return card.project_id ?? NONE_KEY;
    case 'state_category':
    default:
      return card.state_category;
  }
}

/** 帧元字段:不得扩散到卡片对象(F11)。 */
const FRAME_META_KEYS = new Set([
  'changes',
  'issue',
  'from',
  'to',
  'position',
  'view_id',
  'from_project_id',
  'to_project_id',
  'mapped_fields',
  'cleared_fields',
  'visibility',
]);

function mergedCard(existing: BoardCard, payload: FramePayload): BoardCard {
  const { changes, ...top } = payload;
  const topFields: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(top)) {
    if (!FRAME_META_KEYS.has(key)) topFields[key] = value;
  }
  const changeFields =
    typeof changes === 'object' && changes !== null ? (changes as Record<string, unknown>) : {};
  return { ...existing, ...topFields, ...changeFields } as BoardCard;
}

function isStale(existing: BoardCard, payload: FramePayload): boolean {
  const frameUpdatedAt = typeof payload.updated_at === 'string' ? payload.updated_at : undefined;
  if (frameUpdatedAt === undefined) return false;
  return frameUpdatedAt < existing.updated_at;
}

export interface BoardMergeContext {
  readonly groupBy: string;
  /** 卡片是否仍属于当前视图(视图 filters 的本地重判;无法判定 → true 保守保留)。 */
  readonly belongs: (card: BoardCard) => boolean;
}

export interface BoardMergeResult {
  readonly groups: readonly BoardGroup[];
  /** view.updated 等投影规则变更 → 上层整板重拉。 */
  readonly refetch: boolean;
}

function removeFromGroups(
  groups: readonly BoardGroup[],
  id: string,
): { groups: BoardGroup[]; removed: BoardCard | null } {
  let removed: BoardCard | null = null;
  const next = groups.map((group) => {
    const card = group.data.find((item) => item.id === id);
    if (card === undefined) return group;
    removed = card;
    return { ...group, count: Math.max(0, group.count - 1), data: group.data.filter((item) => item.id !== id) };
  });
  return { groups: next, removed };
}

function upsertIntoGroup(
  groups: readonly BoardGroup[],
  key: string,
  card: BoardCard,
  label: string,
): BoardGroup[] {
  const exists = groups.some((group) => group.key === key);
  if (!exists) {
    return [
      ...groups,
      { key, label, count: 1, wip: null, data: [card] },
    ];
  }
  return groups.map((group) => {
    if (group.key !== key) return group;
    const present = group.data.some((item) => item.id === card.id);
    return {
      ...group,
      count: present ? group.count : group.count + 1,
      data: present
        ? group.data.map((item) => (item.id === card.id ? card : item))
        : [...group.data, card],
    };
  });
}

function labelFor(groups: readonly BoardGroup[], key: string): string {
  return groups.find((group) => group.key === key)?.label ?? key;
}

/**
 * 把一帧并入看板分组(单卡增量,§3.5)。
 * - issue.created:belongs → 插入对应列;
 * - issue.updated/moved/project_changed:合并字段,按 group_by 重分桶;不再 belongs → 移除;
 * - issue.deleted:移除;
 * - view.updated:投影规则可能变 → refetch=true(整板重拉);
 * - view.presence / 其它:不变。
 */
export function applyBoardFrame(
  groups: readonly BoardGroup[],
  frame: RealtimeEventFrame,
  ctx: BoardMergeContext,
): BoardMergeResult {
  const entity = entityOf(frame.event);
  if (entity === 'view') {
    // view.updated 改到投影规则(filters/group/sort)→ 整板重拉;presence 不动分组。
    return { groups, refetch: actionOf(frame.event) === 'updated' };
  }
  if (entity !== 'issue') return { groups, refetch: false };

  const payload = payloadOf(frame);
  const action = actionOf(frame.event);

  if (action === 'created') {
    const nested = payload.issue;
    if (typeof nested !== 'object' || nested === null) return { groups, refetch: false };
    const card = nested as BoardCard;
    if (typeof card.id !== 'string') return { groups, refetch: false };
    const flat = groups.flatMap((group) => group.data);
    if (flat.some((item) => item.id === card.id)) return { groups, refetch: false };
    if (!ctx.belongs(card)) return { groups, refetch: false };
    const key = groupKeyForCard(card, ctx.groupBy);
    return { groups: upsertIntoGroup(groups, key, card, labelFor(groups, key)), refetch: false };
  }

  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id === undefined) return { groups, refetch: false };

  if (action === 'deleted') {
    return { groups: removeFromGroups(groups, id).groups, refetch: false };
  }

  // updated / moved / project_changed
  const { groups: stripped, removed } = removeFromGroups(groups, id);
  if (removed === null) return { groups, refetch: false };
  if (isStale(removed, payload)) return { groups, refetch: false };
  const merged = mergedCard(removed, payload);
  if (!ctx.belongs(merged)) return { groups: stripped, refetch: false };
  const key = groupKeyForCard(merged, ctx.groupBy);
  return { groups: upsertIntoGroup(stripped, key, merged, labelFor(groups, key)), refetch: false };
}

/**
 * 视图 filters 的本地轻量重判(§3.5 增量合并归属)。
 * 仅评估顶层 AND/OR 的内置字段 eq/neq/in/not_in;含嵌套或无法判定的条件 →
 * 保守返回 true(保留卡片;复杂 filters 由上层按 id 轻量 refetch 对账)。
 */
export function cardBelongsToView(
  card: BoardCard,
  filters: unknown,
): boolean {
  if (typeof filters !== 'object' || filters === null) return true;
  const group = filters as { operator?: unknown; conditions?: unknown };
  if (!Array.isArray(group.conditions) || group.conditions.length === 0) return true;
  const results = group.conditions.map((condition) => evaluateCondition(card, condition));
  if (results.some((result) => result === null)) return true; // 无法判定 → 保守保留
  const values = results as boolean[];
  return group.operator === 'OR' ? values.some(Boolean) : values.every(Boolean);
}

function fieldValue(card: BoardCard, field: string): unknown {
  switch (field) {
    case 'state_category':
      return card.state_category;
    case 'status_id':
      return card.status_id;
    case 'priority':
      return card.priority;
    case 'assignee_id':
      return card.assignee_id;
    case 'project_id':
      return card.project_id;
    default:
      return undefined; // 不支持本地评估的字段
  }
}

/** 返回 true/false,无法判定 → null。 */
function evaluateCondition(card: BoardCard, condition: unknown): boolean | null {
  if (typeof condition !== 'object' || condition === null) return null;
  const cond = condition as {
    operator?: unknown;
    field?: unknown;
    field_kind?: unknown;
    op?: unknown;
    value?: unknown;
  };
  if (cond.operator !== undefined) return null; // 嵌套 → 保守(上层处理)
  if (cond.field_kind !== undefined) return null; // 自定义字段 → 保守
  const field = typeof cond.field === 'string' ? cond.field : null;
  if (field === null || field === 'label' || field === 'q') return null;
  const actual = fieldValue(card, field);
  if (actual === undefined) return null;
  const op = cond.op;
  if (op === 'eq') return actual === cond.value;
  if (op === 'neq') return actual !== cond.value;
  if (op === 'in') return Array.isArray(cond.value) && cond.value.includes(actual);
  if (op === 'not_in') return Array.isArray(cond.value) && !cond.value.includes(actual);
  return null; // 其它 op(range/null)不本地评估 → 保守
}

function fallbackLabel(key: string, data: readonly BoardCard[], groupBy: string): string {
  if (key === NONE_KEY) return groupBy === 'assignee' ? 'No assignee' : 'No project';
  if (groupBy === 'status') return data[0]?.status?.name ?? key;
  if (groupBy === 'assignee') return data[0]?.assignee?.name ?? key;
  return key;
}

/**
 * 按(草稿)group_by 对已加载卡片本地重分桶(§4.2 分组切换即时反映)。
 * 标签优先取投影响应携带的(已本地化)组标签,缺失时按卡片内嵌字段回退;
 * count 为本地组内卡片数(整板已分页加载完毕,故等于组内总数)。
 */
export function rebucketGroups(
  groups: readonly BoardGroup[],
  groupBy: string,
): BoardGroup[] {
  const labelMap = new Map<string, string>();
  const wipMap = new Map<string, WipLimit | null>();
  const cards: BoardCard[] = [];
  for (const group of groups) {
    labelMap.set(group.key, group.label);
    wipMap.set(group.key, group.wip);
    cards.push(...group.data);
  }
  const byKey = new Map<string, BoardCard[]>();
  for (const card of cards) {
    const key = groupKeyForCard(card, groupBy);
    const bucket = byKey.get(key);
    if (bucket === undefined) {
      byKey.set(key, [card]);
    } else {
      bucket.push(card);
    }
  }
  const result: BoardGroup[] = [];
  for (const [key, data] of byKey) {
    // 组标签优先取服务端(已本地化)标签;仅当服务端缺失时才回退到卡片内嵌字段
    // (fallbackLabel,英文兜底,正常路径不会触发)。
    result.push({
      key,
      label: labelMap.get(key) ?? fallbackLabel(key, data, groupBy),
      count: data.length,
      wip: wipMap.get(key) ?? null,
      data,
    });
  }
  return result;
}
