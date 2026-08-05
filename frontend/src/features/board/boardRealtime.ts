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
import type { CustomFieldDef } from '../labels/types';
import type {
  BoardCard,
  BoardCustomFieldValue,
  BoardGroup,
  BoardLane,
  BoardProjectionColumn,
} from './projection';

/** 空分组 key(assignee/project 无值列,对齐后端 §2.4)。 */
export const NONE_KEY = '__none__';

interface FramePayload {
  readonly id?: unknown;
  readonly issue_id?: unknown;
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

const LOCAL_AXIS_KEYS = new Set(['state_category', 'status', 'assignee', 'priority', 'project']);

function isDynamicAxis(axis: string): boolean {
  return axis === 'label' || !LOCAL_AXIS_KEYS.has(axis);
}

function customValueKeys(
  definition: CustomFieldDef | undefined,
  value: BoardCustomFieldValue | undefined,
): string[] {
  if (value === undefined) return [NONE_KEY];
  if (definition === undefined) {
    if (Array.isArray(value.value_json)) {
      const keys = value.value_json.filter((item): item is string => typeof item === 'string');
      return [...new Set(keys)].length > 0 ? [...new Set(keys)] : [NONE_KEY];
    }
    if (typeof value.value_json === 'string') return [value.value_json];
    if (typeof value.value_text === 'string') return [value.value_text];
    if (typeof value.value_number === 'number') return [String(value.value_number)];
    if (typeof value.value_date === 'string') return [value.value_date];
    if (typeof value.value_member_id === 'string') return [value.value_member_id];
    if (typeof value.value_boolean === 'boolean') return [String(value.value_boolean)];
    return [NONE_KEY];
  }
  switch (definition.type) {
    case 'text':
    case 'textarea':
    case 'url':
      return typeof value.value_text === 'string' ? [value.value_text] : [NONE_KEY];
    case 'number':
      return typeof value.value_number === 'number' ? [String(value.value_number)] : [NONE_KEY];
    case 'date':
    case 'datetime':
      return typeof value.value_date === 'string' ? [value.value_date] : [NONE_KEY];
    case 'member':
      return typeof value.value_member_id === 'string' ? [value.value_member_id] : [NONE_KEY];
    case 'boolean':
      return typeof value.value_boolean === 'boolean' ? [String(value.value_boolean)] : [NONE_KEY];
    case 'single_select':
      return typeof value.value_json === 'string' ? [value.value_json] : [NONE_KEY];
    case 'multi_select': {
      const keys = Array.isArray(value.value_json)
        ? value.value_json.filter((item): item is string => typeof item === 'string')
        : [];
      return [...new Set(keys)].length > 0 ? [...new Set(keys)] : [NONE_KEY];
    }
  }
}

/** 一张卡在动态轴上的完整 memberships；多值轴返回多个去重 key。 */
export function groupKeysForCard(
  card: BoardCard,
  groupBy: string,
  customFields: readonly CustomFieldDef[] = [],
): readonly string[] {
  if (groupBy === 'label') {
    const keys = (card.labels ?? []).map((label) => label.id);
    return [...new Set(keys)].length > 0 ? [...new Set(keys)] : [NONE_KEY];
  }
  if (!isDynamicAxis(groupBy)) return [groupKeyForCard(card, groupBy)];
  const definition = customFields.find((field) => field.id === groupBy);
  const value = card.custom_field_values?.find((item) => item.field_def_id === groupBy);
  return customValueKeys(definition, value);
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
  'issue_id',
  'field_def_id',
  'field_key',
  'value',
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

function withCustomFieldPayload(card: BoardCard, payload: FramePayload): BoardCard {
  if (typeof payload.field_def_id !== 'string') return card;
  const retained = (card.custom_field_values ?? []).filter(
    (value) => value.field_def_id !== payload.field_def_id,
  );
  if (typeof payload.value !== 'object' || payload.value === null) {
    return { ...card, custom_field_values: retained };
  }
  const value = payload.value as BoardCustomFieldValue;
  return {
    ...card,
    custom_field_values: [...retained, { ...value, field_def_id: payload.field_def_id }],
  };
}

/** 将关联帧快照并入单卡；用于投影内更新及缺失卡的单卡 GET 对账。 */
export function mergeBoardCardForRealtime(card: BoardCard, frame: RealtimeEventFrame): BoardCard {
  const payload = payloadOf(frame);
  const merged = mergedCard(card, payload);
  return actionOf(frame.event) === 'custom_field_changed'
    ? withCustomFieldPayload(merged, payload)
    : merged;
}

function isStale(existing: BoardCard, payload: FramePayload): boolean {
  const frameUpdatedAt = typeof payload.updated_at === 'string' ? payload.updated_at : undefined;
  if (frameUpdatedAt === undefined) return false;
  return frameUpdatedAt < existing.updated_at;
}

export interface BoardMergeContext {
  readonly groupBy: string;
  readonly customFields?: readonly CustomFieldDef[];
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
  return (
    action === 'updated' ||
    action === 'moved' ||
    action === 'project_changed' ||
    action === 'labels_changed' ||
    action === 'custom_field_changed'
  );
}

function issueIdFromPayload(payload: FramePayload, action: string): string | undefined {
  const value =
    action === 'labels_changed' || action === 'custom_field_changed'
      ? payload.issue_id
      : payload.id;
  return typeof value === 'string' ? value : undefined;
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
    return insertBeforeNone(groups, { key, label, count: 1, wip: null, data: [card] });
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

function labelForCardAxis(
  groups: readonly { readonly key: string; readonly label: string }[],
  card: BoardCard,
  axis: string,
  key: string,
  customFields: readonly CustomFieldDef[],
): string {
  const existing = groups.find((group) => group.key === key)?.label;
  if (existing !== undefined) return existing;
  if (axis === 'label') {
    if (key === NONE_KEY) return 'No label';
    return card.labels?.find((label) => label.id === key)?.name ?? key;
  }
  const definition = customFields.find((field) => field.id === axis);
  if (key === NONE_KEY) return definition === undefined ? key : `No ${definition.name}`;
  return definition?.options.find((option) => option.id === key)?.name ?? key;
}

function groupKeysContaining(groups: readonly BoardGroup[], issueId: string): string[] {
  return groups
    .filter((group) => group.data.some((card) => card.id === issueId))
    .map((group) => group.key);
}

function upsertIntoGroups(
  groups: readonly BoardGroup[],
  keys: readonly string[],
  card: BoardCard,
  axis: string,
  customFields: readonly CustomFieldDef[],
): BoardGroup[] {
  let next = [...groups];
  for (const key of new Set(keys)) {
    next = upsertIntoGroup(
      next,
      key,
      card,
      labelForCardAxis(groups, card, axis, key, customFields),
    );
  }
  return next;
}

interface LabelDefinitionPayload {
  readonly id: string;
  readonly name?: string;
  readonly color?: string;
}

interface AxisSkeletonMutation {
  readonly key?: string;
  readonly label?: string;
  readonly remove?: boolean;
  readonly noneLabel?: string;
  readonly removeAxis?: boolean;
}

function customFieldSkeletonMutation(
  frame: RealtimeEventFrame,
  axis: string,
): AxisSkeletonMutation | null {
  const payload = payloadOf(frame);
  if (frame.event === 'custom_field.updated') {
    if (payload.id !== axis) return null;
    if (payload.change === 'deleted' || payload.is_active === false) return { removeAxis: true };
    return typeof payload.name === 'string' ? { noneLabel: `No ${payload.name}` } : null;
  }
  if (frame.event !== 'custom_field_option.updated' || payload.field_def_id !== axis) return null;
  const change = typeof payload.change === 'string' ? payload.change : 'updated';
  if (typeof payload.option === 'object' && payload.option !== null) {
    const option = payload.option as Record<string, unknown>;
    if (typeof option.id !== 'string') return null;
    return {
      key: option.id,
      ...(typeof option.name === 'string' ? { label: option.name } : {}),
      remove: change === 'deleted' || option.is_active === false,
    };
  }
  return typeof payload.id === 'string' && change === 'deleted'
    ? { key: payload.id, remove: true }
    : null;
}

function labelDefinitionPayload(frame: RealtimeEventFrame): LabelDefinitionPayload | null {
  if (!frame.event.startsWith('label.')) return null;
  const payload = payloadOf(frame);
  return typeof payload.id === 'string'
    ? {
        id: payload.id,
        ...(typeof payload.name === 'string' ? { name: payload.name } : {}),
        ...(typeof payload.color === 'string' ? { color: payload.color } : {}),
      }
    : null;
}

function patchCardLabel(
  card: BoardCard,
  label: LabelDefinitionPayload,
  deleted: boolean,
): BoardCard {
  if (card.labels === undefined) return card;
  const labels = deleted
    ? card.labels.filter((item) => item.id !== label.id)
    : card.labels.map((item) =>
        item.id === label.id
          ? {
              ...item,
              ...(label.name === undefined ? {} : { name: label.name }),
              ...(label.color === undefined ? {} : { color: label.color }),
            }
          : item,
      );
  return { ...card, labels };
}

function insertBeforeNone<T extends { readonly key: string }>(items: readonly T[], item: T): T[] {
  const noneIndex = items.findIndex((candidate) => candidate.key === NONE_KEY);
  if (noneIndex === -1) return [...items, item];
  return [...items.slice(0, noneIndex), item, ...items.slice(noneIndex)];
}

/** label 定义帧只改一维列骨架/卡片快照，不触发投影重拉。 */
export function applyLabelDefinitionToGroups(
  groups: readonly BoardGroup[],
  frame: RealtimeEventFrame,
): readonly BoardGroup[] {
  const label = labelDefinitionPayload(frame);
  if (label === null) return groups;
  const action = actionOf(frame.event);
  if (action === 'created') {
    if (groups.some((group) => group.key === label.id) || label.name === undefined) return groups;
    return insertBeforeNone(groups, {
      key: label.id,
      label: label.name,
      count: 0,
      wip: null,
      data: [],
    });
  }
  if (action === 'updated') {
    if (label.name === undefined) return groups;
    return groups.map((group) => ({
      ...group,
      ...(group.key === label.id ? { label: label.name } : {}),
      data: group.data.map((card) => patchCardLabel(card, label, false)),
    }));
  }
  if (action !== 'deleted') return groups;

  const removedCards = groups.find((group) => group.key === label.id)?.data ?? [];
  let next: BoardGroup[] = groups
    .filter((group) => group.key !== label.id)
    .map((group) => ({
      ...group,
      data: group.data.map((card) => patchCardLabel(card, label, true)),
    }));
  for (const removed of removedCards) {
    if (next.some((group) => group.data.some((card) => card.id === removed.id))) continue;
    const patched = patchCardLabel(removed, label, true);
    next = upsertIntoGroup(next, NONE_KEY, patched, 'No label');
  }
  return next;
}

/** custom field/option 定义帧局部维护一维 skeleton。 */
export function applyCustomFieldDefinitionToGroups(
  groups: readonly BoardGroup[],
  frame: RealtimeEventFrame,
  axis: string,
): readonly BoardGroup[] {
  const mutation = customFieldSkeletonMutation(frame, axis);
  if (mutation === null) return groups;
  if (mutation.removeAxis === true) return [];
  let next = groups.map((group) =>
    group.key === NONE_KEY && mutation.noneLabel !== undefined
      ? { ...group, label: mutation.noneLabel }
      : group,
  );
  if (mutation.key === undefined) return next;
  if (mutation.remove === true) {
    const removedCards = next.find((group) => group.key === mutation.key)?.data ?? [];
    next = next.filter((group) => group.key !== mutation.key);
    for (const card of removedCards) {
      if (next.some((group) => group.data.some((item) => item.id === card.id))) continue;
      next = upsertIntoGroup(next, NONE_KEY, card, mutation.noneLabel ?? 'None');
    }
    return next;
  }
  if (mutation.label === undefined) return next;
  if (next.some((group) => group.key === mutation.key)) {
    return next.map((group) =>
      group.key === mutation.key ? { ...group, label: mutation.label! } : group,
    );
  }
  return insertBeforeNone(next, {
    key: mutation.key,
    label: mutation.label,
    count: 0,
    wip: null,
    data: [],
  });
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
  const customFields = ctx.customFields ?? [];

  if (action === 'created') {
    const nested = payload.issue;
    if (typeof nested !== 'object' || nested === null) return { groups, refetch: false };
    const card = nested as BoardCard;
    if (typeof card.id !== 'string') return { groups, refetch: false };
    const flat = groups.flatMap((group) => group.data);
    if (flat.some((item) => item.id === card.id)) return { groups, refetch: false };
    if (!ctx.belongs(card)) return { groups, refetch: false };
    const keys = groupKeysForCard(card, ctx.groupBy, customFields);
    return {
      groups: upsertIntoGroups(groups, keys, card, ctx.groupBy, customFields),
      refetch: false,
    };
  }

  const id = issueIdFromPayload(payload, action);
  if (id === undefined) return { groups, refetch: false };

  if (action === 'deleted') {
    return { groups: removeFromGroups(groups, id).groups, refetch: false };
  }
  if (!needsIssueReconcile(action)) return { groups, refetch: false };

  // updated / moved / project_changed / labels_changed
  const priorKeys = groupKeysContaining(groups, id);
  const { groups: stripped, removed } = removeFromGroups(groups, id);
  if (removed === null) return { groups, refetch: false, reconcileIssueId: id };
  if (isStale(removed, payload)) return { groups, refetch: false };
  const merged = mergeBoardCardForRealtime(removed, frame);
  if (!ctx.belongs(merged)) return { groups: stripped, refetch: false };
  const associationChangesAxis =
    (action === 'labels_changed' && ctx.groupBy === 'label') ||
    (action === 'custom_field_changed' && payload.field_def_id === ctx.groupBy);
  let keys =
    isDynamicAxis(ctx.groupBy) && !associationChangesAxis
      ? priorKeys
      : groupKeysForCard(merged, ctx.groupBy, customFields);
  if (action === 'moved' && typeof payload.to === 'object' && payload.to !== null) {
    const targetKey = (payload.to as Record<string, unknown>).group_key;
    if (typeof targetKey === 'string') keys = [targetKey];
  }
  return {
    groups: upsertIntoGroups(stripped, keys, merged, ctx.groupBy, customFields),
    refetch: false,
  };
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
  const noneIndex = keys.indexOf(NONE_KEY);
  if (key !== NONE_KEY && noneIndex !== -1) {
    keys.splice(noneIndex, 0, key);
  } else {
    keys.push(key);
  }
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

  const columnIssueIds = new Map(columnKeys.map((key) => [key, new Set<string>()]));
  const laneIssueIds = new Map(laneKeys.map((key) => [key, new Set<string>()]));
  const cellCards = new Map<string, Map<string, BoardCard[]>>();
  const firstColumnCard = new Map<string, BoardCard>();
  const firstLaneCard = new Map<string, BoardCard>();
  const customFields = ctx.customFields ?? [];

  for (const placement of placements) {
    columnIssueIds.get(placement.groupKey)!.add(placement.card.id);
    laneIssueIds.get(placement.subGroupKey)!.add(placement.card.id);
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
        label:
          template?.label ??
          (isDynamicAxis(ctx.groupBy)
            ? labelForCardAxis(columns, card!, ctx.groupBy, key, customFields)
            : fallbackLabel(key, [card!], ctx.groupBy)),
        count: columnIssueIds.get(key)!.size,
        wip: template?.wip ?? null,
      };
    }),
    lanes: laneKeys.map((key) => {
      const template = laneTemplates.get(key);
      const card = firstLaneCard.get(key);
      const cells = cellCards.get(key);
      return {
        key,
        label:
          template?.label ??
          (isDynamicAxis(ctx.subGroupBy)
            ? labelForCardAxis(lanes, card!, ctx.subGroupBy, key, customFields)
            : fallbackLabel(key, [card!], ctx.subGroupBy)),
        count: laneIssueIds.get(key)!.size,
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
      return { ...card, state_category: targetKey };
    default:
      return card;
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

function uniquePlacementKeys(
  placements: readonly BoardCellPlacement[],
  field: 'groupKey' | 'subGroupKey',
): string[] {
  return [...new Set(placements.map((placement) => placement[field]))];
}

function cartesianPlacements(
  card: BoardCard,
  groupKeys: readonly string[],
  subGroupKeys: readonly string[],
): BoardCellPlacement[] {
  return groupKeys.flatMap((groupKey) =>
    subGroupKeys.map((subGroupKey) => ({ card, groupKey, subGroupKey })),
  );
}

/** label 定义帧局部维护二维 column/lane skeleton，并在删除时把孤儿落入 __none__。 */
export function applyLabelDefinitionToLanes(
  columns: readonly BoardProjectionColumn[],
  lanes: readonly BoardLane[],
  frame: RealtimeEventFrame,
  ctx: BoardLaneMergeContext,
): Pick<BoardLaneMergeResult, 'columns' | 'lanes'> {
  const label = labelDefinitionPayload(frame);
  const groupUsesLabels = ctx.groupBy === 'label';
  const laneUsesLabels = ctx.subGroupBy === 'label';
  if (label === null || (!groupUsesLabels && !laneUsesLabels)) return { columns, lanes };
  const action = actionOf(frame.event);
  const existsAsColumn = columns.some((column) => column.key === label.id);
  const existsAsLane = lanes.some((lane) => lane.key === label.id);

  let templates = [...columns];
  let laneTemplates = [...lanes];
  if (action === 'created') {
    if (label.name === undefined) return { columns, lanes };
    if (groupUsesLabels && !existsAsColumn) {
      templates = insertBeforeNone(templates, {
        key: label.id,
        label: label.name,
        count: 0,
        wip: null,
      });
    }
    if (laneUsesLabels && !existsAsLane) {
      laneTemplates = insertBeforeNone(laneTemplates, {
        key: label.id,
        label: label.name,
        count: 0,
        groups: templates.map((column) => ({ key: column.key, count: 0, data: [] })),
      });
    }
    return rebuildLaneProjection(templates, laneTemplates, flattenLaneCards(lanes), ctx);
  }

  if (action === 'updated') {
    if (label.name === undefined) return { columns, lanes };
    templates = templates.map((column) =>
      column.key === label.id ? { ...column, label: label.name! } : column,
    );
    laneTemplates = laneTemplates.map((lane) =>
      lane.key === label.id ? { ...lane, label: label.name! } : lane,
    );
    const placements = flattenLaneCards(lanes).map((placement) => ({
      ...placement,
      card: patchCardLabel(placement.card, label, false),
    }));
    return rebuildLaneProjection(templates, laneTemplates, placements, ctx);
  }

  if (action !== 'deleted') return { columns, lanes };
  if (groupUsesLabels) templates = templates.filter((column) => column.key !== label.id);
  if (laneUsesLabels) laneTemplates = laneTemplates.filter((lane) => lane.key !== label.id);

  const byIssue = new Map<
    string,
    { card: BoardCard; groupKeys: Set<string>; subGroupKeys: Set<string> }
  >();
  for (const placement of flattenLaneCards(lanes)) {
    const current = byIssue.get(placement.card.id) ?? {
      card: patchCardLabel(placement.card, label, true),
      groupKeys: new Set<string>(),
      subGroupKeys: new Set<string>(),
    };
    current.groupKeys.add(placement.groupKey);
    current.subGroupKeys.add(placement.subGroupKey);
    byIssue.set(placement.card.id, current);
  }
  const placements = [...byIssue.values()].flatMap((current) => {
    if (groupUsesLabels) current.groupKeys.delete(label.id);
    if (laneUsesLabels) current.subGroupKeys.delete(label.id);
    if (current.groupKeys.size === 0) current.groupKeys.add(NONE_KEY);
    if (current.subGroupKeys.size === 0) current.subGroupKeys.add(NONE_KEY);
    return cartesianPlacements(current.card, [...current.groupKeys], [...current.subGroupKeys]);
  });
  return rebuildLaneProjection(templates, laneTemplates, placements, ctx);
}

/** custom field/option 定义帧局部维护二维 skeleton，不请求整板投影。 */
export function applyCustomFieldDefinitionToLanes(
  columns: readonly BoardProjectionColumn[],
  lanes: readonly BoardLane[],
  frame: RealtimeEventFrame,
  ctx: BoardLaneMergeContext,
): Pick<BoardLaneMergeResult, 'columns' | 'lanes'> {
  const groupMutation = customFieldSkeletonMutation(frame, ctx.groupBy);
  const laneMutation = customFieldSkeletonMutation(frame, ctx.subGroupBy);
  if (groupMutation === null && laneMutation === null) return { columns, lanes };
  if (groupMutation?.removeAxis === true && laneMutation?.removeAxis === true) {
    return { columns: [], lanes: [] };
  }
  if (groupMutation?.removeAxis === true) {
    return {
      columns: [],
      lanes: lanes.map((lane) => ({ ...lane, count: 0, groups: [] })),
    };
  }
  if (laneMutation?.removeAxis === true) {
    return {
      columns: columns.map((column) => ({ ...column, count: 0 })),
      lanes: [],
    };
  }
  let templates = columns.map((column) =>
    column.key === NONE_KEY && groupMutation?.noneLabel !== undefined
      ? { ...column, label: groupMutation.noneLabel }
      : column,
  );
  let laneTemplates = lanes.map((lane) =>
    lane.key === NONE_KEY && laneMutation?.noneLabel !== undefined
      ? { ...lane, label: laneMutation.noneLabel }
      : lane,
  );

  const applyColumnMutation = (mutation: AxisSkeletonMutation | null): void => {
    if (mutation?.key === undefined) return;
    if (mutation.remove === true) {
      templates = templates.filter((column) => column.key !== mutation.key);
    } else if (mutation.label !== undefined) {
      if (templates.some((column) => column.key === mutation.key)) {
        templates = templates.map((column) =>
          column.key === mutation.key ? { ...column, label: mutation.label! } : column,
        );
      } else {
        templates = insertBeforeNone(templates, {
          key: mutation.key,
          label: mutation.label,
          count: 0,
          wip: null,
        });
      }
    }
  };
  const applyLaneMutation = (mutation: AxisSkeletonMutation | null): void => {
    if (mutation?.key === undefined) return;
    if (mutation.remove === true) {
      laneTemplates = laneTemplates.filter((lane) => lane.key !== mutation.key);
    } else if (mutation.label !== undefined) {
      if (laneTemplates.some((lane) => lane.key === mutation.key)) {
        laneTemplates = laneTemplates.map((lane) =>
          lane.key === mutation.key ? { ...lane, label: mutation.label! } : lane,
        );
      } else {
        laneTemplates = insertBeforeNone(laneTemplates, {
          key: mutation.key,
          label: mutation.label,
          count: 0,
          groups: templates.map((column) => ({ key: column.key, count: 0, data: [] })),
        });
      }
    }
  };
  applyColumnMutation(groupMutation);
  applyLaneMutation(laneMutation);

  const removedGroupKey = groupMutation?.remove === true ? groupMutation.key : undefined;
  const removedLaneKey = laneMutation?.remove === true ? laneMutation.key : undefined;
  if (removedGroupKey === undefined && removedLaneKey === undefined) {
    return rebuildLaneProjection(templates, laneTemplates, flattenLaneCards(lanes), ctx);
  }
  const byIssue = new Map<
    string,
    { card: BoardCard; groupKeys: Set<string>; subGroupKeys: Set<string> }
  >();
  for (const placement of flattenLaneCards(lanes)) {
    const current = byIssue.get(placement.card.id) ?? {
      card: placement.card,
      groupKeys: new Set<string>(),
      subGroupKeys: new Set<string>(),
    };
    current.groupKeys.add(placement.groupKey);
    current.subGroupKeys.add(placement.subGroupKey);
    byIssue.set(placement.card.id, current);
  }
  const placements = [...byIssue.values()].flatMap((current) => {
    if (removedGroupKey !== undefined) current.groupKeys.delete(removedGroupKey);
    if (removedLaneKey !== undefined) current.subGroupKeys.delete(removedLaneKey);
    if (current.groupKeys.size === 0) current.groupKeys.add(NONE_KEY);
    if (current.subGroupKeys.size === 0) current.subGroupKeys.add(NONE_KEY);
    return cartesianPlacements(current.card, [...current.groupKeys], [...current.subGroupKeys]);
  });
  return rebuildLaneProjection(templates, laneTemplates, placements, ctx);
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
  const customFields = ctx.customFields ?? [];

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
        ...cartesianPlacements(
          created,
          groupKeysForCard(created, ctx.groupBy, customFields),
          groupKeysForCard(created, ctx.subGroupBy, customFields),
        ),
      ],
      ctx,
    );
    return { ...rebuilt, refetch: false };
  }

  const id = issueIdFromPayload(payload, action);
  if (id === undefined) return unchangedLaneResult(columns, lanes);
  const issuePlacements = placements.filter((placement) => placement.card.id === id);
  if (issuePlacements.length === 0) {
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
  if (
    action !== 'updated' &&
    action !== 'moved' &&
    action !== 'project_changed' &&
    action !== 'labels_changed' &&
    action !== 'custom_field_changed'
  ) {
    return unchangedLaneResult(columns, lanes);
  }

  const existing = issuePlacements[0]!;
  if (isStale(existing.card, payload)) {
    return unchangedLaneResult(columns, lanes);
  }
  let merged = mergeBoardCardForRealtime(existing.card, frame);
  if (action === 'project_changed') merged = applyProjectChangedPayload(merged, payload);

  const groupAssociationChanged =
    (action === 'labels_changed' && ctx.groupBy === 'label') ||
    (action === 'custom_field_changed' && payload.field_def_id === ctx.groupBy);
  const subGroupAssociationChanged =
    (action === 'labels_changed' && ctx.subGroupBy === 'label') ||
    (action === 'custom_field_changed' && payload.field_def_id === ctx.subGroupBy);
  let groupKeys =
    isDynamicAxis(ctx.groupBy) && !groupAssociationChanged
      ? uniquePlacementKeys(issuePlacements, 'groupKey')
      : [...groupKeysForCard(merged, ctx.groupBy, customFields)];
  let subGroupKeys =
    isDynamicAxis(ctx.subGroupBy) && !subGroupAssociationChanged
      ? uniquePlacementKeys(issuePlacements, 'subGroupKey')
      : [...groupKeysForCard(merged, ctx.subGroupBy, customFields)];
  if (action === 'moved') {
    const target =
      typeof payload.to === 'object' && payload.to !== null
        ? (payload.to as Record<string, unknown>)
        : {};
    const groupKey =
      (typeof target.group_key === 'string' ? target.group_key : undefined) ??
      movedAxisTarget(payload, ctx.groupBy) ??
      groupKeys[0]!;
    const subGroupKey =
      (typeof payload.to_sub_group === 'string' ? payload.to_sub_group : undefined) ??
      movedAxisTarget(payload, ctx.subGroupBy) ??
      subGroupKeys[0]!;
    groupKeys = [groupKey];
    subGroupKeys = [subGroupKey];
    merged = patchCardAxis(merged, ctx.groupBy, groupKey);
    merged = patchCardAxis(merged, ctx.subGroupBy, subGroupKey);
    if (typeof payload.position === 'number') merged = { ...merged, position: payload.position };
  }

  const remaining = placements.filter((placement) => placement.card.id !== id);
  if (!ctx.belongs(merged)) {
    const rebuilt = rebuildLaneProjection(columns, lanes, remaining, ctx);
    return { ...rebuilt, refetch: false };
  }
  const nextPlacements = [
    ...placements.filter((placement) => placement.card.id !== id),
    ...cartesianPlacements(merged, groupKeys, subGroupKeys),
  ];
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
    case 'label':
    case 'label_id':
      return (card.labels ?? []).map((label) => label.id);
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
  if (field === null || field === 'q') return null;
  const actual = fieldValue(card, field);
  if (actual === undefined) return null;
  const op = cond.op;
  if (Array.isArray(actual)) {
    if (op === 'eq') return actual.includes(cond.value);
    if (op === 'neq') return !actual.includes(cond.value);
    if (op === 'in') {
      return Array.isArray(cond.value) && cond.value.some((value) => actual.includes(value));
    }
    if (op === 'not_in') {
      return Array.isArray(cond.value) && !cond.value.some((value) => actual.includes(value));
    }
    return null;
  }
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
  if (groupBy === 'project') return data[0]?.project?.name ?? key;
  return key;
}

/**
 * 按(草稿)group_by 对已加载卡片本地重分桶(§4.2 分组切换即时反映)。
 * 目标轴标签仅从卡片/字段定义派生,避免与源轴同 key 时串用标签或 WIP;
 * count 为本地组内卡片数(整板已分页加载完毕,故等于组内总数)。
 */
export function rebucketGroups(
  groups: readonly BoardGroup[],
  groupBy: string,
  customFields: readonly CustomFieldDef[] = [],
): BoardGroup[] {
  const cards = new Map<string, BoardCard>();
  for (const group of groups) {
    for (const card of group.data) cards.set(card.id, card);
  }
  const byKey = new Map<string, Map<string, BoardCard>>();
  for (const card of cards.values()) {
    for (const key of groupKeysForCard(card, groupBy, customFields)) {
      const bucket = byKey.get(key) ?? new Map<string, BoardCard>();
      bucket.set(card.id, card);
      byKey.set(key, bucket);
    }
  }
  const result: BoardGroup[] = [];
  for (const [key, bucket] of byKey) {
    const data = [...bucket.values()];
    result.push({
      key,
      label: isDynamicAxis(groupBy)
        ? labelForCardAxis([], data[0]!, groupBy, key, customFields)
        : fallbackLabel(key, data, groupBy),
      count: data.length,
      wip: null,
      data,
    });
  }
  const definition = customFields.find((field) => field.id === groupBy);
  const optionRank = new Map(
    definition?.options.map((option) => [option.id, option.position] as const) ?? [],
  );
  result.sort((left, right) => {
    if (left.key === NONE_KEY) return right.key === NONE_KEY ? 0 : 1;
    if (right.key === NONE_KEY) return -1;
    if (optionRank.size > 0) {
      const byPosition =
        (optionRank.get(left.key) ?? Number.MAX_SAFE_INTEGER) -
        (optionRank.get(right.key) ?? Number.MAX_SAFE_INTEGER);
      if (byPosition !== 0) return byPosition;
    }
    if (groupBy === 'label' || groupBy === 'assignee' || groupBy === 'project') {
      const leftLabel = left.label.toLowerCase();
      const rightLabel = right.label.toLowerCase();
      if (leftLabel < rightLabel) return -1;
      if (leftLabel > rightLabel) return 1;
    }
    return left.key < right.key ? -1 : left.key > right.key ? 1 : 0;
  });
  return result;
}
