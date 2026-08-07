/**
 * ReassignMemberDialog 测试(L247,member.md「reassign」):
 * 选目标确认后 POST /members/reassign、成功 toast、失败就地呈现错误且不关闭。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { ReassignMemberDialog } from '../ReassignMemberDialog';
import type { MemberSummary } from '../types';

const SOURCE: MemberSummary = {
  id: 'mem-h',
  member_type: 'human',
  role: 'member',
  status: 'active',
  display_name: 'Jane Doe',
  joined_at: null,
  profile: null,
};

const TARGET: MemberSummary = {
  id: 'mem-a',
  member_type: 'agent',
  role: 'member',
  status: 'active',
  display_name: 'Code Bot',
  joined_at: null,
  profile: null,
};

function makeClient(result: unknown = { reassigned_issues: 3 }) {
  const request = vi.fn(async () => result);
  return { client: { request } as unknown as MeshApiClient, request };
}

function renderDialog(client: MeshApiClient, onClose: () => void = () => undefined) {
  return renderWithProviders(
    <ReassignMemberDialog
      open
      onClose={onClose}
      client={client}
      workspaceId="ws-1"
      member={SOURCE}
      targets={[TARGET]}
    />,
  );
}

describe('ReassignMemberDialog (L247)', () => {
  it('未选目标时确认禁用;选择后 POST /members/reassign 并回报条数', async () => {
    const user = userEvent.setup();
    const { client, request } = makeClient();
    const onClose = vi.fn();
    renderDialog(client, onClose);

    expect(screen.getByTestId('reassign-dialog-body').textContent).toContain('Jane Doe');
    expect((screen.getByTestId('reassign-dialog-confirm') as HTMLButtonElement).disabled).toBe(
      true,
    );

    await user.selectOptions(screen.getByTestId('reassign-dialog-target'), 'mem-a');
    await user.click(screen.getByTestId('reassign-dialog-confirm'));

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/members/reassign', {
        body: { from_member_id: 'mem-h', to_member_id: 'mem-a' },
      }),
    );
    expect(await screen.findByText('3 issue(s) reassigned')).toBeInTheDocument();
    expect(onClose).toHaveBeenCalled();
  });

  it('失败时就地呈现错误且不关闭', async () => {
    const user = userEvent.setup();
    const request = vi.fn(async () => {
      throw new Error('boom');
    });
    const client = { request } as unknown as MeshApiClient;
    const onClose = vi.fn();
    renderDialog(client, onClose);

    await user.selectOptions(screen.getByTestId('reassign-dialog-target'), 'mem-a');
    await user.click(screen.getByTestId('reassign-dialog-confirm'));

    expect(await screen.findByText('boom')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('非 Error 拒绝时回退通用错误文案', async () => {
    const user = userEvent.setup();
    const request = vi.fn(async () => {
      throw 'string failure';
    });
    const client = { request } as unknown as MeshApiClient;
    renderDialog(client);

    await user.selectOptions(screen.getByTestId('reassign-dialog-target'), 'mem-a');
    await user.click(screen.getByTestId('reassign-dialog-confirm'));

    expect(await screen.findByText('Something went wrong. Please try again.')).toBeInTheDocument();
  });
});
