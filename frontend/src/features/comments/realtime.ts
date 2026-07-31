/**
 * 评论模块实时帧合并(comment-inbox.md §3.6,README §6.7)。
 * 纯函数:绝不修改入参,有变化返回新数组/对象,无变化返回原引用。
 *
 * issue:{issue_id} 频道事件:comment.created / comment.updated / comment.deleted /
 * comment.resolved / reaction.changed / execution.queued。
 * comment.created 载荷为完整评论对象;updated/resolved/reaction.changed 为
 * `{id, ...变更}`(可携嵌套 `changes`);deleted 为 `{id, deleted_at?}`。
 * 防回退以 updated_at 字符串比较为闸(RFC3339 UTC 可直接比较)。
 * 顶层列表内嵌 preview_replies(回复),合并须穿透到线程内层。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { Comment } from './types';
import { isComment } from './types';

interface FramePayload {
  readonly id?: unknown;
  readonly updated_at?: unknown;
  readonly changes?: unknown;
  readonly [key: string]: unknown;
}

/** 帧载荷中的事件元字段:不得扩散到评论对象。 */
const FRAME_META_KEYS = new Set(['changes', 'comment']);

/** 原型污染防护键(纵深防御,帧来源虽已鉴权)。 */
const PROTO_POLLUTION_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

function payloadOf(frame: RealtimeEventFrame): FramePayload {
  return frame.payload as FramePayload;
}

function entityOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? '' : event.slice(0, dot);
}

/** 防回退:两侧 updated_at 皆存在且帧更旧 → 丢弃。 */
function isStale(existing: Comment, payload: FramePayload): boolean {
  const frameUpdatedAt = typeof payload.updated_at === 'string' ? payload.updated_at : undefined;
  if (frameUpdatedAt === undefined) return false;
  return frameUpdatedAt < existing.updated_at;
}

/** 从载荷提取可并入评论对象的字段(剥离元字段与原型污染键,合并嵌套 changes)。 */
function mergedComment(existing: Comment, payload: FramePayload): Comment {
  const { changes, ...top } = payload;
  const fields: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const [key, value] of Object.entries(top)) {
    if (PROTO_POLLUTION_KEYS.has(key)) continue;
    if (!FRAME_META_KEYS.has(key)) fields[key] = value;
  }
  const changeFields =
    typeof changes === 'object' && changes !== null ? (changes as Record<string, unknown>) : {};
  for (const key of Object.keys(changeFields)) {
    if (PROTO_POLLUTION_KEYS.has(key)) continue;
    fields[key] = changeFields[key];
  }
  return { ...existing, ...fields } as Comment;
}

function idOf(payload: FramePayload): string | undefined {
  return typeof payload.id === 'string' ? payload.id : undefined;
}

/** 在顶层列表(含内嵌 preview_replies)中按 id 替换评论;未命中返回原引用。 */
function replaceById(comments: readonly Comment[], id: string, next: Comment): Comment[] {
  let changed = false;
  const topLevel = comments.map((comment) => {
    if (comment.id === id) {
      changed = true;
      return next;
    }
    if (comment.preview_replies === undefined) return comment;
    let nestedChanged = false;
    const replies = comment.preview_replies.map((reply) => {
      if (reply.id !== id) return reply;
      nestedChanged = true;
      return next;
    });
    if (nestedChanged) {
      changed = true;
      return { ...comment, preview_replies: replies };
    }
    return comment;
  });
  return changed ? topLevel : (comments as Comment[]);
}

function findById(comments: readonly Comment[], id: string): Comment | undefined {
  for (const comment of comments) {
    if (comment.id === id) return comment;
    const nested = comment.preview_replies?.find((reply) => reply.id === id);
    if (nested !== undefined) return nested;
  }
  return undefined;
}

/**
 * 评论列表帧合并(issue:{id} 频道)。
 * - comment.created:顶层(parent_id=null)去重 upsert;回复挂入所属线程根 preview_replies。
 * - comment.updated / comment.resolved / reaction.changed:按 id 并入(防回退)。
 * - comment.deleted:置 deleted_at + 清空正文(留占位保线程完整)。
 * 无关帧返回原引用。
 */
export function applyCommentsFrame(
  comments: readonly Comment[],
  frame: RealtimeEventFrame,
): Comment[] {
  if (entityOf(frame.event) !== 'comment' && entityOf(frame.event) !== 'reaction') {
    return comments as Comment[];
  }
  const payload = payloadOf(frame);

  if (frame.event === 'comment.created') {
    const candidate = payload.comment ?? payload;
    if (!isComment(candidate)) return comments as Comment[];
    if (findById(comments, candidate.id) !== undefined) return comments as Comment[];
    if (candidate.parent_id === null) {
      return [...comments, candidate];
    }
    const rootId = candidate.thread_root_id ?? candidate.parent_id;
    const root = comments.find((comment) => comment.id === rootId);
    if (root === undefined) return comments as Comment[];
    const preview = root.preview_replies ?? [];
    if (preview.some((reply) => reply.id === candidate.id)) return comments as Comment[];
    return replaceById(comments, root.id, {
      ...root,
      preview_replies: [...preview, candidate],
      reply_count: root.reply_count + 1,
    });
  }

  const id = idOf(payload);
  if (id === undefined) return comments as Comment[];
  const existing = findById(comments, id);
  if (existing === undefined) return comments as Comment[];

  if (frame.event === 'comment.deleted') {
    const deletedAt =
      typeof payload.deleted_at === 'string' ? payload.deleted_at : existing.updated_at;
    return replaceById(comments, id, {
      ...existing,
      deleted_at: deletedAt,
      body_markdown: '',
      body_html: '',
      body_text: '',
    });
  }

  if (
    frame.event === 'comment.updated' ||
    frame.event === 'comment.resolved' ||
    frame.event === 'reaction.changed'
  ) {
    if (isStale(existing, payload)) return comments as Comment[];
    return replaceById(comments, id, mergedComment(existing, payload));
  }

  return comments as Comment[];
}

