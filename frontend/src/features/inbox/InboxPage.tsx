/**
 * 收件箱页(comment-inbox.md §4.2,替换 /inbox 占位):
 * 顶部筛选 tabs(全部/未读/提及我的/分派/Agent)+ 工具条(全部已读/归档已读);
 * 列表按 issue 分组(组头 issue 标识+标题 + 「不再关注此 issue」静音);
 * 行 hover 操作(标已读/归档/跳转);点击通知直达评论锚点并自动标已读;空状态;
 * realtime member:{me}:inbox 合并 notification.created/read。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, EmptyState, ErrorState, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { formatRelativeTime, useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useSettingsStore } from '../../state/settingsStore';
import { BOARD_PATH } from '../onboarding/deeplinks';
import { EmptyInboxTray } from '../onboarding/illustrations';
import {
  archiveNotification,
  archiveRead,
  inboxChannel,
  listInbox,
  markRead,
  muteIssue,
  readAll,
} from './api';
import { setCurrentInboxView } from './currentFilter';
import { groupNotifications } from './grouping';
import { notificationTargetPath } from './links';
import { applyInboxFrame } from './realtime';
import type { InboxFilter, Notification } from './types';
import { isUnread } from './types';
import { useInboxContext } from './useInboxContext';
import './inbox.css';

const FILTERS: readonly InboxFilter[] = ['all', 'unread', 'mentions', 'assigned', 'agent'];
const PAGE_LIMIT = 30;

export function InboxPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const { status, workspaceId, memberId } = useInboxContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const locale = useSettingsStore((state) => state.preferences.locale) ?? 'en';

  const [filter, setFilter] = useState<InboxFilter>('all');
  // 命令面板「标记全部已读」命令随当前视图 filter 口径(§1.2 S3 命令 ⑧)。
  useEffect(() => {
    setCurrentInboxView(workspaceId, filter);
    return () => setCurrentInboxView(null, 'all');
  }, [workspaceId, filter]);
  const [notifications, setNotifications] = useState<readonly Notification[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void (async () => {
      try {
        const page = await listInbox(client, {
          workspaceId,
          filter,
          grouped: true,
          limit: PAGE_LIMIT,
        });
        if (cancelled) return;
        setNotifications(page.data);
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
  }, [client, workspaceId, filter, reloadKey]);

  // realtime 合并(新通知前置 / 标已读多端同步)。
  useEffect(() => {
    if (realtime === null || memberId === null) return;
    const channel = inboxChannel(memberId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      setNotifications((prev) => applyInboxFrame(prev, frame));
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, memberId]);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const handleOpen = useCallback(
    (notification: Notification) => {
      const path = notificationTargetPath(notification);
      if (workspaceId !== null && notification.read_at === null) {
        void markRead(client, workspaceId, notification.id).catch(() => undefined);
        setNotifications((prev) =>
          prev.map((item) =>
            item.id === notification.id ? { ...item, read_at: new Date().toISOString() } : item,
          ),
        );
      }
      if (path !== null) navigate(path);
    },
    [client, navigate, workspaceId],
  );

  const handleArchive = useCallback(
    (notification: Notification) => {
      if (workspaceId === null) return;
      void archiveNotification(client, workspaceId, notification.id)
        .then(() => setNotifications((prev) => prev.filter((item) => item.id !== notification.id)))
        .catch(() => undefined);
    },
    [client, workspaceId],
  );

  const handleMute = useCallback(
    (issueId: string) => {
      void muteIssue(client, issueId)
        .then(() => {
          toast.addToast(t('inbox.mutedToast'), { tone: 'success', closeLabel: t('common.close') });
          setNotifications((prev) => prev.filter((item) => item.issue_id !== issueId));
        })
        .catch(() => undefined);
    },
    [client, toast, t],
  );

  const handleReadAll = useCallback(() => {
    if (workspaceId === null) return;
    void readAll(client, workspaceId, filter)
      .then(() => reload())
      .catch(() => undefined);
  }, [client, workspaceId, filter, reload]);

  const handleArchiveRead = useCallback(() => {
    if (workspaceId === null) return;
    void archiveRead(client, workspaceId)
      .then(() => reload())
      .catch(() => undefined);
  }, [client, workspaceId, reload]);

  if (status === 'loading' || workspaceId === null) {
    return <Skeleton loadingLabel={t('common.loading')} className="mesh-inbox__skeleton" />;
  }

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

  const groups = groupNotifications(notifications);

  return (
    <div className="mesh-inbox" data-testid="inbox-page">
      <header className="mesh-inbox__head">
        <h1>{t('inbox.title')}</h1>
        <div className="mesh-inbox__toolbar">
          <Button size="sm" variant="secondary" data-testid="inbox-read-all" onClick={handleReadAll}>
            {t('inbox.readAll')}
          </Button>
          <Button size="sm" variant="secondary" data-testid="inbox-archive-read" onClick={handleArchiveRead}>
            {t('inbox.archiveRead')}
          </Button>
        </div>
      </header>

      <div className="mesh-inbox__tabs" role="tablist" aria-label={t('inbox.filterLabel')}>
        {FILTERS.map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={filter === value}
            className={filter === value ? 'mesh-inbox__tab mesh-inbox__tab--active' : 'mesh-inbox__tab'}
            data-testid={`inbox-tab-${value}`}
            onClick={() => setFilter(value)}
          >
            {t(`inbox.filter.${value}`)}
          </button>
        ))}
      </div>

      {isLoading ? (
        <Skeleton loadingLabel={t('common.loading')} className="mesh-inbox__skeleton" />
      ) : groups.length === 0 ? (
        <EmptyState
          illustration={<EmptyInboxTray />}
          title={t('onboarding.empty.inbox.title')}
          description={t('onboarding.empty.inbox.description')}
          action={
            <Button
              size="sm"
              data-testid="inbox-empty-action"
              onClick={() => navigate(BOARD_PATH)}
            >
              {t('onboarding.empty.inbox.action')}
            </Button>
          }
        />
      ) : (
        <div className="mesh-inbox__groups" data-testid="inbox-groups">
          {groups.map((group) => (
            <section key={group.issueId} className="mesh-inbox__group" data-testid={`inbox-group-${group.issueId}`}>
              <header className="mesh-inbox__group-head">
                <span className="mesh-inbox__group-title">
                  {group.issue !== null
                    ? `${group.issue.identifier} · ${group.issue.title}`
                    : group.issueId}
                </span>
                {group.issueId !== 'none' ? (
                  <button
                    type="button"
                    className="mesh-inbox__mute"
                    data-testid={`inbox-mute-${group.issueId}`}
                    onClick={() => handleMute(group.issueId)}
                  >
                    {t('inbox.mute')}
                  </button>
                ) : null}
              </header>
              <ul className="mesh-inbox__rows">
                {group.items.map((notification) => (
                  <li
                    key={notification.id}
                    className={isUnread(notification) ? 'mesh-inbox__row mesh-inbox__row--unread' : 'mesh-inbox__row'}
                    data-testid={`inbox-row-${notification.id}`}
                  >
                    <button
                      type="button"
                      className="mesh-inbox__row-main"
                      onClick={() => handleOpen(notification)}
                    >
                      {isUnread(notification) ? (
                        <span className="mesh-inbox__dot" aria-hidden="true" data-testid={`inbox-unread-dot-${notification.id}`} />
                      ) : null}
                      <span className="mesh-inbox__row-title">{notification.title}</span>
                      <span className="mesh-inbox__row-preview">{notification.preview}</span>
                      {notification.count > 1 ? (
                        <span className="mesh-inbox__row-count" data-testid={`inbox-count-${notification.id}`}>
                          ×{notification.count}
                        </span>
                      ) : null}
                      <time>{formatRelativeTime(notification.created_at, { locale })}</time>
                    </button>
                    <span className="mesh-inbox__row-actions">
                      {notification.read_at === null ? (
                        <button
                          type="button"
                          data-testid={`inbox-mark-read-${notification.id}`}
                          onClick={() => handleOpen(notification)}
                        >
                          {t('inbox.action.read')}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        data-testid={`inbox-archive-${notification.id}`}
                        onClick={() => handleArchive(notification)}
                      >
                        {t('inbox.action.archive')}
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
