/**
 * 通知跳转目标(comment-inbox.md I12 / §4.3):点击通知直达对应 issue + 评论锚点。
 * 锚点优先取 latest_comment_id(分组最新),退回 comment_id;无 issue 则不可跳。
 */
import type { Notification } from './types';

export function notificationTargetPath(notification: Notification): string | null {
  if (notification.issue_id === null) return null;
  const anchor = notification.latest_comment_id ?? notification.comment_id;
  return anchor !== null
    ? `/issues/${notification.issue_id}#comment-${anchor}`
    : `/issues/${notification.issue_id}`;
}
