/**
 * Issue 模块实时帧合并(issue.md §3.6/§4.5,README §6.7)。
 * 纯函数:绝不修改入参,有变化返回新数组/对象,无变化返回原引用。
 *
 * 帧形态:{op:'event', channel, seq, event, payload};event ∈
 * issue.created/updated/deleted/moved/project_changed/labels_changed · dependency.changed。
 * issue.created → `{issue: 摘要}`;issue.updated/moved/project_changed 为
 * 扁平 `{id, changes, version, updated_at}`;issue.labels_changed 为
 * `{issue_id, labels}`;issue.deleted → `{id}`。
 * 防回退以 updated_at 字符串比较为闸(RFC3339 UTC 可直接比较,§6.7 水位)。
 * 客户端按 id 增量合并,不整页刷新(§3.6)。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { IssueSummary } from './types';

interface FramePayload {
  readonly id?: unknown;
  readonly issue_id?: unknown;
  readonly labels?: unknown;
  readonly updated_at?: unknown;
  readonly version?: unknown;
  readonly changes?: unknown;
  readonly issue?: unknown;
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

/** 防回退:两侧 updated_at 皆存在且帧更旧 → 丢弃(§6.7 可见性水位)。 */
function isStale(existing: IssueSummary, payload: FramePayload): boolean {
  const frameUpdatedAt = typeof payload.updated_at === 'string' ? payload.updated_at : undefined;
  if (frameUpdatedAt === undefined) return false;
  return frameUpdatedAt < existing.updated_at;
}

/** 合并顶层字段与嵌套 `changes`(浅层;changes 为服务端字段 diff)。 */
/** 帧载荷中的事件元字段(F11:不得扩散到行对象)。 */
const FRAME_META_KEYS = new Set([
  'changes',
  'issue',
  'from_project_id',
  'to_project_id',
  'mapped_fields',
  'cleared_fields',
  'visibility',
  'issue_id',
]);

/**
 * 原型污染防护键(LOW-1):帧载荷顶层出现这些键时一律跳过。
 * JSON.parse('{"__proto__": ...}') 产生自有 `__proto__` 属性,经 Object.entries
 * 枚举后若下标赋值进普通对象会触发 Object.prototype 的 setter 改写原型;
 * 跳过 + null 原型承载双重隔离,杜绝该 sink(帧来源为已鉴权服务端,仍纵深防御)。
 */
const PROTO_POLLUTION_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

function mergedFields(existing: IssueSummary, payload: FramePayload): IssueSummary {
  const { changes, issue: _issue, ...top } = payload;
  const topFields: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const [key, value] of Object.entries(top)) {
    if (PROTO_POLLUTION_KEYS.has(key)) continue;
    if (!FRAME_META_KEYS.has(key)) topFields[key] = value;
  }
  const changeFields =
    typeof changes === 'object' && changes !== null ? (changes as Record<string, unknown>) : {};
  return { ...existing, ...topFields, ...changeFields } as IssueSummary;
}

/**
 * 列表级帧合并(workspace:{ws}:issues 频道)。
 * `belongs` 判定 issue 是否属于当前筛选视图;不属于则从列表移除(可见性水位)。
 */
export function applyIssueListFrame(
  issues: readonly IssueSummary[],
  frame: RealtimeEventFrame,
  belongs: (issue: IssueSummary) => boolean,
): IssueSummary[] {
  if (entityOf(frame.event) !== 'issue') return issues as IssueSummary[];
  const payload = payloadOf(frame);
  const action = actionOf(frame.event);

  if (action === 'created') {
    const nested = payload.issue;
    if (typeof nested !== 'object' || nested === null) return issues as IssueSummary[];
    const created = nested as IssueSummary;
    if (typeof created.id !== 'string') return issues as IssueSummary[];
    if (issues.some((issue) => issue.id === created.id)) return issues as IssueSummary[];
    if (!belongs(created)) return issues as IssueSummary[];
    return [...issues, created];
  }

  if (action === 'deleted' || action === 'project_changed') {
    const id = typeof payload.id === 'string' ? payload.id : undefined;
    if (id === undefined) return issues as IssueSummary[];
    const existing = issues.find((issue) => issue.id === id);
    if (existing === undefined) return issues as IssueSummary[];
    // project_changed:载荷携带 to_project_id,先合并再按 belongs 决定去留
    const merged = mergedFields(existing, payload);
    if (!belongs(merged)) return issues.filter((issue) => issue.id !== id);
    if (action === 'deleted') return issues.filter((issue) => issue.id !== id);
    return issues.map((issue) => (issue.id === id ? merged : issue));
  }

  if (action === 'updated' || action === 'moved') {
    const id = typeof payload.id === 'string' ? payload.id : undefined;
    if (id === undefined) return issues as IssueSummary[];
    const existing = issues.find((issue) => issue.id === id);
    if (existing === undefined) return issues as IssueSummary[];
    if (isStale(existing, payload)) return issues as IssueSummary[];
    const merged = mergedFields(existing, payload);
    if (!belongs(merged)) return issues.filter((issue) => issue.id !== id);
    return issues.map((issue) => (issue.id === id ? merged : issue));
  }

  if (action === 'labels_changed') {
    const id = typeof payload.issue_id === 'string' ? payload.issue_id : undefined;
    if (id === undefined || !Array.isArray(payload.labels)) return issues as IssueSummary[];
    const existing = issues.find((issue) => issue.id === id);
    if (existing === undefined) return issues as IssueSummary[];
    const merged = { ...existing, labels: payload.labels } as IssueSummary;
    if (!belongs(merged)) return issues.filter((issue) => issue.id !== id);
    return issues.map((issue) => (issue.id === id ? merged : issue));
  }

  return issues as IssueSummary[];
}

/**
 * 详情级帧合并(issue:{id} 频道):把帧字段并入当前详情。
 * 防回退同列表级;无关帧返回原对象引用。
 */
export function applyIssueDetailFrame<T extends IssueSummary>(
  issue: T,
  frame: RealtimeEventFrame,
): T {
  if (entityOf(frame.event) !== 'issue') return issue;
  const payload = payloadOf(frame);
  const action = actionOf(frame.event);
  if (action === 'created' || action === 'deleted') return issue;
  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id !== issue.id) return issue;
  if (isStale(issue, payload)) return issue;
  return mergedFields(issue, payload) as T;
}
