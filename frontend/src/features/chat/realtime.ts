/**
 * 聊天会话列表实时帧合并(chat-session.md §3.6 / README §6.7)。
 * 纯函数:绝不修改入参,有变化返回新数组,无变化返回原引用。
 *
 * 帧来源:owner 私有 `chat_list:{myMemberId}`(载荷含 session_id,H1)与
 * `chat_session:{id}`。仅 message.created / message.done / message.interrupted
 * 改动列表预览(last_message_preview / last_message_at / message_count);delta
 * 太噪不并入列表。载荷仅含本人会话的预览字段,无 partial_content。
 * 防回退以 last_message_at 字符串比较为闸(RFC3339 UTC 可直接比较,§6.7 水位);
 * 未知 session(不在当前列表)一律忽略。SSE 为流式主路径,本合并仅校正列表快照。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { ChatSession } from './types';

/** 会改动列表预览的事件(其余事件返回原引用)。 */
const PREVIEW_EVENTS: ReadonlySet<string> = new Set([
  'message.created',
  'message.done',
  'message.interrupted',
]);

interface SessionFramePayload {
  readonly session_id?: unknown;
  readonly last_message_preview?: unknown;
  readonly last_message_at?: unknown;
  readonly message_count?: unknown;
}

function payloadOf(frame: RealtimeEventFrame): SessionFramePayload {
  return frame.payload as SessionFramePayload;
}

/** 防回退:帧的 last_message_at 比现有更旧 → 丢弃(§6.7 水位)。 */
function isStale(existing: ChatSession, payload: SessionFramePayload): boolean {
  const frameAt = typeof payload.last_message_at === 'string' ? payload.last_message_at : null;
  if (frameAt === null) return false;
  if (existing.last_message_at === null) return false;
  return frameAt < existing.last_message_at;
}

/** 预览字段增量(可变内部类型;并入后产出新的不可变 ChatSession)。 */
interface SessionPreviewPatch {
  last_message_preview?: string;
  last_message_at?: string;
  message_count?: number;
}

/** 从帧载荷挑出合法的预览字段(仅取已知键,天然隔离原型污染)。 */
function previewPatch(
  existing: ChatSession,
  payload: SessionFramePayload,
): SessionPreviewPatch | null {
  const patch: SessionPreviewPatch = {};
  let changed = false;
  if (
    typeof payload.last_message_preview === 'string' &&
    payload.last_message_preview !== existing.last_message_preview
  ) {
    patch.last_message_preview = payload.last_message_preview;
    changed = true;
  }
  if (
    typeof payload.last_message_at === 'string' &&
    payload.last_message_at !== existing.last_message_at
  ) {
    patch.last_message_at = payload.last_message_at;
    changed = true;
  }
  if (
    typeof payload.message_count === 'number' &&
    payload.message_count !== existing.message_count
  ) {
    patch.message_count = payload.message_count;
    changed = true;
  }
  return changed ? patch : null;
}

/**
 * 列表级帧合并:命中会话则并入预览字段(防回退 + 无变化返回原引用);
 * 未知 session / 非预览事件 / 过期帧 → 原样返回。
 */
export function applySessionListFrame(
  sessions: readonly ChatSession[],
  frame: RealtimeEventFrame,
): ChatSession[] {
  if (!PREVIEW_EVENTS.has(frame.event)) return sessions as ChatSession[];
  const payload = payloadOf(frame);
  const sessionId = typeof payload.session_id === 'string' ? payload.session_id : null;
  if (sessionId === null) return sessions as ChatSession[];
  const existing = sessions.find((session) => session.id === sessionId);
  if (existing === undefined) return sessions as ChatSession[];
  if (isStale(existing, payload)) return sessions as ChatSession[];
  const patch = previewPatch(existing, payload);
  if (patch === null) return sessions as ChatSession[];
  return sessions.map((session) => (session.id === sessionId ? { ...session, ...patch } : session));
}
