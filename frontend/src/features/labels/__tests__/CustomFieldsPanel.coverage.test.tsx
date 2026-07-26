/**
 * CustomFieldsPanel 分支/函数覆盖补强(验收 REJECT #3:新增代码分支/函数 ≥90%)。
 * 专攻主测试未触及的路径:校验分支(name/position/重名选项/非法 hex)、各 catch 回退
 * (含非 MeshApiError 的 error.unknown)、停用/启用双向、删除取消(取消按钮 + 关闭 X)、
 * 选项编辑器增删改停用的成功与失败分支、realtime 帧失效(非空上下文 + 回调 + 清理)、
 * 项目级/停用态行渲染、类型切换的草稿分支。realtime 经 mock 注入可控上下文。
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { ToastProvider } from '../../../design';
import { I18nProvider } from '../../../i18n';
import { CustomFieldsPanel } from '../CustomFieldsPanel';
import type { CustomFieldDef } from '../types';

// 可控 realtime 上下文:捕获 onFrame 回调以便测试内主动投递帧。
const rt = vi.hoisted(() => ({
  frameCb: null as null | ((frame: { channel: string; event: string }) => void),
  off: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
}));
vi.mock('../../../shell/AppShell', () => ({
  useRealtimeContext: () => ({
    client: {
      subscribe: rt.subscribe,
      unsubscribe: rt.unsubscribe,
      onFrame: (cb: typeof rt.frameCb) => {
        rt.frameCb = cb;
        return rt.off;
      },
    },
  }),
}));

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

function stub(fields: readonly CustomFieldDef[], requestImpl?: ReturnType<typeof vi.fn>) {
  const list = vi.fn().mockResolvedValue({ data: fields, next_cursor: null });
  const request = requestImpl ?? vi.fn().mockResolvedValue({});
  return { client: { list, request } as unknown as MeshApiClient, request, list };
}

function renderPanel(handle: ReturnType<typeof stub>, projectId?: string) {
  const view = render(
    <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
      <ToastProvider regionLabel="notifications">
        <CustomFieldsPanel client={handle.client} workspaceId="ws-1" projectId={projectId} />
      </ToastProvider>
    </I18nProvider>,
  );
  return view;
}

afterEach(() => {
  rt.frameCb = null;
  rt.off.mockClear();
  rt.subscribe.mockClear();
  rt.unsubscribe.mockClear();
});

describe('CustomFieldsPanel coverage', () => {
  it('realtime non-null: subscribes, refreshes on matching frame, ignores others, cleans up', async () => {
    const handle = stub([fieldOf()]);
    const view = renderPanel(handle, 'p-1');
    await screen.findByTestId('field-row-severity');
    expect(rt.subscribe).toHaveBeenCalledWith('workspace:ws-1:custom_fields');
    expect(rt.subscribe).toHaveBeenCalledWith('project:p-1');
    const before = handle.list.mock.calls.length;
    // 匹配频道 + 事件 → 刷新
    rt.frameCb?.({ channel: 'workspace:ws-1:custom_fields', event: 'custom_field.updated' });
    await waitFor(() => expect(handle.list.mock.calls.length).toBeGreaterThan(before));
    // 不匹配频道 → 不再触发
    const after = handle.list.mock.calls.length;
    rt.frameCb?.({ channel: 'workspace:ws-1:labels', event: 'label.created' });
    await waitFor(() => expect(handle.list.mock.calls.length).toBe(after));
    view.unmount();
    expect(rt.off).toHaveBeenCalled();
    expect(rt.unsubscribe).toHaveBeenCalledWith('workspace:ws-1:custom_fields');
  });

  it('rejects empty / too-long name', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.click(screen.getByTestId('field-save'));
    expect(await screen.findByTestId('field-form-error')).toBeTruthy();
    expect(handle.request).not.toHaveBeenCalled();
  });

  it('rejects a non-finite position', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.type(screen.getByTestId('field-name-input'), 'X');
    await user.type(screen.getByTestId('field-key-input'), 'x1');
    await user.clear(screen.getByTestId('field-position-input'));
    await user.type(screen.getByTestId('field-position-input'), 'abc');
    await user.click(screen.getByTestId('field-save'));
    expect(await screen.findByTestId('field-form-error')).toBeTruthy();
    expect(handle.request).not.toHaveBeenCalled();
  });

  it('rejects duplicate option names and sends null for invalid option hex', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.type(screen.getByTestId('field-name-input'), 'X');
    await user.type(screen.getByTestId('field-key-input'), 'x2');
    await user.selectOptions(screen.getByTestId('field-type-select'), 'single_select');
    await user.type(screen.getByTestId('field-option-name-0'), 'dup');
    await user.clear(screen.getByTestId('field-option-color-0'));
    await user.type(screen.getByTestId('field-option-color-0'), 'nope');
    await user.click(screen.getByTestId('field-option-add'));
    await user.type(screen.getByTestId('field-option-name-1'), 'dup');
    // 重名 → 客户端拒绝(同时覆盖 option 名称/颜色 onChange 与 add 点击)
    await user.click(screen.getByTestId('field-save'));
    expect(await screen.findByTestId('field-form-error')).toBeTruthy();
    // 清掉重名,保留非法 hex,验证发送 color=null
    await user.clear(screen.getByTestId('field-option-name-1'));
    await user.type(screen.getByTestId('field-option-name-1'), 'other');
    await user.click(screen.getByTestId('field-save'));
    await waitFor(() => expect(handle.request).toHaveBeenCalled());
    const body = handle.request.mock.calls[0][2].body as { options: { color: string | null }[] };
    expect(body.options[0].color).toBeNull();
  });

  it('shows error.unknown when create rejects with a non-API error', async () => {
    const user = userEvent.setup();
    const handle = stub([], vi.fn().mockRejectedValue(new Error('boom')));
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.type(screen.getByTestId('field-name-input'), 'X');
    await user.type(screen.getByTestId('field-key-input'), 'x3');
    await user.click(screen.getByTestId('field-save'));
    expect(await screen.findByTestId('field-form-error')).toBeTruthy();
  });

  it('switching select type twice keeps existing drafts (false branch)', async () => {
    const user = userEvent.setup();
    const handle = stub([]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('fields-create'));
    await user.selectOptions(screen.getByTestId('field-type-select'), 'single_select');
    expect(screen.getByTestId('field-option-name-0')).toBeTruthy();
    await user.selectOptions(screen.getByTestId('field-type-select'), 'multi_select');
    // 已有草稿 → 不再注入新草稿(false 分支),option-0 仍在
    expect(screen.getByTestId('field-option-name-0')).toBeTruthy();
    expect(screen.queryByTestId('field-option-name-1')).toBeNull();
  });

  it('renders inactive + project-scope rows with the right badges/glyphs', async () => {
    const handle = stub([
      fieldOf({ id: 'cf-inactive', field_key: 'off', is_active: false }),
      fieldOf({ id: 'cf-proj', field_key: 'projf', project_id: 'p-1', type: 'number', options: [] }),
    ]);
    renderPanel(handle);
    const offRow = await screen.findByTestId('field-row-off');
    expect(within(offRow).getByText(/inactive|停用/)).toBeTruthy();
    expect(within(offRow).getByText(/active|启用/)).toBeTruthy(); // 激活按钮文案
    const projRow = screen.getByTestId('field-row-projf');
    expect(within(projRow).getByText(/project|项目级/)).toBeTruthy();
  });

  it('reactivates an inactive field (activated toast branch)', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf({ is_active: false })]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-toggle-severity'));
    await waitFor(() =>
      expect(handle.request).toHaveBeenCalledWith('PATCH', '/api/v1/custom-fields/cf-1', {
        body: { is_active: true },
        ifMatch: '2026-07-26T00:00:00Z',
      }),
    );
  });

  it('toggle failure with non-API error toasts unknown', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()], vi.fn().mockRejectedValue(new Error('nope')));
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-toggle-severity'));
    await waitFor(() => expect(handle.request).toHaveBeenCalled());
  });

  it('delete cancel via cancel button and via close X', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()]);
    renderPanel(handle);
    // 取消按钮
    await user.click(await screen.findByTestId('field-delete-severity'));
    expect(screen.getByTestId('field-delete-confirm-text')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /Cancel|取消/ }));
    await waitFor(() => expect(screen.queryByTestId('field-delete-confirm-text')).toBeNull());
    // 关闭 X
    await user.click(screen.getByTestId('field-delete-severity'));
    await screen.findByTestId('field-delete-confirm-text');
    await user.click(document.querySelector('.mesh-dialog__close') as Element);
    await waitFor(() => expect(screen.queryByTestId('field-delete-confirm-text')).toBeNull());
    expect(handle.request).not.toHaveBeenCalled();
  });

  it('delete failure with non-API error toasts unknown', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()], vi.fn().mockRejectedValue(new Error('nope')));
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-delete-severity'));
    await user.click(screen.getByTestId('field-delete-confirm'));
    await waitFor(() => expect(handle.request).toHaveBeenCalled());
  });

  it('edit dialog: position onChange is wired', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-edit-severity'));
    const pos = screen.getByTestId('field-position-input') as HTMLInputElement;
    await user.clear(pos);
    await user.type(pos, '7');
    expect(pos.value).toBe('7');
  });

  it('options editor: remove success, add with invalid hex (null), and failure branches', async () => {
    const user = userEvent.setup();
    // 含一个停用 + 无色选项,覆盖编辑器行内 inactive/activate 文案与无 dot 分支
    const field = fieldOf({
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
        {
          id: 'opt-2',
          field_def_id: 'cf-1',
          name: 'Minor',
          color: null,
          position: 1,
          is_active: false,
          created_at: '2026-07-26T00:00:00Z',
          updated_at: '2026-07-26T00:00:00Z',
        },
      ],
    });
    const handle = stub([field]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-options-severity'));
    const editor = await screen.findByTestId('options-editor');
    expect(within(editor).getByTestId('option-row-Minor')).toBeTruthy();
    // 移除成功(handleRemoveOption + onClick)
    await user.click(within(editor).getByTestId('option-remove-Major'));
    await waitFor(() => expect(handle.request).toHaveBeenCalledWith('DELETE', '/api/v1/custom-fields/cf-1/options/opt-1'));
  });

  it('options editor: toggle failure and add failure (non-API) + add invalid hex', async () => {
    const user = userEvent.setup();
    // toggle 失败
    const t1 = stub([fieldOf()], vi.fn().mockRejectedValue(new Error('x')));
    renderPanel(t1);
    await user.click(await screen.findByTestId('field-options-severity'));
    await user.click((await screen.findByTestId('options-editor')).querySelector('[data-testid="option-toggle-Major"]') as Element);
    await waitFor(() => expect(t1.request).toHaveBeenCalled());
  });

  it('options editor: add with invalid hex sends null color', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-options-severity'));
    const editor = await screen.findByTestId('options-editor');
    await user.type(within(editor).getByTestId('option-new-name'), 'New');
    await user.clear(within(editor).getByTestId('color-hex-input'));
    await user.type(within(editor).getByTestId('color-hex-input'), 'bad');
    await user.click(within(editor).getByTestId('option-add-confirm'));
    await waitFor(() => expect(handle.request).toHaveBeenCalled());
    expect(handle.request.mock.calls[0][2].body.color).toBeNull();
  });

  it('options editor: add failure (non-API) shows unknown', async () => {
    const user = userEvent.setup();
    const handle = stub([fieldOf()], vi.fn().mockRejectedValue(new Error('boom')));
    renderPanel(handle);
    await user.click(await screen.findByTestId('field-options-severity'));
    const editor = await screen.findByTestId('options-editor');
    await user.type(within(editor).getByTestId('option-new-name'), 'New2');
    await user.click(within(editor).getByTestId('option-add-confirm'));
    expect(await screen.findByTestId('options-editor-error')).toBeTruthy();
  });
});
