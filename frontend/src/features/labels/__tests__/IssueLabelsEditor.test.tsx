/**
 * 标签选择器组件测试:chip 渲染、联想添加、× 移除、就地新建、
 * issue.labels_changed 增量合并(§4.2/§3.5)。
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { I18nProvider } from '../../../i18n';
import { ToastProvider } from '../../../design';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { IssueLabelsEditor } from '../IssueLabelsEditor';
import type { Label } from '../types';

function labelOf(overrides: Partial<Label>): Label {
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

const BUG = labelOf({ id: 'lbl-bug', name: 'bug' });
const UX = labelOf({ id: 'lbl-ux', name: 'ux', color: '#3e63dd' });

interface Stub {
  client: MeshApiClient;
  list: ReturnType<typeof vi.fn>;
  request: ReturnType<typeof vi.fn>;
}

function stubClient(current: Label[], catalog: Label[]): Stub {
  const list = vi.fn().mockImplementation((path: string) => {
    if (path === '/api/v1/issues/iss-1/labels') {
      return Promise.resolve({ data: current, next_cursor: null });
    }
    return Promise.resolve({ data: catalog, next_cursor: null });
  });
  const request = vi.fn().mockResolvedValue({ labels: current });
  return { client: { list, request } as unknown as MeshApiClient, list, request };
}

function renderEditor(stub: Stub, realtime: RealtimeContextValue | null = null) {
  return render(
    <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
      <ToastProvider regionLabel="notifications">
        <IssueLabelsEditor
          client={stub.client}
          workspaceId="ws-1"
          projectId={null}
          issueId="iss-1"
          reloadKey={0}
          issueUpdatedAt="2026-07-26T12:00:00Z"
          realtime={realtime}
        />
      </ToastProvider>
    </I18nProvider>,
  );
}

function fakeRealtime() {
  const listeners: Array<(frame: unknown) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((listener: (frame: unknown) => void) => {
      listeners.push(listener);
      return () => undefined;
    }),
  };
  const value = { state: 'open', client } as unknown as RealtimeContextValue;
  return {
    value,
    emit: (frame: unknown) => listeners.forEach((l) => l(frame)),
    client,
  };
}

describe('IssueLabelsEditor', () => {
  it('renders current labels as removable chips', async () => {
    const stub = stubClient([BUG], [BUG, UX]);
    renderEditor(stub);
    const chips = await screen.findByTestId('issue-label-chips');
    expect(chips.textContent).toContain('bug');
    expect(chips.textContent).not.toContain('ux');
  });

  it('adds a label from the suggestion list', async () => {
    const stub = stubClient([BUG], [BUG, UX]);
    stub.request.mockResolvedValue({ labels: [BUG, UX] });
    renderEditor(stub);
    await screen.findByTestId('issue-label-chips');
    const search = screen.getByTestId('issue-label-search');
    await userEvent.type(search, 'ux');
    const suggest = await screen.findByTestId('issue-label-suggest');
    expect(suggest.textContent).toContain('ux');
    await userEvent.click(screen.getByText('ux'));
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'POST',
        '/api/v1/issues/iss-1/labels/lbl-ux',
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId('issue-label-chips').textContent).toContain('ux'),
    );
  });

  it('removes a label via the chip × button', async () => {
    const stub = stubClient([BUG, UX], [BUG, UX]);
    stub.request.mockResolvedValue({ labels: [UX] });
    renderEditor(stub);
    await userEvent.click(await screen.findByLabelText('Remove label bug'));
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'DELETE',
        '/api/v1/issues/iss-1/labels/lbl-bug',
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId('issue-label-chips').textContent).not.toContain('bug'),
    );
  });

  it('offers inline creation when no suggestion matches', async () => {
    const stub = stubClient([], [BUG]);
    // createLabel → POST /labels; then add → POST /issues/.../labels/{new}
    stub.request
      .mockResolvedValueOnce({ ...labelOf({ id: 'lbl-new', name: 'fresh' }) })
      .mockResolvedValueOnce({ labels: [labelOf({ id: 'lbl-new', name: 'fresh' })] });
    renderEditor(stub);
    await screen.findByTestId('issue-label-chips');
    await userEvent.type(screen.getByTestId('issue-label-search'), 'fresh');
    await userEvent.click(await screen.findByTestId('issue-label-create-inline'));
    // Dialog with the color picker appears; confirm creates and attaches.
    await userEvent.click(await screen.findByText('Create'));
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/labels', {
        body: { name: 'fresh', color: expect.any(String), project_id: null },
      }),
    );
    await waitFor(() =>
      expect(stub.request).toHaveBeenCalledWith(
        'POST',
        '/api/v1/issues/iss-1/labels/lbl-new',
      ),
    );
  });

  it('merges issue.labels_changed frames into the chip set (§3.5)', async () => {
    const stub = stubClient([BUG], [BUG, UX]);
    const rt = fakeRealtime();
    renderEditor(stub, rt.value);
    await waitFor(() =>
      expect(screen.getByTestId('issue-label-chips').textContent).toContain('bug'),
    );
    expect(rt.client.subscribe).toHaveBeenCalledWith('workspace:ws-1:labels');
    rt.emit({
      op: 'event',
      channel: 'issue:iss-1',
      seq: 1,
      event: 'issue.labels_changed',
      payload: { issue_id: 'iss-1', labels: [UX] },
    });
    await waitFor(() => {
      const chips = screen.getByTestId('issue-label-chips');
      expect(chips.textContent).toContain('ux');
      expect(chips.textContent).not.toContain('bug');
    });
  });

  it('ignores labels_changed frames for other issues', async () => {
    const stub = stubClient([BUG], [BUG, UX]);
    const rt = fakeRealtime();
    renderEditor(stub, rt.value);
    await waitFor(() =>
      expect(screen.getByTestId('issue-label-chips').textContent).toContain('bug'),
    );
    rt.emit({
      op: 'event',
      channel: 'issue:other',
      seq: 1,
      event: 'issue.labels_changed',
      payload: { issue_id: 'other', labels: [] },
    });
    expect(screen.getByTestId('issue-label-chips').textContent).toContain('bug');
  });

  it('ignores labels_changed frames whose payload is not a label array', async () => {
    const stub = stubClient([BUG], [BUG, UX]);
    const rt = fakeRealtime();
    renderEditor(stub, rt.value);
    await waitFor(() =>
      expect(screen.getByTestId('issue-label-chips').textContent).toContain('bug'),
    );
    rt.emit({
      op: 'event',
      channel: 'issue:iss-1',
      seq: 1,
      event: 'issue.labels_changed',
      payload: { issue_id: 'iss-1', labels: 'corrupt' },
    });
    expect(screen.getByTestId('issue-label-chips').textContent).toContain('bug');
  });

  it('refreshes the suggestion catalog on label.* definition frames', async () => {
    const stub = stubClient([BUG], [BUG, UX]);
    const rt = fakeRealtime();
    renderEditor(stub, rt.value);
    await screen.findByTestId('issue-label-chips');
    const callsBefore = stub.list.mock.calls.length;
    rt.emit({
      op: 'event',
      channel: 'workspace:ws-1:labels',
      seq: 2,
      event: 'label.updated',
      payload: {},
    });
    await waitFor(() => expect(stub.list.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  it('drains a paginated label catalog', async () => {
    let catalogCalls = 0;
    const list = vi.fn().mockImplementation((path: string) => {
      if (path === '/api/v1/issues/iss-1/labels') {
        return Promise.resolve({ data: [], next_cursor: null });
      }
      catalogCalls += 1;
      return catalogCalls === 1
        ? Promise.resolve({ data: [BUG], next_cursor: 'page-2' })
        : Promise.resolve({ data: [UX], next_cursor: null });
    });
    const client = { list, request: vi.fn() } as unknown as MeshApiClient;
    render(
      <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
        <ToastProvider regionLabel="notifications">
          <IssueLabelsEditor
            client={client}
            workspaceId="ws-1"
            projectId={null}
            issueId="iss-1"
            reloadKey={0}
            issueUpdatedAt="2026-07-26T12:00:00Z"
            realtime={null}
          />
        </ToastProvider>
      </I18nProvider>,
    );
    await screen.findByTestId('issue-label-search');
    // 'u' 同时匹配两页的 bug / ux。
    await userEvent.type(screen.getByTestId('issue-label-search'), 'u');
    // 两页目录都进了联想集(bug + ux)。
    await waitFor(() => {
      const suggest = screen.getByTestId('issue-label-suggest');
      expect(suggest.textContent).toContain('bug');
      expect(suggest.textContent).toContain('ux');
    });
    expect(catalogCalls).toBe(2);
  });

  it('toasts when adding a label fails', async () => {
    const stub = stubClient([], [BUG]);
    stub.request.mockRejectedValue(new Error('boom'));
    renderEditor(stub);
    await screen.findByTestId('issue-label-search');
    await userEvent.type(screen.getByTestId('issue-label-search'), 'bug');
    const suggest = await screen.findByTestId('issue-label-suggest');
    await userEvent.click(within(suggest).getByText('bug'));
    expect(await screen.findByText('Network error. Please check your connection and try again.')).toBeTruthy();
  });

  it('toasts when removing a label fails', async () => {
    const stub = stubClient([BUG], [BUG]);
    stub.request.mockRejectedValue(new Error('boom'));
    renderEditor(stub);
    await userEvent.click(await screen.findByLabelText('Remove label bug'));
    expect(await screen.findByText('Network error. Please check your connection and try again.')).toBeTruthy();
  });

  it('toasts when inline creation fails', async () => {
    const stub = stubClient([], [BUG]);
    stub.request.mockRejectedValue(new Error('boom'));
    renderEditor(stub);
    await screen.findByTestId('issue-label-search');
    await userEvent.type(screen.getByTestId('issue-label-search'), 'fresh');
    await userEvent.click(await screen.findByTestId('issue-label-create-inline'));
    await userEvent.click(await screen.findByText('Create'));
    expect(await screen.findByText('Network error. Please check your connection and try again.')).toBeTruthy();
  });

  it('toasts when the initial label load fails', async () => {
    const list = vi.fn().mockRejectedValue(new Error('boom'));
    const client = { list, request: vi.fn() } as unknown as MeshApiClient;
    render(
      <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
        <ToastProvider regionLabel="notifications">
          <IssueLabelsEditor
            client={client}
            workspaceId="ws-1"
            projectId={null}
            issueId="iss-1"
            reloadKey={0}
            issueUpdatedAt="2026-07-26T12:00:00Z"
            realtime={null}
          />
        </ToastProvider>
      </I18nProvider>,
    );
    expect(await screen.findByText('Network error. Please check your connection and try again.')).toBeTruthy();
  });
});

describe('IssueLabelsEditor branch coverage', () => {
  it('ignores non-event realtime frames', async () => {
    const stub = stubClient([BUG], [BUG, UX]);
    const rt = fakeRealtime();
    renderEditor(stub, rt.value);
    await waitFor(() =>
      expect(screen.getByTestId('issue-label-chips').textContent).toContain('bug'),
    );
    const callsBefore = stub.list.mock.calls.length;
    rt.emit({ op: 'state', channel: 'workspace:ws-1:labels', seq: 1, event: 'x', payload: {} });
    expect(stub.list.mock.calls.length).toBe(callsBefore);
  });

  it('treats a malformed catalog envelope as empty', async () => {
    const list = vi.fn().mockImplementation((path: string) => {
      if (path === '/api/v1/issues/iss-1/labels') {
        return Promise.resolve({ data: [BUG], next_cursor: null });
      }
      return Promise.resolve({ data: 'junk', next_cursor: null });
    });
    const client = { list, request: vi.fn() } as unknown as MeshApiClient;
    render(
      <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
        <ToastProvider regionLabel="notifications">
          <IssueLabelsEditor
            client={client}
            workspaceId="ws-1"
            projectId={null}
            issueId="iss-1"
            reloadKey={0}
            issueUpdatedAt="2026-07-26T12:00:00Z"
            realtime={null}
          />
        </ToastProvider>
      </I18nProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId('issue-label-chips').textContent).toContain('bug'),
    );
    await userEvent.type(screen.getByTestId('issue-label-search'), 'zzz');
    // 目录畸形 → 联想为空 → 出现就地新建项。
    expect(await screen.findByTestId('issue-label-create-inline')).toBeTruthy();
  });

  it('skips a concurrent add while one is in flight', async () => {
    let resolveAdd!: (value: unknown) => void;
    const stub = stubClient([], [BUG]);
    stub.request.mockImplementation(
      () => new Promise((resolve) => { resolveAdd = resolve; }),
    );
    renderEditor(stub);
    await userEvent.type(screen.getByTestId('issue-label-search'), 'bug');
    const item = await screen.findByText('bug');
    void userEvent.click(item);
    await waitFor(() => expect(stub.request).toHaveBeenCalledTimes(1));
    await userEvent.click(item); // 第二次被 busy 守卫跳过
    resolveAdd({ labels: [BUG] });
    await waitFor(() =>
      expect(screen.getByTestId('issue-label-chips').textContent).toContain('bug'),
    );
    expect(stub.request).toHaveBeenCalledTimes(1);
  });
});
