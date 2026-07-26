/**
 * 收件箱按 issue 分组(comment-inbox.md I8 / §4.2):同一 issue 的通知折叠成组,
 * 组头为 issue 标识 + 标题(取自通知 issue 快照,缺失退回 id)。纯函数,稳定组序
 * (按组内最新通知的 created_at 降序)。
 */
import type { Notification, NotificationIssueRef } from './types';

export interface NotificationGroup {
  readonly issueId: string;
  readonly issue: NotificationIssueRef | null;
  readonly items: readonly Notification[];
  readonly latestCreatedAt: string;
}

/** 按 issue_id 分组;issue_id 为 null 的通知归入 'none' 组。 */
export function groupNotifications(notifications: readonly Notification[]): readonly NotificationGroup[] {
  const byIssue = new Map<string, Notification[]>();
  for (const notification of notifications) {
    const key = notification.issue_id ?? 'none';
    const bucket = byIssue.get(key);
    if (bucket === undefined) byIssue.set(key, [notification]);
    else bucket.push(notification);
  }
  const groups: NotificationGroup[] = [];
  for (const [issueId, items] of byIssue) {
    const issue = items.find((item) => item.issue !== undefined)?.issue ?? null;
    let latestCreatedAt = '';
    for (const item of items) {
      if (item.created_at > latestCreatedAt) latestCreatedAt = item.created_at;
    }
    groups.push({ issueId, issue, items, latestCreatedAt });
  }
  groups.sort((a, b) => (a.latestCreatedAt < b.latestCreatedAt ? 1 : -1));
  return groups;
}
