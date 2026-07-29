/**
 * 激活路径深链唯一真源(onboarding.md §4.2「空状态主操作与清单 CTA 复用同一深链,
 * 避免双份维护」+ §5.1「与清单 CTA 共享深链」+ §1.2.1 深链既有向导目录)。
 *
 * 清单步骤 CTA(OnboardingChecklist)与各页空状态主操作(BoardPage / MembersPage /
 * InboxPage …)同读本模块,深链一律落地既有页面/入口,不另建向导。
 */
import type { OnboardingStepKey } from './types';

/** 新建 issue 既有入口(快捷键 `c` / `/issues?create=1` 快速创建,issue.md)。 */
export const CREATE_ISSUE_PATH = '/issues?create=1';
/** 成员名册页(邀请面板 + 唯一 agent 创建入口,README §6.12)。 */
export const MEMBERS_ROSTER_PATH = '/members';
/** 收件箱(aha moment 观测面,comment-inbox.md)。 */
export const INBOX_PATH = '/inbox';
/** 看板(issue 的可视化投影,kanban.md)。 */
export const BOARD_PATH = '/board';

/** 工作区设置(workspace.md §4.2 创建向导落点)。 */
export function workspaceSettingsPath(workspaceSlug: string | null): string {
  return workspaceSlug !== null ? `/w/${workspaceSlug}/settings` : '/settings';
}

/** issue 详情页(分派 assignee / @提及 composer 所在,issue.md / comment-inbox.md)。 */
export function issueDetailPath(issueId: string): string {
  return `/issues/${issueId}`;
}

export interface StepDeeplinkContext {
  readonly workspaceSlug: string | null;
  /** 工作区最新 issue id(用于步骤 4 深链 issue 详情;无 issue 时为 null)。 */
  readonly latestIssueId: string | null;
}

/**
 * 激活路径五步 CTA 深链(§1.2.1 表):
 * 1 建区 → 工作区设置;2 邀请/加 agent → 成员名册;3 建首 issue → 新建 issue 入口;
 * 4 分派/@ → issue 详情(分派 assignee / 评论 composer;无 issue 时回退看板);
 * 5 见回评 → 收件箱。
 */
export function stepDeeplink(stepKey: OnboardingStepKey, ctx: StepDeeplinkContext): string {
  switch (stepKey) {
    case 'create_workspace':
      return workspaceSettingsPath(ctx.workspaceSlug);
    case 'invite_member_or_add_agent':
      return MEMBERS_ROSTER_PATH;
    case 'create_first_issue':
      return CREATE_ISSUE_PATH;
    case 'dispatch_or_mention_agent':
      return ctx.latestIssueId !== null ? issueDetailPath(ctx.latestIssueId) : BOARD_PATH;
    case 'see_agent_reply_in_inbox':
      return INBOX_PATH;
  }
}
