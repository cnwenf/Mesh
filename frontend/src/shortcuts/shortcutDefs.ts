/**
 * 快捷键声明全表(search-command-palette.md §4.3 全集,评审 H6)。
 *
 * 单一声明源:运行时注册(shell / board / issue / chat 各处)的 combo/group
 * 与本表一致;CI 静态断言(__tests__/conflicts.test.ts)枚举本表,按 active
 * context 组合检查 combo 唯一性(同优先级冲突 = 编程错误)与跨上下文仲裁
 * 胜者(§4.3.1)。labelKey 经 i18n 消息目录外部化。
 */
import type { ShortcutContext } from './registry';

export interface ShortcutDecl {
  readonly id: string;
  /** 归一化组合键:'c'、'/'、'mod+k'、序列 'g i' 等(与 ShortcutProvider 约定一致) */
  readonly combo: string;
  readonly labelKey: string;
  readonly group: ShortcutContext;
}

export const SHORTCUT_DECLS: readonly ShortcutDecl[] = [
  // —— 全局组(任意页面,§4.3 全局组 8 条)——
  { id: 'palette', combo: 'mod+k', labelKey: 'shortcuts.actionPalette', group: 'global' },
  { id: 'focus.search', combo: '/', labelKey: 'shortcuts.actionFocusSearch', group: 'global' },
  { id: 'new.issue', combo: 'c', labelKey: 'shortcuts.actionNewIssue', group: 'global' },
  { id: 'help', combo: '?', labelKey: 'shortcuts.actionHelp', group: 'global' },
  { id: 'go.inbox', combo: 'g i', labelKey: 'shortcuts.actionGoInbox', group: 'global' },
  { id: 'go.board', combo: 'g b', labelKey: 'shortcuts.actionGoBoard', group: 'global' },
  { id: 'go.members', combo: 'g m', labelKey: 'shortcuts.actionGoMembers', group: 'global' },
  { id: 'go.automation', combo: 'g a', labelKey: 'shortcuts.actionGoAutomation', group: 'global' },

  // —— 看板组(看板页挂载激活,S10 / §4.3)——
  { id: 'board.move.up', combo: 'arrowup', labelKey: 'shortcuts.boardMove', group: 'board' },
  { id: 'board.move.down', combo: 'arrowdown', labelKey: 'shortcuts.boardMove', group: 'board' },
  { id: 'board.move.left', combo: 'arrowleft', labelKey: 'shortcuts.boardMove', group: 'board' },
  { id: 'board.move.right', combo: 'arrowright', labelKey: 'shortcuts.boardMove', group: 'board' },
  { id: 'board.move.up.vim', combo: 'k', labelKey: 'shortcuts.boardMove', group: 'board' },
  { id: 'board.move.down.vim', combo: 'j', labelKey: 'shortcuts.boardMove', group: 'board' },
  { id: 'board.move.left.vim', combo: 'h', labelKey: 'shortcuts.boardMove', group: 'board' },
  { id: 'board.move.right.vim', combo: 'l', labelKey: 'shortcuts.boardMove', group: 'board' },
  // §4.3.1 规则 3:看板 C 复用全局新建弹窗并预填当前列;看板激活时仲裁胜出于全局 C。
  { id: 'board.new.card', combo: 'c', labelKey: 'shortcuts.boardNewCard', group: 'board' },
  { id: 'board.change.status', combo: 's', labelKey: 'shortcuts.boardChangeStatus', group: 'board' },
  { id: 'board.change.assignee', combo: 'a', labelKey: 'shortcuts.boardChangeAssignee', group: 'board' },
  { id: 'board.open.card', combo: 'enter', labelKey: 'shortcuts.boardOpenCard', group: 'board' },
  { id: 'board.filter', combo: 'f', labelKey: 'shortcuts.boardFilter', group: 'board' },

  // —— issue 详情组(详情挂载激活,S11 / §4.3)——
  { id: 'issue.edit', combo: 'e', labelKey: 'shortcuts.issueEdit', group: 'issue' },
  { id: 'issue.status', combo: 's', labelKey: 'shortcuts.issueStatus', group: 'issue' },
  { id: 'issue.assignee', combo: 'a', labelKey: 'shortcuts.issueAssignee', group: 'issue' },
  { id: 'issue.priority', combo: 'p', labelKey: 'shortcuts.issuePriority', group: 'issue' },
  { id: 'issue.labels', combo: 'l', labelKey: 'shortcuts.issueLabels', group: 'issue' },
  { id: 'issue.milestone', combo: 'm', labelKey: 'shortcuts.issueMilestone', group: 'issue' },
  { id: 'issue.submit.comment', combo: 'mod+enter', labelKey: 'shortcuts.issueSubmitComment', group: 'issue' },
  { id: 'issue.close', combo: 'esc', labelKey: 'shortcuts.issueClose', group: 'issue' },

  // —— 聊天组(会话页独占激活,S12 / §4.3)——
  { id: 'chat.send', combo: 'enter', labelKey: 'shortcuts.chatSend', group: 'chat' },
  { id: 'chat.newline', combo: 'shift+enter', labelKey: 'shortcuts.chatNewline', group: 'chat' },
  { id: 'chat.edit.last', combo: 'mod+arrowup', labelKey: 'shortcuts.chatEditLast', group: 'chat' },
  { id: 'chat.blur', combo: 'esc', labelKey: 'shortcuts.chatBlur', group: 'chat' },
];
