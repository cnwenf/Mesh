/**
 * RemoveMemberDialog 测试(member.md §4.3):移除经 DELETE(可带转派目标),停用经 PATCH status。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { RemoveMemberDialog } from '../RemoveMemberDialog';
import type { MemberSummary } from '../types';

const MEMBER: MemberSummary = {
  id: 'mem-h',
  member_type: 'human',
  role: 'member',
  status: 'active',
  display_name: 'Jane Doe',
  joined_at: null,
  profile: null,
};

const TARGET: MemberSummary = {
  id: 'mem-t',
  member_type: 'human',
  role: 'member',
  status: 'active',
  display_name: 'Target',
  joined_at: null,
  profile: null,
};

function makeClient(result: unknown = { removed: true, reassigned_issues: 1 }) {
  const request = vi.fn(async () => result);
  return { client: { request } as unknown as MeshApiClient, request };
}

describe('RemoveMemberDialog', () => {
  it('移除模式:确认后以 DELETE + reassign_to 调用', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    const onClose = vi.fn();
    const onChanged = vi.fn();
    renderWithProviders(
      <RemoveMemberDialog
        open
        mode="remove"
        onClose={onClose}
        client={client}
        workspaceId="ws-1"
        member={MEMBER}
        reassignTargets={[TARGET]}
        onChanged={onChanged}
      />,
    );

    await user.selectOptions(screen.getByTestId('reassign-target'), 'mem-t');
    await user.click(screen.getByTestId('remove-confirm'));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/workspaces/ws-1/members/mem-h', {
        query: { reassign_to: 'mem-t' },
      }),
    );
    expect(onChanged).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('停用模式:确认后以 PATCH status=disabled 调用', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    renderWithProviders(
      <RemoveMemberDialog
        open
        mode="disable"
        onClose={() => undefined}
        client={client}
        workspaceId="ws-1"
        member={MEMBER}
        reassignTargets={[]}
        onChanged={() => undefined}
      />,
    );
    await user.click(screen.getByTestId('remove-confirm'));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/workspaces/ws-1/members/mem-h', {
        body: { status: 'disabled' },
      }),
    );
  });

  it('失败时展示错误信息', async () => {
    const user = userEvent.setup();
    const request = vi.fn(async () => {
      throw new Error('boom');
    });
    const client = { request } as unknown as MeshApiClient;
    renderWithProviders(
      <RemoveMemberDialog
        open
        mode="remove"
        onClose={() => undefined}
        client={client}
        workspaceId="ws-1"
        member={MEMBER}
        reassignTargets={[]}
        onChanged={() => undefined}
      />,
    );
    await user.click(screen.getByTestId('remove-confirm'));
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });
});
