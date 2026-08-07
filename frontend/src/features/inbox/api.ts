/**
 * 收件箱 / 通知 API 调用(契约层,comment-inbox.md §3.2 / README §6.14 包络)。
 * workspace 经必填查询参数 workspace_id 传递;列表走 `list`(自动解 {data,next_cursor}),
 * 单对象走 `request`。
 */
import type { MeshApiClient } from '../../api';
import type {
  InboxFilter,
  ListInboxParams,
  Notification,
  NotificationType,
  Preference,
  PreferenceInput,
} from './types';

export interface Page<T> {
  readonly data: readonly T[];
  readonly nextCursor: string | null;
}

const INBOX_PATH = '/api/v1/inbox';
const PREFERENCES_PATH = '/api/v1/notification-preferences';

/** 收件箱级实时频道(§3.6):member:{member_id}:inbox。 */
export function inboxChannel(memberId: string): string {
  return `member:${memberId}:inbox`;
}

/** 列出我的通知(游标分页 + 筛选 + 可选分组;整体游标契约)。 */
export async function listInbox(
  client: MeshApiClient,
  params: ListInboxParams,
): Promise<Page<Notification>> {
  const envelope = await client.list<Notification>(INBOX_PATH, {
    query: {
      workspace_id: params.workspaceId,
      limit: params.limit,
      cursor: params.cursor,
      filter: params.filter,
      type: params.type,
      grouped: params.grouped,
      archived: params.archived,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 未读计数(顶栏徽标)。 */
export async function unreadCount(client: MeshApiClient, workspaceId: string): Promise<number> {
  const result = await client.request<{ count: number }>('GET', `${INBOX_PATH}/unread-count`, {
    query: { workspace_id: workspaceId },
  });
  return result.count;
}

/** 全部已读(可按筛选条件)。 */
export async function readAll(
  client: MeshApiClient,
  workspaceId: string,
  filter?: InboxFilter,
): Promise<number> {
  const result = await client.request<{ updated: number }>('POST', `${INBOX_PATH}/read-all`, {
    query: { workspace_id: workspaceId },
    body: filter !== undefined ? { filter } : {},
  });
  return result.updated;
}

/** 归档全部已读。 */
export async function archiveRead(client: MeshApiClient, workspaceId: string): Promise<number> {
  const result = await client.request<{ archived: number }>('POST', `${INBOX_PATH}/archive-read`, {
    query: { workspace_id: workspaceId },
    body: {},
  });
  return result.archived;
}

/** 标记单条已读。 */
export async function markRead(
  client: MeshApiClient,
  workspaceId: string,
  notificationId: string,
): Promise<Notification> {
  return client.request<Notification>('POST', `${INBOX_PATH}/${notificationId}/read`, {
    query: { workspace_id: workspaceId },
    body: {},
  });
}

/** 标记单条未读。 */
export async function markUnread(
  client: MeshApiClient,
  workspaceId: string,
  notificationId: string,
): Promise<Notification> {
  return client.request<Notification>('POST', `${INBOX_PATH}/${notificationId}/unread`, {
    query: { workspace_id: workspaceId },
    body: {},
  });
}

/** 归档单条。 */
export async function archiveNotification(
  client: MeshApiClient,
  workspaceId: string,
  notificationId: string,
): Promise<Notification> {
  return client.request<Notification>('POST', `${INBOX_PATH}/${notificationId}/archive`, {
    query: { workspace_id: workspaceId },
    body: {},
  });
}

/** 按 issue 静音(保留订阅但不出通知)。 */
export async function muteIssue(
  client: MeshApiClient,
  issueId: string,
): Promise<{ issue_id: string; muted: boolean; reason: string }> {
  return client.request<{ issue_id: string; muted: boolean; reason: string }>(
    'POST',
    `/api/v1/issues/${issueId}/mute`,
    { body: {} },
  );
}

/** 取消静音。 */
export async function unmuteIssue(
  client: MeshApiClient,
  issueId: string,
): Promise<{ issue_id: string; muted: boolean; reason: string }> {
  return client.request<{ issue_id: string; muted: boolean; reason: string }>(
    'POST',
    `/api/v1/issues/${issueId}/unmute`,
    { body: {} },
  );
}

/** 读取通知偏好。 */
export async function getPreferences(
  client: MeshApiClient,
  workspaceId: string,
): Promise<readonly Preference[]> {
  const envelope = await client.list<Preference>(PREFERENCES_PATH, {
    query: { workspace_id: workspaceId },
  });
  return envelope.data;
}

/** 更新通知偏好(整体提交)。 */
export async function updatePreferences(
  client: MeshApiClient,
  workspaceId: string,
  preferences: readonly PreferenceInput[],
): Promise<readonly Preference[]> {
  return client.request<readonly Preference[]>('PUT', PREFERENCES_PATH, {
    query: { workspace_id: workspaceId },
    body: { preferences },
  });
}

/** 通知类型清单(偏好矩阵行;核心差异:execution_finished 单列「Agent 执行通知」分区)。 */
export const NOTIFICATION_TYPES: readonly NotificationType[] = [
  'assigned',
  'mentioned',
  'subscribed_update',
  'comment_created',
  'status_changed',
  'review_requested',
  'due_soon',
  'execution_finished',
];
