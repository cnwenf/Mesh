/**
 * CreateCycleDialog 分支级补充测试(project.md §1.2.5):auto_roll 勾选路径、
 * 空表单提交守卫、非 API 错误回退(合法创建/范围校验/API 400 见 CyclesPage.test.tsx)。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import * as projectsApi from '../api';
import { CreateCycleDialog } from '../CreateCycleDialog';
import type { Cycle } from '../types';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, createCycle: vi.fn() };
});

const createCycleMock = vi.mocked(projectsApi.createCycle);

function makeCycle(overrides: Partial<Cycle> = {}): Cycle {
  return {
    id: 'cyc-1',
    project_id: null,
    name: 'Sprint 12',
    starts_at: '2026-08-01',
    ends_at: '2026-08-14',
    state: 'planned',
    auto_roll: false,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function renderDialog(): { onClose: () => void; onCreated: (cycle: Cycle) => void } {
  const onClose = vi.fn();
  const onCreated = vi.fn();
  renderWithProviders(
    <CreateCycleDialog
      open
      onClose={onClose}
      client={new MeshApiClient({ baseUrl: '', getToken: () => 'tok-test' })}
      workspaceId="ws-1"
      onCreated={onCreated}
    />,
  );
  return { onClose, onCreated };
}

async function fillValidForm(): Promise<void> {
  const user = userEvent.setup();
  await user.type(screen.getByTestId('cycle-name-input'), 'Sprint 12');
  fireEvent.change(screen.getByTestId('cycle-starts-input'), { target: { value: '2026-08-01' } });
  fireEvent.change(screen.getByTestId('cycle-ends-input'), { target: { value: '2026-08-14' } });
}

describe('CreateCycleDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('sends auto_roll=true when the checkbox is ticked', async () => {
    createCycleMock.mockResolvedValue(makeCycle({ auto_roll: true }));
    const user = userEvent.setup();
    const { onClose, onCreated } = renderDialog();

    await fillValidForm();
    await user.click(screen.getByTestId('cycle-auto-roll'));
    await user.click(screen.getByTestId('create-cycle-submit'));

    await waitFor(() =>
      expect(createCycleMock).toHaveBeenCalledWith(expect.anything(), 'ws-1', {
        name: 'Sprint 12',
        starts_at: '2026-08-01',
        ends_at: '2026-08-14',
        auto_roll: true,
      }),
    );
    expect(onCreated).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('sends auto_roll=false when the checkbox is left untouched', async () => {
    createCycleMock.mockResolvedValue(makeCycle());
    const user = userEvent.setup();
    renderDialog();

    await fillValidForm();
    await user.click(screen.getByTestId('create-cycle-submit'));

    await waitFor(() =>
      expect(createCycleMock).toHaveBeenCalledWith(
        expect.anything(),
        'ws-1',
        expect.objectContaining({ auto_roll: false }),
      ),
    );
  });

  it('does not call the API when required fields are blank (submit guard)', () => {
    createCycleMock.mockResolvedValue(makeCycle());
    renderDialog();

    expect((screen.getByTestId('create-cycle-submit') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(screen.getByTestId('create-cycle-form'));

    expect(createCycleMock).not.toHaveBeenCalled();
  });

  it('falls back to the unknown error message for non-API failures', async () => {
    createCycleMock.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderDialog();

    await fillValidForm();
    await user.click(screen.getByTestId('create-cycle-submit'));

    expect(await screen.findByTestId('create-cycle-error')).toBeDefined();
    expect(await screen.findByText('Something went wrong. Please try again.')).toBeDefined();
  });
});
