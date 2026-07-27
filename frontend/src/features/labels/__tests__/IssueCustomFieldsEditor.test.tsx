/**
 * 自定义字段编辑面板测试:按类型渲染控件、单字段提交(PUT + If-Match)、
 * 必填标记、错误提示、空态(§4.3)。
 */
// 固定 UTC+8,钉死 datetime 本地墙钟 ↔ UTC 时刻的双向转换语义(B3 回归)。
process.env.TZ = 'Asia/Shanghai';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { MeshApiError } from '../../../api';
import { I18nProvider } from '../../../i18n';
import { ToastProvider } from '../../../design';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { IssueCustomFieldsEditor } from '../IssueCustomFieldsEditor';
import type { CustomFieldDef } from '../types';
import type { CustomFieldValue, FieldValueListingEntry } from '../associationTypes';

function defOf(overrides: Partial<CustomFieldDef>): CustomFieldDef {
  return {
    id: 'fd-1',
    workspace_id: 'ws-1',
    project_id: null,
    name: 'Field',
    field_key: 'field',
    type: 'text',
    is_required: false,
    required_on: [],
    default_value: null,
    config: {},
    position: 0,
    is_active: true,
    options: [],
    created_at: '2026-07-26T00:00:00Z',
    updated_at: '2026-07-26T00:00:00Z',
    ...overrides,
  };
}

function entryOf(def: CustomFieldDef, value: FieldValueListingEntry['value'] = null) {
  return { field_def: def, value };
}

function stubClient(entries: FieldValueListingEntry[]) {
  const list = vi.fn().mockResolvedValue({ data: entries, next_cursor: null });
  const request = vi.fn().mockResolvedValue(entries);
  return {
    client: { list, request } as unknown as MeshApiClient,
    list,
    request,
  };
}

function renderEditor(
  stub: ReturnType<typeof stubClient>,
  realtime: RealtimeContextValue | null = null,
) {
  return render(
    <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
      <ToastProvider regionLabel="notifications">
        <IssueCustomFieldsEditor
          client={stub.client}
          workspaceId="ws-1"
          issueId="iss-1"
          issueUpdatedAt="2026-07-26T12:00:00Z"
          members={[
            { id: 'm-1', display_name: 'Ann', member_type: 'human', status: 'active' },
            { id: 'm-2', display_name: 'Bot', member_type: 'agent', status: 'active' },
          ]}
          reloadKey={0}
          realtime={realtime}
        />
      </ToastProvider>
    </I18nProvider>,
  );
}

