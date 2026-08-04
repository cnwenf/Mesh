/**
 * 收件箱预览窗格(design-quality.md §3.2 收件箱行 / §4.4 Conversation 详情栏):
 * 展示选中通知的标题、优先级徽标、来源者、正文、时间与操作(标已读/归档/打开来源)。
 * 优先级经「图标 + 文案」表达(颜色不是唯一信号);来源删除时优雅降级;
 * 手机(≤720px,ConversationLayout 断点)经返回按钮回列表路由 /inbox。
 */
import { useNavigate } from 'react-router';
import { Avatar, Badge, Button, EmptyState, Skeleton } from '../../design';
import { formatDateTime, useT } from '../../i18n';
import { workspaceRoute } from '../members/useWorkspaceMembership';
import { notificationTargetPath } from './links';
import type { Notification } from './types';
import { isUnread } from './types';

export interface InboxPreviewPaneProps {
  /** 选中的通知(未选中 / 尚未加载完成时为 null) */
  readonly notification: Notification | null;
  /** 列表加载中(深链直达时先显骨架) */
  readonly isLoading: boolean;
  /** 加载完成后选中 id 仍不在列表(源已删除或不存在) */
  readonly unknownId: string | null;
  readonly locale: string;
  /** 规范工作区路由 slug;独立/旧测试入口为空时保留兼容扁平路径。 */
  readonly workspaceSlug?: string | null;
  readonly onMarkRead: (notification: Notification) => void;
  readonly onArchive: (notification: Notification) => void;
}

export function InboxPreviewPane(props: InboxPreviewPaneProps): React.JSX.Element {
  const { notification, isLoading, unknownId, locale, onMarkRead, onArchive } = props;
  const t = useT();
  const navigate = useNavigate();
  const inboxPath =
    props.workspaceSlug === undefined || props.workspaceSlug === null
      ? '/inbox'
      : workspaceRoute(props.workspaceSlug, 'inbox');

  if (unknownId !== null) {
    // H5:unknownId 仅表示「不在已加载窗口」(深链旧通知 / 桌面选中后归档出列 /
    // 切到未读 tab 深链已读),并非源实体被删;故用「未找到/已归档」文案,不得把裸 UUID
    // 当标题、不得误报 sourceDeleted(comment-inbox §5.3:该文案仅用于源实体删除)。
    return (
      <div className="mesh-inbox-preview mesh-inbox-preview--missing" data-testid="inbox-preview-missing">
        <Button
          variant="ghost"
          size="sm"
          className="mesh-inbox-preview__back"
          data-testid="inbox-preview-back"
          onClick={() => navigate(inboxPath)}
        >
          {t('inbox.back')}
        </Button>
        <EmptyState
          title={t('inbox.preview.notFound')}
          description={t('inbox.preview.selectDescription')}
        />
      </div>
    );
  }

  if (notification === null) {
    if (isLoading) {
      return <Skeleton loadingLabel={t('common.loading')} className="mesh-inbox-preview__skeleton" />;
    }
    return (
      <div className="mesh-inbox-preview mesh-inbox-preview--empty" data-testid="inbox-preview-empty">
        <EmptyState
          title={t('inbox.preview.selectTitle')}
          description={t('inbox.preview.selectDescription')}
        />
      </div>
    );
  }

  const targetPath = notificationTargetPath(notification, props.workspaceSlug ?? null);
  const sourceDeleted = notification.issue === undefined && notification.issue_id !== null;
  const actor = notification.actor;

  return (
    <div className="mesh-inbox-preview" data-testid="inbox-preview">
      <Button
        variant="ghost"
        size="sm"
        className="mesh-inbox-preview__back"
        data-testid="inbox-preview-back"
        onClick={() => navigate(inboxPath)}
      >
        {t('inbox.back')}
      </Button>
      <h2 className="mesh-text-title-3 mesh-inbox-preview__title" data-testid="inbox-preview-title">
        {notification.title}
      </h2>
      <div className="mesh-inbox-preview__meta">
        <span className="mesh-inbox-preview__priority" data-testid="inbox-preview-priority">
          {notification.priority === 'critical' ? (
            <Badge icon="warning" tone="neutral">
              {t('inbox.priority.critical')}
            </Badge>
          ) : (
            <Badge tone="neutral">
              {t('inbox.priority.normal')}
            </Badge>
          )}
        </span>
      </div>
      {actor !== null ? (
        <p className="mesh-inbox-preview__actor mesh-text-caption" data-testid="inbox-preview-actor">
          <Avatar name={actor.name} size={20} kind={actor.member_type} />
          <span>
            {actor.member_type === 'agent'
              ? t('inbox.preview.fromAgent', { name: actor.name })
              : t('inbox.preview.fromUser', { name: actor.name })}
          </span>
        </p>
      ) : null}
      {sourceDeleted ? (
        <p className="mesh-text-caption mesh-inbox-preview__note" data-testid="inbox-preview-deleted">
          {t('inbox.preview.sourceDeleted')}
        </p>
      ) : null}
      <p className="mesh-text-body mesh-inbox-preview__body" data-testid="inbox-preview-body">
        {notification.preview}
      </p>
      <time className="mesh-text-caption mesh-tnum mesh-inbox-preview__time">
        {formatDateTime(notification.created_at, { locale })}
      </time>
      <div className="mesh-inbox-preview__actions">
        {isUnread(notification) ? (
          <Button
            variant="secondary"
            size="sm"
            data-testid="inbox-preview-mark-read"
            onClick={() => onMarkRead(notification)}
          >
            {t('inbox.preview.markRead')}
          </Button>
        ) : null}
        <Button
          variant="secondary"
          size="sm"
          data-testid="inbox-preview-archive"
          onClick={() => {
            onArchive(notification);
            navigate(inboxPath);
          }}
        >
          {t('inbox.preview.archive')}
        </Button>
        <Button
          size="sm"
          data-testid="inbox-preview-open"
          disabled={targetPath === null}
          onClick={() => {
            if (targetPath !== null) navigate(targetPath);
          }}
        >
          {t('inbox.preview.open')}
        </Button>
      </div>
    </div>
  );
}
