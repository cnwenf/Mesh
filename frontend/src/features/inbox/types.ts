/**
 * 收件箱 / 通知实体类型(comment-inbox.md §2.6 / §2.7 / §3.2)。
 * 字段一律 snake_case(与后端信封逐字对齐)。`actor.member_type` 为服务端快照(真源 members)。
 */

export type NotificationType =
  | 'assigned'
  | 'mentioned'
  | 'subscribed_update'
  | 'comment_created'
  | 'status_changed'
  | 'execution_finished'
  | 'review_requested'
  | 'due_soon';

export type NotificationPriority = 'critical' | 'normal';

export type InboxFilter = 'unread' | 'all' | 'mentions' | 'assigned' | 'agent';

export type EmailPolicy = 'none' | 'realtime' | 'digest';

export interface NotificationActor {
  readonly id: string;
  readonly member_type: 'human' | 'agent';
  readonly name: string;
}

/** 关联 issue 快照(分组组头渲染:标识 + 标题;源实体删除时由 payload 兜底)。 */
export interface NotificationIssueRef {
  readonly id: string;
  readonly identifier: string;
  readonly title: string;
}

export interface Notification {
  readonly id: string;
  readonly type: NotificationType;
  readonly priority: NotificationPriority;
  readonly issue_id: string | null;
  readonly comment_id: string | null;
  readonly execution_id: string | null;
  readonly group_key: string | null;
  readonly actor: NotificationActor | null;
  readonly preview: string;
  readonly title: string;
  readonly count: number;
  readonly read_at: string | null;
  readonly archived_at: string | null;
  readonly created_at: string;
  readonly latest_comment_id: string | null;
  /** 分组渲染所需的 issue 快照(§3.2;可选,缺失时组头退回 id)。 */
  readonly issue?: NotificationIssueRef;
}

/** 通知偏好(§2.7):event_type 为通知类型或 'all'。 */
export interface Preference {
  readonly id?: string;
  readonly event_type: string;
  readonly in_app: boolean;
  readonly email: EmailPolicy;
  readonly quiet_hours_start: string | null;
  readonly quiet_hours_end: string | null;
}

/** PUT /notification-preferences 请求体条目。 */
export interface PreferenceInput {
  readonly event_type: string;
  readonly in_app: boolean;
  readonly email: EmailPolicy;
  readonly quiet_hours_start?: string | null;
  readonly quiet_hours_end?: string | null;
}

/** 收件箱列表参数(§3.2)。 */
export interface ListInboxParams {
  readonly workspaceId: string;
  readonly limit?: number;
  readonly cursor?: string;
  readonly filter?: InboxFilter;
  readonly type?: NotificationType;
  readonly grouped?: boolean;
}

/** Notification 运行时结构守卫(realtime 帧 / 边界处校验,不信任外部载荷)。 */
export function isNotification(value: unknown): value is Notification {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.id === 'string' && typeof candidate.type === 'string';
}

/** 未读判定:read_at 为空且未归档。 */
export function isUnread(notification: Notification): boolean {
  return notification.read_at === null && notification.archived_at === null;
}
