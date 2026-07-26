/**
 * HealthUpdateDialog 分支级测试(project.md §4.2/§4.3):健康度选择 + 空/非空说明、
 * 在途重复提交守卫、API 失败与非 API 失败分支。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient, MeshApiError } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import * as projectsApi from '../api';
import { HealthUpdateDialog } from '../HealthUpdateDialog';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, addProjectUpdate: vi.fn() };
});

const addProjectUpdateMock = vi.mocked(projectsApi.addProjectUpdate);

function renderDialog(): { onClose: () => void; onSaved: () => void } {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  renderWithProviders(
    <HealthUpdateDialog
      open
      onClose={onClose}
      client={new MeshApiClient({ baseUrl: '', getToken: () => 'tok-test' })}
      projectId="prj-1"
      onSaved={onSaved}
    />,
  );
  return { onClose, onSaved };
}

describe('HealthUpdateDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('posts the selected health with a trimmed message and closes', async () => {
    addProjectUpdateMock.mockResolvedValue({} as never);
    const user = userEvent.setup();
    const { onClose, onSaved } = renderDialog();

    await user.selectOptions(screen.getByTestId('health-select'), 'at_risk');
    await user.type(screen.getByLabelText('Note'), '  API latency rising  ');
    await user.click(screen.getByTestId('health-update-submit'));

    await waitFor(() =>
      expect(addProjectUpdateMock).toHaveBeenCalledWith(expect.anything(), 'prj-1', {
        health: 'at_risk',
        message: 'API latency rising',
      }),
    );
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    expect(await screen.findByText('Status update posted.')).toBeDefined();
  });

  it('omits message when the textarea is blank', async () => {
    addProjectUpdateMock.mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByTestId('health-update-submit'));

    await waitFor(() =>
      expect(addProjectUpdateMock).toHaveBeenCalledWith(expect.anything(), 'prj-1', {
        health: 'on_track',
        message: undefined,
      }),
    );
  });

  it('guards against a duplicate submit while the first is in flight', async () => {
    let resolvePost: () => void = () => undefined;
    addProjectUpdateMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePost = () => resolve({} as never);
        }),
    );
    const { onSaved } = renderDialog();

    fireEvent.submit(screen.getByTestId('health-update-form'));
    fireEvent.submit(screen.getByTestId('health-update-form'));
    resolvePost();

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(addProjectUpdateMock).toHaveBeenCalledTimes(1);
  });

  it('shows the API error inline and does not close', async () => {
    addProjectUpdateMock.mockRejectedValue(
      new MeshApiError({ status: 500, code: 'internal_error', message: 'x' }),
    );
    const user = userEvent.setup();
    const { onClose } = renderDialog();

    await user.click(screen.getByTestId('health-update-submit'));

    expect(await screen.findByTestId('health-update-error')).toBeDefined();
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('falls back to the unknown error message for non-API failures', async () => {
    addProjectUpdateMock.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByTestId('health-update-submit'));

    expect(await screen.findByTestId('health-update-error')).toBeDefined();
    expect(await screen.findByText('Something went wrong. Please try again.')).toBeDefined();
  });
});
