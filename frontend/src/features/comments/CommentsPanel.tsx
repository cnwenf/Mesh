/**
 * 评论区面板(comment-inbox.md §4.1):系统活动行(author_kind='system',灰色小字)与
 * 评论卡片按时间穿插;线程单层折叠「N 条回复 ▸」;执行占位卡片「⏳ {name} 正在执行…」;
 * 深链锚点 #comment-{id} 高亮;底部 composer(顶层 / 回复)。数据与乐观操作经 useCommentsData。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { ErrorState, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { formatRelativeTime, useT } from '../../i18n';
import { CommentCard } from './CommentCard';
import { CommentComposer } from './CommentComposer';
import { listReplies } from './api';
import type { MentionCandidate } from './mentions';
import type { Comment, CommentMemberRef } from './types';
import { useCommentsData } from './useCommentsData';
import type { SubmitOptions } from './useCommentsData';
import './comment.css';

export interface CommentsPanelProps {
  readonly issueId: string;
  readonly workspaceId: string;
  readonly locale: string;
  readonly candidates: readonly MentionCandidate[];
  readonly currentMember: CommentMemberRef | null;
}

function readAnchorId(): string | null {
  const hash = window.location.hash;
  const match = /^#comment-(.+)$/.exec(hash);
  return match !== null ? match[1] : null;
}

export function CommentsPanel(props: CommentsPanelProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const {
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
  } = useCommentsData(props.issueId, props.currentMember);

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [loadedReplies, setLoadedReplies] = useState<Record<string, readonly Comment[]>>({});
  const [replyTarget, setReplyTarget] = useState<Comment | null>(null);
  const [highlightedId, setHighlightedId] = useState<string | null>(readAnchorId);

  // 深链锚点:初始 + hashchange 同步高亮 id。
  useEffect(() => {
    const onHashChange = (): void => setHighlightedId(readAnchorId());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  useEffect(() => {
    if (highlightedId === null) return;
    const element = window.document.getElementById(`comment-${highlightedId}`);
    element?.scrollIntoView({ block: 'center' });
  }, [highlightedId, comments]);

  const toggleThread = useCallback(
    (root: Comment) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(root.id)) next.delete(root.id);
        else next.add(root.id);
        return next;
      });
      if (loadedReplies[root.id] === undefined) {
        void (async () => {
          try {
            const page = await listReplies(client, root.id, { limit: 50 });
            setLoadedReplies((prev) => ({ ...prev, [root.id]: page.data }));
          } catch {
            // 拉取失败时退回 preview_replies(已有),不中断展开。
          }
        })();
      }
    },
    [client, loadedReplies],
  );

  const handleCopyLink = useCallback(
    (comment: Comment) => {
      const url = `${window.location.origin}${window.location.pathname}#comment-${comment.id}`;
      void navigator.clipboard?.writeText(url).then(
        () => toast.addToast(t('comments.linkCopied'), { tone: 'success', closeLabel: t('common.close') }),
        () => toast.addToast(t('comments.linkCopyFailed'), { tone: 'danger', closeLabel: t('common.close') }),
      );
    },
    [toast, t],
  );

  const handleSubmit = useCallback(
    async (body: string, opts: SubmitOptions): Promise<void> => {
      if (replyTarget !== null) await createReply(replyTarget, body, opts);
      else await createTopLevel(body, opts);
      setReplyTarget(null);
    },
    [replyTarget, createReply, createTopLevel],
  );

  const canModify = useCallback(
    (comment: Comment): boolean =>
      props.currentMember !== null && comment.author !== null && comment.author.id === props.currentMember.id,
    [props.currentMember],
  );

  const handleReply = useCallback((target: Comment) => {
    const rootId = target.thread_root_id ?? target.id;
    setReplyTarget(target);
    // 回复时展开所属线程,既看到已有回复又露出回复输入框。
    setExpanded((prev) => {
      const next = new Set(prev);
      next.add(rootId);
      return next;
    });
  }, []);

  if (error !== null) {
    return (
      <ErrorState
        title={t('state.errorTitle')}
        description={t(error)}
        retryLabel={t('common.retry')}
        onRetry={reload}
      />
    );
  }

  const renderCard = (comment: Comment): React.JSX.Element => (
    <CommentCard
      key={comment.id}
      comment={comment}
      workspaceId={props.workspaceId}
      locale={props.locale}
      highlighted={highlightedId === comment.id}
      canModify={canModify(comment)}
      onReply={handleReply}
      onResolve={(target) => void setResolved(target, true)}
      onReopen={(target) => void setResolved(target, false)}
      onToggleReaction={(target, emoji) => void toggleReaction(target, emoji)}
      onAddReaction={(target, emoji) => void toggleReaction(target, emoji)}
      onSaveEdit={async (target, body) => {
        await saveEdit(target, body);
      }}
      onDelete={(target) => void remove(target)}
      onCopyLink={handleCopyLink}
    />
  );

  const renderThread = (root: Comment): React.JSX.Element => {
    const isExpanded = expanded.has(root.id);
    const replies = loadedReplies[root.id] ?? root.preview_replies ?? [];
    return (
      <div className="mesh-comments__thread" key={root.id} data-testid={`thread-${root.id}`}>
        {renderCard(root)}
        {root.reply_count > 0 ? (
          <button
            type="button"
            className="mesh-comments__thread-toggle"
            data-testid={`thread-toggle-${root.id}`}
            aria-expanded={isExpanded}
            onClick={() => toggleThread(root)}
          >
            {t('comments.thread.toggle', { count: root.reply_count })}
          </button>
        ) : null}
        {isExpanded ? (
          <div className="mesh-comments__thread-replies" data-testid={`thread-replies-${root.id}`}>
            {replies.map((reply) => renderCard(reply))}
          </div>
        ) : null}
        {/* 回复输入框:对该线程根处于回复态时常驻(不依赖是否已展开),即使尚无回复也可回复。 */}
        {replyTarget !== null && (replyTarget.thread_root_id ?? replyTarget.id) === root.id ? (
          <CommentComposer
            draftKey={`${props.issueId}:reply:${root.id}`}
            candidates={props.candidates}
            replyToName={replyTarget.author?.name ?? null}
            onSubmit={handleSubmit}
            autoFocus
          />
        ) : null}
      </div>
    );
  };

  return (
    <section className="mesh-comments" aria-label={t('comments.title')} data-testid="comments-panel">
      <h2 className="mesh-comments__heading">{t('comments.title')}</h2>
      {isLoading ? (
        <Skeleton loadingLabel={t('common.loading')} className="mesh-comments__skeleton" />
      ) : (
        <div className="mesh-comments__timeline" data-testid="comments-timeline">
          {comments.length === 0 ? (
            <p className="mesh-comments__empty" data-testid="comments-empty">
              {t('comments.empty')}
            </p>
          ) : (
            comments.map((comment) =>
              comment.author_kind === 'system' ? (
                <div className="mesh-comments__activity" key={comment.id} data-testid={`activity-${comment.id}`}>
                  <span>{comment.body_text}</span>
                  <time>{formatRelativeTime(comment.created_at, { locale: props.locale })}</time>
                </div>
              ) : (
                renderThread(comment)
              ),
            )
          )}
          {placeholders.map((placeholder) => (
            <div
              className="mesh-comments__executing"
              key={placeholder.execution_id}
              data-testid={`executing-${placeholder.execution_id}`}
            >
              ⏳ {t('comments.executing', { name: placeholder.agent_name })}
            </div>
          ))}
        </div>
      )}

      {!isLoading ? (
        <CommentComposer
          draftKey={props.issueId}
          candidates={props.candidates}
          onSubmit={handleSubmit}
        />
      ) : null}
    </section>
  );
}
