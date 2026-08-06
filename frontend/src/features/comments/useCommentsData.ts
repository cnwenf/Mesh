/**
 * 评论数据 + 乐观操作 hook(comment-inbox.md §4.3):首屏拉取(顶层 + 预览回复)、
 * issue:{id} 频道实时合并(comment.* / reaction.* / execution.*)、以及
 * 发表/回复/反应/解决/删除的乐观更新(失败回滚)。视图层(CommentsPanel)消费本 hook。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { uuidv4 } from '../../api/uuid';
import { useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import type { RealtimeEventFrame } from '../../types/realtime';
import {
  addReaction as addReactionApi,
  createComment,
  deleteComment,
  getComment,
  issueChannel,
  listCommentIssueExecutions,
  listComments,
  listReplies,
  removeReaction as removeReactionApi,
  reopenThread,
  resolveThread,
  updateComment,
} from './api';
import {
  applyCommentsFrame,
  applyExecutionFrame,
  clearPlaceholdersForAgentComment,
  executionChannel,
  restoreExecutionPlaceholders,
} from './realtime';
import type { ExecutionPlaceholder } from './realtime';
import { isComment } from './types';
import type { Comment, CommentExecutionSnapshot, CommentMemberRef, ReactionSummary } from './types';
import { UNDO_WINDOW_MS, useDeferredDelete } from './useDeferredDelete';

/** 纯函数:按 id 在顶层列表(含内嵌 preview_replies)中 patch 一条评论;未命中原样返回。 */
export function patchCommentById(
  comments: readonly Comment[],
  id: string,
  patch: (comment: Comment) => Comment,
): Comment[] {
  let changed = false;
  const next = comments.map((comment) => {
    if (comment.id === id) {
      changed = true;
      return patch(comment);
    }
    if (comment.preview_replies === undefined) return comment;
    let nestedChanged = false;
    const replies = comment.preview_replies.map((reply) => {
      if (reply.id !== id) return reply;
      nestedChanged = true;
      return patch(reply);
    });
    if (nestedChanged) {
      changed = true;
      return { ...comment, preview_replies: replies };
    }
    return comment;
  });
  return changed ? next : (comments as Comment[]);
}

/**
 * 纯函数:按 id 从顶层列表(含内嵌 preview_replies)中移除一条评论。
 * 顶层命中 → 直接过滤;嵌套命中 → 从所属线程根 preview_replies 移除并递减 reply_count。
 * 未命中原样返回(同引用)。保留为列表归并工具；删除状态机本身使用 tombstone。
 */
export function removeCommentById(comments: readonly Comment[], id: string): Comment[] {
  if (comments.some((comment) => comment.id === id)) {
    return comments.filter((comment) => comment.id !== id);
  }
  let changed = false;
  const next = comments.map((comment) => {
    if (comment.preview_replies === undefined) return comment;
    if (!comment.preview_replies.some((reply) => reply.id === id)) return comment;
    changed = true;
    return {
      ...comment,
      preview_replies: comment.preview_replies.filter((reply) => reply.id !== id),
      reply_count: Math.max(0, comment.reply_count - 1),
    };
  });
  return changed ? next : (comments as Comment[]);
}

/** 纯函数:乐观切换某 emoji 的反应聚合(增/减自己的一份)。 */
export function toggleReactionLocal(
  reactions: readonly ReactionSummary[],
  emoji: string,
  me: CommentMemberRef,
): ReactionSummary[] {
  const existing = reactions.find((reaction) => reaction.emoji === emoji);
  if (existing === undefined) {
    return [...reactions, { emoji, count: 1, reacted_by_me: true, actors: [me] }];
  }
  if (existing.reacted_by_me) {
    const actors = existing.actors.filter((actor) => actor.id !== me.id);
    const count = Math.max(0, existing.count - 1);
    if (count === 0) return reactions.filter((reaction) => reaction.emoji !== emoji);
    return reactions.map((reaction) =>
      reaction.emoji === emoji ? { ...reaction, count, reacted_by_me: false, actors } : reaction,
    );
  }
  return reactions.map((reaction) =>
    reaction.emoji === emoji
      ? {
          ...reaction,
          count: reaction.count + 1,
          reacted_by_me: true,
          actors: [...reaction.actors, me],
        }
      : reaction,
  );
}

const LOCAL_ID_PREFIX = 'local-';

