/**
 * IssueSquadAssignment 组件测试(issue.md §4.3-2 / squad.md §1.2 S4):
 * 活跃分派 → 单一责任主体徽章(组长头像 + 「{squad} · led by {leader}」深链);
 * 「分派给小队」对话框列出活跃小队(含 member_preview),选定即 assignTask(202)
 * → 刷新徽章并通知父级;422 squad_no_leader 呈现服务端错误。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { IssueSquadAssignment } from '../IssueSquadAssignment';

const LEADER = { member_id: 'mem-1', member_type: 'human', name: 'Owner' };

const ASSIGNMENT = {
  assignment_id: 'asg-1',
  squad_id: 'sq-1',
  squad_name: 'Platform',
  issue_id: 'iss-1',
  root_task_id: 'tk-1',
  leader: LEADER,
  assigned_at: '2026-07-01T00:00:00Z',
};

function squadFixture(id: string, name: string) {
  return {
    id,
    workspace_id: 'ws-1',
    name,
    description: 'Owns the platform',
    instructions: null,
    avatar_url: null,
    kind: 'standing',
    status: 'active',
    leader_mode: 'single',
    primary_leader_id: 'mem-1',
    primary_leader: LEADER,
    require_plan_approval: false,
    max_decompose_depth: 2,
    member_count: 2,
    active_task_count: 1,
    leaders: [LEADER],
    member_preview: [
      { member_id: 'mem-1', member_type: 'human', name: 'Owner', role: 'leader' },
      { member_id: 'mem-2', member_type: 'agent', name: 'Builder', role: 'member' },
    ],
    archived_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  };
}

function renderAssignment(onChanged: () => void = () => undefined): void {
  renderWithProviders(
    <IssueSquadAssignment workspaceId="ws-1" issueId="iss-1" onChanged={onChanged} />,
    { route: '/issues/iss-1' },
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('IssueSquadAssignment', () => {
  it('renders the single-responsibility badge when an active assignment exists', async () => {
    const stub = stubFetch(fakeResponse({ body: { data: ASSIGNMENT } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderAssignment();
    const badge = await screen.findByTestId('issue-squad-badge');
    expect(badge.textContent).toContain('Platform');
    expect(badge.textContent).toContain('Owner');
    expect(badge.getAttribute('href')).toBe('/squads/sq-1');
    // 查询走 by-issue 端点
    expect(String(stub.calls[0].url)).toContain('/squads/assignments/by-issue/iss-1');
  });

  it('hides the badge when there is no active assignment', async () => {
    const stub = stubFetch(fakeResponse({ body: { data: null } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderAssignment();
    await screen.findByTestId('issue-squad-assignment');
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(1));
    expect(screen.queryByTestId('issue-squad-badge')).toBeNull();
  });

  it('assigns the issue to a squad from the dialog and refreshes', async () => {
    const onChanged = vi.fn();
    const stub = stubFetch(
      fakeResponse({ body: { data: null } }),
      fakeResponse({ body: { data: [squadFixture('sq-1', 'Platform')], next_cursor: null } }),
      fakeResponse({ status: 202, body: { data: ASSIGNMENT } }),
      fakeResponse({ body: { data: ASSIGNMENT } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderAssignment(onChanged);
    await screen.findByTestId('issue-assign-squad');
    fireEvent.click(screen.getByTestId('issue-assign-squad'));
    // 对话框列出活跃小队(含成员墙)
    await screen.findByTestId('issue-squad-option-sq-1');
    expect(screen.getByTestId('squad-avatarwall')).toBeTruthy();
    fireEvent.click(screen.getByTestId('issue-squad-assign-sq-1'));
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.init?.method === 'POST');
      expect(posts.length).toBe(1);
      expect(String(posts[0].url)).toContain('/squads/sq-1/tasks');
      expect(JSON.parse(String(posts[0].init?.body))).toEqual({ issue_id: 'iss-1' });
    });
    // 分派成功:刷新徽章 + 通知父级
    await screen.findByTestId('issue-squad-badge');
    expect(onChanged).toHaveBeenCalled();
  });

  it('surfaces the server error on 422 squad_no_leader', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: null } }),
      fakeResponse({ body: { data: [squadFixture('sq-1', 'Platform')], next_cursor: null } }),
      fakeResponse({
        status: 422,
        body: { error: { code: 'squad_no_leader', message: 'no leader' } },
      }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderAssignment();
    await screen.findByTestId('issue-assign-squad');
    fireEvent.click(screen.getByTestId('issue-assign-squad'));
    await screen.findByTestId('issue-squad-option-sq-1');
    fireEvent.click(screen.getByTestId('issue-squad-assign-sq-1'));
    await screen.findByTestId('issue-squad-error');
    expect(screen.getByTestId('issue-squad-error').textContent).toContain('no leader');
  });
});
