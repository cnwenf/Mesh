/**
 * MembersPage 空状态主操作 → 乐观推进步骤 2 接线测试(onboarding.md §1.2.2 O9/末注):
 * 邀请发送成功 / agent 创建成功后请求乐观完成 invite_member_or_add_agent,
 * 服务端领域事件(member.added)复核收敛。对话框以最小替身隔离(本页只测接线)。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { requestOptimisticStepComplete } from '../../onboarding/notify';
import { MembersPage } from '../MembersPage';

vi.mock('../../onboarding/notify', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../onboarding/notify')>();
  return { ...actual, requestOptimisticStepComplete: vi.fn() };
});

vi.mock('../AddMemberDialog', () => ({
  AddMemberDialog: (props: { open: boolean; onInvited: () => void }): React.JSX.Element | null =>
    props.open ? (
      <button type="button" data-testid="mock-invite-succeed" onClick={props.onInvited}>
        invite-succeed
      </button>
    ) : null,
}));

vi.mock('../../agents/AgentWizard', () => ({
  AgentWizard: (props: { open: boolean; onSaved: (agentId: string) => void }): React.JSX.Element | null =>
    props.open ? (
      <button type="button" data-testid="mock-agent-created" onClick={() => props.onSaved('agt-1')}>
        agent-created
      </button>
    ) : null,
}));

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

function stubEmptyRoster(): void {
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME_OWNER } });
    if (method === 'GET' && url.includes('/members')) {
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nope' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
}

beforeEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(requestOptimisticStepComplete).mockClear();
});
afterEach(() => vi.unstubAllGlobals());

describe('MembersPage 空状态主操作乐观推进步骤 2(§1.2.2 O9)', () => {
  it('requests optimistic step-2 completion after an invite succeeds', async () => {
    const user = userEvent.setup();
    stubEmptyRoster();
    renderWithProviders(<MembersPage />);

    await waitFor(() => expect(screen.getByTestId('illustration-roster')).toBeInTheDocument());
    await user.click(screen.getByTestId('members-empty-invite'));
    await user.click(await screen.findByTestId('mock-invite-succeed'));

    expect(requestOptimisticStepComplete).toHaveBeenCalledWith('invite_member_or_add_agent');
  });

  it('requests optimistic step-2 completion after an agent is created', async () => {
    const user = userEvent.setup();
    stubEmptyRoster();
    renderWithProviders(<MembersPage />);

    await waitFor(() => expect(screen.getByTestId('illustration-roster')).toBeInTheDocument());
    await user.click(screen.getByTestId('members-empty-agent'));
    await user.click(await screen.findByTestId('mock-agent-created'));

    expect(requestOptimisticStepComplete).toHaveBeenCalledWith('invite_member_or_add_agent');
  });
});
