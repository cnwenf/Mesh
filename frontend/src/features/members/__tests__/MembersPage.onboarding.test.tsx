/**
 * MembersPage 上手引导接线测试(onboarding.md §1.2.2/§4.2):
 * 空态四要素(邀请成员 + 添加 agent 双主操作,沿用既有入口)、管理员重置上手进度
 * (仅人类成员行 + 二次确认 + toast)。
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { MembersPage } from '../MembersPage';

const ME_OWNER = {
  user: { id: 'usr-owner', email: 'owner@acme.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'Team',
      workspace_slug: 'team',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};
const ME_MEMBER = {
  ...ME_OWNER,
  memberships: [{ ...ME_OWNER.memberships[0], role: 'member' }],
};

const HUMAN = {
  id: 'mem-h',
  member_type: 'human',
  role: 'member',
  status: 'active',
  display_name: 'Jane Doe',
  joined_at: '2026-01-10T08:00:00Z',
  profile: { id: 'usr-1', full_name: 'Jane Doe', email: 'jane@acme.com', avatar_url: null },
};
const AGENT = {
  id: 'mem-a',
  member_type: 'agent',
  role: 'member',
  status: 'active',
  display_name: 'Code Bot',
  joined_at: '2026-02-01T08:00:00Z',
  profile: { id: 'agt-9', name: 'Code Bot', description: 'fixes code', avatar_url: null, is_active: true },
};

interface RoutedResult {
  calls: Array<{ url: string; method: string }>;
}

function stubRoster(members: unknown[], me: unknown = ME_OWNER): RoutedResult {
  const calls: Array<{ url: string; method: string }> = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (method === 'POST' && url.includes('/onboarding/reset')) {
      return fakeResponse({
        body: {
          data: {
            id: 'obs-new',
            checklist: 'activation',
            aha_reached_at: null,
            dismissed_at: null,
            progress: { total: 5, completed: 1, skipped: 0 },
          },
        },
      });
    }
    if (method === 'GET' && url.includes('/members')) {
      return fakeResponse({ body: { data: members, next_cursor: null } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nope' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return { calls };
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe('MembersPage onboarding empty state', () => {
  it('offers invite + add-agent actions reusing the existing entry points', async () => {
    const user = userEvent.setup();
    stubRoster([]);
    renderWithProviders(<MembersPage />);

    await waitFor(() => expect(screen.getByTestId('illustration-roster')).toBeInTheDocument());
    expect(screen.getByText('No members yet')).toBeInTheDocument();
    expect(
      screen.getByText('Invite human colleagues, or add an AI agent as a teammate.'),
    ).toBeInTheDocument();

    // 邀请成员 → 打开既有 AddMemberDialog
    await user.click(screen.getByTestId('members-empty-invite'));
    expect(screen.getByTestId('invite-email')).toBeInTheDocument();

    // 添加 agent → 打开既有 AgentWizard(唯一入口,README §6.12)
    await user.click(screen.getByTestId('members-empty-agent'));
    expect(await screen.findByTestId('agent-wizard-basic')).toBeInTheDocument();
  });
});

describe('MembersPage onboarding reset (admin only)', () => {
  it('resets a human member checklist after confirmation', async () => {
    const user = userEvent.setup();
    const routed = stubRoster([HUMAN, AGENT]);
    renderWithProviders(<MembersPage />);
    await waitFor(() => expect(screen.getByTestId('member-open-mem-h')).toBeInTheDocument());

    // agent 行菜单不含「Reset onboarding」(仅人类成员行,§3.5)。
    const agentRow = screen.getByTestId('member-open-mem-a').closest('tr') as HTMLElement;
    await user.click(within(agentRow).getByRole('button', { name: 'Row actions' }));
    expect(screen.queryByRole('menuitem', { name: 'Reset onboarding' })).not.toBeInTheDocument();

    // 人类成员行菜单含「Reset onboarding」→ 二次确认。
    // (点击人类行触发钮的 pointerdown 在 agent 菜单之外,自动关闭 agent 菜单。)
    const humanRow = screen.getByTestId('member-open-mem-h').closest('tr') as HTMLElement;
    await user.click(within(humanRow).getByRole('button', { name: 'Row actions' }));
    await user.click(screen.getByRole('menuitem', { name: 'Reset onboarding' }));
    expect(screen.getByTestId('reset-onboarding-body')).toBeInTheDocument();

    await user.click(screen.getByTestId('reset-onboarding-confirm'));
    await waitFor(() =>
      expect(
        routed.calls.some(
          (call) => call.method === 'POST' && call.url.includes('/onboarding/reset'),
        ),
      ).toBe(true),
    );
    const resetCall = routed.calls.find((call) => call.url.includes('/onboarding/reset'));
    expect(resetCall?.url).toContain('/workspaces/ws-1/onboarding/reset');
    // 成功 toast
    expect(await screen.findByText('Onboarding progress reset')).toBeInTheDocument();
  });

  it('hides the reset entry from non-admin viewers', async () => {
    stubRoster([HUMAN], ME_MEMBER);
    renderWithProviders(<MembersPage />);
    await waitFor(() => expect(screen.getByTestId('member-open-mem-h')).toBeInTheDocument());
    // 非管理员无行操作菜单(canManage 否),自然无重置入口。
    expect(screen.queryByRole('button', { name: 'Row actions' })).not.toBeInTheDocument();
  });
});