describe('IssueCustomFieldsEditor', () => {
  it('shows the empty state when no fields apply', async () => {
    renderEditor(stubClient([]));
    expect(await screen.findByText('No custom fields apply to this issue yet')).toBeTruthy();
  });

  it('renders a required marker on required defs', async () => {
    const def = defOf({ name: 'Acceptor', field_key: 'acceptor', is_required: true });
    renderEditor(stubClient([entryOf(def)]));
    expect(await screen.findByText('Acceptor *')).toBeTruthy();
  });

  it('commits a single_select change with If-Match', async () => {
    const def = defOf({
      id: 'fd-sev',
      name: 'Severity',
      field_key: 'severity',
      type: 'single_select',
      options: [
        {
          id: 'opt-major',
          field_def_id: 'fd-sev',
          name: 'Major',
          color: null,
          position: 0,
          is_active: true,
          created_at: '2026-07-26T00:00:00Z',
          updated_at: '2026-07-26T00:00:00Z',
        },
      ],
    });
    const stub = stubClient([entryOf(def)]);
    renderEditor(stub);
    const select = await screen.findByTestId('issue-field-severity');
    await userEvent.selectOptions(select, 'opt-major');
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-sev', value_json: 'opt-major' }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('commits a boolean toggle', async () => {
    const def = defOf({ name: 'Needs docs', field_key: 'docs', type: 'boolean' });
    const stub = stubClient([entryOf(def)]);
    renderEditor(stub);
    const checkbox = await screen.findByTestId('issue-field-docs');
    await userEvent.click(checkbox);
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-1', value_boolean: true }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('commits a member selection', async () => {
    const def = defOf({ name: 'Acceptor', field_key: 'acceptor', type: 'member' });
    const stub = stubClient([entryOf(def)]);
    renderEditor(stub);
    const select = await screen.findByTestId('issue-field-acceptor');
    expect(select.textContent).toContain('Bot (agent)');
    await userEvent.selectOptions(select, 'm-1');
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-1', value_member_id: 'm-1' }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('surfaces a 422 error code via toast without crashing', async () => {
    const def = defOf({ name: 'Users', field_key: 'users', type: 'number' });
    const stub = stubClient([entryOf(def)]);
    stub.request.mockRejectedValue(
      new MeshApiError({
        status: 422,
        code: 'invalid_field_value',
        message: 'bad',
      }),
    );
    renderEditor(stub);
    const input = await screen.findByTestId('issue-field-users');
    await userEvent.type(input, '50');
    await userEvent.tab();
    await waitFor(() =>
      expect(screen.getByText('That value does not match the field type')).toBeTruthy(),
    );
  });

  it('renders multi_select toggles and commits the id array', async () => {
    const def = defOf({
      id: 'fd-mod',
      name: 'Modules',
      field_key: 'modules',
      type: 'multi_select',
      options: [
        {
          id: 'opt-api',
          field_def_id: 'fd-mod',
          name: 'api',
          color: null,
          position: 0,
          is_active: true,
          created_at: '2026-07-26T00:00:00Z',
          updated_at: '2026-07-26T00:00:00Z',
        },
      ],
    });
    const stub = stubClient([entryOf(def)]);
    renderEditor(stub);
    const option = await screen.findByTestId('issue-field-modules-api');
    await userEvent.click(option);
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-mod', value_json: ['opt-api'] }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  function valueOf(overrides: Partial<CustomFieldValue>): CustomFieldValue {
    return {
      field_def_id: 'fd-1',
      issue_id: 'iss-1',
      value_text: null,
      value_number: null,
      value_date: null,
      value_member_id: null,
      value_member: null,
      value_boolean: null,
      value_json: null,
      created_at: '2026-07-26T00:00:00Z',
      updated_at: '2026-07-26T00:00:00Z',
      ...overrides,
    };
  }

  it('commits text and url inputs on blur', async () => {
    const text = defOf({ id: 'fd-t', name: 'Note', field_key: 'note', type: 'text' });
    const url = defOf({ id: 'fd-u', name: 'Link', field_key: 'link', type: 'url' });
    const stub = stubClient([entryOf(text), entryOf(url)]);
    renderEditor(stub);
    const note = await screen.findByTestId('issue-field-note');
    await userEvent.type(note, 'hello');
    await userEvent.tab();
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-t', value_text: 'hello' }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
    const link = screen.getByTestId('issue-field-link');
    await userEvent.type(link, 'https://mesh.dev/a');
    await userEvent.tab();
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-u', value_text: 'https://mesh.dev/a' }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('commits a textarea value on blur', async () => {
    const def = defOf({ id: 'fd-ta', name: 'Remark', field_key: 'remark', type: 'textarea' });
    const stub = stubClient([entryOf(def)]);
    renderEditor(stub);
    const area = await screen.findByTestId('issue-field-remark');
    await userEvent.type(area, 'multi line');
    await userEvent.tab();
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-ta', value_text: 'multi line' }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('renders stored date / datetime values and commits changes', async () => {
    const day = defOf({ id: 'fd-d', name: 'Launch', field_key: 'launch', type: 'date' });
    const moment = defOf({ id: 'fd-dt', name: 'Outage', field_key: 'outage', type: 'datetime' });
    const stub = stubClient([
      entryOf(day, valueOf({ field_def_id: 'fd-d', value_date: '2026-08-01T00:00:00Z' })),
      entryOf(moment, valueOf({ field_def_id: 'fd-dt', value_date: '2026-08-02T10:30:00Z' })),
    ]);
    renderEditor(stub);
    const dayInput = (await screen.findByTestId('issue-field-launch')) as HTMLInputElement;
    expect(dayInput.value).toBe('2026-08-01');
    const momentInput = screen.getByTestId('issue-field-outage') as HTMLInputElement;
    // 回显是本地墙钟:UTC 10:30 在 UTC+8 显示为 18:30(B3:不再把 Z 当墙钟)。
    expect(momentInput.value).toBe('2026-08-02T18:30');
    await userEvent.clear(dayInput);
    await userEvent.type(dayInput, '2026-09-05');
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-d', value_date: '2026-09-05' }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('clears a number field when emptied', async () => {
    const def = defOf({
      id: 'fd-n',
      name: 'Users',
      field_key: 'users',
      type: 'number',
    });
    const stub = stubClient([entryOf(def, valueOf({ field_def_id: 'fd-n', value_number: 7 }))]);
    renderEditor(stub);
    const input = (await screen.findByTestId('issue-field-users')) as HTMLInputElement;
    expect(input.value).toBe('7');
    await userEvent.clear(input);
    await userEvent.tab();
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-n', value_number: null }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('refetches when a realtime value / definition frame arrives (§3.5)', async () => {
    const def = defOf({ id: 'fd-t', name: 'Note', field_key: 'note', type: 'text' });
    const stub = stubClient([entryOf(def)]);
    const listeners: Array<(frame: unknown) => void> = [];
    const client = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn((listener: (frame: unknown) => void) => {
        listeners.push(listener);
        return () => undefined;
      }),
    };
    const realtime = { state: 'open', client } as unknown as RealtimeContextValue;
    renderEditor(stub, realtime);
    await screen.findByTestId('issue-field-note');
    expect(client.subscribe).toHaveBeenCalledWith('workspace:ws-1:custom_fields');
    const callsBefore = stub.list.mock.calls.length;
    for (const frame of [
      { op: 'event', channel: 'issue:iss-1', seq: 1, event: 'issue.custom_field_changed', payload: {} },
      { op: 'event', channel: 'workspace:ws-1:custom_fields', seq: 2, event: 'custom_field.updated', payload: {} },
      { op: 'state', channel: 'x', seq: 3, event: 'ignored', payload: {} },
    ]) {
      listeners.forEach((l) => l(frame));
    }
    await waitFor(() => expect(stub.list.mock.calls.length).toBe(callsBefore + 2));
  });

  it('renders empty date / datetime inputs when values are null', async () => {
    const day = defOf({ id: 'fd-d', name: 'Launch', field_key: 'launch', type: 'date' });
    const moment = defOf({ id: 'fd-dt', name: 'Outage', field_key: 'outage', type: 'datetime' });
    const stub = stubClient([entryOf(day), entryOf(moment)]);
    renderEditor(stub);
    expect(((await screen.findByTestId('issue-field-launch')) as HTMLInputElement).value).toBe('');
    expect((screen.getByTestId('issue-field-outage') as HTMLInputElement).value).toBe('');
  });

  it('does not commit when a text blur leaves the value unchanged', async () => {
    const def = defOf({ id: 'fd-t', name: 'Note', field_key: 'note', type: 'text' });
    const stub = stubClient([entryOf(def, valueOf({ field_def_id: 'fd-t', value_text: 'same' }))]);
    renderEditor(stub);
    const input = await screen.findByTestId('issue-field-note');
    await userEvent.click(input);
    await userEvent.tab();
    expect(stub.request).not.toHaveBeenCalled();
  });

  it('commits null when a text value is cleared', async () => {
    const def = defOf({ id: 'fd-t', name: 'Note', field_key: 'note', type: 'text' });
    const stub = stubClient([entryOf(def, valueOf({ field_def_id: 'fd-t', value_text: 'old' }))]);
    renderEditor(stub);
    const input = await screen.findByTestId('issue-field-note');
    await userEvent.clear(input);
    await userEvent.tab();
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-t', value_text: null }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('commits null when a date is cleared', async () => {
    const day = defOf({ id: 'fd-d', name: 'Launch', field_key: 'launch', type: 'date' });
    const stub = stubClient([entryOf(day, valueOf({ field_def_id: 'fd-d', value_date: '2026-08-01T00:00:00Z' }))]);
    renderEditor(stub);
    const input = await screen.findByTestId('issue-field-launch');
    await userEvent.clear(input);
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-d', value_date: null }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('submits datetime as UTC converted from the local wall clock (B3)', async () => {
    const moment = defOf({ id: 'fd-dt', name: 'Outage', field_key: 'outage', type: 'datetime' });
    const stub = stubClient([entryOf(moment)]);
    renderEditor(stub);
    const input = await screen.findByTestId('issue-field-outage');
    // 本地墙钟 2026-09-10T08:15(UTC+8)= UTC 2026-09-10T00:15Z。
    await userEvent.type(input, '2026-09-10T08:15');
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-dt', value_date: '2026-09-10T00:15:00.000Z' }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('does not commit a number blur that keeps or fails to parse the value', async () => {
    const def = defOf({ id: 'fd-n', name: 'Users', field_key: 'users', type: 'number' });
    const stub = stubClient([entryOf(def, valueOf({ field_def_id: 'fd-n', value_number: 7 }))]);
    renderEditor(stub);
    const input = await screen.findByTestId('issue-field-users');
    await userEvent.click(input);
    await userEvent.tab(); // unchanged
    expect(stub.request).not.toHaveBeenCalled();
  });

  it('toasts a network error message when the commit failure is not a MeshApiError', async () => {
    const def = defOf({ id: 'fd-t', name: 'Note', field_key: 'note', type: 'text' });
    const stub = stubClient([entryOf(def)]);
    stub.request.mockRejectedValue(new Error('boom'));
    renderEditor(stub);
    const input = await screen.findByTestId('issue-field-note');
    await userEvent.type(input, 'x');
    await userEvent.tab();
    expect(await screen.findByText('Network error. Please check your connection and try again.')).toBeTruthy();
  });

  it('refreshes mounted controls when a realtime refetch changes values', async () => {
    // 他端提交 → 本端收到 issue.custom_field_changed → 重拉 → 控件重挂载
    // 显示新值(非受控 defaultValue 随 key 身份刷新,review round 2 🟡)。
    const def = defOf({ id: 'fd-t', name: 'Note', field_key: 'note', type: 'text' });
    const v1 = valueOf({ field_def_id: 'fd-t', value_text: 'remote-1', updated_at: '2026-07-26T01:00:00Z' });
    const v2 = valueOf({ field_def_id: 'fd-t', value_text: 'remote-2', updated_at: '2026-07-26T02:00:00Z' });
    let calls = 0;
    const list = vi.fn().mockImplementation(() => {
      calls += 1;
      const value = calls === 1 ? v1 : v2;
      return Promise.resolve({ data: [{ field_def: def, value }], next_cursor: null });
    });
    const client = { list, request: vi.fn() } as unknown as MeshApiClient;
    const listeners: Array<(frame: unknown) => void> = [];
    const realtime = {
      state: 'open',
      client: {
        subscribe: vi.fn(),
        unsubscribe: vi.fn(),
        onFrame: vi.fn((listener: (frame: unknown) => void) => {
          listeners.push(listener);
          return () => undefined;
        }),
      },
    } as unknown as RealtimeContextValue;
    render(
      <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
        <ToastProvider regionLabel="notifications">
          <IssueCustomFieldsEditor
            client={client}
            workspaceId="ws-1"
            issueId="iss-1"
            issueUpdatedAt="2026-07-26T12:00:00Z"
            members={[]}
            reloadKey={0}
            realtime={realtime}
          />
        </ToastProvider>
      </I18nProvider>,
    );
    await screen.findByDisplayValue('remote-1');
    listeners.forEach((l) =>
      l({ op: 'event', channel: 'issue:iss-1', seq: 9, event: 'issue.custom_field_changed', payload: {} }),
    );
    await screen.findByDisplayValue('remote-2');
  });

  it('toasts when the initial field-values load fails', async () => {
    const list = vi.fn().mockRejectedValue(new Error('boom'));
    const client = { list, request: vi.fn() } as unknown as MeshApiClient;
    render(
      <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
        <ToastProvider regionLabel="notifications">
          <IssueCustomFieldsEditor
            client={client}
            workspaceId="ws-1"
            issueId="iss-1"
            issueUpdatedAt="2026-07-26T12:00:00Z"
            members={[]}
            reloadKey={0}
            realtime={null}
          />
        </ToastProvider>
      </I18nProvider>,
    );
    expect(await screen.findByText('Network error. Please check your connection and try again.')).toBeTruthy();
  });
});