function localId(): string {
  // uuidv4 而非裸 crypto.randomUUID():后者在 HTTP 非安全上下文缺失(MES-129)。
  return LOCAL_ID_PREFIX + uuidv4();
}

function mergeServerSnapshot(current: Comment, incoming: Comment): Comment {
  const newer =
    current.id === incoming.id && incoming.updated_at < current.updated_at ? current : incoming;
  return {
    ...current,
    ...newer,
    id: incoming.id,
    reply_count: incoming.reply_count ?? current.reply_count,
    preview_replies: incoming.preview_replies ?? current.preview_replies,
    delivery_state: 'sent',
    client_request_id: undefined,
    suppress_triggers: undefined,
  };
}

function reconcileTopLevel(
  comments: readonly Comment[],
  optimisticId: string,
  incoming: Comment,
): Comment[] {
  const serverCopy = comments.find((comment) => comment.id === incoming.id);
  const optimistic = comments.find((comment) => comment.id === optimisticId);
  const canonical = mergeServerSnapshot(serverCopy ?? optimistic ?? incoming, incoming);
  const next: Comment[] = [];
  let inserted = false;
  for (const comment of comments) {
    if (comment.id === optimisticId || comment.id === incoming.id) {
      if (!inserted) {
        next.push(canonical);
        inserted = true;
      }
    } else {
      next.push(comment);
    }
  }
  if (!inserted) next.push(canonical);
  return next;
}

function reconcileReply(root: Comment, optimisticId: string, incoming: Comment): Comment {
  const replies = root.preview_replies ?? [];
  const serverCopy = replies.find((reply) => reply.id === incoming.id);
  const optimistic = replies.find((reply) => reply.id === optimisticId);
  const canonical = mergeServerSnapshot(serverCopy ?? optimistic ?? incoming, incoming);
  const next: Comment[] = [];
  let inserted = false;
  let matched = 0;
  for (const reply of replies) {
    if (reply.id === optimisticId || reply.id === incoming.id) {
      matched += 1;
      if (!inserted) {
        next.push(canonical);
        inserted = true;
      }
    } else {
      next.push(reply);
    }
  }
  if (!inserted) next.push(canonical);
  return {
    ...root,
    preview_replies: next,
    reply_count: Math.max(next.length, root.reply_count - Math.max(0, matched - 1)),
  };
}

function failedSubmissionMatches(
  comment: Comment,
  body: string,
  parentId: string | null,
  suppressTriggers: boolean,
): boolean {
  return (
    comment.delivery_state === 'failed' &&
    comment.body_markdown === body &&
    comment.parent_id === parentId &&
    comment.suppress_triggers === suppressTriggers
  );
}

export interface SubmitOptions {
  readonly suppressTriggers: boolean;
}

export interface UseCommentsData {
  readonly comments: readonly Comment[];
  readonly isLoading: boolean;
  readonly error: string | null;
  readonly placeholders: readonly ExecutionPlaceholder[];
  /** 失败占位重试(§9.8/comment-inbox §4.1):重发触发评论以重新入队执行。 */
  readonly retryExecution: (executionId: string) => Promise<void>;
  readonly reload: () => void;
  readonly createTopLevel: (body: string, opts: SubmitOptions) => Promise<Comment>;
  readonly createReply: (parent: Comment, body: string, opts: SubmitOptions) => Promise<Comment>;
  readonly retrySend: (comment: Comment) => Promise<Comment>;
  /** 深链目标不在首屏时按单条/线程回补，供视图展开并定位。 */
  readonly locateComment: (commentId: string) => Promise<void>;
  /** 将完整回复列表并入线程实体；之后 WS patch 与页面读取共享同一状态源。 */
  readonly loadReplies: (root: Comment) => Promise<void>;
  readonly toggleReaction: (comment: Comment, emoji: string) => Promise<void>;
  readonly setResolved: (comment: Comment, resolved: boolean) => Promise<void>;
  /** 延迟删除(§9.5.5):乐观 tombstone + 撤销 toast;窗口到期才真正调用 DELETE。 */
  readonly remove: (comment: Comment) => void;
  /** 编辑评论(If-Match: updated_at 乐观锁;409 conflict 抛给调用方提示)。 */
  readonly saveEdit: (comment: Comment, bodyMarkdown: string) => Promise<Comment>;
}

