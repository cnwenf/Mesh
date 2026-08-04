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
import type { BoardCard, BoardGroup, BoardLane, BoardProjectionColumn } from './projection';
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
  'from_sub_group',
  'to_sub_group',
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
  /** 目标卡当前不在投影中：上层仅 GET 该卡后按当前视图重判，禁止整板刷新。 */
  readonly reconcileIssueId?: string;
}

function needsIssueReconcile(action: string): boolean {
  return action === 'updated' || action === 'moved' || action === 'project_changed';
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
    return {
      ...group,
      count: Math.max(0, group.count - 1),
      data: group.data.filter((item) => item.id !== id),
    };
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
    return [...groups, { key, label, count: 1, wip: null, data: [card] }];
  }
  return groups.map((group) => {
    if (group.key !== key) return group;
    return {
      ...group,
      count: group.count + 1,
      data: [...group.data, card],
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
  if (!needsIssueReconcile(action)) return { groups, refetch: false };

  // updated / moved / project_changed
  const { groups: stripped, removed } = removeFromGroups(groups, id);
  if (removed === null) return { groups, refetch: false, reconcileIssueId: id };
  if (isStale(removed, payload)) return { groups, refetch: false };
  const merged = mergedCard(removed, payload);
  if (!ctx.belongs(merged)) return { groups: stripped, refetch: false };
  const key = groupKeyForCard(merged, ctx.groupBy);
  return { groups: upsertIntoGroup(stripped, key, merged, labelFor(groups, key)), refetch: false };
}

export interface BoardLaneMergeContext extends BoardMergeContext {
  readonly subGroupBy: string;
}

export interface BoardLaneMergeResult {
  readonly columns: readonly BoardProjectionColumn[];
  readonly lanes: readonly BoardLane[];
  /** 仅 view.updated 改变投影规则时请求整板重拉。 */
  readonly refetch: boolean;
  /** 目标卡当前不在投影中：上层仅 GET 该卡后按当前视图重判。 */
  readonly reconcileIssueId?: string;
}

interface BoardCellPlacement {
  readonly card: BoardCard;
  readonly groupKey: string;
  readonly subGroupKey: string;
}

function unchangedLaneResult(
  columns: readonly BoardProjectionColumn[],
  lanes: readonly BoardLane[],
  refetch = false,
  reconcileIssueId?: string,
): BoardLaneMergeResult {
  return {
    columns,
    lanes,
    refetch,
    ...(reconcileIssueId === undefined ? {} : { reconcileIssueId }),
  };
}

function flattenLaneCards(lanes: readonly BoardLane[]): BoardCellPlacement[] {
  return lanes.flatMap((lane) =>
    lane.groups.flatMap((group) =>
      group.data.map((card) => ({ card, groupKey: group.key, subGroupKey: lane.key })),
    ),
  );
}

function addMissingKey(keys: string[], seen: Set<string>, key: string): void {
  if (seen.has(key)) return;
  seen.add(key);
  keys.push(key);
}

/**
 * 已加载二维投影使用整体游标，故所有 cell 均在内存中；一次单卡变化后可从
 * placements 精确重算 column/lane/cell 三层 count，同时保留服务端骨架顺序、标签与 WIP。
 */
function rebuildLaneProjection(
  columns: readonly BoardProjectionColumn[],
  lanes: readonly BoardLane[],
  placements: readonly BoardCellPlacement[],
  ctx: BoardLaneMergeContext,
): Pick<BoardLaneMergeResult, 'columns' | 'lanes'> {
  const columnTemplates = new Map(columns.map((column) => [column.key, column]));
  const laneTemplates = new Map(lanes.map((lane) => [lane.key, lane]));
  const columnKeys = columns.map((column) => column.key);
  const laneKeys = lanes.map((lane) => lane.key);
  const knownColumns = new Set(columnKeys);
  const knownLanes = new Set(laneKeys);

  for (const placement of placements) {
    addMissingKey(columnKeys, knownColumns, placement.groupKey);
    addMissingKey(laneKeys, knownLanes, placement.subGroupKey);
  }

  const columnCounts = new Map(columnKeys.map((key) => [key, 0]));
  const laneCounts = new Map(laneKeys.map((key) => [key, 0]));
  const cellCards = new Map<string, Map<string, BoardCard[]>>();
  const firstColumnCard = new Map<string, BoardCard>();
  const firstLaneCard = new Map<string, BoardCard>();

  for (const placement of placements) {
    columnCounts.set(placement.groupKey, columnCounts.get(placement.groupKey)! + 1);
    laneCounts.set(placement.subGroupKey, laneCounts.get(placement.subGroupKey)! + 1);
    if (!firstColumnCard.has(placement.groupKey)) {
      firstColumnCard.set(placement.groupKey, placement.card);
    }
    if (!firstLaneCard.has(placement.subGroupKey)) {
      firstLaneCard.set(placement.subGroupKey, placement.card);
    }
    let laneCells = cellCards.get(placement.subGroupKey);
    if (laneCells === undefined) {
      laneCells = new Map<string, BoardCard[]>();
      cellCards.set(placement.subGroupKey, laneCells);
    }
    const data = laneCells.get(placement.groupKey);
    if (data === undefined) {
      laneCells.set(placement.groupKey, [placement.card]);
    } else {
      data.push(placement.card);
    }
  }

  return {
    columns: columnKeys.map((key) => {
      const template = columnTemplates.get(key);
      const card = firstColumnCard.get(key);
      return {
        key,
        label: template?.label ?? fallbackLabel(key, [card!], ctx.groupBy),
        count: columnCounts.get(key)!,
        wip: template?.wip ?? null,
      };
    }),
    lanes: laneKeys.map((key) => {
      const template = laneTemplates.get(key);
      const card = firstLaneCard.get(key);
      const cells = cellCards.get(key);
      return {
        key,
        label: template?.label ?? fallbackLabel(key, [card!], ctx.subGroupBy),
        count: laneCounts.get(key)!,
        groups: columnKeys.map((columnKey) => {
          const data = cells?.get(columnKey) ?? [];
          return { key: columnKey, count: data.length, data };
        }),
      };
    }),
  };
}

function patchCardAxis(card: BoardCard, axis: string, targetKey: string): BoardCard {
  switch (axis) {
    case 'status':
      return { ...card, status_id: targetKey };
    case 'assignee':
      return {
        ...card,
        assignee_id: targetKey === NONE_KEY ? null : targetKey,
        ...(targetKey === NONE_KEY ? { assignee: null } : {}),
      };
    case 'priority':
      return { ...card, priority: targetKey };
    case 'project':
      return { ...card, project_id: targetKey === NONE_KEY ? null : targetKey };
    case 'state_category':
    default:
      return { ...card, state_category: targetKey };
  }
}

function movedAxisTarget(payload: FramePayload, axis: string): string | undefined {
  if (typeof payload.to !== 'object' || payload.to === null) return undefined;
  const target = payload.to as Record<string, unknown>;
  const value = target[axis];
  return typeof value === 'string' ? value : undefined;
}

function applyProjectChangedPayload(card: BoardCard, payload: FramePayload): BoardCard {
  let next = card;
  if (typeof payload.to_project_id === 'string' || payload.to_project_id === null) {
    next = { ...next, project_id: payload.to_project_id };
  }
  if (!Array.isArray(payload.mapped_fields)) return next;

  const statusMapping = payload.mapped_fields.find(
    (entry) =>
      typeof entry === 'object' &&
      entry !== null &&
      (entry as Record<string, unknown>).field === 'status',
  ) as Record<string, unknown> | undefined;
  if (
    statusMapping === undefined ||
    typeof statusMapping.to !== 'object' ||
    statusMapping.to === null
  ) {
    return next;
  }
  const status = statusMapping.to as Record<string, unknown>;
  const id = typeof status.id === 'string' ? status.id : undefined;
  const category = typeof status.category === 'string' ? status.category : undefined;
  if (id === undefined) return next;
  return {
    ...next,
    status_id: id,
    ...(category === undefined ? {} : { state_category: category }),
    status: {
      id,
      name: typeof status.name === 'string' ? status.name : (next.status?.name ?? id),
      category: category ?? next.status?.category ?? next.state_category,
    },
  };
}

/**
 * 把一帧并入二维泳道投影。issue.* 仅改动目标卡及其 cell；三层 count 由
 * 当前整体游标数据同步重算。view.updated 才请求投影重拉，presence/WIP 等视图帧不动。
 */
export function applyBoardLaneFrame(
  columns: readonly BoardProjectionColumn[],
  lanes: readonly BoardLane[],
  frame: RealtimeEventFrame,
  ctx: BoardLaneMergeContext,
): BoardLaneMergeResult {
  const entity = entityOf(frame.event);
  if (entity === 'view') {
    return unchangedLaneResult(columns, lanes, actionOf(frame.event) === 'updated');
  }
  if (entity !== 'issue') return unchangedLaneResult(columns, lanes);

  const payload = payloadOf(frame);
  const action = actionOf(frame.event);
  const placements = flattenLaneCards(lanes);

  if (action === 'created') {
    const nested = payload.issue;
    if (typeof nested !== 'object' || nested === null) return unchangedLaneResult(columns, lanes);
    const created = nested as BoardCard;
    if (
      typeof created.id !== 'string' ||
      placements.some((placement) => placement.card.id === created.id) ||
      !ctx.belongs(created)
    ) {
      return unchangedLaneResult(columns, lanes);
    }
    const rebuilt = rebuildLaneProjection(
      columns,
      lanes,
      [
        ...placements,
        {
          card: created,
          groupKey: groupKeyForCard(created, ctx.groupBy),
          subGroupKey: groupKeyForCard(created, ctx.subGroupBy),
        },
      ],
      ctx,
    );
    return { ...rebuilt, refetch: false };
  }

  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id === undefined) return unchangedLaneResult(columns, lanes);
  const placementIndex = placements.findIndex((placement) => placement.card.id === id);
  if (placementIndex === -1) {
    return unchangedLaneResult(columns, lanes, false, needsIssueReconcile(action) ? id : undefined);
  }

  if (action === 'deleted') {
    const rebuilt = rebuildLaneProjection(
      columns,
      lanes,
      placements.filter((placement) => placement.card.id !== id),
      ctx,
    );
    return { ...rebuilt, refetch: false };
  }
  if (action !== 'updated' && action !== 'moved' && action !== 'project_changed') {
    return unchangedLaneResult(columns, lanes);
  }

  const existing = placements[placementIndex]!;
  if (isStale(existing.card, payload)) {
    return unchangedLaneResult(columns, lanes);
  }
  let merged = mergedCard(existing.card, payload);
  if (action === 'project_changed') merged = applyProjectChangedPayload(merged, payload);

  let groupKey = groupKeyForCard(merged, ctx.groupBy);
  let subGroupKey = groupKeyForCard(merged, ctx.subGroupBy);
  if (action === 'moved') {
    const target =
      typeof payload.to === 'object' && payload.to !== null
        ? (payload.to as Record<string, unknown>)
        : {};
    groupKey =
      (typeof target.group_key === 'string' ? target.group_key : undefined) ??
      movedAxisTarget(payload, ctx.groupBy) ??
      groupKey;
    subGroupKey =
      (typeof payload.to_sub_group === 'string' ? payload.to_sub_group : undefined) ??
      movedAxisTarget(payload, ctx.subGroupBy) ??
      subGroupKey;
    merged = patchCardAxis(merged, ctx.groupBy, groupKey);
    merged = patchCardAxis(merged, ctx.subGroupBy, subGroupKey);
    if (typeof payload.position === 'number') merged = { ...merged, position: payload.position };
  }

  const remaining = placements.filter((placement) => placement.card.id !== id);
  if (!ctx.belongs(merged)) {
    const rebuilt = rebuildLaneProjection(columns, lanes, remaining, ctx);
    return { ...rebuilt, refetch: false };
  }
  const nextPlacements = [...placements];
  nextPlacements[placementIndex] = { card: merged, groupKey, subGroupKey };
  const rebuilt = rebuildLaneProjection(columns, lanes, nextPlacements, ctx);
  return { ...rebuilt, refetch: false };
}

/**
 * 视图 filters 的本地轻量重判(§3.5 增量合并归属)。
 * 仅评估顶层 AND/OR 的内置字段 eq/neq/in/not_in;含嵌套或无法判定的条件 →
 * 保守返回 true(保留卡片;复杂 filters 由上层按 id 轻量 refetch 对账)。
 */
export function cardBelongsToView(card: BoardCard, filters: unknown): boolean {
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
export function rebucketGroups(groups: readonly BoardGroup[], groupBy: string): BoardGroup[] {
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
