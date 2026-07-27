/**
 * AddMemberDialog 测试(member.md §4.2):邀请人类经 invitations API。
 * Agent 创建不经本弹窗(唯一入口为名册页「+ 新建 Agent」向导,README §6.12 / T35)。
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
        onInvited={() => undefined}
      />,
    );
    await user.type(screen.getByTestId('invite-email'), 'x@corp.com');
    await user.click(screen.getByTestId('invite-submit'));
    expect(await screen.findByText('invite failed')).toBeInTheDocument();
  });
});