export function useCommentsData(
  issueId: string,
  currentMember: CommentMemberRef | null,
  workspaceId?: string,
): UseCommentsData {
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();
  const toast = useToast();
  const t = useT();
  const [comments, setComments] = useState<Comment[]>([]);
  const [placeholders, setPlaceholders] = useState<ExecutionPlaceholder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const commentsRef = useRef<readonly Comment[]>(comments);
  commentsRef.current = comments;
  const pendingRequestIdsRef = useRef<Set<string>>(new Set());
  const realtimeCommitsRef = useRef<Map<string, Comment>>(new Map());
  // REST 首屏在途时到达的帧，待快照返回后依序重放，避免旧 REST 覆盖新实时态。
  const placeholderFramesRef = useRef<RealtimeEventFrame[]>([]);
  // 延迟删除快照:乐观 tombstone 前的完整列表,撤销/失败时整体恢复。
  const deleteSnapshotRef = useRef<readonly Comment[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const replayFrom = placeholderFramesRef.current.length;
    setIsLoading(true);
    setError(null);
    void (async () => {
      try {
        const page = await listComments(client, issueId, {
          include: 'replies',
          order: 'asc',
          limit: 50,
        });
        let executionSnapshots: CommentExecutionSnapshot[] | null = null;
        if (workspaceId !== undefined) {
          executionSnapshots = [];
          try {
            let cursor: string | undefined;
            do {
              const executionPage = await listCommentIssueExecutions(client, workspaceId, issueId, {
                limit: 100,
                cursor,
              });
              executionSnapshots.push(...executionPage.data);
              cursor = executionPage.nextCursor ?? undefined;
            } while (cursor !== undefined);
          } catch {
            // 评论仍可用；执行快照失败时继续依赖已订阅的 issue/execution 帧。
            executionSnapshots = null;
          }
        }
        if (cancelled) return;
        setComments([...page.data]);
        if (executionSnapshots !== null) {
          let restored = restoreExecutionPlaceholders(page.data, executionSnapshots, issueId);
          for (const frame of placeholderFramesRef.current.slice(replayFrom)) {
            restored = clearPlaceholdersForAgentComment(
              applyExecutionFrame(restored, frame),
              frame,
            );
          }
          setPlaceholders(restored);
        }
        // All frames observed during this request are now represented either by
        // the replayed snapshot or by their already-applied state updates.
        placeholderFramesRef.current = [];
      } catch (err: unknown) {
        if (cancelled) return;
        setError(err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription');
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, issueId, reloadKey, workspaceId]);

  // issue:{id} 频道实时合并(与详情页共享频道;Set 去重,生命周期随面板)。
  useEffect(() => {
    if (realtime === null) return;
    const channel = issueChannel(issueId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      if (frame.event.startsWith('execution.') || frame.event === 'comment.created') {
        placeholderFramesRef.current.push(frame);
      }
      if (frame.event === 'comment.created') {
        const nested = frame.payload.comment;
        const candidate = isComment(nested) ? nested : frame.payload;
        if (
          isComment(candidate) &&
          typeof candidate.client_request_id === 'string' &&
          pendingRequestIdsRef.current.has(candidate.client_request_id)
        ) {
          // Record before scheduling React state so an immediately lost HTTP
          // response can still resolve from the already-committed WS entity.
          realtimeCommitsRef.current.set(candidate.client_request_id, candidate);
        }
      }
      setComments((prev) => applyCommentsFrame(prev, frame));
      setPlaceholders((prev) =>
        clearPlaceholdersForAgentComment(applyExecutionFrame(prev, frame), frame),
      );
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, issueId]);

  // execution:{id} 是终态/重排补充频道。非终态在 issue 频道已直接驱动同一
  // 状态机；逐 execution 订阅确保 terminal 即使 issue 投影延迟也能及时收敛。
  const placeholderIdsKey = placeholders.map((item) => item.execution_id).join('|');
  useEffect(() => {
    if (realtime === null || placeholders.length === 0) return;
    const channels = placeholders.map((item) => executionChannel(item.execution_id));
    channels.forEach((ch) => realtime.client.subscribe(ch));
    const offFrames = realtime.client.onFrame((frame) => {
      if (!channels.includes(frame.channel)) return;
      placeholderFramesRef.current.push(frame);
      setPlaceholders((prev) => applyExecutionFrame(prev, frame));
    });
    return () => {
      offFrames();
      channels.forEach((ch) => realtime.client.unsubscribe(ch));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅按占位 id 集合重订阅,函数式更新不依赖 placeholders 引用
  }, [realtime, placeholderIdsKey]);

  // 失败占位重试:取回触发评论原文并以相同正文/线程重发(§6.9 每条评论的
  // agent 提及触发一次执行 → 重发即重新入队;新 execution.queued 会新增占位)。
  const placeholdersRef = useRef(placeholders);
  placeholdersRef.current = placeholders;
  const retryExecution = useCallback(
    async (executionId: string) => {
      const placeholder = placeholdersRef.current.find((item) => item.execution_id === executionId);
      if (placeholder === undefined || placeholder.comment_id === null) return;
      try {
        const original = await getComment(client, placeholder.comment_id);
        await createComment(client, issueId, {
          body_markdown: original.body_markdown,
          parent_id: original.parent_id,
        });
        // 移除失败占位;新执行的 queued 帧会追加新占位。
        setPlaceholders((prev) => prev.filter((item) => item.execution_id !== executionId));
      } catch (err: unknown) {
        toast.addToast(
          t(err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError'),
          {
            tone: 'danger',
            closeLabel: t('common.close'),
          },
        );
      }
    },
    [client, issueId, toast, t],
  );

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const buildOptimistic = useCallback(
    (
      body: string,
      parentId: string | null,
      threadRootId: string | null,
      opts: SubmitOptions,
    ): Comment => {
      const now = new Date().toISOString();
      return {
        id: localId(),
        issue_id: issueId,
        parent_id: parentId,
        thread_root_id: threadRootId,
        author_kind: 'member',
        author: currentMember,
        body_markdown: body,
        body_html: `<p>${body.replace(/</g, '&lt;')}</p>`,
        body_text: body,
        reactions: [],
        reply_count: 0,
        resolved_at: null,
        resolved_by: null,
        mentions: [],
        triggered_execution_ids: [],
        deleted_at: null,
        created_at: now,
        updated_at: now,
        edited_at: null,
        delivery_state: 'sending',
        client_request_id: uuidv4(),
        suppress_triggers: opts.suppressTriggers,
      };
    },
    [currentMember, issueId],
  );

  const sendTopLevel = useCallback(
    async (optimistic: Comment): Promise<Comment> => {
      const requestId = optimistic.client_request_id;
      if (typeof requestId === 'string') pendingRequestIdsRef.current.add(requestId);
      setComments((prev) =>
        patchCommentById(prev, optimistic.id, (target) => ({
          ...target,
          delivery_state: 'sending',
        })),
      );
      try {
        const created = await createComment(
          client,
          issueId,
          {
            body_markdown: optimistic.body_markdown,
            suppress_triggers: optimistic.suppress_triggers ?? false,
          },
          requestId ?? undefined,
        );
        if (typeof requestId === 'string') {
          pendingRequestIdsRef.current.delete(requestId);
          realtimeCommitsRef.current.delete(requestId);
        }
        setComments((prev) => reconcileTopLevel(prev, optimistic.id, created));
        return created;
      } catch (err) {
        const committed =
          typeof requestId === 'string' ? realtimeCommitsRef.current.get(requestId) : undefined;
        if (typeof requestId === 'string') {
          pendingRequestIdsRef.current.delete(requestId);
          realtimeCommitsRef.current.delete(requestId);
        }
        if (committed !== undefined) return committed;
        setComments((prev) =>
          patchCommentById(prev, optimistic.id, (target) => ({
            ...target,
            delivery_state: 'failed',
          })),
        );
        throw err;
      }
    },
    [client, issueId],
  );

  const createTopLevel = useCallback(
    async (body: string, opts: SubmitOptions): Promise<Comment> => {
      const failed = commentsRef.current.find((comment) =>
        failedSubmissionMatches(comment, body, null, opts.suppressTriggers),
      );
      if (failed !== undefined) return sendTopLevel(failed);

      const optimistic = buildOptimistic(body, null, null, opts);
      setComments((prev) => [...prev, optimistic]);
      return sendTopLevel(optimistic);
    },
    [buildOptimistic, sendTopLevel],
  );

  const sendReply = useCallback(
    async (rootId: string, optimistic: Comment): Promise<Comment> => {
      const requestId = optimistic.client_request_id;
      if (typeof requestId === 'string') pendingRequestIdsRef.current.add(requestId);
      setComments((prev) =>
        patchCommentById(prev, optimistic.id, (target) => ({
          ...target,
          delivery_state: 'sending',
        })),
      );
      try {
        const created = await createComment(
          client,
          issueId,
          {
            body_markdown: optimistic.body_markdown,
            parent_id: rootId,
            suppress_triggers: optimistic.suppress_triggers ?? false,
          },
          requestId ?? undefined,
        );
        if (typeof requestId === 'string') {
          pendingRequestIdsRef.current.delete(requestId);
          realtimeCommitsRef.current.delete(requestId);
        }
        setComments((prev) =>
          patchCommentById(prev, rootId, (root) => reconcileReply(root, optimistic.id, created)),
        );
        return created;
      } catch (err) {
        const committed =
          typeof requestId === 'string' ? realtimeCommitsRef.current.get(requestId) : undefined;
        if (typeof requestId === 'string') {
          pendingRequestIdsRef.current.delete(requestId);
          realtimeCommitsRef.current.delete(requestId);
        }
        if (committed !== undefined) return committed;
        setComments((prev) =>
          patchCommentById(prev, optimistic.id, (target) => ({
            ...target,
            delivery_state: 'failed',
          })),
        );
        throw err;
      }
    },
    [client, issueId],
  );

  const createReply = useCallback(
    async (parent: Comment, body: string, opts: SubmitOptions): Promise<Comment> => {
      const rootId = parent.thread_root_id ?? parent.id;
      const root = commentsRef.current.find((comment) => comment.id === rootId);
      const failed = root?.preview_replies?.find((reply) =>
        failedSubmissionMatches(reply, body, rootId, opts.suppressTriggers),
      );
      if (failed !== undefined) return sendReply(rootId, failed);

      const optimistic = buildOptimistic(body, rootId, rootId, opts);
      setComments((prev) =>
        patchCommentById(prev, rootId, (root) => ({
          ...root,
          preview_replies: [...(root.preview_replies ?? []), optimistic],
          reply_count: root.reply_count + 1,
        })),
      );
      return sendReply(rootId, optimistic);
    },
    [buildOptimistic, sendReply],
  );

  const retrySend = useCallback(
    async (comment: Comment): Promise<Comment> => {
      if (comment.delivery_state !== 'failed') return comment;
      const retryable =
        comment.client_request_id == null ? { ...comment, client_request_id: uuidv4() } : comment;
      const rootId = retryable.thread_root_id ?? retryable.parent_id;
      return rootId === null ? sendTopLevel(retryable) : sendReply(rootId, retryable);
    },
    [sendReply, sendTopLevel],
  );

  const loadReplies = useCallback(
    async (root: Comment): Promise<void> => {
      const fetched: Comment[] = [];
      let cursor: string | undefined;
      do {
        const page = await listReplies(client, root.id, { limit: 200, cursor });
        fetched.push(...page.data);
        cursor = page.nextCursor ?? undefined;
      } while (cursor !== undefined);

      setComments((prev) =>
        patchCommentById(prev, root.id, (currentRoot) => {
          const currentReplies = currentRoot.preview_replies ?? [];
          const currentById = new Map(currentReplies.map((reply) => [reply.id, reply]));
          const merged = fetched.map((reply) => {
            const current = currentById.get(reply.id);
            currentById.delete(reply.id);
            return current === undefined ? reply : mergeServerSnapshot(current, reply);
          });
          merged.push(...currentById.values());
          return {
            ...currentRoot,
            preview_replies: merged,
            reply_count: Math.max(currentRoot.reply_count, merged.length),
          };
        }),
      );
    },
    [client],
  );

  const locateComment = useCallback(
    async (commentId: string): Promise<void> => {
      for (const comment of commentsRef.current) {
        if (
          comment.id === commentId ||
          comment.preview_replies?.some((reply) => reply.id === commentId) === true
        ) {
          return;
        }
      }

      const target = await getComment(client, commentId);
      if (target.issue_id !== issueId) return;
      const rootId = target.thread_root_id ?? target.parent_id;
      if (rootId === null) {
        setComments((prev) =>
          prev.some((comment) => comment.id === target.id) ? prev : [...prev, target],
        );
        return;
      }

      const root = await getComment(client, rootId);
      if (root.issue_id !== issueId) return;
      const replies: Comment[] = [];
      let cursor: string | undefined;
      do {
        const page = await listReplies(client, root.id, { limit: 200, cursor });
        replies.push(...page.data);
        cursor = page.nextCursor ?? undefined;
      } while (cursor !== undefined);
      if (!replies.some((reply) => reply.id === target.id)) replies.push(target);

      setComments((prev) => {
        const hydratedRoot: Comment = {
          ...root,
          preview_replies: replies,
          reply_count: Math.max(root.reply_count, replies.length),
        };
        const index = prev.findIndex((comment) => comment.id === root.id);
        if (index === -1) return [...prev, hydratedRoot];
        return prev.map((comment, current) =>
          current === index ? mergeServerSnapshot(comment, hydratedRoot) : comment,
        );
      });
    },
    [client, issueId],
  );

  const toggleReaction = useCallback(
    async (comment: Comment, emoji: string): Promise<void> => {
      if (currentMember === null) return;
      const reacted = comment.reactions.find((reaction) => reaction.emoji === emoji)?.reacted_by_me;
      const snapshot = comments;
      setComments((prev) =>
        patchCommentById(prev, comment.id, (target) => ({
          ...target,
          reactions: toggleReactionLocal(target.reactions, emoji, currentMember),
        })),
      );
      try {
        if (reacted === true) await removeReactionApi(client, comment.id, emoji);
        else await addReactionApi(client, comment.id, emoji);
      } catch {
        setComments(snapshot);
      }
    },
    [client, comments, currentMember],
  );

  const setResolved = useCallback(
    async (comment: Comment, resolved: boolean): Promise<void> => {
      const snapshot = comments;
      try {
        const updated = resolved
          ? await resolveThread(client, comment.id)
          : await reopenThread(client, comment.id);
        setComments((prev) =>
          patchCommentById(prev, comment.id, (current) => mergeServerSnapshot(current, updated)),
        );
      } catch {
        setComments(snapshot);
      }
    },
    [client, comments],
  );

  // 延迟删除状态机(§9.5.5):窗口到期才真正 DELETE;失败经 onFailed 回滚 + 危险提示。
  const deferredDelete = useDeferredDelete<Comment>({
    windowMs: UNDO_WINDOW_MS,
    commit: (comment) => deleteComment(client, comment.id),
    onCommitted: () => {
      deleteSnapshotRef.current = null;
    },
    onFailed: () => {
      if (deleteSnapshotRef.current !== null) setComments([...deleteSnapshotRef.current]);
      deleteSnapshotRef.current = null;
      // 错误四部分(§7.7):发生了什么 + 影响(评论仍可见)+ 恢复动作(重试)。
      toast.addToast(t('comments.deleteFailed'), { tone: 'danger', closeLabel: t('common.close') });
    },
  });
  const { request: requestDelete, undo: undoDelete, pending: pendingDelete } = deferredDelete;

  const remove = useCallback(
    (comment: Comment): void => {
      // 双重删除守卫:已有一条待删除则忽略。
      if (pendingDelete !== null) return;
      deleteSnapshotRef.current = comments;
      const deletedAt = new Date().toISOString();
      setComments((prev) =>
        patchCommentById(prev, comment.id, (target) => ({
          ...target,
          body_markdown: '',
          body_html: '',
          body_text: '',
          deleted_at: deletedAt,
          updated_at: deletedAt,
        })),
      );
      requestDelete(comment);
      // 撤销 toast:与撤销窗口等长,action 在窗口内可恢复。
      toast.addToast(t('comments.deletedToast'), {
        tone: 'info',
        closeLabel: t('common.close'),
        actionLabel: t('comments.undo'),
        durationMs: UNDO_WINDOW_MS,
        onAction: () => {
          const undone = undoDelete();
          if (undone && deleteSnapshotRef.current !== null) {
            setComments([...deleteSnapshotRef.current]);
            deleteSnapshotRef.current = null;
          }
        },
      });
    },
    [comments, pendingDelete, requestDelete, toast, t, undoDelete],
  );

  const saveEdit = useCallback(
    async (comment: Comment, bodyMarkdown: string): Promise<Comment> => {
      const updated = await updateComment(client, comment.id, bodyMarkdown, comment.updated_at);
      setComments((prev) => patchCommentById(prev, comment.id, () => updated));
      return updated;
    },
    [client],
  );

  return {
    comments,
    isLoading,
    error,
    placeholders,
    retryExecution,
    reload,
    createTopLevel,
    createReply,
    retrySend,
    locateComment,
    loadReplies,
    toggleReaction,
    setResolved,
    remove,
    saveEdit,
  };
}
