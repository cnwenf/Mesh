/**
 * 实时事件帧类型 — 权威:docs/specs/README.md §6.7 与 features/kanban.md §3.5。
 * - seq 为「频道内」单调递增(at-least-once,客户端按 seq 幂等去重)
 * - 连接鉴权经 WebSocket 子协议(禁止 token 进 URL query,§6.16):
 *   `new WebSocket(url, ['mesh.auth.v1', token])`
 */

/** 服务端 → 客户端:数据帧(payload 携带完整变更字段 + visibility,§6.7 可见性水位) */
export interface RealtimeFrame<T = Record<string, unknown>> {
  /** 频道内单调递增序号 */
  seq: number;
  /** 事件名 <entity>.<action>,取自 README §6.7 事件词汇注册表 */
  type: string;
  /** 频道,如 workspace:{ws}:issues / view:{view_id} */
  topic: string;
  /** RFC3339 UTC */
  ts: string;
  data: T;
}

/** 服务端 → 客户端:控制帧 */
export type ServerControlFrame =
  | { op: 'subscribed'; topic: string }
  | { op: 'pong' }
  | {
      op: 'resync_required';
      topic: string;
      /** 该频道当前最大 seq(对账水位) */
      watermark: number;
      /** 对账 REST URL(带 since=…),客户端整拉对账后无感恢复 */
      rest: string;
    }
  | { op: 'error'; code: string; message: string; topic?: string };

/** 客户端 → 服务端:订阅(有该频道 last_seq 时带 resume_from=last_seq+1) */
export interface SubscribeOp {
  op: 'subscribe';
  topic: string;
  resume_from?: number;
}

export interface UnsubscribeOp {
  op: 'unsubscribe';
  topic: string;
}

export interface PingOp {
  op: 'ping';
}

export type ClientOp = SubscribeOp | UnsubscribeOp | PingOp;

/** 判定一个服务端帧是数据帧还是控制帧 */
export function isDataFrame(frame: unknown): frame is RealtimeFrame {
  if (typeof frame !== 'object' || frame === null) return false;
  const f = frame as Record<string, unknown>;
  return typeof f.seq === 'number' && typeof f.type === 'string' && typeof f.topic === 'string';
}

export function isControlFrame(frame: unknown): frame is ServerControlFrame {
  if (typeof frame !== 'object' || frame === null) return false;
  return typeof (frame as { op?: unknown }).op === 'string';
}

/** WebSocket 子协议名(鉴权用) */
export const AUTH_SUBPROTOCOL = 'mesh.auth.v1';
