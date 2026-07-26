/**
 * MilestonesPanel 分支级测试(project.md §4.1 里程碑 Tab):逾期样式/无目标日回退、
 * 关闭态里程碑重开(toggle 双向)、切换失败的非 API 错误 toast、删除二次确认的
 * 取消/确认/失败三分支、空列表空态。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient, MeshApiError } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import * as projectsApi from '../api';
import { MilestonesPanel } from '../MilestonesPanel';
import type { Milestone } from '../types';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, updateMilestone: vi.fn(), deleteMilestone: vi.fn() };
});

const updateMilestoneMock = vi.mocked(projectsApi.updateMilestone);
const deleteMilestoneMock = vi.mocked(projectsApi.deleteMilestone);

function makeMilestone(overrides: Partial<Milestone> = {}): Milestone {
  return {
    id: 'mst-1',
    project_id: 'prj-1',
    title: 'Beta',
    description: null,
    target_date: '2026-09-01',
    state: 'open',
    overdue: false,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function renderPanel(
  milestones: readonly Milestone[],
): { upsertMilestone: (milestone: Milestone) => void; removeMilestone: (id: string) => void } {
  const upsertMilestone = vi.fn();
  const removeMilestone = vi.fn();
  renderWithProviders(
    <MilestonesPanel
      client={new MeshApiClient({ baseUrl: '', getToken: () => 'tok-test' })}
      projectId="prj-1"
      milestones={milestones}
      upsertMilestone={upsertMilestone}
      removeMilestone={removeMilestone}
    />,
  );
  return { upsertMilestone, removeMilestone };
}

describe('MilestonesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the empty state when there are no milestones', () => {
    renderPanel([]);
    expect(screen.getByText('Nothing here yet')).toBeDefined();
    expect(screen.getByText('No milestones yet.')).toBeDefined();
    expect(screen.queryByTestId('milestone-list')).toBeNull();
  });

  it('marks overdue milestones and omits the due text without a target date', () => {
    renderPanel([
      makeMilestone({ id: 'mst-over', overdue: true, target_date: '2026-07-01' }),
      makeMilestone({ id: 'mst-nodate', target_date: null, state: 'closed' }),
    ]);
    const overdueRow = screen.getByTestId('milestone-mst-over');
    const noDateRow = screen.getByTestId('milestone-mst-nodate');
    expect(overdueRow.className).toContain('--overdue');
    expect(noDateRow.className).not.toContain('--overdue');
    expect(overdueRow.textContent).toContain('Overdue');
    expect(noDateRow.textContent).not.toContain('Overdue');
  });

  it('closes an open milestone (toggle open → closed)', async () => {
    const closed = makeMilestone({ state: 'closed' });
    updateMilestoneMock.mockResolvedValue(closed);
    const user = userEvent.setup();
    const { upsertMilestone } = renderPanel([makeMilestone()]);

    await user.click(screen.getByTestId('milestone-toggle-mst-1'));

    await waitFor(() =>
      expect(updateMilestoneMock).toHaveBeenCalledWith(expect.anything(), 'mst-1', {
        state: 'closed',
      }),
    );
    expect(upsertMilestone).toHaveBeenCalledWith(closed);
  });

  it('reopens a closed milestone (toggle closed → open)', async () => {
    updateMilestoneMock.mockResolvedValue(makeMilestone({ state: 'open' }));
    const user = userEvent.setup();
    const { upsertMilestone } = renderPanel([makeMilestone({ state: 'closed' })]);

    await user.click(screen.getByTestId('milestone-toggle-mst-1'));

    await waitFor(() =>
      expect(updateMilestoneMock).toHaveBeenCalledWith(expect.anything(), 'mst-1', {
        state: 'open',
      }),
    );
    expect(upsertMilestone).toHaveBeenCalled();
  });

  it('shows the unknown error toast when a toggle fails with a non-API error', async () => {
    updateMilestoneMock.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    const { upsertMilestone } = renderPanel([makeMilestone()]);

    await user.click(screen.getByTestId('milestone-toggle-mst-1'));

    expect(await screen.findByText('Something went wrong. Please try again.')).toBeDefined();
    expect(upsertMilestone).not.toHaveBeenCalled();
  });

  it('shows the API error toast when a toggle fails with an API error', async () => {
    updateMilestoneMock.mockRejectedValue(
      new MeshApiError({ status: 500, code: 'internal_error', message: 'x' }),
    );
    const user = userEvent.setup();
    const { upsertMilestone } = renderPanel([makeMilestone()]);

    await user.click(screen.getByTestId('milestone-toggle-mst-1'));

    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
    expect(upsertMilestone).not.toHaveBeenCalled();
  });

  it('cancels the delete confirmation without deleting', async () => {
    const user = userEvent.setup();
    const { removeMilestone } = renderPanel([makeMilestone()]);

    await user.click(screen.getByTestId('milestone-delete-mst-1'));
    expect(screen.getByTestId('milestone-delete-confirm-text')).toBeDefined();
    await user.click(screen.getByText('Cancel'));

    expect(screen.queryByTestId('milestone-delete-confirm')).toBeNull();
    expect(deleteMilestoneMock).not.toHaveBeenCalled();
    expect(removeMilestone).not.toHaveBeenCalled();
  });

  it('deletes after confirmation', async () => {
    deleteMilestoneMock.mockResolvedValue({ id: 'mst-1', deleted: true });
    const user = userEvent.setup();
    const { removeMilestone } = renderPanel([makeMilestone()]);

    await user.click(screen.getByTestId('milestone-delete-mst-1'));
    await user.click(screen.getByTestId('milestone-delete-confirm'));

    await waitFor(() => expect(deleteMilestoneMock).toHaveBeenCalledWith(expect.anything(), 'mst-1'));
    expect(removeMilestone).toHaveBeenCalledWith('mst-1');
    expect(screen.queryByTestId('milestone-delete-confirm')).toBeNull();
  });

  it('keeps the confirmation open and toasts when delete fails with an API error', async () => {
    deleteMilestoneMock.mockRejectedValue(
      new MeshApiError({ status: 409, code: 'conflict', message: 'x' }),
    );
    const user = userEvent.setup();
    const { removeMilestone } = renderPanel([makeMilestone()]);

    await user.click(screen.getByTestId('milestone-delete-mst-1'));
    await user.click(screen.getByTestId('milestone-delete-confirm'));

    expect(
      await screen.findByText('This item was updated elsewhere. Please refresh and try again.'),
    ).toBeDefined();
    expect(removeMilestone).not.toHaveBeenCalled();
    expect(screen.getByTestId('milestone-delete-confirm')).toBeDefined();
  });

  it('opens the create dialog from the panel button', async () => {
    const user = userEvent.setup();
    renderPanel([]);
    await user.click(screen.getByTestId('create-milestone-button'));
    expect(await screen.findByText('New milestone')).toBeDefined();
  });
});
