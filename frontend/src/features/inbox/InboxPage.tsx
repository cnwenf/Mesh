/**
 * 收件箱页(comment-inbox.md §4.2,design-quality.md §3.2/§4.4 Conversation 模板):
 * ConversationLayout 双栏——左栏:筛选 tabs(全部/未读/提及我的/分派/Agent)、
 * 免打扰横幅、按 issue 分组的通知列表(组头 issue 标识+标题 + 静音);
 * 右栏:选中通知预览(标已读/归档/打开来源)。
 * 选中经路由 /inbox/:notificationId 持久化(手机单栏路由化,桌面双栏);
 * 行点击 = 选中 + 乐观标已读;「打开来源」才跳 issue 评论锚点。
 * 免打扰窗口经通知偏好解析(quietHours.ts),激活时横幅提示(关键事件仍通知)。
 * realtime member:{me}:inbox 合并 notification.created/read。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { useUrlState } from '../../hooks/useUrlState';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import {
  Avatar,
  Badge,
  Banner,
  Button,
  ConversationLayout,
  DataView,
  EmptyState,
  ErrorState,
  Skeleton,
  Tabs,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { formatRelativeTime, useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useSettingsStore } from '../../state/settingsStore';
import { workspaceRoute } from '../members/useWorkspaceMembership';
import { BOARD_PATH } from '../onboarding/deeplinks';
import { EmptyInboxTray } from '../onboarding/illustrations';
import {
  archiveNotification,
  archiveRead,
  getPreferences,
  inboxChannel,
  listInbox,
  markRead,
  muteIssue,
  readAll,
} from './api';
import { setCurrentInboxView } from './currentFilter';
import { groupNotifications } from './grouping';
import { InboxApprovalActions } from './InboxApprovalActions';
import { InboxPreviewPane } from './InboxPreviewPane';
import { extractQuietHours, isInQuietHours } from './quietHours';
import type { QuietHours } from './quietHours';
import { applyInboxFrame } from './realtime';
import type { InboxFilter, Notification } from './types';
import { isUnread } from './types';
import { useInboxContext } from './useInboxContext';
import './inbox.css';

const FILTERS: readonly InboxFilter[] = ['all', 'unread', 'mentions', 'assigned', 'agent'];
/** L202 归档视图 tab 值(客户端视图态;API filter 参数不含 archived,走独立查询参数)。 */
const ARCHIVED_TAB = 'archived' as const;
type InboxTab = InboxFilter | typeof ARCHIVED_TAB;
const TABS: readonly InboxTab[] = [...FILTERS, ARCHIVED_TAB];
const PAGE_LIMIT = 30;

/** URL filter 参数 → 合法枚举值;非法/缺失一律回落 all(L92 可分享、刷新不丢)。 */
function tabFromParam(raw: string | null): InboxTab {
  return (TABS as readonly string[]).includes(raw ?? '') ? (raw as InboxTab) : 'all';
}

