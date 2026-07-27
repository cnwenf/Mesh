/**
 * 附件模块实时帧合并(attachment.md §3.7,README §6.7)。
 * 纯函数:绝不修改入参,有变化返回新数组,无变化返回原引用。
 *
 * 帧形态:{op:'event', channel, seq, event, payload};event ∈
 * attachment.processed · attachment.deleted(经 issue:{id} 频道下发)。
 * processed → `{id, blob_id, scan_status, mime_type, is_image, file_name, thumbnail_url, visibility}`;
 * deleted → `{id, linked_type, linked_id, visibility}`。
 * 客户端按 id 增量合并,不整页刷新。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { Attachment } from './types';

/**
 * 原型污染防护键:帧载荷顶层出现这些键时一律跳过。
 * JSON.parse('{"__proto__": ...}') 产生自有 `__proto__` 属性,经 Object.entries
 * 枚举后若下标赋值进普通对象会触发 Object.prototype 的 setter 改写原型;
 * 跳过 + 白名单字段双重隔离(帧来源为已鉴权服务端,仍纵深防御)。
 */
const PROTO_POLLUTION_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

/** processed 帧允许并入附件行的字段白名单(其余如 visibility 为事件元字段,不扩散到行)。 */
const PROCESSED_MERGE_KEYS = new Set([
  'blob_id',
  'scan_status',
  'mime_type',
  'is_image',
  'file_name',
  'thumbnail_url',
]);

interface ProcessedPayload {
  readonly id?: unknown;
  readonly [key: string]: unknown;
}

interface DeletedPayload {
  readonly id?: unknown;
}

/**
 * M4:帧载荷防御。上游 isEventFrame 不校验 payload,真实链路可能出现
 * `payload: null / undefined / 非对象` 的帧;在 setAttachments updater 内
 * 访问其属性会抛 TypeError 崩掉整棵组件树。非对象载荷一律视为无操作,
 * 合并函数返回原引用(纯函数契约:无变化不改引用)。
 */
function payloadOf(frame: RealtimeEventFrame): Record<string, unknown> | null {
  const payload: unknown = frame.payload;
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return null;
  return payload as Record<string, unknown>;
}

function actionOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? event : event.slice(dot + 1);
}

function entityOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? '' : event.slice(0, dot);
}

/**
 * 合并 attachment.processed:把放行后的扫描结果并入既有行(如 pending → clean +
 * thumbnail_url)。载荷非完整渲染对象,故仅更新已知字段;列表中不存在的 id 跳过
 * (新增附件经 composer onUploaded / 列表刷新进入,不由本帧凭空补全)。
 */
export function applyAttachmentProcessed(
  attachments: readonly Attachment[],
  frame: RealtimeEventFrame,
): Attachment[] {
  if (entityOf(frame.event) !== 'attachment' || actionOf(frame.event) !== 'processed') {
    return attachments as Attachment[];
  }
  const payload = payloadOf(frame) as ProcessedPayload | null;
  if (payload === null) return attachments as Attachment[];
  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id === undefined) return attachments as Attachment[];
  const existing = attachments.find((item) => item.id === id);
  if (existing === undefined) return attachments as Attachment[];

  const patch: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) {
    if (PROTO_POLLUTION_KEYS.has(key)) continue;
    if (PROCESSED_MERGE_KEYS.has(key)) patch[key] = value;
  }
  if (Object.keys(patch).length === 0) return attachments as Attachment[];
  return attachments.map((item) => (item.id === id ? ({ ...item, ...patch } as Attachment) : item));
}

/** 合并 attachment.deleted:按 id 从列表移除;不存在则返回原引用。 */
export function applyAttachmentDeleted(
  attachments: readonly Attachment[],
  frame: RealtimeEventFrame,
): Attachment[] {
  if (entityOf(frame.event) !== 'attachment' || actionOf(frame.event) !== 'deleted') {
    return attachments as Attachment[];
  }
  const payload = payloadOf(frame) as DeletedPayload | null;
  if (payload === null) return attachments as Attachment[];
  const id = typeof payload.id === 'string' ? payload.id : undefined;
  if (id === undefined) return attachments as Attachment[];
  if (!attachments.some((item) => item.id === id)) return attachments as Attachment[];
  return attachments.filter((item) => item.id !== id);
}
