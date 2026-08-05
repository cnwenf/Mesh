/**
 * 评论卡片(comment-inbox.md §4.1 第 2/4 点):作者 + 身份徽标(人类/agent)+ 相对时间 +
 * 「已编辑」;正文经服务端净化 body_html 渲染(仅此字段允许 dangerouslySetInnerHTML);
 * 底部反应区 + 操作条(回复 / 解决·重开 / 复制链接 / 编辑 / 删除)。
 * 已删除评论渲染「该评论已删除」占位以保线程完整。深链锚点 id=comment-{id} + 高亮。
 * 纯展示 + 回调;数据获取与乐观更新在父级。
 */
import { useEffect, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { Avatar, Icon, Menu } from '../../design';
import type { MenuEntry } from '../../design';
import { useUgcColorGuard } from '../../design/ugcColorGuard';
import { env } from '../../env';
import { RelativeTime, useT } from '../../i18n';
import { getIssueByIdentifier } from '../issues/api';
import { ReactionBar } from './ReactionBar';
import type { Comment } from './types';
import { isDeletedComment, isResolved } from './types';

// C6: server emits `#IDENTIFIER` links as <a class="mesh-issue-link"
// data-issue-identifier="X" href="...">; we hydrate them into title+status
// reference cards. Identifiers come from a strict server regex, so the match
// is controlled; card text is HTML-escaped before injection.
export const ISSUE_LINK_RE =
  /<a\b[^>]*\bdata-issue-identifier="([A-Za-z0-9._-]{1,40})"[^>]*>[\s\S]*?<\/a>/g;

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Hydrate mesh-issue-link anchors into reference cards (C6). */
function useIssueLinkCards(bodyHtml: string, workspaceId: string): string {
  const client = useMemoClient();
  const [cards, setCards] = useState<Record<string, string>>({});
  useEffect(() => {
    const ids = Array.from(bodyHtml.matchAll(ISSUE_LINK_RE), (m) => m[1]);
    const unique = Array.from(new Set(ids));
    if (unique.length === 0) {
      setCards({});
      return;
    }
    let cancelled = false;
    void Promise.all(
      unique.map(async (ident) => {
        try {
          const detail = await getIssueByIdentifier(client, workspaceId, ident);
          const title = escapeHtml(detail.title);
          const status = escapeHtml(detail.status?.name ?? '');
          return [
            ident,
            `<span class="mesh-issue-card" data-issue-identifier="${escapeHtml(ident)}">` +
              `<span class="mesh-issue-card__id">${escapeHtml(ident)}</span>` +
              `<span class="mesh-issue-card__title">${title}</span>` +
              (status ? `<span class="mesh-issue-card__status">${status}</span>` : '') +
              `</span>`,
          ] as const;
        } catch {
          return null;
        }
      }),
    ).then((results) => {
      if (cancelled) return;
      const map: Record<string, string> = {};
      for (const r of results) if (r) map[r[0]] = r[1];
      setCards(map);
    });
    return () => {
      cancelled = true;
    };
  }, [bodyHtml, workspaceId, client]);
  if (Object.keys(cards).length === 0) return bodyHtml;
  return bodyHtml.replace(ISSUE_LINK_RE, (match, ident: string) => cards[ident] ?? match);
}

function useMemoClient(): MeshApiClient {
  const [client] = useState(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }));
  return client;
}

export interface CommentCardProps {
  readonly comment: Comment;
  readonly workspaceId: string;
  readonly locale: string;
  /** 深链命中时高亮闪烁。 */
  readonly highlighted: boolean;
  /** 当前用户可否编辑/删除(作者或管理员;权威校验在后端)。 */
  readonly canModify: boolean;
  readonly onReply: (comment: Comment) => void;
  readonly onResolve: (comment: Comment) => void;
  readonly onReopen: (comment: Comment) => void;
  readonly onToggleReaction: (comment: Comment, emoji: string) => void;
  readonly onAddReaction: (comment: Comment, emoji: string) => void;
  /** 保存编辑(乐观锁冲突由调用方提示;reject 时保持编辑态)。 */
  readonly onSaveEdit: (comment: Comment, bodyMarkdown: string) => Promise<void>;
  readonly onDelete: (comment: Comment) => void;
  readonly onCopyLink: (comment: Comment) => void;
}