export function InboxPage(): React.JSX.Element {
  const t = useT();
  useDocumentTitle(t('inbox.title')); // L93 标签页标题
  const toast = useToast();
  const navigate = useNavigate();
  const { notificationId } = useParams();
  const selectedId = notificationId ?? null;
  const realtime = useRealtimeContext();
  const { status, workspaceId, workspaceSlug, memberId } = useInboxContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const locale = useSettingsStore((state) => state.preferences.locale) ?? 'en';

  // L92:筛选同步 URL(?filter=),刷新不丢、可分享;all 为缺省不占参数。
  const [filterParam, setFilterParam] = useUrlState('filter');
  const tab = tabFromParam(filterParam);
  // L202:归档视图为客户端 tab 态;API filter 保持 Spec 五值,archived 走独立查询参数。
  const isArchivedView = tab === ARCHIVED_TAB;
  const filter: InboxFilter = isArchivedView ? 'all' : tab;
  const setFilter = useCallback(
    (next: InboxTab) => setFilterParam(next === 'all' ? null : next),
    [setFilterParam],
  );
  // 命令面板「标记全部已读」命令随当前视图 filter 口径(§1.2 S3 命令 ⑧);
  // 归档视图回落 all(后端 filter 不接受 archived,命令作用于主视图)。
  useEffect(() => {
    setCurrentInboxView(workspaceId, filter);
    return () => setCurrentInboxView(null, 'all');
  }, [workspaceId, filter]);
  const [notifications, setNotifications] = useState<readonly Notification[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [quietHours, setQuietHours] = useState<QuietHours | null>(null);
  const inboxPath = workspaceSlug === null ? '/inbox' : workspaceRoute(workspaceSlug, 'inbox');
  const boardPath = workspaceSlug === null ? BOARD_PATH : workspaceRoute(workspaceSlug, 'board');

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
          archived: isArchivedView,
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
  }, [client, workspaceId, filter, isArchivedView, reloadKey]);

  // 免打扰偏好:best-effort 一次,失败静默(不渲染横幅)。
  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    void getPreferences(client, workspaceId)
      .then((prefs) => {
        if (!cancelled) setQuietHours(extractQuietHours(prefs));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId]);

  // realtime 合并(新通知前置 / 标已读多端同步)。
  useEffect(() => {
    if (realtime === null || memberId === null) return;
    // L202:归档视图不合并实时帧(帧均为主视图语义;行以进页拉取为准)。
    if (isArchivedView) return;
    const channel = inboxChannel(memberId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      setNotifications((prev) => applyInboxFrame(prev, frame, filter));
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, memberId, filter, isArchivedView]);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  // 乐观标已读(无导航):行内操作与预览窗格共用。
  // M9:乐观操作失败不再静默吞错——回滚乐观态 + danger toast。
  const notifyFailure = useCallback(() => {
    toast.addToast(t('common.unknownError'), {
      tone: 'danger',
      closeLabel: t('common.close'),
    });
  }, [toast, t]);

  const handleMarkRead = useCallback(
    (notification: Notification) => {
      if (workspaceId === null || notification.read_at !== null) return;
      void markRead(client, workspaceId, notification.id).catch(() => {
        setNotifications((prev) =>
          prev.map((item) => (item.id === notification.id ? { ...item, read_at: null } : item)),
        );
        notifyFailure();
      });
      setNotifications((prev) =>
        prev.map((item) =>
          item.id === notification.id ? { ...item, read_at: new Date().toISOString() } : item,
        ),
      );
    },
    [client, workspaceId, notifyFailure],
  );

  // 行点击 = 选中(路由 /inbox/:id,push 以支持后退)+ 乐观标已读。
  const handleSelect = useCallback(
    (notification: Notification) => {
      handleMarkRead(notification);
      // L8:已选中行重复点击不再 push,避免重复历史项。
      if (notification.id !== selectedId) {
        navigate(`${inboxPath}/${notification.id}`);
      }
    },
    [handleMarkRead, inboxPath, navigate, selectedId],
  );

  const handleArchive = useCallback(
    (notification: Notification) => {
      if (workspaceId === null) return;
      const snapshot = notifications;
      void archiveNotification(client, workspaceId, notification.id)
        .then(() => {
          setNotifications((prev) => prev.filter((item) => item.id !== notification.id));
          // H5:归档当前选中通知后清选中,避免预览窗悬空(桌面双栏不再突变为缺失态)。
          if (notification.id === selectedId) {
            navigate(inboxPath, { replace: true });
          }
        })
        .catch(() => {
          setNotifications(snapshot);
          notifyFailure();
        });
    },
    [client, workspaceId, selectedId, navigate, notifications, notifyFailure, inboxPath],
  );

  const handleMute = useCallback(
    (issueId: string) => {
      void muteIssue(client, issueId)
        .then(() => {
          toast.addToast(t('inbox.mutedToast'), { tone: 'success', closeLabel: t('common.close') });
          setNotifications((prev) => prev.filter((item) => item.issue_id !== issueId));
        })
        .catch(() => notifyFailure());
    },
    [client, toast, t, notifyFailure],
  );

  const handleReadAll = useCallback(() => {
    if (workspaceId === null) return;
    void readAll(client, workspaceId, filter)
      .then(() => reload())
      .catch(() => notifyFailure());
  }, [client, workspaceId, filter, reload, notifyFailure]);

  const handleArchiveRead = useCallback(() => {
    if (workspaceId === null) return;
    void archiveRead(client, workspaceId)
      .then(() => reload())
      .catch(() => notifyFailure());
  }, [client, workspaceId, reload, notifyFailure]);

  // M7:分钟级 tick,使横幅在跨越窗口边界时出现/消失(页面停留 21:58、窗口 22:00 起)。
  const [quietTick, setQuietTick] = useState(0);
  useEffect(() => {
    if (quietHours === null) return undefined;
    const id = window.setInterval(() => setQuietTick((value) => value + 1), 60_000);
    return () => window.clearInterval(id);
  }, [quietHours]);

  const quietHoursActive = useMemo(() => {
    if (quietHours === null) return false;
    const now = new Date();
    return isInQuietHours(quietHours.start, quietHours.end, {
      hour: now.getHours(),
      minute: now.getMinutes(),
    });
    // quietTick 不在函数体读取,仅作为每分钟重算的触发器(读取 new Date());
    // exhaustive-deps 看不到这层意图,故显式豁免。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quietHours, quietTick]);

  // M8:错误优先于骨架——否则 status==='error' 且 workspaceId 未解析时会落入
  // 下方骨架分支无限转,无 ErrorState/重试(对照 ChatPage bootError)。
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

  if (status === 'error') {
    return <ErrorState title={t('state.errorTitle')} description={t('state.errorDescription')} />;
  }

  if (status === 'loading' || workspaceId === null) {
    return <Skeleton loadingLabel={t('common.loading')} className="mesh-inbox__skeleton" />;
  }

  const groups = groupNotifications(notifications);
  const selectedNotification =
    selectedId === null ? null : (notifications.find((item) => item.id === selectedId) ?? null);
  const unknownId =
    selectedId !== null && !isLoading && selectedNotification === null ? selectedId : null;

  const listContent = (
    <>
      {quietHoursActive ? (
        <div className="mesh-inbox__quiet" data-testid="inbox-quiet-hours">
          <Banner tone="info">{t('inbox.quietHours.active')}</Banner>
        </div>
      ) : null}

      {isLoading ? (
        <Skeleton loadingLabel={t('common.loading')} className="mesh-inbox__skeleton" />
      ) : groups.length === 0 ? (
        isArchivedView ? (
          <div data-testid="inbox-archived-empty">
            <EmptyState
              illustration={<EmptyInboxTray />}
              title={t('inbox.archivedEmpty.title')}
              description={t('inbox.archivedEmpty.description')}
            />
          </div>
        ) : (
          <EmptyState
            illustration={<EmptyInboxTray />}
            title={t('onboarding.empty.inbox.title')}
            description={t('onboarding.empty.inbox.description')}
            action={
              <Button
                size="sm"
                data-testid="inbox-empty-action"
                onClick={() => navigate(boardPath)}
              >
                {t('onboarding.empty.inbox.action')}
              </Button>
            }
          />
        )
      ) : (
        <div className="mesh-inbox__groups" data-testid="inbox-groups">
          {groups.map((group) => (
            <section
              key={group.issueId}
              className="mesh-inbox__group"
              data-testid={`inbox-group-${group.issueId}`}
            >
              <header className="mesh-inbox__group-head">
                <span className="mesh-inbox__group-title mesh-text-caption">
                  {group.issue !== null
                    ? [group.issue.identifier, group.issue.title]
                        .filter((part): part is string => part !== null && part !== '')
                        .join(' · ') || group.issueId
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
                  <InboxRow
                    key={notification.id}
                    notification={notification}
                    workspaceId={workspaceId}
                    isSelected={notification.id === selectedId}
                    locale={locale}
                    onSelect={handleSelect}
                    onMarkRead={handleMarkRead}
                    onArchive={handleArchive}
                    showArchive={!isArchivedView}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
  const listPane = (
    <Tabs
      className="mesh-inbox__filter-tabs"
      label={t('inbox.filterLabel')}
      value={tab}
      onChange={(value) => setFilter(value as InboxTab)}
      items={TABS.map((value) => ({
        value,
        label: t(`inbox.filter.${value}`),
        content: listContent,
        testId: `inbox-tab-${value}`,
      }))}
    />
  );

  return (
    <div className="mesh-inbox-page" data-testid="inbox-page">
      <DataView
        className="mesh-inbox__data-view"
        title={t('inbox.title')}
        actions={
          isArchivedView ? undefined : (
            <div className="mesh-inbox__toolbar">
              <Button
                size="sm"
                variant="secondary"
                data-testid="inbox-read-all"
                onClick={handleReadAll}
              >
                {t('inbox.readAll')}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                data-testid="inbox-archive-read"
                onClick={handleArchiveRead}
              >
                {t('inbox.archiveRead')}
              </Button>
            </div>
          )
        }
      >
        <ConversationLayout
          className="mesh-inbox"
          listLabel={t('inbox.list.label')}
          detailLabel={t('inbox.preview.label')}
          activePane={selectedId !== null ? 'detail' : 'list'}
          list={listPane}
        >
          <InboxPreviewPane
            notification={selectedNotification}
            isLoading={isLoading}
            unknownId={unknownId}
            locale={locale}
            workspaceSlug={workspaceSlug}
            onMarkRead={handleMarkRead}
            onArchive={handleArchive}
            showArchive={!isArchivedView}
          />
        </ConversationLayout>
      </DataView>
    </div>
  );
}

interface InboxRowProps {
  readonly notification: Notification;
  readonly workspaceId: string;
  readonly isSelected: boolean;
  readonly locale: string;
  readonly onSelect: (notification: Notification) => void;
  readonly onMarkRead: (notification: Notification) => void;
  readonly onArchive: (notification: Notification) => void;
  /** L202 归档视图:行已归档,不再提供归档操作。 */
  readonly showArchive: boolean;
}

/**
 * 收件箱行(§3.2 收件箱行状态矩阵):未读 = 圆点 + 选中面 + 加粗标题(非仅颜色);
 * 选中行 = surface-selected + 起始侧 3px 强调边;critical 经「警告图标 + 文案」
 * 徽标表达优先级;来源者在标题前显迷你头像。行操作 hover/focus 出现,≤720px 常驻。
 */
function InboxRow(props: InboxRowProps): React.JSX.Element {
  const {
    notification,
    workspaceId,
    isSelected,
    locale,
    onSelect,
    onMarkRead,
    onArchive,
    showArchive,
  } = props;
  const t = useT();
  const unread = isUnread(notification);
  const rowClasses = [
    'mesh-inbox__row',
    unread ? 'mesh-inbox__row--unread' : null,
    isSelected ? 'mesh-inbox__row--selected' : null,
  ]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  const titleClasses = [
    'mesh-inbox__row-title',
    unread ? 'mesh-text-body-strong' : 'mesh-text-body',
  ].join(' ');

  return (
    <li
      className={rowClasses}
      data-testid={`inbox-row-${notification.id}`}
      aria-current={isSelected ? 'true' : undefined}
    >
      <button
        type="button"
        className="mesh-inbox__row-main"
        aria-pressed={isSelected}
        onClick={() => onSelect(notification)}
      >
        <span className="mesh-inbox__row-lead">
          {unread ? (
            <span
              className="mesh-inbox__dot"
              aria-hidden="true"
              data-testid={`inbox-unread-dot-${notification.id}`}
            />
          ) : null}
          {notification.actor !== null ? (
            <span
              className="mesh-inbox__row-actor"
              data-testid={`inbox-row-actor-${notification.id}`}
            >
              <Avatar
                name={notification.actor.name}
                size={20}
                kind={notification.actor.member_type}
              />
            </span>
          ) : null}
        </span>
        <span className="mesh-inbox__row-body">
          <span className={titleClasses} title={notification.title ?? undefined}>
            {notification.title}
          </span>
          <span
            className="mesh-inbox__row-preview mesh-text-caption mesh-truncate"
            title={notification.preview}
          >
            {notification.preview}
          </span>
        </span>
        {notification.count > 1 ? (
          <span className="mesh-inbox__row-count" data-testid={`inbox-count-${notification.id}`}>
            ×{notification.count}
          </span>
        ) : null}
        {notification.priority === 'critical' ? (
          <span
            className="mesh-inbox__row-priority"
            data-testid={`inbox-row-priority-${notification.id}`}
          >
            <Badge icon="warning" tone="warning">
              {t('inbox.priority.critical')}
            </Badge>
          </span>
        ) : null}
        <time
          className="mesh-text-caption mesh-tnum mesh-inbox__row-time"
          dateTime={notification.created_at}
        >
          {formatRelativeTime(notification.created_at, { locale })}
        </time>
      </button>
      <span className="mesh-inbox__row-actions">
        {notification.type === 'review_requested' && notification.approval_id != null ? (
          <InboxApprovalActions workspaceId={workspaceId} approvalId={notification.approval_id} />
        ) : null}
        {unread ? (
          <button
            type="button"
            data-testid={`inbox-mark-read-${notification.id}`}
            onClick={() => onMarkRead(notification)}
          >
            {t('inbox.action.read')}
          </button>
        ) : null}
        {showArchive ? (
          <button
            type="button"
            data-testid={`inbox-archive-${notification.id}`}
            onClick={() => onArchive(notification)}
          >
            {t('inbox.action.archive')}
          </button>
        ) : null}
      </span>
    </li>
  );
}
