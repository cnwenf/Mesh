/**
 * 评论区面板(comment-inbox.md §4.1):系统活动行(author_kind='system',灰色小字)与
 * 评论卡片按时间穿插;线程单层折叠「N 条回复 ▸」;执行占位卡片「⏳ {name} 正在执行…」;
 * 深链锚点 #comment-{id} 高亮;底部 composer(顶层 / 回复)。数据与乐观操作经 useCommentsData。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ErrorState, Icon, Skeleton, useToast } from '../../design';
import { formatRelativeTime, useT } from '../../i18n';
import { CommentCard } from './CommentCard';
import { CommentComposer } from './CommentComposer';
import { RunStatus } from './RunStatus';
import type { MentionCandidate } from './mentions';
import { scrollToAndHighlight } from './scrollToAndHighlight';
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
  const {
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
  } = useCommentsData(props.issueId, props.currentMember, props.workspaceId);

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [loadedThreadIds, setLoadedThreadIds] = useState<ReadonlySet<string>>(new Set());
  const [resolvedExpanded, setResolvedExpanded] = useState(false);
  const [replyTarget, setReplyTarget] = useState<Comment | null>(null);
  const [highlightedId, setHighlightedId] = useState<string | null>(readAnchorId);
  const attemptedAnchors = useRef<Set<string>>(new Set());

  // 深链锚点:初始 + hashchange 同步高亮 id。
  useEffect(() => {
    const onHashChange = (): void => setHighlightedId(readAnchorId());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  useEffect(() => {
    attemptedAnchors.current.clear();
  }, [props.issueId]);

  // 首屏只含一页顶层评论；永久链接缺失时通过单条评论接口解析线程根，
  // 再回补完整线程，避免链接只能定位首屏或 reply preview 的内容。
  useEffect(() => {
    if (isLoading || highlightedId === null || attemptedAnchors.current.has(highlightedId)) return;
    const present = comments.some(
      (comment) =>
        comment.id === highlightedId ||
        comment.preview_replies?.some((reply) => reply.id === highlightedId) === true,
    );
    if (present) return;
    attemptedAnchors.current.add(highlightedId);
    void locateComment(highlightedId).catch(() => undefined);
  }, [comments, highlightedId, isLoading, locateComment]);

  // 永久链接优先保证目标可见：已解决区默认折叠，回复线程也默认折叠，
  // 因此要先展开其容器，再由下面的统一入口滚动并高亮。
  useEffect(() => {
    if (highlightedId === null) return;
    const root = comments.find(
      (comment) =>
        comment.id === highlightedId ||
        comment.preview_replies?.some((reply) => reply.id === highlightedId) === true,
    );
    if (root === undefined) return;
    if (root.resolved_at !== null) setResolvedExpanded(true);
    if (root.id !== highlightedId) {
      setExpanded((prev) => {
        if (prev.has(root.id)) return prev;
        return new Set(prev).add(root.id);
      });
    }
  }, [comments, highlightedId]);

  // 深链跳转与发表成功共用同一滚动 + 高亮入口(§9.5.5)。
  useEffect(() => {
    if (highlightedId === null) return;
    const element = window.document.getElementById(`comment-${highlightedId}`);
    scrollToAndHighlight(element);
  }, [highlightedId, comments, resolvedExpanded, expanded]);

  const toggleThread = useCallback(
    (root: Comment) => {
      const willExpand = !expanded.has(root.id);
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(root.id)) next.delete(root.id);
        else next.add(root.id);
        return next;
      });
      if (willExpand && !loadedThreadIds.has(root.id)) {
        void (async () => {
          try {
            await loadReplies(root);
            setLoadedThreadIds((prev) => new Set(prev).add(root.id));
          } catch {
            // 拉取失败时退回 preview_replies(已有),不中断展开。
          }
        })();
      }
    },
    [expanded, loadReplies, loadedThreadIds],
  );

  const handleCopyLink = useCallback(
    (comment: Comment) => {
      const url = `${window.location.origin}${window.location.pathname}#comment-${comment.id}`;
      void navigator.clipboard?.writeText(url).then(
        () =>
          toast.addToast(t('comments.linkCopied'), {
            tone: 'success',
            closeLabel: t('common.close'),
          }),
        () =>
          toast.addToast(t('comments.linkCopyFailed'), {
            tone: 'danger',
            closeLabel: t('common.close'),
          }),
      );
    },
    [toast, t],
  );

  const handleSubmit = useCallback(
    async (body: string, opts: SubmitOptions): Promise<void> => {
      // 发表成功 → 滚动到新评论并短暂高亮(§9.5.5)。服务端返回的评论 id 用于定位。
      const created =
        replyTarget !== null
          ? await createReply(replyTarget, body, opts)
          : await createTopLevel(body, opts);
      setReplyTarget(null);
      setHighlightedId(created.id);
    },
    [replyTarget, createReply, createTopLevel],
  );

  const canModify = useCallback(
    (comment: Comment): boolean =>
      props.currentMember !== null &&
      comment.author !== null &&
      comment.author.id === props.currentMember.id,
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

  const activeComments = useMemo(
    () =>
      comments.filter(
        (comment) => comment.author_kind === 'system' || comment.resolved_at === null,
      ),
    [comments],
  );
  const resolvedThreads = useMemo(
    () =>
      comments.filter(
        (comment) => comment.author_kind === 'member' && comment.resolved_at !== null,
      ),
    [comments],
  );

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
      onRetrySend={(target) => {
        void retrySend(target).catch(() => undefined);
      }}
    />
  );

  const renderThread = (root: Comment): React.JSX.Element => {
    const isExpanded = expanded.has(root.id);
    const replies = root.preview_replies ?? [];
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
    <section
      className="mesh-comments"
      aria-label={t('comments.title')}
      data-testid="comments-panel"
    >
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
            activeComments.map((comment) =>
              comment.author_kind === 'system' ? (
                <div
                  className="mesh-comments__activity"
                  key={comment.id}
                  data-testid={`activity-${comment.id}`}
                >
                  {/* 系统活动:左轨上的紧凑灰色小字行 + 小活动图标(§3.2 时间线视觉)。 */}
                  <span className="mesh-comments__activity-node" aria-hidden="true">
                    <Icon name="activity" size={16} />
                  </span>
                  <span className="mesh-comments__activity-text">{comment.body_text}</span>
                  <time className="mesh-comments__activity-time">
                    {formatRelativeTime(comment.created_at, { locale: props.locale })}
                  </time>
                </div>
              ) : (
                renderThread(comment)
              ),
            )
          )}
          {resolvedThreads.length > 0 ? (
            <div className="mesh-comments__resolved-section" data-testid="resolved-threads-section">
              <button
                type="button"
                className="mesh-comments__resolved-toggle"
                data-testid="resolved-threads-toggle"
                aria-expanded={resolvedExpanded}
                onClick={() => setResolvedExpanded((value) => !value)}
              >
                {t('comments.resolvedThreads')} ({resolvedThreads.length})
              </button>
              {resolvedExpanded ? (
                <div className="mesh-comments__resolved-list">
                  {resolvedThreads.map((comment) => renderThread(comment))}
                </div>
              ) : null}
            </div>
          ) : null}
          {/* AI 运行占位:统一五态组件(§9.8)。queued/running/waiting/failed 经
              execution:{id} 频道生命周期帧迁移(验收必修 3);completed 由 agent
              评论回流替换;failed 留失败占位 + 重试入口(comment-inbox §4.1)。 */}
          {placeholders.map((placeholder) => (
            <div
              className="mesh-comments__executing"
              key={placeholder.execution_id}
              data-testid={`executing-${placeholder.execution_id}`}
              title={placeholder.failure_reason ?? undefined}
            >
              <RunStatus
                status={placeholder.status}
                agentName={placeholder.agent_name}
                onRetry={
                  placeholder.status === 'failed'
                    ? () => void retryExecution(placeholder.execution_id)
                    : undefined
                }
              />
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
