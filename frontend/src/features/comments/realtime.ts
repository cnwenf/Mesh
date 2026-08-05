/**
 * 评论模块实时帧合并(comment-inbox.md §3.6,README §6.7)。
 * 纯函数:绝不修改入参,有变化返回新数组/对象,无变化返回原引用。
 *
 * issue:{issue_id} 频道事件:comment.created / comment.updated / comment.deleted /
 * comment.resolved / reaction.changed / execution 生命周期投影。
 * comment.created 载荷为完整评论对象;updated/resolved/reaction.changed 为
 * `{id, ...变更}`(可携嵌套 `changes`);deleted 为 `{id, deleted_at?}`。
 * 防回退以 updated_at 字符串比较为闸(RFC3339 UTC 可直接比较)。
 * 顶层列表内嵌 preview_replies(回复),合并须穿透到线程内层。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { Comment, CommentExecutionSnapshot, CommentMemberRef } from './types';
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

function sameCreatedEntity(existing: Comment, candidate: Comment): boolean {
  return (
    existing.id === candidate.id ||
    (typeof candidate.client_request_id === 'string' &&
      existing.client_request_id === candidate.client_request_id)
  );
}

function delivered(candidate: Comment): Comment {
  return { ...candidate, delivery_state: 'sent', suppress_triggers: undefined };
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
    const canonical = delivered(candidate);
    if (findById(comments, candidate.id) !== undefined) return comments as Comment[];
    if (candidate.parent_id === null) {
      const correlated = comments.findIndex((comment) => sameCreatedEntity(comment, candidate));
      if (correlated === -1) return [...comments, canonical];
      return comments.map((comment, index) => (index === correlated ? canonical : comment));
    }
    const rootId = candidate.thread_root_id ?? candidate.parent_id;
    const root = comments.find((comment) => comment.id === rootId);
    if (root === undefined) return comments as Comment[];
    const preview = root.preview_replies ?? [];
    const correlated = preview.findIndex((reply) => sameCreatedEntity(reply, candidate));
    if (correlated !== -1) {
      return replaceById(comments, root.id, {
        ...root,
        preview_replies: preview.map((reply, index) => (index === correlated ? canonical : reply)),
      });
    }
    return replaceById(comments, root.id, {
      ...root,
      preview_replies: [...preview, canonical],
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
      updated_at: typeof payload.updated_at === 'string' ? payload.updated_at : existing.updated_at,
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

/** 执行终态补充频道；非终态权威投影同时经 issue:{id} 到达评论区。 */
export function executionChannel(executionId: string): string {
  return `execution:${executionId}`;
}

const PLACEHOLDER_EVENT_STATUS: Readonly<Record<string, ExecutionPlaceholderStatus>> = {
  'execution.queued': 'queued',
  'execution.requeued': 'queued',
  'execution.claimed': 'running',
  'execution.started': 'running',
  'execution.awaiting_approval': 'waiting',
  'execution.failed': 'failed',
  'execution.timeout': 'failed',
};

const PLACEHOLDER_TERMINAL_EVENTS = new Set(['execution.completed', 'execution.cancelled']);

function stringField(payload: FramePayload, key: string): string | undefined {
  return typeof payload[key] === 'string' ? payload[key] : undefined;
}

/**
 * issue/execution 频道上的 execution 生命周期 → 同一占位 upsert/迁移。
 * queued/requeued 回 queued（含 waiting 审批恢复）；claimed/started → running；
 * awaiting_approval → waiting；failed/timeout → failed；completed/cancelled → 移除。
 */
export function applyExecutionFrame(
  placeholders: readonly ExecutionPlaceholder[],
  frame: RealtimeEventFrame,
): ExecutionPlaceholder[] {
  const payload = payloadOf(frame);
  const executionId = stringField(payload, 'execution_id');
  if (executionId === undefined) return placeholders as ExecutionPlaceholder[];

  if (PLACEHOLDER_TERMINAL_EVENTS.has(frame.event)) {
    const next = placeholders.filter((item) => item.execution_id !== executionId);
    return next.length === placeholders.length ? (placeholders as ExecutionPlaceholder[]) : next;
  }

  const status = PLACEHOLDER_EVENT_STATUS[frame.event];
  if (status === undefined) return placeholders as ExecutionPlaceholder[];

  const existing = placeholders.find((item) => item.execution_id === executionId);
  const agentId = stringField(payload, 'agent_member_id') ?? stringField(payload, 'agent_id');
  const agentName = stringField(payload, 'agent_name');
  const commentId = stringField(payload, 'comment_id');
  const failureReason =
    status === 'failed' ? (stringField(payload, 'failure_reason') ?? null) : null;

  if (existing !== undefined) {
    const updated: ExecutionPlaceholder = {
      ...existing,
      agent_id: agentId ?? existing.agent_id,
      agent_name: agentName ?? existing.agent_name,
      comment_id: commentId ?? existing.comment_id,
      status,
      failure_reason: failureReason,
    };
    if (
      updated.agent_id === existing.agent_id &&
      updated.agent_name === existing.agent_name &&
      updated.comment_id === existing.comment_id &&
      updated.status === existing.status &&
      updated.failure_reason === existing.failure_reason
    ) {
      return placeholders as ExecutionPlaceholder[];
    }
    return placeholders.map((item) => (item.execution_id === executionId ? updated : item));
  }

  // A non-queued phase may be the first frame observed after reconnect. It can
  // still restore the placeholder when the issue projection supplies identity.
  if (agentId === undefined) return placeholders as ExecutionPlaceholder[];
  return [
    ...placeholders,
    {
      execution_id: executionId,
      comment_id: commentId ?? null,
      agent_id: agentId,
      agent_name: agentName ?? agentId,
      status,
      failure_reason: failureReason,
    },
  ];
}

