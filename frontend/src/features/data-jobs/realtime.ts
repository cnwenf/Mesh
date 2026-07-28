/**
 * 数据作业实时帧合并(import-export.md §3.11,README §6.7)。
 * 纯函数:绝不修改入参,有变化返回新数组,无变化返回原引用。
 *
 * 帧形态:{op:'event', channel, seq, event, payload};event 恒为
 * `data_job.updated`(data_job:{id} 频道),载荷含 status/计数/产物/
 * failure_reason 与 updated_at(收敛用)。状态迁移、每批进度、终态共用
 * 同一事件名,客户端按 id 合并并以 updated_at 做防回退闸门。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { DataJob } from './types';

/** 帧载荷顶层原型污染防护键(与附件模块同款纵深防御)。 */
const PROTO_POLLUTION_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

/** data_job.updated 允许并入作业行的字段白名单。 */
const MERGE_KEYS = new Set([
  'status',
  'total_rows',
  'succeeded_rows',
  'failed_rows',
  'result_attachment_id',
  'failure_reason',
  'updated_at',
  'finished_at',
  'started_at',
  'error_report',
  'download_url',
]);

function payloadOf(frame: RealtimeEventFrame): Record<string, unknown> | null {
  const payload: unknown = frame.payload;
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return null;
  return payload as Record<string, unknown>;
}

/**
 * 合并一帧到作业列表:
 * - 非 data_job.updated 事件 / 非对象载荷 / 缺 id → 原引用返回(无操作);
 * - 列表中存在该作业 → 白名单字段浅合并(updated_at 早于本地则跳过,防回退);
 * - 列表中不存在 → 该帧属于其他视图的作业,同样无操作(页面按 REST 对账)。
 */
export function applyDataJobFrame(jobs: readonly DataJob[], frame: RealtimeEventFrame): DataJob[] {
  if (frame.event !== 'data_job.updated') return jobs as DataJob[];
  const payload = payloadOf(frame);
  if (payload === null) return jobs as DataJob[];
  const id = payload['id'];
  if (typeof id !== 'string' || id.length === 0) return jobs as DataJob[];

  let changed = false;
  const next = jobs.map((job) => {
    if (job.id !== id) return job;
    const incomingStamp = typeof payload['updated_at'] === 'string' ? payload['updated_at'] : null;
    if (incomingStamp !== null && job.updated_at !== null && incomingStamp < job.updated_at) {
      return job; // stale frame — keep local state
    }
    const merged: Record<string, unknown> = { ...job };
    for (const [key, value] of Object.entries(payload)) {
      if (PROTO_POLLUTION_KEYS.has(key)) continue;
      if (!MERGE_KEYS.has(key)) continue;
      merged[key] = value;
      if (merged[key] !== (job as unknown as Record<string, unknown>)[key]) changed = true;
    }
    return merged as unknown as DataJob;
  });
  return changed ? next : (jobs as DataJob[]);
}