/** 执行占位卡片状态(design-quality §9.8 五态;succeeded → 占位移除,由真实评论替换)。 */
export type ExecutionPlaceholderStatus = 'queued' | 'running' | 'waiting' | 'failed';

/** 执行占位卡片:提及 agent 入队后,直至该 agent 评论回流/执行终态前显示运行态。 */
export interface ExecutionPlaceholder {
  readonly execution_id: string;
  readonly comment_id: string | null;
  readonly agent_id: string;
  readonly agent_name: string;
  readonly status: ExecutionPlaceholderStatus;
  readonly failure_reason: string | null;
}

/** 执行生命周期帧所在频道(后端按执行 id 发布,不在 issue 频道)。 */
export function executionChannel(executionId: string): string {
  return `execution:${executionId}`;
}

/**
 * execution.queued → 追加占位(去重;五态初始 queued);其余帧原样返回。
 * 载荷形态:`{execution_id, agent_member_id, comment_id, status, trigger}` +
 * 可选 `agent_name`(无则以 agent_member_id 兜底显示)。
 */
export function applyExecutionFrame(
  placeholders: readonly ExecutionPlaceholder[],
  frame: RealtimeEventFrame,
): ExecutionPlaceholder[] {
  if (frame.event !== 'execution.queued') return placeholders as ExecutionPlaceholder[];
  const payload = payloadOf(frame);
  const executionId = typeof payload.execution_id === 'string' ? payload.execution_id : undefined;
  const agentId =
    typeof payload.agent_member_id === 'string' ? payload.agent_member_id : undefined;
  if (executionId === undefined || agentId === undefined) {
    return placeholders as ExecutionPlaceholder[];
  }
  if (placeholders.some((item) => item.execution_id === executionId)) {
    return placeholders as ExecutionPlaceholder[];
  }
  const agentName = typeof payload.agent_name === 'string' ? payload.agent_name : agentId;
  const commentId = typeof payload.comment_id === 'string' ? payload.comment_id : null;
  return [
    ...placeholders,
    {
      execution_id: executionId,
      comment_id: commentId,
      agent_id: agentId,
      agent_name: agentName,
      status: 'queued',
      failure_reason: null,
    },
  ];
}

/**
 * 执行生命周期帧 → 占位五态迁移(验收必修 3 / §9.8 / comment-inbox §4.1):
 * started→running;awaiting_approval→waiting(等待确认);failed/timeout→failed
 * (留失败占位 + 原因,配重试入口);completed/cancelled→移除占位(completed 由
 * agent 评论回流替换;cancelled 为主动取消,不再占位)。其余帧原样返回。
 */
export function applyExecutionLifecycleFrame(
  placeholders: readonly ExecutionPlaceholder[],
  frame: RealtimeEventFrame,
): ExecutionPlaceholder[] {
  const payload = payloadOf(frame);
  const executionId = typeof payload.execution_id === 'string' ? payload.execution_id : undefined;
  if (executionId === undefined) return placeholders as ExecutionPlaceholder[];
  switch (frame.event) {
    case 'execution.started':
      return patchPlaceholder(placeholders, executionId, { status: 'running' });
    case 'execution.awaiting_approval':
      return patchPlaceholder(placeholders, executionId, { status: 'waiting' });
    case 'execution.failed':
    case 'execution.timeout': {
      const reason = typeof payload.failure_reason === 'string' ? payload.failure_reason : null;
      return patchPlaceholder(placeholders, executionId, { status: 'failed', failure_reason: reason });
    }
    case 'execution.completed':
    case 'execution.cancelled':
      return placeholders.filter((item) => item.execution_id !== executionId);
    default:
      return placeholders as ExecutionPlaceholder[];
  }
}

function patchPlaceholder(
  placeholders: readonly ExecutionPlaceholder[],
  executionId: string,
  patch: Partial<Pick<ExecutionPlaceholder, 'status' | 'failure_reason'>>,
): ExecutionPlaceholder[] {
  if (!placeholders.some((item) => item.execution_id === executionId)) {
    return placeholders as ExecutionPlaceholder[];
  }
  return placeholders.map((item) =>
    item.execution_id === executionId ? { ...item, ...patch } : item,
  );
}

/**
 * agent 评论回流(comment.created 且 author.member_type=agent)后,
 * 移除该 agent 的占位卡片(经 comment.created 推送替换为真实评论)。
 */
export function clearPlaceholdersForAgentComment(
  placeholders: readonly ExecutionPlaceholder[],
  frame: RealtimeEventFrame,
): ExecutionPlaceholder[] {
  if (frame.event !== 'comment.created') return placeholders as ExecutionPlaceholder[];
  const payload = payloadOf(frame);
  const candidate = payload.comment ?? payload;
  if (!isComment(candidate)) return placeholders as ExecutionPlaceholder[];
  const author = candidate.author;
  if (author === null || author.member_type !== 'agent') return placeholders as ExecutionPlaceholder[];
  const next = placeholders.filter((item) => item.agent_id !== author.id);
  return next.length === placeholders.length ? (placeholders as ExecutionPlaceholder[]) : next;
}