/** 向后兼容旧调用点；所有频道现统一经过同一状态机。 */
export function applyExecutionLifecycleFrame(
  placeholders: readonly ExecutionPlaceholder[],
  frame: RealtimeEventFrame,
): ExecutionPlaceholder[] {
  return applyExecutionFrame(placeholders, frame);
}

/**
 * agent 评论回流后只清除可精确关联的单个 execution：优先使用帧/评论携带
 * 的 execution id；其次接受“该 agent 对触发评论的直接/线程回复”。没有关联
 * 信息时保持所有占位，等待 execution 终态帧，禁止按 agent 批量猜测删除。
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
  if (author === null || author.member_type !== 'agent') {
    return placeholders as ExecutionPlaceholder[];
  }

  const candidateRecord = candidate as Comment & {
    readonly execution_id?: unknown;
    readonly source_execution_id?: unknown;
  };
  const explicitExecutionId =
    stringField(payload, 'execution_id') ??
    stringField(payload, 'source_execution_id') ??
    (typeof candidateRecord.execution_id === 'string' ? candidateRecord.execution_id : undefined) ??
    (typeof candidateRecord.source_execution_id === 'string'
      ? candidateRecord.source_execution_id
      : undefined);
  if (explicitExecutionId !== undefined) {
    const next = placeholders.filter((item) => item.execution_id !== explicitExecutionId);
    return next.length === placeholders.length ? (placeholders as ExecutionPlaceholder[]) : next;
  }

  const linkedCommentIds = new Set(
    [candidate.parent_id, candidate.thread_root_id].filter((id): id is string => id !== null),
  );
  const correlated = placeholders.filter(
    (item) =>
      item.agent_id === author.id &&
      item.comment_id !== null &&
      linkedCommentIds.has(item.comment_id),
  );
  if (correlated.length !== 1) return placeholders as ExecutionPlaceholder[];
  const correlatedId = correlated[0].execution_id;
  return placeholders.filter((item) => item.execution_id !== correlatedId);
}

const ACTIVE_REST_STATUS: Readonly<Record<string, ExecutionPlaceholderStatus>> = {
  queued: 'queued',
  claimed: 'running',
  running: 'running',
  cancelling: 'running',
  awaiting_approval: 'waiting',
};

interface TriggerCorrelation {
  readonly commentId: string;
  readonly agent: CommentMemberRef | null;
}

function listedComments(comments: readonly Comment[]): Comment[] {
  const result: Comment[] = [];
  for (const comment of comments) result.push(comment, ...(comment.preview_replies ?? []));
  return result;
}

/**
 * Rebuild active placeholders from the existing issue execution REST list.
 * A comment gives an exact execution→trigger-comment relation. Member name/id
 * is used only when REST proves a single execution and single agent mention;
 * multi-agent comments deliberately fall back to execution.agent_id because
 * the current response does not expose an execution→mentioned-member mapping.
 */
export function restoreExecutionPlaceholders(
  comments: readonly Comment[],
  executions: readonly CommentExecutionSnapshot[],
  issueId: string,
): ExecutionPlaceholder[] {
  const correlations = new Map<string, TriggerCorrelation>();
  for (const comment of listedComments(comments)) {
    const agentMentions = comment.mentions.filter((mention) => mention.member_type === 'agent');
    const exactAgent =
      comment.triggered_execution_ids.length === 1 && agentMentions.length === 1
        ? agentMentions[0]
        : null;
    for (const executionId of comment.triggered_execution_ids) {
      correlations.set(executionId, { commentId: comment.id, agent: exactAgent });
    }
  }

  const seen = new Set<string>();
  const restored: ExecutionPlaceholder[] = [];
  for (const execution of executions) {
    const status = ACTIVE_REST_STATUS[execution.status];
    if (status === undefined || execution.issue_id !== issueId || seen.has(execution.id)) continue;
    seen.add(execution.id);
    const correlation = correlations.get(execution.id);
    const agentId = correlation?.agent?.id ?? execution.agent_id ?? execution.id;
    restored.push({
      execution_id: execution.id,
      comment_id: correlation?.commentId ?? null,
      agent_id: agentId,
      agent_name: correlation?.agent?.name ?? execution.agent_id ?? execution.id,
      status,
      failure_reason: null,
    });
  }
  return restored;
}
