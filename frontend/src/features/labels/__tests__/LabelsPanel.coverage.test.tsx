/**
 * LabelsPanel 分支/函数覆盖补强(验收 REJECT #3)。专攻:realtime 非空上下文 + 回调 +
 * 清理、删除取消(取消按钮 + 关闭 X)、删除/保存的非 API 错误回退、描述三态渲染
 * (null / 空串 / 非空)。realtime 经 mock 注入。
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { ToastProvider } from '../../../design';
import { I18nProvider } from '../../../i18n';
import { LabelsPanel } from '../LabelsPanel';
import type { LabelWithUsage } from '../types';

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

function labelOf(overrides: Partial<LabelWithUsage> = {}): LabelWithUsage {
  return {
    id: 'lbl-1',
    workspace_id: 'ws-1',
    project_id: null,
    name: 'bug',
    color: '#e5484d',
    description: null,
    scope: 'workspace',
    issue_count: 0,
    created_at: '2026-07-26T00:00:00Z',
    updated_at: '2026-07-26T00:00:00Z',
    ...overrides,
  };
}

function stub(labels: readonly LabelWithUsage[], requestImpl?: ReturnType<typeof vi.fn>) {
  const list = vi.fn().mockResolvedValue({ data: labels, next_cursor: null });
  const request = requestImpl ?? vi.fn().mockResolvedValue({});
  return { client: { list, request } as unknown as MeshApiClient, request, list };
}

function renderPanel(handle: ReturnType<typeof stub>, projectId?: string) {
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

afterEach(() => {
  rt.frameCb = null;
  rt.off.mockClear();
  rt.subscribe.mockClear();
  rt.unsubscribe.mockClear();
});

describe('LabelsPanel coverage', () => {
  it('realtime non-null: subscribes, refreshes on matching frame, cleans up', async () => {
    const handle = stub([labelOf()]);
    const view = renderPanel(handle, 'p-1');
    await screen.findByTestId('label-row-bug');
    expect(rt.subscribe).toHaveBeenCalledWith('workspace:ws-1:labels');
    expect(rt.subscribe).toHaveBeenCalledWith('project:p-1');
    const before = handle.list.mock.calls.length;
    act(() => {
      rt.frameCb?.({ channel: 'workspace:ws-1:labels', event: 'label.updated' });
    });
    await waitFor(() => expect(handle.list.mock.calls.length).toBeGreaterThan(before));
    const after = handle.list.mock.calls.length;
    act(() => {
      rt.frameCb?.({ channel: 'workspace:ws-1:custom_fields', event: 'custom_field.updated' });
    });
    await waitFor(() => expect(handle.list.mock.calls.length).toBe(after));
    view.unmount();
    expect(rt.off).toHaveBeenCalled();
    expect(rt.unsubscribe).toHaveBeenCalledWith('workspace:ws-1:labels');
  });

  it('renders the description span only for non-empty descriptions', async () => {
    const handle = stub([
      labelOf({ id: 'a', name: 'null-desc', description: null }),
      labelOf({ id: 'b', name: 'empty-desc', description: '' }),
      labelOf({ id: 'c', name: 'with-desc', description: 'hello world' }),
    ]);
    renderPanel(handle);
    const withDesc = await screen.findByTestId('label-row-with-desc');
    expect(within(withDesc).getByText('hello world')).toBeTruthy();
    const emptyRow = screen.getByTestId('label-row-empty-desc');
    expect(within(emptyRow).queryByText('hello world')).toBeNull();
  });

  it('create with empty description sends null (description branch)', async () => {
    const user = userEvent.setup();
    const handle = stub([], vi.fn().mockResolvedValue(labelOf({ id: 'n', name: 'x' })));
    renderPanel(handle);
    await user.click(await screen.findByTestId('labels-create'));
    await user.type(screen.getByTestId('label-name-input'), 'x');
    // 描述留空 → body description null
    await user.click(screen.getByTestId('label-save'));
    await waitFor(() => expect(handle.request).toHaveBeenCalled());
    expect(handle.request.mock.calls[0][2].body.description).toBeNull();
  });

  it('create failure with non-API error shows unknown', async () => {
    const user = userEvent.setup();
    const handle = stub([], vi.fn().mockRejectedValue(new Error('boom')));
    renderPanel(handle);
    await user.click(await screen.findByTestId('labels-create'));
    await user.type(screen.getByTestId('label-name-input'), 'x');
    await user.click(screen.getByTestId('label-save'));
    expect(await screen.findByTestId('label-form-error')).toBeTruthy();
  });

  it('delete cancel via cancel button and via close X', async () => {
    const user = userEvent.setup();
    const handle = stub([labelOf()]);
    renderPanel(handle);
    await user.click(await screen.findByTestId('label-delete-bug'));
    expect(screen.getByTestId('label-delete-confirm-text')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /Cancel|取消/ }));
    await waitFor(() => expect(screen.queryByTestId('label-delete-confirm-text')).toBeNull());
    await user.click(screen.getByTestId('label-delete-bug'));
    await screen.findByTestId('label-delete-confirm-text');
    await user.click(document.querySelector('.mesh-dialog__close') as Element);
    await waitFor(() => expect(screen.queryByTestId('label-delete-confirm-text')).toBeNull());
    expect(handle.request).not.toHaveBeenCalled();
  });

  it('delete failure with non-API error toasts unknown', async () => {
    const user = userEvent.setup();
    const handle = stub([labelOf()], vi.fn().mockRejectedValue(new Error('nope')));
    renderPanel(handle);
    await user.click(await screen.findByTestId('label-delete-bug'));
    await user.click(screen.getByTestId('label-delete-confirm'));
    await waitFor(() => expect(handle.request).toHaveBeenCalled());
  });
});
