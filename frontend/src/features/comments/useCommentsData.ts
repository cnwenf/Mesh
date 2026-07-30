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
import {
  addReaction as addReactionApi,
  createComment,
  deleteComment,
  getComment,
  issueChannel,
  listComments,
  removeReaction as removeReactionApi,
  reopenThread,
  resolveThread,
  updateComment,
} from './api';
import {
  applyCommentsFrame,
  applyExecutionFrame,
  applyExecutionLifecycleFrame,
  clearPlaceholdersForAgentComment,
  executionChannel,
} from './realtime';
import type { ExecutionPlaceholder } from './realtime';
import type { Comment, CommentMemberRef, ReactionSummary } from './types';
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
 * 未命中原样返回(同引用)。用于延迟删除的乐观隐藏(§9.5.5),撤销经快照整体恢复。
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
  readonly toggleReaction: (comment: Comment, emoji: string) => Promise<void>;
  readonly setResolved: (comment: Comment, resolved: boolean) => Promise<void>;
  /** 延迟删除(§9.5.5):乐观隐藏 + 撤销 toast;窗口到期才真正调用 DELETE。 */
  readonly remove: (comment: Comment) => void;
  /** 编辑评论(If-Match: updated_at 乐观锁;409 conflict 抛给调用方提示)。 */
  readonly saveEdit: (comment: Comment, bodyMarkdown: string) => Promise<Comment>;
}

export function useCommentsData(
  issueId: string,
  currentMember: CommentMemberRef | null,
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
  // 延迟删除快照:乐观隐藏前的完整列表,撤销/失败时整体恢复。
  const deleteSnapshotRef = useRef<readonly Comment[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void (async () => {
      try {
        const page = await listComments(client, issueId, { include: 'replies', order: 'asc', limit: 50 });
        if (cancelled) return;
        setComments([...page.data]);
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
  }, [client, issueId, reloadKey]);

  // issue:{id} 频道实时合并(与详情页共享频道;Set 去重,生命周期随面板)。
  useEffect(() => {
    if (realtime === null) return;
    const channel = issueChannel(issueId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      setComments((prev) => applyCommentsFrame(prev, frame));
      setPlaceholders((prev) => clearPlaceholdersForAgentComment(applyExecutionFrame(prev, frame), frame));
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, issueId]);

  // 执行生命周期频道订阅(验收必修 3):execution.queued 发布于 issue 频道,
  // 而 started/awaiting_approval/failed/timeout/completed 发布于 execution:{id}
  // 自身频道(后端 attempts/reaper),须按占位逐一订阅消费,驱动五态迁移,
  // 否则失败/等待确认的执行永远停在「运行中」(§9.8)。占位集合变化 → 重订阅。
  const placeholderIdsKey = placeholders.map((item) => item.execution_id).join('|');
  useEffect(() => {
    if (realtime === null || placeholders.length === 0) return;
    const channels = placeholders.map((item) => executionChannel(item.execution_id));
    channels.forEach((ch) => realtime.client.subscribe(ch));
    const offFrames = realtime.client.onFrame((frame) => {
      setPlaceholders((prev) => applyExecutionLifecycleFrame(prev, frame));
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
        toast.addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError'), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      }
    },
    [client, issueId, toast, t],
  );

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const buildOptimistic = useCallback(
    (body: string, parentId: string | null, threadRootId: string | null): Comment => {
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
      };
    },
    [currentMember, issueId],
  );

  const createTopLevel = useCallback(
    async (body: string, opts: SubmitOptions): Promise<Comment> => {
      const optimistic = buildOptimistic(body, null, null);
      setComments((prev) => [...prev, optimistic]);
      try {
        const created = await createComment(client, issueId, {
          body_markdown: body,
          suppress_triggers: opts.suppressTriggers,
        });
        setComments((prev) => prev.map((comment) => (comment.id === optimistic.id ? created : comment)));
        return created;
      } catch (err) {
        setComments((prev) => prev.filter((comment) => comment.id !== optimistic.id));
        throw err;
      }
    },
    [buildOptimistic, client, issueId],
  );

  const createReply = useCallback(
    async (parent: Comment, body: string, opts: SubmitOptions): Promise<Comment> => {
      const rootId = parent.thread_root_id ?? parent.id;
      const optimistic = buildOptimistic(body, parent.id === rootId ? parent.id : rootId, rootId);
      setComments((prev) =>
        patchCommentById(prev, rootId, (root) => ({
          ...root,
          preview_replies: [...(root.preview_replies ?? []), optimistic],
          reply_count: root.reply_count + 1,
        })),
      );
      try {
        const created = await createComment(client, issueId, {
          body_markdown: body,
          parent_id: rootId,
          suppress_triggers: opts.suppressTriggers,
        });
        setComments((prev) =>
          patchCommentById(prev, rootId, (root) => ({
            ...root,
            preview_replies: (root.preview_replies ?? []).map((reply) =>
              reply.id === optimistic.id ? created : reply,
            ),
          })),
        );
        return created;
      } catch (err) {
        setComments((prev) =>
          patchCommentById(prev, rootId, (root) => ({
            ...root,
            preview_replies: (root.preview_replies ?? []).filter((reply) => reply.id !== optimistic.id),
            reply_count: Math.max(0, root.reply_count - 1),
          })),
        );
        throw err;
      }
    },
    [buildOptimistic, client, issueId],
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
        const updated = resolved ? await resolveThread(client, comment.id) : await reopenThread(client, comment.id);
        setComments((prev) => patchCommentById(prev, comment.id, () => updated));
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
      setComments((prev) => removeCommentById(prev, comment.id));
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
    toggleReaction,
    setResolved,
    remove,
    saveEdit,
  };
}
