/**
 * UpdatesPanel 分支级测试(project.md §4.1 更新动态 Tab):空列表空态、
 * author/health/message 为 null 的回退渲染、空说明省略、在途重复提交守卫、
 * 健康度选择与非 API 错误分支。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient, MeshApiError } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import * as projectsApi from '../api';
import { UpdatesPanel } from '../UpdatesPanel';
import type { ProjectUpdateEntry } from '../types';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, addProjectUpdate: vi.fn() };
});

const addProjectUpdateMock = vi.mocked(projectsApi.addProjectUpdate);

function makeUpdate(overrides: Partial<ProjectUpdateEntry> = {}): ProjectUpdateEntry {
  return {
    id: 'upd-1',
    project_id: 'prj-1',
    author: { id: 'mem-1', name: 'Alice', member_type: 'human' },
    health: 'on_track',
    status: null,
    message: 'All green.',
    created_at: '2026-07-20T10:00:00Z',
    ...overrides,
  };
}

function renderPanel(
  updates: readonly ProjectUpdateEntry[],
): {
  prependUpdate: (update: ProjectUpdateEntry) => void;
  onSubmitted: () => void;
  form: HTMLFormElement;
} {
  const prependUpdate = vi.fn();
  const onSubmitted = vi.fn();
  const { container } = renderWithProviders(
    <UpdatesPanel
      client={new MeshApiClient({ baseUrl: '', getToken: () => 'tok-test' })}
      projectId="prj-1"
      updates={updates}
      prependUpdate={prependUpdate}
      onSubmitted={onSubmitted}
    />,
  );
  const form = container.querySelector('form');
  if (form === null) throw new Error('UpdatesPanel form not rendered');
  return { prependUpdate, onSubmitted, form };
}

describe('UpdatesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the empty state when there are no updates', () => {
    renderPanel([]);
    expect(screen.getByText('Nothing here yet')).toBeDefined();
    expect(screen.getByText('No status updates yet.')).toBeDefined();
    expect(screen.queryByTestId('update-list')).toBeNull();
  });

  it('renders author, health chip and message for a full update', () => {
    renderPanel([makeUpdate()]);
    const row = screen.getByTestId('update-upd-1');
    expect(within(row).getByText('Alice')).toBeDefined();
    expect(within(row).getByText('On track')).toBeDefined();
    expect(within(row).getByText('All green.')).toBeDefined();
  });

  it('falls back to the unknown author and hides chip/message when null', () => {
    renderPanel([makeUpdate({ id: 'upd-2', author: null, health: null, message: null })]);
    const row = screen.getByTestId('update-upd-2');
    expect(within(row).getByText('Unknown author')).toBeDefined();
    expect(row.querySelector('.mesh-projects__health-chip')).toBeNull();
    expect(row.querySelector('.mesh-projects__update-message')).toBeNull();
  });

  it('posts the selected health with a message and clears the textarea', async () => {
    const created = makeUpdate({ id: 'upd-new', health: 'at_risk', message: 'Risk noted' });
    addProjectUpdateMock.mockResolvedValue(created);
    const user = userEvent.setup();
    const { prependUpdate, onSubmitted } = renderPanel([makeUpdate()]);

    await user.selectOptions(screen.getByTestId('update-health-select'), 'at_risk');
    await user.type(screen.getByLabelText('What changed?'), 'Risk noted');
    await user.click(screen.getByTestId('update-submit'));

    await waitFor(() =>
      expect(addProjectUpdateMock).toHaveBeenCalledWith(expect.anything(), 'prj-1', {
        health: 'at_risk',
        message: 'Risk noted',
      }),
    );
    expect(prependUpdate).toHaveBeenCalledWith(created);
    expect(onSubmitted).toHaveBeenCalled();
    expect((screen.getByLabelText('What changed?') as HTMLTextAreaElement).value).toBe('');
    expect(await screen.findByText('Update posted.')).toBeDefined();
  });

  it('omits message when the textarea is blank', async () => {
    addProjectUpdateMock.mockResolvedValue(makeUpdate());
    const user = userEvent.setup();
    renderPanel([]);

    await user.click(screen.getByTestId('update-submit'));

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
          resolvePost = () => resolve(makeUpdate());
        }),
    );
    const { onSubmitted, form } = renderPanel([]);

    fireEvent.submit(form);
    fireEvent.submit(form);
    resolvePost();

    await waitFor(() => expect(onSubmitted).toHaveBeenCalled());
    expect(addProjectUpdateMock).toHaveBeenCalledTimes(1);
  });

  it('shows the API error inline', async () => {
    addProjectUpdateMock.mockRejectedValue(
      new MeshApiError({ status: 500, code: 'internal_error', message: 'x' }),
    );
    const user = userEvent.setup();
    renderPanel([]);

    await user.click(screen.getByTestId('update-submit'));

    expect(await screen.findByTestId('update-submit-error')).toBeDefined();
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
  });

  it('falls back to the unknown error message for non-API failures', async () => {
    addProjectUpdateMock.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderPanel([]);

    await user.click(screen.getByTestId('update-submit'));

    expect(await screen.findByTestId('update-submit-error')).toBeDefined();
    expect(await screen.findByText('Something went wrong. Please try again.')).toBeDefined();
  });
});
