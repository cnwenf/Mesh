/**
 * 顶栏铃铛(comment-inbox.md §4.2,design-quality.md §7.1 禁 emoji):
 * 底座 IconButton + bell 线性图标 + 未读数字徽标;点击下拉最近 ~5 条通知,
 * 底部「查看全部」进 /inbox。外点(pointerdown)或 Esc 关闭下拉。
 * 工作区切换时轮询 unread-count;realtime 经 member:{me}:inbox 频道实时同步
 * (inbox.unread_count 为未读数权威源,notification.created 仅更新下拉预览)。
 * 点击通知直达 issue 评论锚点并自动标已读。
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import { Icon, IconButton } from '../../design';
import { env } from '../../env';
import { formatRelativeTime, useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useSettingsStore } from '../../state/settingsStore';
import { inboxChannel, listInbox, markRead, unreadCount } from './api';
import { notificationTargetPath } from './links';
import { extractUnreadCount } from './realtime';
import type { Notification } from './types';
import { useInboxContext } from './useInboxContext';

const DROPDOWN_LIMIT = 5;

export function InboxBell(): React.JSX.Element {
  const t = useT();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const { workspaceId, memberId } = useInboxContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [latest, setLatest] = useState<readonly Notification[]>([]);
  const locale = useSettingsStore((state) => state.preferences.locale) ?? 'en';
  const rootRef = useRef<HTMLDivElement>(null);
  const dropdownId = useId();

  // 工作区切换时拉取未读计数。
  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    void unreadCount(client, workspaceId)
      .then((value) => {
        if (!cancelled) setCount(value);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId]);

  // realtime:member:{me}:inbox 频道未读计数与最新通知同步。
  // 未读数只由权威源驱动(挂载时 REST 快照 + inbox.unread_count 帧);后端在
  // 未读数变化的每一种情形下都会发 inbox.unread_count,故 notification.created
  // 不再 +1(否则在「REST 快照已含该通知」或「聚入已有未读组未读数不变」两种
  // 情形下会多计/错计 —— 后者未读数本不应变)。created 仅更新下拉预览列表。
  useEffect(() => {
    if (realtime === null || memberId === null) return;
    const channel = inboxChannel(memberId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      const nextCount = extractUnreadCount(frame);
      if (nextCount !== null) setCount(nextCount);
      if (frame.event === 'notification.created') {
        setLatest((prev) =>
          [frame.payload as unknown as Notification, ...prev].slice(0, DROPDOWN_LIMIT),
        );
      }
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, memberId]);

  // 下拉打开时:外部 pointerdown 与 Esc 关闭(卸载/关闭时解绑)。
  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      const root = rootRef.current;
      if (root !== null && !root.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const toggleOpen = useCallback(() => {
    // M5:拉取移出 updater(setState updater 须纯)。由闭包 open 计算下一态
    // (open 在 deps 内,回调总持最新值;避免 updater 内副作用在 StrictMode 双调用下错乱)。
    const next = !open;
    setOpen(next);
    if (next && workspaceId !== null) {
      void listInbox(client, { workspaceId, limit: DROPDOWN_LIMIT, filter: 'all' })
        .then((page) => setLatest(page.data.slice(0, DROPDOWN_LIMIT)))
        .catch(() => undefined);
    }
  }, [client, workspaceId, open]);

  const handleClick = useCallback(
    (notification: Notification) => {
      setOpen(false);
      const path = notificationTargetPath(notification);
      if (workspaceId !== null && notification.read_at === null) {
        void markRead(client, workspaceId, notification.id).catch(() => undefined);
        setCount((prev) => Math.max(0, prev - 1));
      }
      if (path !== null) navigate(path);
    },
    [client, navigate, workspaceId],
  );

  return (
    <div className="mesh-inbox-bell" ref={rootRef}>
      <IconButton
        label={t('a11y.notifications')}
        className="mesh-inbox-bell__button"
        data-testid="inbox-bell"
        aria-expanded={open}
        aria-controls={open ? dropdownId : undefined}
        onClick={toggleOpen}
      >
        <Icon name="bell" size={20} />
      </IconButton>
      {count > 0 ? (
        <span className="mesh-inbox-bell__badge mesh-text-micro mesh-tnum" data-testid="inbox-badge">
          {count > 99 ? '99+' : count}
        </span>
      ) : null}
      {open ? (
        // M6:下拉是导航列表(条目跳详情 + 「查看全部」链接),非 ARIA menu 命令菜单;
        // 用 role=region + aria-label 取代非法 menu 结构(避免缺方向键漫游/menuitem 子结构非法)。
        <div
          id={dropdownId}
          className="mesh-inbox-bell__dropdown"
          role="region"
          aria-label={t('a11y.notifications')}
          data-testid="inbox-dropdown"
        >
          {latest.length === 0 ? (
            <p className="mesh-inbox-bell__empty mesh-text-caption" data-testid="inbox-bell-empty">
              {t('inbox.empty')}
            </p>
          ) : (
            <ul className="mesh-inbox-bell__list">
              {latest.map((notification) => (
                <li key={notification.id}>
                  <button
                    type="button"
                    className="mesh-inbox-bell__item"
                    data-testid={`inbox-bell-item-${notification.id}`}
                    onClick={() => handleClick(notification)}
                  >
                    <span className="mesh-inbox-bell__item-title mesh-text-body-sm">
                      {notification.title}
                    </span>
                    <span className="mesh-inbox-bell__item-preview mesh-text-caption mesh-truncate">
                      {notification.preview}
                    </span>
                    <time className="mesh-text-caption mesh-tnum" dateTime={notification.created_at}>
                      {formatRelativeTime(notification.created_at, { locale })}
                    </time>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            className="mesh-inbox-bell__all"
            data-testid="inbox-bell-all"
            onClick={() => {
              setOpen(false);
              navigate('/inbox');
            }}
          >
            {t('inbox.viewAll')}
          </button>
        </div>
      ) : null}
    </div>
  );
}
