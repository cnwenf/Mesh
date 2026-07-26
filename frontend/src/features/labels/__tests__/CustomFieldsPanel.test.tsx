/**
 * 自定义字段管理面板测试:列表、创建(含枚举选项)、校验、停用/启用、删除、选项编辑器。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { MeshApiError } from '../../../api';
import { ToastProvider } from '../../../design';
import { I18nProvider } from '../../../i18n';
import { CustomFieldsPanel } from '../CustomFieldsPanel';
import type { CustomFieldDef } from '../types';

function fieldOf(overrides: Partial<CustomFieldDef> = {}): CustomFieldDef {
  return {
    id: 'cf-1',
    workspace_id: 'ws-1',
    project_id: null,
    name: 'Severity',
    field_key: 'severity',
    type: 'single_select',
    is_required: false,
    required_on: [],
    default_value: null,
    config: {},
    position: 0,
    is_active: true,
    options: [
      {
        id: 'opt-1',
        field_def_id: 'cf-1',
        name: 'Major',
        color: '#f5a623',
        position: 0,
        is_active: true,
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:00Z',
      },
    ],
    created_at: '2026-07-26T00:00:00Z',
    updated_at: '2026-07-26T00:00:00Z',
    ...overrides,
  };
}

function stub(fields: readonly CustomFieldDef[], requestResult: unknown = {}) {
  const list = vi.fn().mockResolvedValue({ data: fields, next_cursor: null });
  const request = vi.fn().mockResolvedValue(requestResult);
  return {
    client: { list, request } as unknown as MeshApiClient,
    request,
    list,
  };
}

function renderPanel(handle: ReturnType<typeof stub>, projectId?: string) {
  return render(
    <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
      <ToastProvider regionLabel="notifications">
        <CustomFieldsPanel client={handle.client} workspaceId="ws-1" projectId={projectId} />
      </ToastProvider>
    </I18nProvider>,
  );
}

describe('CustomFieldsPanel', () => {
  it('renders field rows with type, key, badges and status', async () => {
    const handle = stub([fieldOf(), fieldOf({ id: 'cf-2', name: 'Users', field_key: 'users', type: 'number', options: [], is_required: true })]);
    renderPanel(handle);
    expect(await screen.findByTestId('field-row-severity')).toBeTruthy();
    expect(screen.getByTestId('field-row-users')).toBeTruthy();
    expect(screen.getByText('required')).toBeTruthy();
  });

  it('shows empty state without fields', async () => {
    renderPanel(stub([]));
    expect(await screen.findByText('No custom fields yet')).toBeTruthy();
  });

  it('creates a select field with initial options', async () => {
    const user = userEvent.setup();
    const handle = stub([], fieldOf());
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.type(screen.getByTestId('field-name-input'), 'Severity');
    await user.type(screen.getByTestId('field-key-input'), 'severity');
    // 默认类型 text → 切到 single_select,自动生成一行选项草稿。
    await user.selectOptions(screen.getByTestId('field-type-select'), 'single_select');
    await user.type(screen.getByTestId('field-option-name-0'), 'Major');
    await user.click(screen.getByTestId('field-save'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith(
        'POST',
        '/api/v1/workspaces/ws-1/custom-fields',
        {
          body: {
            name: 'Severity',
            field_key: 'severity',
            type: 'single_select',
            project_id: null,
            is_required: false,
            position: 0,
            options: [{ name: 'Major', color: '#3e63dd', position: 0 }],
          },
        },
      ),
    );
  });

  it('adds and removes option drafts in the editor', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.selectOptions(screen.getByTestId('field-type-select'), 'multi_select');
    await user.click(screen.getByTestId('field-option-add'));
    expect(screen.getByTestId('field-option-name-1')).toBeTruthy();
    await user.click(screen.getByTestId('field-option-remove-1'));
    expect(screen.queryByTestId('field-option-name-1')).toBeNull();
  });

  it('rejects an invalid field key client-side', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.type(screen.getByTestId('field-name-input'), 'X');
    await user.type(screen.getByTestId('field-key-input'), 'Not-Valid');
    await user.click(screen.getByTestId('field-save'));
    expect(await screen.findByTestId('field-form-error')).toBeTruthy();
    expect(handle.request).not.toHaveBeenCalled();
  });

  it('rejects empty option names for select types', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.type(screen.getByTestId('field-name-input'), 'X');
    await user.type(screen.getByTestId('field-key-input'), 'x_key');
    await user.selectOptions(screen.getByTestId('field-type-select'), 'single_select');
    // 选项草稿名称留空 → 客户端拒绝
    await user.click(screen.getByTestId('field-save'));
    expect(await screen.findByTestId('field-form-error')).toBeTruthy();
    expect(handle.request).not.toHaveBeenCalled();
  });

  it('renders the field_key_taken server error', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    handle.request.mockRejectedValueOnce(
      new MeshApiError({ status: 409, code: 'field_key_taken', message: 'taken' }),
    );
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.type(screen.getByTestId('field-name-input'), 'X');
    await user.type(screen.getByTestId('field-key-input'), 'severity');
    await user.click(screen.getByTestId('field-save'));
    const error = await screen.findByTestId('field-form-error');
    expect(error.textContent).toContain('already exists');
  });

  it('edits a field (name/required/position) with If-Match', async () => {
    const user = userEvent.setup();
    const existing = fieldOf();
    const handle = stub([existing], fieldOf({ name: 'Renamed' }));
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-edit-severity'));
    const nameInput = screen.getByTestId('field-name-input') as HTMLInputElement;
    await user.clear(nameInput);
    await user.type(nameInput, 'Renamed');
    await user.click(screen.getByTestId('field-required-checkbox'));
    await user.click(screen.getByTestId('field-save'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('PATCH', '/api/v1/custom-fields/cf-1', {
        body: { name: 'Renamed', is_required: true, position: 0 },
        ifMatch: '2026-07-26T00:00:00Z',
      }),
    );
  });

  it('toggles field active state', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()], fieldOf({ is_active: false }));
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-toggle-severity'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('PATCH', '/api/v1/custom-fields/cf-1', {
        body: { is_active: false },
        ifMatch: '2026-07-26T00:00:00Z',
      }),
    );
  });

  it('deletes a field after confirmation', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()], { id: 'cf-1', deleted: true });
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-delete-severity'));
    expect(screen.getByTestId('field-delete-confirm-text')).toBeTruthy();
    await user.click(screen.getByTestId('field-delete-confirm'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('DELETE', '/api/v1/custom-fields/cf-1'),
    );
  });

  it('opens the options editor and adds an option', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()], { id: 'opt-9', name: 'Minor' });
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-options-severity'));
    expect(screen.getByTestId('option-row-Major')).toBeTruthy();
    await user.type(screen.getByTestId('option-new-name'), 'Minor');
    await user.click(screen.getByTestId('option-add-confirm'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith(
        'POST',
        '/api/v1/custom-fields/cf-1/options',
        { body: { name: 'Minor', color: '#3e63dd', position: 1 } },
      ),
    );
  });

  it('toggles and removes options in the editor', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()], {});
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-options-severity'));
    await user.click(screen.getByTestId('option-toggle-Major'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith(
        'PATCH',
        '/api/v1/custom-fields/cf-1/options/opt-1',
        { body: { is_active: false }, ifMatch: undefined },
      ),
    );
  });

  it('option add with empty name shows inline error', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-options-severity'));
    await user.click(screen.getByTestId('option-add-confirm'));
    expect(await screen.findByTestId('options-editor-error')).toBeTruthy();
    expect(handle.request).not.toHaveBeenCalled();
  });
});
