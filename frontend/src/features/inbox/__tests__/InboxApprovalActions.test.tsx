/**
 * 收件箱行内联审批测试(agent.md §5.4 / README §6.10,L206):
 * review_requested 行据 approval_id + pending 态渲染内联批准/拒绝,
 * 复用 POST /approvals/{id}/approve|reject;决定后行内态收敛;
 * 已决/过期/取消 → 按钮不出现(服务端幂等兜底);approval.decided 帧多端同步。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { InboxApprovalActions } from '../InboxApprovalActions';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

const PENDING = {
  id: 'ap-1',
  subject_type: 'tool_call',
  subject_execution_id: 'exec-1',
  subject_task_id: null,
  status: 'pending',
  action_summary: { action: 'shell.execute', capability: 'shell' },
  requested_at: '2026-08-07T00:00:00Z',
  expires_at: '2099-01-01T00:00:00Z',
  decided_at: null,
  decision_comment: null,
  execution_status: 'awaiting_approval',
};

function ToastLayer(props: { readonly children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

function renderActions(realtime: RealtimeContextValue | null = null): ReturnType<typeof render> {
  return render(
    <ThemeProvider>
      <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
        <ToastLayer>
          <RealtimeContext.Provider value={realtime}>
            <InboxApprovalActions workspaceId="ws-1" approvalId="ap-1" />
          </RealtimeContext.Provider>
        </ToastLayer>
      </I18nProvider>
    </ThemeProvider>,
  );
}

function stubApprovalFetch(
  handler: (url: string, init?: RequestInit) => Promise<Response> | Response,
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function jsonResponse(body: unknown): Response {
  return fakeResponse({ body });
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('InboxApprovalActions', () => {
  it('renders inline approve/reject for a pending approval and converges after approving', async () => {
    const fetchMock = stubApprovalFetch((url, init) => {
      if (url.endsWith('/approvals/ap-1') && (init?.method ?? 'GET') === 'GET') {
        return jsonResponse({ data: PENDING });
      }
      if (url.endsWith('/approvals/ap-1/approve')) {
        return jsonResponse({
          data: { ...PENDING, status: 'approved', decided_at: '2026-08-07T01:00:00Z' },
        });
      }
      throw new Error(`unexpected url ${url}`);
    });

    renderActions();

    fireEvent.click(await screen.findByTestId('inbox-approval-approve-ap-1'));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith('/approvals/ap-1/approve') && init?.method === 'POST',
        ),
      ).toBe(true),
    );
    expect(await screen.findByTestId('inbox-approval-decided-ap-1')).toHaveTextContent('Approved');
    await waitFor(() => expect(screen.queryByTestId('inbox-approval-approve-ap-1')).toBeNull());
    expect(screen.queryByTestId('inbox-approval-reject-ap-1')).toBeNull();
  });

  it('converges to rejected after using the reject action', async () => {
    stubApprovalFetch((url, init) => {
      if (url.endsWith('/approvals/ap-1') && (init?.method ?? 'GET') === 'GET') {
        return jsonResponse({ data: PENDING });
      }
      if (url.endsWith('/approvals/ap-1/reject')) {
        return jsonResponse({
          data: { ...PENDING, status: 'rejected', decided_at: '2026-08-07T01:00:00Z' },
        });
      }
      throw new Error(`unexpected url ${url}`);
    });

    renderActions();

    fireEvent.click(await screen.findByTestId('inbox-approval-reject-ap-1'));

    expect(await screen.findByTestId('inbox-approval-decided-ap-1')).toHaveTextContent('Rejected');
    await waitFor(() => expect(screen.queryByTestId('inbox-approval-reject-ap-1')).toBeNull());
  });

  it('hides the buttons and shows a chip when the approval is already decided', async () => {
    stubApprovalFetch(() =>
      jsonResponse({
        data: { ...PENDING, status: 'approved', decided_at: '2026-08-07T01:00:00Z' },
      }),
    );

    renderActions();

    expect(await screen.findByTestId('inbox-approval-decided-ap-1')).toHaveTextContent('Approved');
    expect(screen.queryByTestId('inbox-approval-approve-ap-1')).toBeNull();
    expect(screen.queryByTestId('inbox-approval-reject-ap-1')).toBeNull();
  });

  it('offers no inline action when the pending approval is already expired', async () => {
    const fetchMock = stubApprovalFetch(() =>
      jsonResponse({ data: { ...PENDING, expires_at: '2020-01-01T00:00:00Z' } }),
    );

    renderActions();

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(0));
    // 状态收敛(决定不渲染任何按钮)发生在 GET 解析后的异步渲染,先冲刷微任务再断言
    await act(async () => undefined);
    expect(screen.queryByTestId('inbox-approval-approve-ap-1')).toBeNull();
    expect(screen.queryByTestId('inbox-approval-reject-ap-1')).toBeNull();
    expect(screen.queryByTestId('inbox-approval-decided-ap-1')).toBeNull();
  });

  it('treats an idempotent non-pending response as the authoritative converged state', async () => {
    stubApprovalFetch((url) => {
      if (url.endsWith('/approvals/ap-1/approve')) {
        // 并发已取消:服务端幂等返回当前态(非 pending)
        return jsonResponse({
          data: { ...PENDING, status: 'cancelled', decided_at: '2026-08-07T01:00:00Z' },
        });
      }
      return jsonResponse({ data: PENDING });
    });

    renderActions();

    fireEvent.click(await screen.findByTestId('inbox-approval-approve-ap-1'));

    expect(await screen.findByTestId('inbox-approval-decided-ap-1')).toHaveTextContent('Cancelled');
  });

  it('surfaces a danger toast on decision failure and keeps the actions available', async () => {
    stubApprovalFetch((url) => {
      if (url.endsWith('/approvals/ap-1/approve')) {
        return fakeResponse({ status: 403, body: { error: { code: 'forbidden', message: 'no' } } });
      }
      return jsonResponse({ data: PENDING });
    });

    renderActions();

    fireEvent.click(await screen.findByTestId('inbox-approval-approve-ap-1'));

    expect(
      await screen.findByText('You do not have permission to perform this action.'),
    ).toBeTruthy();
    expect(await screen.findByTestId('inbox-approval-approve-ap-1')).toBeEnabled();
  });

  it('converges the row when an approval.decided frame arrives from another session', async () => {
    stubApprovalFetch(() => jsonResponse({ data: PENDING }));
    let frameListener: ((frame: RealtimeEventFrame) => void) | null = null;
    const client = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn((listener: (frame: RealtimeEventFrame) => void) => {
        frameListener = listener;
        return vi.fn();
      }),
    };

    renderActions({ state: 'connected', client: client as never } as RealtimeContextValue);

    await screen.findByTestId('inbox-approval-approve-ap-1');
    await waitFor(() => expect(client.subscribe).toHaveBeenCalledWith('workspace:ws-1:executions'));

    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'workspace:ws-1:executions',
        seq: 1,
        event: 'approval.decided',
        payload: { approval_id: 'ap-1', decision: 'rejected' },
      });
    });

    expect(await screen.findByTestId('inbox-approval-decided-ap-1')).toHaveTextContent('Rejected');
    await waitFor(() => expect(screen.queryByTestId('inbox-approval-approve-ap-1')).toBeNull());
  });

  it('ignores approval.decided frames for other approvals', async () => {
    stubApprovalFetch(() => jsonResponse({ data: PENDING }));
    let frameListener: ((frame: RealtimeEventFrame) => void) | null = null;
    const client = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn((listener: (frame: RealtimeEventFrame) => void) => {
        frameListener = listener;
        return vi.fn();
      }),
    };

    renderActions({ state: 'connected', client: client as never } as RealtimeContextValue);

    await screen.findByTestId('inbox-approval-approve-ap-1');
    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'workspace:ws-1:executions',
        seq: 1,
        event: 'approval.decided',
        payload: { approval_id: 'ap-other', decision: 'rejected' },
      });
    });

    await act(async () => undefined);
    expect(screen.getByTestId('inbox-approval-approve-ap-1')).toBeTruthy();
    expect(screen.queryByTestId('inbox-approval-decided-ap-1')).toBeNull();
  });

  it('renders nothing when the approval lookup fails (stale notification)', async () => {
    const fetchMock = stubApprovalFetch(() =>
      fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'gone' } } }),
    );

    renderActions();

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(0));
    await act(async () => undefined);
    expect(screen.queryByTestId('inbox-approval-approve-ap-1')).toBeNull();
    expect(screen.queryByTestId('inbox-approval-reject-ap-1')).toBeNull();
    expect(screen.queryByTestId('inbox-approval-decided-ap-1')).toBeNull();
  });
});
