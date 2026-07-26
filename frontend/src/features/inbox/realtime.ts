/**
 * 收件箱实时帧合并(comment-inbox.md §3.6,README §6.7)。
 * 纯函数:绝不修改入参,有变化返回新数组/对象,无变化返回原引用。
 *
 * member:{member_id}:inbox 频道事件:
 * - notification.created(载荷 = 通知对象)→ 去重前置;
 * - notification.read(载荷 {id, read_at})→ 按 id 标已读(多端同步);
 * - inbox.unread_count(载荷 {count})→ 未读计数变更(经 extractUnreadCount 取出)。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import type { Notification } from './types';
import { isNotification } from './types';

function actionOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? event : event.slice(dot + 1);
}

function entityOf(event: string): string {
  const dot = event.lastIndexOf('.');
  return dot === -1 ? '' : event.slice(0, dot);
}

/**
 * 收件箱列表帧合并。
 * - notification.created:去重后前置(最新在前);
 * - notification.read:按 id 写入 read_at;
 * 无关帧返回原引用。
 */
export function applyInboxFrame(
  notifications: readonly Notification[],
  frame: RealtimeEventFrame,
): Notification[] {
  const entity = entityOf(frame.event);
  if (entity !== 'notification') return notifications as Notification[];
  const payload = frame.payload as Record<string, unknown>;
  const action = actionOf(frame.event);

  if (action === 'created') {
    const candidate = (payload.notification ?? payload) as unknown;
    if (!isNotification(candidate)) return notifications as Notification[];
    if (notifications.some((item) => item.id === candidate.id)) return notifications as Notification[];
    return [candidate, ...notifications];
  }

  if (action === 'read') {
    const id = typeof payload.id === 'string' ? payload.id : undefined;
    if (id === undefined) return notifications as Notification[];
    const readAt = typeof payload.read_at === 'string' ? payload.read_at : new Date().toISOString();
    let changed = false;
    const next = notifications.map((item) => {
      if (item.id !== id) return item;
      changed = true;
      return { ...item, read_at: readAt };
    });
    return changed ? next : (notifications as Notification[]);
  }

  return notifications as Notification[];
}

/** 从 inbox.unread_count 帧取出计数;非该事件返回 null。 */
export function extractUnreadCount(frame: RealtimeEventFrame): number | null {
  if (frame.event !== 'inbox.unread_count') return null;
  const payload = frame.payload as Record<string, unknown>;
  return typeof payload.count === 'number' ? payload.count : null;
}
