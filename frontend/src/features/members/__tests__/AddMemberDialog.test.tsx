/**
 * AddMemberDialog 测试(member.md §4.2):「邀请人类」Tab 经 invitations API;「AI agent」
 * Tab 为占位态(agents 表落地前)。单一创建入口,不形成第二套名册。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { AddMemberDialog } from '../AddMemberDialog';

function makeClient(result: unknown = [{ invite_link: '/invite/tok' }]) {
  const request = vi.fn(async () => result);
  return { client: { request } as unknown as MeshApiClient, request };
}

describe('AddMemberDialog', () => {
  it('邀请人类:填写邮箱并提交后调用 invitations API', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    const onInvited = vi.fn();
    renderWithProviders(
      <AddMemberDialog
        open
        onClose={() => undefined}
        client={client}
        workspaceId="ws-1"
        initialTab="human"
        onInvited={onInvited}
      />,
    );
    await user.type(screen.getByTestId('invite-email'), 'new@corp.com');
    await user.click(screen.getByTestId('invite-submit'));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/invitations', {
        body: { emails: ['new@corp.com'], role: 'member' },
      }),
    );
    expect(onInvited).toHaveBeenCalled();
    expect(await screen.findByTestId('invite-done')).toBeInTheDocument();
  });

  it('邀请失败展示错误', async () => {
    const user = userEvent.setup();
    const request = vi.fn(async () => {
      throw new Error('invite failed');
    });
    const client = { request } as unknown as MeshApiClient;
    renderWithProviders(
      <AddMemberDialog
        open
        onClose={() => undefined}
        client={client}
        workspaceId="ws-1"
        initialTab="human"
        onInvited={() => undefined}
      />,
    );
    await user.type(screen.getByTestId('invite-email'), 'x@corp.com');
    await user.click(screen.getByTestId('invite-submit'));
    expect(await screen.findByText('invite failed')).toBeInTheDocument();
  });

  it('AI agent Tab 显示「即将上线」占位态', async () => {
    const user = userEvent.setup();
    const { client } = makeClient();
    renderWithProviders(
      <AddMemberDialog
        open
        onClose={() => undefined}
        client={client}
        workspaceId="ws-1"
        initialTab="agent"
        onInvited={() => undefined}
      />,
    );
    await user.click(screen.getByTestId('add-tab-agent'));
    expect(screen.getByTestId('agent-coming-soon')).toBeInTheDocument();
  });
});