export function CommentCard(props: CommentCardProps): React.JSX.Element {
  const t = useT();
  const { comment } = props;
  const hydratedBody = useIssueLinkCards(comment.body_html ?? '', props.workspaceId);
  const ugcGuard = useUgcColorGuard();
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const startEdit = (): void => {
    setEditValue(comment.body_markdown);
    setEditError(null);
    setEditing(true);
  };

  const saveEdit = async (): Promise<void> => {
    const body = editValue.trim();
    if (body === '' || editBusy) return;
    setEditBusy(true);
    setEditError(null);
    try {
      await props.onSaveEdit(comment, body);
      setEditing(false);
    } catch {
      setEditError(t('error.conflict'));
    } finally {
      setEditBusy(false);
    }
  };

  const deleted = isDeletedComment(comment);
  const resolved = isResolved(comment);
  const author = comment.author;
  const isAgent = author !== null && author.member_type === 'agent';

  const cardClass = [
    'mesh-comments__card',
    props.highlighted ? 'mesh-comments__card--highlight' : null,
    resolved ? 'mesh-comments__card--resolved' : null,
  ]
    .filter((part): part is string => part !== null)
    .join(' ');

  // 触控「更多」菜单(§9.5.6 / §8.2):承载与桌面操作条相同的动作;
  // 桌面端经 CSS 隐藏(hover:none 时显示),触控端常驻,命中区 ≥44px。
  const menuEntries: ReadonlyArray<MenuEntry> = deleted
    ? []
    : [
        {
          key: 'reply',
          label: t('comments.action.reply'),
          icon: 'message',
          onSelect: () => props.onReply(comment),
        },
        ...(comment.parent_id === null
          ? resolved
            ? [
                {
                  key: 'reopen',
                  label: t('comments.action.reopen'),
                  icon: 'refresh',
                  onSelect: () => props.onReopen(comment),
                },
              ]
            : [
                {
                  key: 'resolve',
                  label: t('comments.action.resolve'),
                  icon: 'check',
                  onSelect: () => props.onResolve(comment),
                },
              ]
          : []),
        {
          key: 'copy',
          label: t('comments.action.copyLink'),
          icon: 'link',
          onSelect: () => props.onCopyLink(comment),
        },
        ...(props.canModify
          ? [
              {
                key: 'edit',
                label: t('comments.action.edit'),
                icon: 'edit',
                onSelect: startEdit,
              },
              {
                key: 'delete',
                label: t('comments.action.delete'),
                icon: 'trash',
                danger: true,
                onSelect: () => props.onDelete(comment),
              },
            ]
          : []),
      ];

  return (
    <article
      className={cardClass}
      id={`comment-${comment.id}`}
      data-testid={`comment-card-${comment.id}`}
    >
      {/* 时间线头像列(40px):头像下接连接线(CSS),系统活动与回复共用同一左轨。 */}
      <div className="mesh-comments__avatar-col" aria-hidden="true">
        <Avatar
          name={author !== null ? author.name : '?'}
          kind={isAgent ? 'agent' : 'human'}
          size={40}
          className="mesh-comments__avatar"
        />
      </div>

      <div className="mesh-comments__card-main">
        <header className="mesh-comments__card-head">
          <span className="mesh-comments__author" data-testid={`comment-author-${comment.id}`}>
            {author !== null ? author.name : t('comments.unknownAuthor')}
          </span>
          {isAgent ? (
            <span
              className="mesh-comments__badge mesh-comments__badge--agent"
              data-testid="agent-badge"
            >
              {t('comments.badge.agent')}
            </span>
          ) : null}
          <RelativeTime
            utcIso={comment.created_at}
            locale={props.locale}
            className="mesh-comments__time"
          />
          {comment.edited_at !== null ? (
            <span className="mesh-comments__edited" data-testid="comment-edited">
              {t('comments.edited')}
            </span>
          ) : null}
          {resolved ? (
            <span className="mesh-comments__resolved-tag" data-testid="comment-resolved-tag">
              {t('comments.resolved')}
            </span>
          ) : null}
        </header>

        {deleted ? (
          <p className="mesh-comments__deleted" data-testid="comment-deleted">
            {t('comments.deleted')}
          </p>
        ) : editing ? (
          <div className="mesh-comments__edit" data-testid={`comment-edit-form-${comment.id}`}>
            <textarea
              className="mesh-comments__edit-input"
              data-testid={`comment-edit-input-${comment.id}`}
              value={editValue}
              aria-label={t('comments.action.edit')}
              rows={3}
              onChange={(event) => setEditValue(event.target.value)}
            />
            {editError !== null ? (
              <p
                className="mesh-comments__edit-error"
                role="alert"
                data-testid="comment-edit-error"
              >
                {editError}
              </p>
            ) : null}
            <div className="mesh-comments__edit-actions">
              <button
                type="button"
                data-testid={`comment-edit-save-${comment.id}`}
                disabled={editBusy}
                onClick={() => void saveEdit()}
              >
                {t('common.save')}
              </button>
              <button
                type="button"
                data-testid={`comment-edit-cancel-${comment.id}`}
                onClick={() => setEditing(false)}
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        ) : (
          <div
            className="mesh-comments__body"
            data-testid={`comment-body-${comment.id}`}
            ref={ugcGuard}
            // body_html 为服务端白名单净化后的 HTML(comment-inbox.md §5.1),系唯一允许的注入源;
            // C6 卡片水合仅替换受控的 mesh-issue-link 锚点,卡片文本已转义。
            // UGC 内联色对比兜底(theme.md §4.3 T5③)经 ugcGuard 回调 ref 执行。
            dangerouslySetInnerHTML={{ __html: hydratedBody }}
          />
        )}

        {!deleted ? (
          <ReactionBar
            reactions={comment.reactions}
            onToggle={(emoji) => props.onToggleReaction(comment, emoji)}
            onAdd={(emoji) => props.onAddReaction(comment, emoji)}
          />
        ) : null}

        {!deleted ? (
          /* 桌面次要操作条:仅 hover/focus-within 显示(CSS opacity+visibility),保留键盘可达。 */
          <footer className="mesh-comments__actions">
            <button
              type="button"
              data-testid={`comment-reply-${comment.id}`}
              onClick={() => props.onReply(comment)}
            >
              {t('comments.action.reply')}
            </button>
            {comment.parent_id === null ? (
              resolved ? (
                <button
                  type="button"
                  data-testid={`comment-reopen-${comment.id}`}
                  onClick={() => props.onReopen(comment)}
                >
                  {t('comments.action.reopen')}
                </button>
              ) : (
                <button
                  type="button"
                  data-testid={`comment-resolve-${comment.id}`}
                  onClick={() => props.onResolve(comment)}
                >
                  {t('comments.action.resolve')}
                </button>
              )
            ) : null}
            <button
              type="button"
              data-testid={`comment-copy-${comment.id}`}
              onClick={() => props.onCopyLink(comment)}
            >
              {t('comments.action.copyLink')}
            </button>
            {props.canModify ? (
              <>
                <button
                  type="button"
                  data-testid={`comment-edit-${comment.id}`}
                  onClick={startEdit}
                >
                  {t('comments.action.edit')}
                </button>
                <button
                  type="button"
                  className="mesh-comments__action-danger"
                  data-testid={`comment-delete-${comment.id}`}
                  onClick={() => props.onDelete(comment)}
                >
                  {t('comments.action.delete')}
                </button>
              </>
            ) : null}
          </footer>
        ) : null}

        {!deleted && menuEntries.length > 0 ? (
          <div className="mesh-comments__actions-touch">
            <Menu
              trigger={<Icon name="more-horizontal" size={20} />}
              triggerLabel={t('comments.moreActions')}
              entries={menuEntries}
              align="end"
            />
          </div>
        ) : null}
      </div>
    </article>
  );
}
