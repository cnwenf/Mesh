/**
 * 实时事件线缆协议类型 — 与后端 v0.1.0(`backend/src/mesh/realtime/session.py`)逐帧对齐,
 * 权威契约见 docs/specs/README.md §6.7 / §6.16。
 *
 * 鉴权:连接建立后**首帧** `{op:'auth', token}` → 服务端回 `{op:'auth_ok'}`(10s 超时)。
 * token 绝不进 URL query(§6.16 明确允许「子协议或首帧」两种机制;已发版后端 v0.1.0
 * 实现首帧鉴权,前端与其对齐,同样满足「token 不进 URL」核心约束)。
 *
 * 客户端 → 服务端:
 *   {op:'auth', token} / {op:'subscribe', channel, resume_from?} /
 *   {op:'unsubscribe', channel} / {op:'ping'}
 * 服务端 → 客户端:
 *   {op:'auth_ok'} / {op:'subscribed', channel, last_seq} /
 *   {op:'event', channel, seq, event, payload} /
 *   {op:'resync_required', channel, watermark, rest} /
 *   {op:'error', code, message} / {op:'ping'}
 */

/** 服务端 → 客户端:数据帧(seq 频道内单调递增;payload 为完整变更字段 + 可见性水位) */
export interface RealtimeEventFrame<P = Record<string, unknown>> {
  op: 'event';
  /** 频道,如 workspace:<uuid>:issues / issue:<uuid> */
  channel: string;
  /** 频道内单调递增序号 */
  seq: number;
  /** 事件名 <entity>.<action>,取自 README §6.7 事件词汇注册表 */
  event: string;
  /** 完整变更字段(非 diff 指针) */
  payload: P;
}

export interface AuthOkFrame {
  op: 'auth_ok';
}

export interface SubscribedFrame {
  op: 'subscribed';
  channel: string;
  /** 服务端频道当前最大 seq(订阅/重放完成确认) */
  last_seq: number;
}

export interface ResyncRequiredFrame {
  op: 'resync_required';
  channel: string;
  /** 该频道当前最大 seq(对账水位) */
  watermark: number;
  /** 对账 REST URL:/api/v1/realtime/events?channel=<urlencoded>&since=<resume_from> */
  rest: string;
}

export interface ErrorFrame {
  op: 'error';
  code: string;
  message: string;
}

export interface PingFrame {
  op: 'ping';
}

export type ServerFrame =
  | AuthOkFrame
  | SubscribedFrame
  | RealtimeEventFrame
  | ResyncRequiredFrame
  | ErrorFrame
  | PingFrame;

/** 客户端 → 服务端:首帧鉴权 */
export interface AuthOp {
  op: 'auth';
  token: string;
}

/** 客户端 → 服务端:订阅(有该频道 last_seq 时带 resume_from=last_seq+1) */
export interface SubscribeOp {
  op: 'subscribe';
  channel: string;
  resume_from?: number;
}

export interface UnsubscribeOp {
  op: 'unsubscribe';
  channel: string;
}

export interface PingOp {
  op: 'ping';
}

export type ClientOp = AuthOp | SubscribeOp | UnsubscribeOp | PingOp;

export function isServerFrame(value: unknown): value is ServerFrame {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { op?: unknown }).op === 'string'
  );
}

export function isEventFrame(value: unknown): value is RealtimeEventFrame {
  if (!isServerFrame(value) || value.op !== 'event') return false;
  const f = value as unknown as Record<string, unknown>;
  return (
    typeof f.channel === 'string' && typeof f.seq === 'number' && typeof f.event === 'string'
  );
}
