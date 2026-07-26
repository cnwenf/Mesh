/**
 * 评论数据 + 乐观操作 hook(comment-inbox.md §4.3):首屏拉取(顶层 + 预览回复)、
 * issue:{id} 频道实时合并(comment.* / reaction.* / execution.*)、以及
 * 发表/回复/反应/解决/删除的乐观更新(失败回滚)。视图层(CommentsPanel)消费本 hook。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { env } from '../../env';
import { useRealtimeContext } from '../../shell/AppShell';
import {
  addReaction as addReactionApi,
  createComment,
  deleteComment,
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
  clearPlaceholdersForAgentComment,
} from './realtime';
import type { ExecutionPlaceholder } from './realtime';
import type { Comment, CommentMemberRef, ReactionSummary } from './types';

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
  return LOCAL_ID_PREFIX + crypto.randomUUID();
}

export interface SubmitOptions {
  readonly suppressTriggers: boolean;
}

export interface UseCommentsData {
  readonly comments: readonly Comment[];
  readonly isLoading: boolean;
  readonly error: string | null;
  readonly placeholders: readonly ExecutionPlaceholder[];
  readonly reload: () => void;
  readonly createTopLevel: (body: string, opts: SubmitOptions) => Promise<Comment>;
  readonly createReply: (parent: Comment, body: string, opts: SubmitOptions) => Promise<Comment>;
  readonly toggleReaction: (comment: Comment, emoji: string) => Promise<void>;
  readonly setResolved: (comment: Comment, resolved: boolean) => Promise<void>;
  readonly remove: (comment: Comment) => Promise<void>;
  /** 编辑评论(If-Match: updated_at 乐观锁;409 conflict 抛给调用方提示)。 */
  readonly saveEdit: (comment: Comment, bodyMarkdown: string) => Promise<Comment>;
}

export function useCommentsData(
  issueId: string,
  currentMember: CommentMemberRef | null,
): UseCommentsData {
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();
  const [comments, setComments] = useState<Comment[]>([]);
  const [placeholders, setPlaceholders] = useState<ExecutionPlaceholder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

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

  const remove = useCallback(
    async (comment: Comment): Promise<void> => {
      const snapshot = comments;
      const now = new Date().toISOString();
      setComments((prev) =>
        patchCommentById(prev, comment.id, (target) => ({
          ...target,
          deleted_at: now,
          body_markdown: '',
          body_html: '',
          body_text: '',
        })),
      );
      try {
        await deleteComment(client, comment.id);
      } catch {
        setComments(snapshot);
      }
    },
    [client, comments],
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
    reload,
    createTopLevel,
    createReply,
    toggleReaction,
    setResolved,
    remove,
    saveEdit,
  };
}
