/**
 * 增量合并(README §6.7 可见性水位 / kanban §3.5,帧形态对齐后端 v0.1.0)。
 * - 数据帧 `{op:'event', channel, seq, event, payload}`:payload 携带完整变更字段 +
 *   visibility;`belongs` 判定归属(复杂嵌套 filters 下可由 belongs 实现方按 id
 *   轻量 refetch,不要求前端仅凭 diff 本地重算任意嵌套条件)
 * - `.created`/`.updated`/`.moved`(及未知后缀,按 §6.7 payload 为权威全量)→ 按归属 upsert
 * - `.deleted` → 移除
 * - 防回退:两侧 `updated_at` 皆存在时 payload 更旧 → 丢弃;无 `updated_at` 时以
 *   `version` 兜底(§6.14 二者为替代关系)
 * - 纯函数:绝不修改入参 map,payload 以浅拷贝存入(不共享服务端帧引用);
 *   有变化返回新 map,无变化返回原引用
 */
import type { RealtimeEventFrame } from '../types/realtime';

/** 归属判定上下文:基于可见性水位判断实体是否属于当前视图/集合 */
export interface MergeContext<T> {
  belongs: (item: T) => boolean;
}

interface MergePayload {
  id?: string;
  updated_at?: string;
  version?: number | string;
}

/** 从 `<entity>.<action>` 事件名取动作后缀 */
function actionOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? event : event.slice(dot + 1);
}

export function mergeEntityFrame<T extends { id: string; updated_at?: string }>(
  map: ReadonlyMap<string, T>,
  frame: RealtimeEventFrame,
  ctx: MergeContext<T>,
): Map<string, T> {
  // 浅拷贝 payload:本地状态不与服务端帧对象共享引用(不可变纯粹性)
  const data = { ...frame.payload } as MergePayload & Partial<T>;
  const id = data.id;
  if (typeof id !== 'string') return map as Map<string, T>;

  if (actionOf(frame.event) === 'deleted') {
    return removeFrom(map, id);
  }

  const existing = map.get(id);
  if (isStale(existing, data)) return map as Map<string, T>;

  const merged = (existing ? { ...existing, ...data } : data) as T;
  if (!ctx.belongs(merged)) {
    return removeFrom(map, id);
  }

  const next = new Map(map);
  next.set(id, merged);
  return next;
}

/**
 * 防回退:优先比较 `updated_at`(RFC3339 UTC 字符串可直接比较);
 * 两侧 updated_at 不全时以 `version` 兜底(数值/字符串,小者旧)。
 */
function isStale<T extends { updated_at?: string }>(
  existing: T | undefined,
  payload: MergePayload,
): boolean {
  if (!existing) return false;
  if (existing.updated_at && payload.updated_at) {
    return payload.updated_at < existing.updated_at;
  }
  const existingVersion = (existing as MergePayload).version;
  const payloadVersion = payload.version;
  if (
    typeof existingVersion === 'number' &&
    typeof payloadVersion === 'number'
  ) {
    return payloadVersion < existingVersion;
  }
  return false;
}

function removeFrom<T>(map: ReadonlyMap<string, T>, id: string): Map<string, T> {
  if (!map.has(id)) return map as Map<string, T>;
  const next = new Map(map);
  next.delete(id);
  return next;
}
