/**
 * 标签管理面板测试:列表渲染、空态/错误态、新建(含失败码)、编辑(If-Match)、删除确认。
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { MeshApiError } from '../../../api';
import { I18nProvider } from '../../../i18n';
import { ToastProvider } from '../../../design';
import { LabelsPanel } from '../LabelsPanel';
import type { LabelWithUsage } from '../types';

function labelOf(overrides: Partial<LabelWithUsage> = {}): LabelWithUsage {
  return {
    id: 'lbl-1',
    workspace_id: 'ws-1',
    project_id: null,
    name: 'bug',
    color: '#e5484d',
    description: null,
    scope: 'workspace',
    issue_count: 3,
    created_at: '2026-07-26T00:00:00Z',
    updated_at: '2026-07-26T00:00:00Z',
    ...overrides,
  };
}

interface StubHandle {
  client: MeshApiClient;
  request: ReturnType<typeof vi.fn>;
  list: ReturnType<typeof vi.fn>;
}

function stub(labels: readonly LabelWithUsage[], requestResult: unknown = {}): StubHandle {
  const list = vi.fn().mockResolvedValue({ data: labels, next_cursor: null });
  const request = vi.fn().mockResolvedValue(requestResult);
  return {
    client: { list, request } as unknown as MeshApiClient,
    request,
    list,
  };
}

function renderPanel(handle: StubHandle, projectId?: string) {
  return render(
    <I18nProvider
      workspaceDefaultLocale={null}
      reporter={{ report: () => undefined, reported: [] }}
    >
      <ToastProvider regionLabel="notifications">
        <LabelsPanel client={handle.client} workspaceId="ws-1" projectId={projectId} />
      </ToastProvider>
    </I18nProvider>,
  );
}

describe('LabelsPanel', () => {
  it('renders labels with dot, name, hex and scope', async () => {
    const handle = stub([
      labelOf(),
      labelOf({
        id: 'lbl-2',
        name: 'frontend',
        color: '#3e63dd',
        project_id: 'p-1',
        scope: 'project',
      }),
    ]);
    renderPanel(handle);
    expect(await screen.findByTestId('label-row-bug')).toBeTruthy();
    expect(screen.getByTestId('label-row-frontend')).toBeTruthy();
    expect(screen.getByText('#e5484d')).toBeTruthy();
    expect(screen.getByText('workspace')).toBeTruthy();
    expect(screen.getByText('project')).toBeTruthy();
  });

  it('shows the empty state when there are no labels', async () => {
    renderPanel(stub([]));
    expect(await screen.findByText('No labels yet')).toBeTruthy();
  });

  it('shows the error state with retry when the load fails', async () => {
    const list = vi
      .fn()
      .mockRejectedValue(new MeshApiError({ status: 500, code: 'internal_error', message: 'x' }));
    const client = { list, request: vi.fn() } as unknown as MeshApiClient;
    render(
      <I18nProvider
        workspaceDefaultLocale={null}
        reporter={{ report: () => undefined, reported: [] }}
      >
        <ToastProvider regionLabel="notifications">
          <LabelsPanel client={client} workspaceId="ws-1" />
        </ToastProvider>
      </I18nProvider>,
    );
    const retry = await screen.findByRole('button', { name: 'Retry' });
    expect(retry).toBeTruthy();
  });

  it('passes the project filter when in project context', async () => {
    const handle = stub([]);
    renderPanel(handle, 'p-1');
    await waitFor(() => expect(handle.list).toHaveBeenCalled());
    expect(handle.list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/labels', {
      query: { project_id: 'p-1', limit: 200, cursor: undefined },
    });
  });

  it('creates a label with name, color and description', async () => {
    const user = userEvent.setup();
    const handle = stub([], labelOf({ id: 'lbl-9', name: 'fresh' }));
    renderPanel(handle);
    await user.click(await screen.findByTestId('labels-create'));
    await user.type(screen.getByTestId('label-name-input'), 'fresh');
    await user.clear(screen.getByTestId('color-hex-input'));
    await user.type(screen.getByTestId('color-hex-input'), '#46a758');
    await user.type(screen.getByTestId('label-description-input'), 'new tag');
    await user.click(screen.getByTestId('label-save'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/labels', {
        body: { name: 'fresh', color: '#46a758', description: 'new tag', project_id: null },
      }),
    );
  });

  it('sends project_id when creating in project context', async () => {
    const user = userEvent.setup();
    const handle = stub([], labelOf());
    renderPanel(handle, 'p-1');
    await user.click(await screen.findByTestId('labels-create'));
    await user.type(screen.getByTestId('label-name-input'), 'scoped');
    await user.click(screen.getByTestId('label-save'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/labels', {
        body: { name: 'scoped', color: '#3e63dd', description: null, project_id: 'p-1' },
      }),
    );
  });

  it('rejects an invalid color client-side before calling the API', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('labels-create'));
    await user.type(screen.getByTestId('label-name-input'), 'x');
    await user.clear(screen.getByTestId('color-hex-input'));
    await user.type(screen.getByTestId('color-hex-input'), 'red');
    await user.click(screen.getByTestId('label-save'));
    expect(await screen.findByTestId('label-form-error')).toBeTruthy();
    expect(handle.request).not.toHaveBeenCalled();
  });

  it('rejects an empty name client-side', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('labels-create'));
    await user.click(screen.getByTestId('label-save'));
    expect(await screen.findByTestId('label-form-error')).toBeTruthy();
    expect(handle.request).not.toHaveBeenCalled();
  });

  it('renders the label_name_taken error from the server', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    handle.request.mockRejectedValueOnce(
      new MeshApiError({ status: 409, code: 'label_name_taken', message: 'taken' }),
    );
    renderPanel(handle);
    await user.click(await screen.findByTestId('labels-create'));
    await user.type(screen.getByTestId('label-name-input'), 'bug');
    await user.click(screen.getByTestId('label-save'));
    const error = await screen.findByTestId('label-form-error');
    expect(error.textContent).toContain('already exists');
  });

  it('edits a label with If-Match optimistic concurrency', async () => {
    const user = userEvent.setup();
    const existing = labelOf();
    const handle = stub([existing], labelOf({ name: 'renamed' }));
    renderPanel(handle);
    await user.click(await screen.findByTestId('label-edit-bug'));
    const nameInput = screen.getByTestId('label-name-input') as HTMLInputElement;
    await user.clear(nameInput);
    await user.type(nameInput, 'renamed');
    await user.click(screen.getByTestId('label-save'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('PATCH', '/api/v1/labels/lbl-1', {
        body: { name: 'renamed', color: '#e5484d', description: null },
        ifMatch: '2026-07-26T00:00:00Z',
      }),
    );
  });

  it('deletes a label after confirmation', async () => {
    const user = userEvent.setup();
    const handle = stub([labelOf()], { id: 'lbl-1', deleted: true });
    renderPanel(handle);
    await user.click(await screen.findByTestId('label-delete-bug'));
    expect(screen.getByTestId('label-delete-confirm-text')).toBeTruthy();
    await user.click(screen.getByTestId('label-delete-confirm'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('DELETE', '/api/v1/labels/lbl-1'),
    );
  });

  it('confirms the source usage count and merges it into a selected target', async () => {
    const user = userEvent.setup();
    const source = labelOf({ issue_count: 7 });
    const target = labelOf({
      id: 'lbl-2',
      name: 'defect',
      color: '#3e63dd',
      issue_count: 4,
    });
    const handle = stub([source, target], {
      merged_issue_count: 7,
      target_label: { id: target.id, name: target.name, color: target.color },
    });
    renderPanel(handle);

    await user.click(await screen.findByTestId('label-merge-bug'));
    expect(screen.getByTestId('label-merge-source')).toHaveTextContent('bug');
    expect(screen.getByTestId('label-merge-impact')).toHaveTextContent('7');
    const targetSelect = screen.getByTestId('label-merge-target');
    expect(within(targetSelect).queryByRole('option', { name: 'bug' })).not.toBeInTheDocument();
    expect(screen.getByTestId('label-merge-confirm')).toBeDisabled();
    await user.selectOptions(targetSelect, target.id);
    await user.click(screen.getByTestId('label-merge-confirm'));

    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('POST', '/api/v1/labels/lbl-1/merge', {
        body: { target_label_id: 'lbl-2' },
      }),
    );
    await waitFor(() => expect(handle.list).toHaveBeenCalledTimes(2));
  });

  it('keeps the merge dialog open and reports a server rejection inline', async () => {
    const user = userEvent.setup();
    const source = labelOf();
    const target = labelOf({ id: 'lbl-2', name: 'defect' });
    const handle = stub([source, target]);
    handle.request.mockRejectedValueOnce(
      new MeshApiError({ status: 422, code: 'label_scope_mismatch', message: 'scope mismatch' }),
    );
    renderPanel(handle);

    await user.click(await screen.findByTestId('label-merge-bug'));
    await user.selectOptions(screen.getByTestId('label-merge-target'), target.id);
    await user.click(screen.getByTestId('label-merge-confirm'));

    expect(await screen.findByTestId('label-merge-error')).toHaveTextContent('same project');
    expect(screen.getByTestId('label-merge-source')).toHaveTextContent('bug');
  });

  it('hides project-private targets that cannot safely receive the source label', async () => {
    const user = userEvent.setup();
    const source = labelOf();
    const workspaceTarget = labelOf({ id: 'lbl-ws', name: 'workspace target' });
    const projectTarget = labelOf({
      id: 'lbl-p1',
      name: 'project target',
      project_id: 'p-1',
      scope: 'project',
    });
    renderPanel(stub([source, workspaceTarget, projectTarget]));

    await user.click(await screen.findByTestId('label-merge-bug'));
    const targetSelect = screen.getByTestId('label-merge-target');
    expect(within(targetSelect).getByRole('option', { name: 'workspace target' })).toBeEnabled();
    expect(
      within(targetSelect).queryByRole('option', { name: 'project target' }),
    ).not.toBeInTheDocument();
  });

  it('allows a project source to merge into a target from the same project only', async () => {
    const user = userEvent.setup();
    const source = labelOf({ project_id: 'p-1', scope: 'project' });
    const sameProject = labelOf({
      id: 'lbl-p1',
      name: 'same project',
      project_id: 'p-1',
      scope: 'project',
    });
    const otherProject = labelOf({
      id: 'lbl-p2',
      name: 'other project',
      project_id: 'p-2',
      scope: 'project',
    });
    renderPanel(stub([source, sameProject, otherProject]));

    await user.click(await screen.findByTestId('label-merge-bug'));
    const targetSelect = screen.getByTestId('label-merge-target');
    expect(within(targetSelect).getByRole('option', { name: 'same project' })).toBeEnabled();
    expect(
      within(targetSelect).queryByRole('option', { name: 'other project' }),
    ).not.toBeInTheDocument();
  });

  it('reports delete failures via toast without crashing', async () => {
    const user = userEvent.setup();
    const handle = stub([labelOf()]);
    handle.request.mockRejectedValueOnce(
      new MeshApiError({ status: 403, code: 'forbidden', message: 'no' }),
    );
    renderPanel(handle);
    await user.click(await screen.findByTestId('label-delete-bug'));
    await user.click(screen.getByTestId('label-delete-confirm'));
    await waitFor(() => expect(handle.request).toHaveBeenCalled());
  });
});
