/**
 * CreateMilestoneDialog 分支级测试(project.md §4.3):带/不带目标日的成功提交、
 * 空标题守卫、API 失败内联错误、非 API 错误回退 unknownError。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient, MeshApiError } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import * as projectsApi from '../api';
import { CreateMilestoneDialog } from '../CreateMilestoneDialog';
import type { Milestone } from '../types';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, createMilestone: vi.fn() };
});

const createMilestoneMock = vi.mocked(projectsApi.createMilestone);

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

function renderDialog(): { onClose: () => void; onCreated: (milestone: Milestone) => void } {
  const onClose = vi.fn();
  const onCreated = vi.fn();
  renderWithProviders(
    <CreateMilestoneDialog
      open
      onClose={onClose}
      client={new MeshApiClient({ baseUrl: '', getToken: () => 'tok-test' })}
      projectId="prj-1"
      onCreated={onCreated}
    />,
  );
  return { onClose, onCreated };
}

describe('CreateMilestoneDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('creates a milestone with a target date, toasts and closes', async () => {
    const created = makeMilestone();
    createMilestoneMock.mockResolvedValue(created);
    const user = userEvent.setup();
    const { onClose, onCreated } = renderDialog();

    await user.type(screen.getByTestId('milestone-title-input'), 'Beta');
    fireEvent.change(screen.getByTestId('milestone-target-input'), {
      target: { value: '2026-09-01' },
    });
    await user.click(screen.getByTestId('create-milestone-submit'));

    await waitFor(() =>
      expect(createMilestoneMock).toHaveBeenCalledWith(expect.anything(), 'prj-1', {
        title: 'Beta',
        target_date: '2026-09-01',
      }),
    );
    expect(onCreated).toHaveBeenCalledWith(created);
    expect(onClose).toHaveBeenCalled();
    expect(await screen.findByText('Milestone created.')).toBeDefined();
  });

  it('omits target_date when no date is picked', async () => {
    createMilestoneMock.mockResolvedValue(makeMilestone());
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByTestId('milestone-title-input'), 'No date');
    await user.click(screen.getByTestId('create-milestone-submit'));

    await waitFor(() =>
      expect(createMilestoneMock).toHaveBeenCalledWith(expect.anything(), 'prj-1', {
        title: 'No date',
        target_date: undefined,
      }),
    );
  });

  it('does not call the API when the title is blank (submit guard)', () => {
    createMilestoneMock.mockResolvedValue(makeMilestone());
    renderDialog();

    expect((screen.getByTestId('create-milestone-submit') as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.submit(screen.getByTestId('create-milestone-form'));

    expect(createMilestoneMock).not.toHaveBeenCalled();
  });

  it('shows the API error inline and keeps the dialog open', async () => {
    createMilestoneMock.mockRejectedValue(
      new MeshApiError({ status: 409, code: 'validation_error', message: 'x' }),
    );
    const user = userEvent.setup();
    const { onClose } = renderDialog();

    await user.type(screen.getByTestId('milestone-title-input'), 'Doomed');
    await user.click(screen.getByTestId('create-milestone-submit'));

    expect(await screen.findByTestId('create-milestone-error')).toBeDefined();
    expect(
      await screen.findByText('Some fields are invalid. Please check your input.'),
    ).toBeDefined();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('falls back to the unknown error message for non-API failures', async () => {
    createMilestoneMock.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByTestId('milestone-title-input'), 'Doomed');
    await user.click(screen.getByTestId('create-milestone-submit'));

    expect(await screen.findByTestId('create-milestone-error')).toBeDefined();
    expect(await screen.findByText('Something went wrong. Please try again.')).toBeDefined();
  });
});
