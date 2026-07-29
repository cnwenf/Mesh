/**
 * 当前收件箱视图 filter 的跨模块共享(comment-inbox.md / search-command-palette.md
 * §1.2 S3 命令 ⑧:「标记全部已读」随当前视图 filter 口径)。
 *
 * InboxPage 在 filter 变化时写入;shell 命令面板的「标记全部已读」命令读取,
 * 使命令与收件箱页按钮发送同一 filter。纯前端内存态(会话级),不落存储。
 */
import type { InboxFilter } from './types';

let currentInboxFilter: InboxFilter = 'all';
let currentInboxWorkspaceId: string | null = null;

export function setCurrentInboxView(workspaceId: string | null, filter: InboxFilter): void {
  currentInboxWorkspaceId = workspaceId;
  currentInboxFilter = filter;
}

export function getCurrentInboxView(): { workspaceId: string | null; filter: InboxFilter } {
  return { workspaceId: currentInboxWorkspaceId, filter: currentInboxFilter };
}