describe('IssueCustomFieldsEditor branch coverage', () => {
  function setupWith(def: CustomFieldDef, value: CustomFieldValue | null = null) {
    const list = vi.fn().mockResolvedValue({ data: [{ field_def: def, value }], next_cursor: null });
    const request = vi.fn().mockResolvedValue([]);
    const client = { list, request } as unknown as MeshApiClient;
    render(
      <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
        <ToastProvider regionLabel="notifications">
          <IssueCustomFieldsEditor
            client={client}
            workspaceId="ws-1"
            issueId="iss-1"
            issueUpdatedAt="2026-07-26T12:00:00Z"
            members={[{ id: 'm-1', display_name: 'Ann', member_type: 'human', status: 'active' }]}
            reloadKey={0}
            realtime={null}
          />
        </ToastProvider>
      </I18nProvider>,
    );
    return { request };
  }

  const base = (over: Partial<CustomFieldDef>): CustomFieldDef => ({
    id: 'fd-1', workspace_id: 'ws-1', project_id: null, name: 'F', field_key: 'f',
    type: 'text', is_required: false, required_on: [], default_value: null, config: {},
    position: 0, is_active: true, options: [],
    created_at: '2026-07-26T00:00:00Z', updated_at: '2026-07-26T00:00:00Z',
    ...over,
  });

  const valueOf = (over: Partial<CustomFieldValue>): CustomFieldValue => ({
    field_def_id: 'fd-1', issue_id: 'iss-1',
    value_text: null, value_number: null, value_date: null, value_member_id: null,
    value_member: null, value_boolean: null, value_json: null,
    created_at: '2026-07-26T00:00:00Z', updated_at: '2026-07-26T01:00:00Z',
    ...over,
  });

  it('textarea: commits on change, no-op blur does nothing', async () => {
    const { request } = setupWith(base({ type: 'textarea' }), valueOf({ value_text: 'old' }));
    const area = await screen.findByTestId('issue-field-f');
    fireEvent.blur(area); // unchanged → no commit
    expect(request).not.toHaveBeenCalled();
    await userEvent.type(area, '+new');
    fireEvent.blur(area);
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        'PUT',
        '/api/v1/issues/iss-1/custom-field-values',
        {
          body: { values: [{ field_def_id: 'fd-1', value_text: 'old+new' }] },
          ifMatch: '2026-07-26T12:00:00Z',
        },
      ),
    );
  });

  it('datetime: commits local wall clock as UTC ISO', async () => {
    const { request } = setupWith(
      base({ type: 'datetime' }),
      valueOf({ value_date: '2026-08-02T10:30:00Z' }),
    );
    const input = await screen.findByTestId('issue-field-f');
    fireEvent.change(input, { target: { value: '2026-09-10T08:15' } });
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = request.mock.calls[0][2].body as { values: { value_date?: string; value_json?: unknown; value_member_id?: string | null }[] };
    expect(body.values[0].value_date).toMatch(/2026-09-10T.*:15:00.000Z/);
  });

  it('number: unchanged blur does nothing', async () => {
    const { request } = setupWith(base({ type: 'number' }), valueOf({ value_number: 7 }));
    const input = await screen.findByTestId('issue-field-f');
    fireEvent.blur(input);
    expect(request).not.toHaveBeenCalled();
  });

  it('single_select: renders stored option and commits clear', async () => {
    const def = base({
      type: 'single_select',
      options: [
        { id: 'opt-a', field_def_id: 'fd-1', name: 'A', color: '#111111', position: 0,
          is_active: true, created_at: 'x', updated_at: 'x' },
      ],
    });
    const { request } = setupWith(def, valueOf({ value_json: 'opt-a' }));
    const select = await screen.findByTestId('issue-field-f');
    expect((select as HTMLSelectElement).value).toBe('opt-a');
    await userEvent.selectOptions(select, '');
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = request.mock.calls[0][2].body as { values: { value_date?: string; value_json?: unknown; value_member_id?: string | null }[] };
    expect(body.values[0].value_json).toBeNull();
  });

  it('multi_select: toggles an existing option off', async () => {
    const def = base({
      type: 'multi_select',
      options: [
        { id: 'opt-a', field_def_id: 'fd-1', name: 'A', color: null, position: 0,
          is_active: true, created_at: 'x', updated_at: 'x' },
      ],
    });
    const { request } = setupWith(def, valueOf({ value_json: ['opt-a'] }));
    const box = await screen.findByTestId('issue-field-f-A');
    await userEvent.click(box); // off
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = request.mock.calls[0][2].body as { values: { value_date?: string; value_json?: unknown; value_member_id?: string | null }[] };
    expect(body.values[0].value_json).toEqual([]);
  });

  it('member: commits clear', async () => {
    const { request } = setupWith(
      base({ type: 'member' }),
      valueOf({ value_member_id: 'm-1' }),
    );
    const select = await screen.findByTestId('issue-field-f');
    await userEvent.selectOptions(select, '');
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = request.mock.calls[0][2].body as { values: { value_date?: string; value_json?: unknown; value_member_id?: string | null }[] };
    expect(body.values[0].value_member_id).toBeNull();
  });

  it('boolean: renders stored false and commits toggle', async () => {
    const { request } = setupWith(base({ type: 'boolean' }), valueOf({ value_boolean: false }));
    const box = await screen.findByTestId('issue-field-f');
    expect((box as HTMLInputElement).checked).toBe(false);
    await userEvent.click(box);
    await waitFor(() => expect(request).toHaveBeenCalled());
  });

  it('url: commits a valid url', async () => {
    const { request } = setupWith(base({ type: 'url' }));
    const input = await screen.findByTestId('issue-field-f');
    await userEvent.type(input, 'https://mesh.dev/x');
    fireEvent.blur(input);
    await waitFor(() => expect(request).toHaveBeenCalled());
  });
});
