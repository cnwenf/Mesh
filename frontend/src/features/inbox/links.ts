/**
 * 通知跳转目标(comment-inbox.md I12 / §4.3):点击通知直达对应 issue + 评论锚点。
 * 锚点优先取 latest_comment_id(分组最新),退回 comment_id;无 issue 则不可跳。
 */
import type { Notification } from './types';
import { workspaceRoute } from '../members/useWorkspaceMembership';

export function notificationTargetPath(
  notification: Notification,
  workspaceSlug: string | null = null,
): string | null {
  // Failed/timeout execution notifications must land on the attempt/log audit,
  // not merely the source issue. The issue remains the fallback for ordinary
  // comment and assignment notifications.
  if (notification.execution_id !== null) {
    const suffix = `executions/${encodeURIComponent(notification.execution_id)}`;
    return workspaceSlug === null ? `/${suffix}` : workspaceRoute(workspaceSlug, suffix);
  }
  if (notification.issue_id === null) return null;
  const anchor = notification.latest_comment_id ?? notification.comment_id;
  const issuePath =
    workspaceSlug === null
      ? `/issues/${notification.issue_id}`
      : workspaceRoute(workspaceSlug, `issues/${notification.issue_id}`);
  return anchor !== null ? `${issuePath}#comment-${anchor}` : issuePath;
}
