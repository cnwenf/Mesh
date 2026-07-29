/**
 * 共享深链唯一真源测试(onboarding.md §4.2 共享深链 / §1.2.1 深链既有向导目录):
 * 五步 CTA 目标精确,空状态主操作与清单 CTA 同读一处。
 */
import { describe, expect, it } from 'vitest';
import {
  BOARD_PATH,
  CREATE_ISSUE_PATH,
  INBOX_PATH,
  MEMBERS_ROSTER_PATH,
  issueDetailPath,
  stepDeeplink,
  workspaceSettingsPath,
} from '../deeplinks';

describe('stepDeeplink(§1.2.1 表)', () => {
  const ctx = { workspaceSlug: 'team', latestIssueId: 'iss-1' };

  it('step 1 → workspace settings', () => {
    expect(stepDeeplink('create_workspace', ctx)).toBe('/w/team/settings');
    expect(stepDeeplink('create_workspace', { workspaceSlug: null, latestIssueId: null })).toBe(
      '/settings',
    );
  });

  it('step 2 → members roster (唯一 agent 创建入口所在,README §6.12)', () => {
    expect(stepDeeplink('invite_member_or_add_agent', ctx)).toBe(MEMBERS_ROSTER_PATH);
  });

  it('step 3 → 新建 issue 既有入口(与看板空状态主操作同源)', () => {
    expect(stepDeeplink('create_first_issue', ctx)).toBe(CREATE_ISSUE_PATH);
    expect(CREATE_ISSUE_PATH).toBe('/issues?create=1');
  });

  it('step 4 → issue 详情(分派 assignee / @ 提及 composer);无 issue 回退看板', () => {
    expect(stepDeeplink('dispatch_or_mention_agent', ctx)).toBe('/issues/iss-1');
    expect(stepDeeplink('dispatch_or_mention_agent', { workspaceSlug: 'team', latestIssueId: null })).toBe(
      BOARD_PATH,
    );
  });

  it('step 5 → 收件箱(aha moment 观测面)', () => {
    expect(stepDeeplink('see_agent_reply_in_inbox', ctx)).toBe(INBOX_PATH);
  });
});

describe('path builders', () => {
  it('issueDetailPath', () => {
    expect(issueDetailPath('abc')).toBe('/issues/abc');
  });

  it('workspaceSettingsPath', () => {
    expect(workspaceSettingsPath('slug')).toBe('/w/slug/settings');
    expect(workspaceSettingsPath(null)).toBe('/settings');
  });
});
