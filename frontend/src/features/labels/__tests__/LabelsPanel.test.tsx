/**
 * 标签管理面板测试:列表渲染、空态/错误态、新建(含失败码)、编辑(If-Match)、删除确认。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { MeshApiError } from '../../../api';
import { I18nProvider } from '../../../i18n';
import { ToastProvider } from '../../../design';
import { LabelsPanel } from '../LabelsPanel';
import type { Label } from '../types';

function labelOf(overrides: Partial<Label> = {}): Label {
  return {
    id: 'lbl-1',
    workspace_id: 'ws-1',
    project_id: null,
    name: 'bug',
    color: '#e5484d',
    description: null,
    scope: 'workspace',
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

function stub(labels: readonly Label[], requestResult: unknown = {}): StubHandle {
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
    <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
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
      labelOf({ id: 'lbl-2', name: 'frontend', color: '#3e63dd', project_id: 'p-1', scope: 'project' }),
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
    const list = vi.fn().mockRejectedValue(new MeshApiError({ status: 500, code: 'internal_error', message: 'x' }));
    const client = { list, request: vi.fn() } as unknown as MeshApiClient;
    render(
      <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
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
      expect(handle.request).toHaveBeenCalledWith(
        'POST',
        '/api/v1/workspaces/ws-1/labels',
        {
          body: { name: 'fresh', color: '#46a758', description: 'new tag', project_id: null },
        },
      ),
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
      expect(handle.request).toHaveBeenCalledWith(
        'POST',
        '/api/v1/workspaces/ws-1/labels',
        {
          body: { name: 'scoped', color: '#3e63dd', description: null, project_id: 'p-1' },
        },
      ),
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
