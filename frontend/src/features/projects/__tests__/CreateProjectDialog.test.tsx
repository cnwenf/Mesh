/**
 * CreateProjectDialog 分支级测试(project.md §4.3):描述/目标日非空的完整提交、
 * 自定义 key(upper-case + keyTouched)、空表单守卫、非 API 错误回退。
 * 409 内联错误路径见 ProjectsPage.test.tsx。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import * as projectsApi from '../api';
import { CreateProjectDialog } from '../CreateProjectDialog';
import type { ProjectSummary } from '../types';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, createProject: vi.fn(), getProjectKeyAvailability: vi.fn() };
});

const createProjectMock = vi.mocked(projectsApi.createProject);
const getProjectKeyAvailabilityMock = vi.mocked(projectsApi.getProjectKeyAvailability);

function makeProject(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    id: 'prj-new',
    workspace_id: 'ws-1',
    name: 'Apollo',
    key: 'APOLLO',
    description: null,
    icon: null,
    color: null,
    status: 'planning',
    health: null,
    visibility: 'public',
    lead: null,
    lead_member_id: null,
    start_date: null,
    target_date: null,
    progress: 0,
    open_issues: 0,
    done_issues: 0,
    issue_seq: 0,
    archived: false,
    archived_at: null,
    my_role: 'lead',
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function renderDialog(): { onClose: () => void; onCreated: (projectId: string) => void } {
  const onClose = vi.fn();
  const onCreated = vi.fn();
  renderWithProviders(
    <CreateProjectDialog
      open
      onClose={onClose}
      client={new MeshApiClient({ baseUrl: '', getToken: () => 'tok-test' })}
      workspaceId="ws-1"
      onCreated={onCreated}
    />,
  );
  return { onClose, onCreated };
}

describe('CreateProjectDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getProjectKeyAvailabilityMock.mockImplementation(async (_client, _workspaceId, key) => ({
      key,
      available: true,
    }));
  });

  it('checks key availability live and blocks a permanently reserved prefix', async () => {
    getProjectKeyAvailabilityMock.mockResolvedValue({ key: 'APOLLO', available: false });
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByTestId('create-project-name'), 'Apollo');
    expect(await screen.findByText('This key is already reserved.')).toBeInTheDocument();
    expect(getProjectKeyAvailabilityMock).toHaveBeenCalledWith(
      expect.anything(),
      'ws-1',
      'APOLLO',
      expect.any(AbortSignal),
    );
    expect(screen.getByTestId('create-project-submit')).toBeDisabled();
  });

  it('disables submit while a live key check is in progress', async () => {
    let resolveCheck!: (value: { key: string; available: boolean }) => void;
    getProjectKeyAvailabilityMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCheck = resolve;
        }),
    );
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByTestId('create-project-name'), 'Apollo');
    expect(await screen.findByText('Checking availability…')).toBeInTheDocument();
    expect(screen.getByTestId('create-project-submit')).toBeDisabled();

    await act(async () => resolveCheck({ key: 'APOLLO', available: true }));
    expect(await screen.findByText('This key is available.')).toBeInTheDocument();
    expect(screen.getByTestId('create-project-submit')).toBeEnabled();
  });

  it('submits description, private visibility and target date when provided', async () => {
    createProjectMock.mockResolvedValue(makeProject());
    const user = userEvent.setup();
    const { onClose, onCreated } = renderDialog();

    await user.type(screen.getByTestId('create-project-name'), 'Apollo');
    await user.type(screen.getByLabelText('Description'), 'Landing plan');
    await user.selectOptions(screen.getByTestId('create-project-visibility'), 'private');
    fireEvent.change(screen.getByTestId('create-project-target-date'), {
      target: { value: '2026-12-31' },
    });
    await user.click(screen.getByTestId('create-project-submit'));

    await waitFor(() =>
      expect(createProjectMock).toHaveBeenCalledWith(expect.anything(), 'ws-1', {
        name: 'Apollo',
        key: 'APOLLO',
        description: 'Landing plan',
        visibility: 'private',
        target_date: '2026-12-31',
      }),
    );
    expect(onCreated).toHaveBeenCalledWith('prj-new');
    expect(onClose).toHaveBeenCalled();
    expect(await screen.findByText('Project created.')).toBeDefined();
  });

  it('omits description and target date when left blank', async () => {
    createProjectMock.mockResolvedValue(makeProject());
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByTestId('create-project-name'), 'Apollo');
    await user.click(screen.getByTestId('create-project-submit'));

    await waitFor(() =>
      expect(createProjectMock).toHaveBeenCalledWith(expect.anything(), 'ws-1', {
        name: 'Apollo',
        key: 'APOLLO',
        description: undefined,
        visibility: 'public',
        target_date: undefined,
      }),
    );
  });

  it('upper-cases a manually edited key and keeps it over the suggestion', async () => {
    createProjectMock.mockResolvedValue(makeProject());
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByTestId('create-project-name'), 'Apollo');
    const keyInput = screen.getByTestId('create-project-key');
    await user.clear(keyInput);
    await user.type(keyInput, 'apl');
    expect((keyInput as HTMLInputElement).value).toBe('APL');
    await user.click(screen.getByTestId('create-project-submit'));

    await waitFor(() =>
      expect(createProjectMock).toHaveBeenCalledWith(
        expect.anything(),
        'ws-1',
        expect.objectContaining({ key: 'APL' }),
      ),
    );
  });

  it('does not call the API when the name is blank (submit guard)', () => {
    createProjectMock.mockResolvedValue(makeProject());
    renderDialog();

    expect((screen.getByTestId('create-project-submit') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(screen.getByTestId('create-project-form'));

    expect(createProjectMock).not.toHaveBeenCalled();
  });

  it('falls back to the unknown error message for non-API failures', async () => {
    createProjectMock.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    const { onClose } = renderDialog();

    await user.type(screen.getByTestId('create-project-name'), 'Apollo');
    await user.click(screen.getByTestId('create-project-submit'));

    expect(await screen.findByTestId('create-project-error')).toBeDefined();
    expect(await screen.findByText('Something went wrong. Please try again.')).toBeDefined();
    expect(onClose).not.toHaveBeenCalled();
  });
});
