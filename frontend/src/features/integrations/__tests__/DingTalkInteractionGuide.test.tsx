import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { DingTalkInteractionGuide } from '../DingTalkInteractionGuide';

const APPROVAL = {
  id: 'approval-17',
  subject_type: 'tool_call',
  subject_execution_id: 'exec-17',
  subject_task_id: null,
  status: 'pending',
  action_summary: {
    action: 'Deploy payment service',
    capability: 'deployment',
    permission: 'production:write',
    impact_scope: 'payments production',
    estimated_cost: '$0.24',
    resume_context: {
      completed_steps: 3,
      pending_tool_call: 'deploy_service',
    },
  },
  requested_at: '2026-08-01T10:00:00Z',
  expires_at: '2026-08-01T11:00:00Z',
  decided_at: null,
  decision_comment: null,
  execution_status: 'awaiting_approval',
} as const;

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('DingTalkInteractionGuide', () => {
  it('documents command affordances, acknowledgement, queue position, and two-stage stop feedback', async () => {
    renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="final_only"
        ackTemplate="✅ 已接收，处理中"
      />,
    );

    expect(screen.getByTestId('dingtalk-command-help')).toHaveTextContent('/btw');
    expect(screen.getByTestId('dingtalk-command-help')).toHaveTextContent('/stop');
    expect(screen.getByTestId('dingtalk-command-help')).toHaveTextContent('/help');
    expect(screen.getByTestId('dingtalk-ack-preview')).toHaveTextContent('✅ 已接收，处理中');
    expect(screen.getByTestId('dingtalk-position-preview')).toHaveTextContent(/position 2/i);
    expect(screen.getByTestId('dingtalk-stop-feedback')).toHaveTextContent('⏳');
    expect(screen.getByTestId('dingtalk-stop-feedback')).toHaveTextContent('🛑');
    expect(screen.getByTestId('dingtalk-verbosity-preview')).toHaveTextContent(
      /Final result only/i,
    );
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(
      /Final result only/i,
    );
    expect(screen.getByTestId('dingtalk-notification-body')).toHaveTextContent(
      /one final result notification/i,
    );

    await userEvent.click(screen.getByTestId('dingtalk-command-btw'));
    expect(screen.getByTestId('dingtalk-command-input')).toHaveValue('/btw ');
    await userEvent.type(screen.getByTestId('dingtalk-command-input'), 'focus on payment');
    expect(screen.getByTestId('dingtalk-command-preview')).toHaveTextContent(
      '/btw focus on payment',
    );

    await userEvent.click(screen.getByTestId('dingtalk-command-stop'));
    expect(screen.getByTestId('dingtalk-command-input')).toHaveValue('/stop ');
    await userEvent.click(screen.getByTestId('dingtalk-command-help-button'));
    expect(screen.getByTestId('dingtalk-command-input')).toHaveValue('/help');
  });

  it('loads and refreshes an approval truth record by id, including execution-backed notification state and workspace fallback', async () => {
    let resolveFirst: ((response: Response) => void) | undefined;
    let requestCount = 0;
    const calls: string[] = [];
    vi.stubGlobal('fetch', ((input: RequestInfo | URL) => {
      calls.push(String(input));
      requestCount += 1;
      if (requestCount === 1) {
        return new Promise<Response>((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve(
        fakeResponse({
          body: {
            data: {
              ...APPROVAL,
              status: 'approved',
              decided_at: '2026-08-01T10:02:00Z',
              execution_status: 'completed',
            },
          },
        }),
      );
    }) as typeof fetch);

    renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme team"
        verbosity="progress"
        ackTemplate="✅ Received"
      />,
    );

    expect(screen.queryByTestId('dingtalk-card-state')).toBeNull();
    expect(screen.getByTestId('dingtalk-approval-empty')).toBeInTheDocument();
    await userEvent.type(screen.getByTestId('dingtalk-approval-id'), APPROVAL.id);
    await userEvent.click(screen.getByTestId('dingtalk-approval-load'));
    expect(screen.getByTestId('dingtalk-approval-load')).toHaveAttribute('aria-busy', 'true');

    await act(async () => {
      resolveFirst?.(fakeResponse({ body: { data: APPROVAL } }));
    });
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(
        'Deploy payment service',
      ),
    );
    expect(calls[0]).toContain('/api/v1/workspaces/ws-1/approvals/approval-17');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent('production:write');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent('payments production');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent('$0.24');
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent('deploy_service');
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(
      /Awaiting approval/i,
    );
    expect(screen.getByTestId('dingtalk-card-fallback')).toHaveAttribute(
      'href',
      '/w/acme%20team/approvals?approval_id=approval-17',
    );

    await userEvent.click(screen.getByTestId('dingtalk-approval-refresh'));
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Approved/i),
    );
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(/Completed/i);
    expect(calls).toHaveLength(2);
  });

  it('distinguishes a loaded approval without a linked execution from the empty approval state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        fakeResponse({
          body: {
            data: {
              ...APPROVAL,
              status: 'approved',
              execution_status: null,
            },
          },
        }),
      ),
    );

    renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="progress"
        ackTemplate="Received"
      />,
    );
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(
      /No approval loaded/i,
    );

    await userEvent.type(screen.getByTestId('dingtalk-approval-id'), APPROVAL.id);
    await userEvent.click(screen.getByTestId('dingtalk-approval-load'));

    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Approved/i),
    );
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(
      /Execution status unavailable/i,
    );
    expect(screen.getByTestId('dingtalk-notification-preview')).not.toHaveTextContent(
      /No approval loaded/i,
    );
  });

  it('cancels the busy state when the approval id changes and ignores the stale response', async () => {
    let resolveFirst: ((response: Response) => void) | undefined;
    let requestCount = 0;
    vi.stubGlobal('fetch', (() => {
      requestCount += 1;
      if (requestCount === 1) {
        return new Promise<Response>((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve(fakeResponse({ body: { data: { ...APPROVAL, id: 'approval-18' } } }));
    }) as typeof fetch);

    renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="progress"
        ackTemplate="Received"
      />,
    );
    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: APPROVAL.id },
    });
    fireEvent.click(screen.getByTestId('dingtalk-approval-load'));
    expect(screen.getByTestId('dingtalk-approval-load')).toHaveAttribute('aria-busy', 'true');

    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: 'approval-18' },
    });
    expect(screen.getByTestId('dingtalk-approval-load')).not.toHaveAttribute('aria-busy', 'true');
    fireEvent.click(screen.getByTestId('dingtalk-approval-load'));
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent('approval-18'),
    );

    await act(async () => {
      resolveFirst?.(fakeResponse({ body: { data: APPROVAL } }));
    });
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent('approval-18');
    expect(screen.getByTestId('dingtalk-card-preview')).not.toHaveTextContent('approval-17');
  });

  it('preserves the entered id across initial and refresh errors while rendering sparse and unknown truth states safely', async () => {
    let attempt = 0;
    vi.stubGlobal('fetch', ((input: RequestInfo | URL) => {
      void input;
      attempt += 1;
      if (attempt === 1) return Promise.reject(new Error('offline'));
      if (attempt === 3) {
        return Promise.resolve(
          fakeResponse({
            status: 500,
            body: { error: { code: 'internal_error', message: 'refresh failed' } },
          }),
        );
      }
      return Promise.resolve(
        fakeResponse({
          body: {
            data: {
              ...APPROVAL,
              status: attempt === 2 ? 'cancelled' : 'expired',
              action_summary:
                attempt === 2
                  ? { action: '', impact_scope: { project: 'payments' } }
                  : { resume_context: {} },
              execution_status: attempt === 2 ? 'failed' : 'paused_by_operator',
            },
          },
        }),
      );
    }) as typeof fetch);

    renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="final_only"
        ackTemplate=""
      />,
    );
    expect(screen.getByTestId('dingtalk-ack-preview')).toHaveTextContent(/disabled/i);
    await userEvent.type(screen.getByTestId('dingtalk-approval-id'), 'approval-18');
    await userEvent.click(screen.getByTestId('dingtalk-approval-load'));
    await waitFor(() => expect(screen.getByText(/network error/i)).toBeInTheDocument());
    expect(screen.getByTestId('dingtalk-approval-id')).toHaveValue('approval-18');

    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Cancelled/i),
    );
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(/Failed/i);
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent('{"project":"payments"}');
    expect(screen.getAllByText(/Not provided/i)).toHaveLength(4);

    await userEvent.click(screen.getByTestId('dingtalk-approval-refresh'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Cancelled/i);
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Expired/i),
    );
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(
      'paused_by_operator',
    );
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Terminal cards/i);
  });

  it('polls a loaded pending approval and stops automatically after the callback truth becomes terminal', async () => {
    vi.useFakeTimers();
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
    let requestCount = 0;
    vi.stubGlobal('fetch', (() => {
      requestCount += 1;
      return Promise.resolve(
        fakeResponse({
          body: {
            data: {
              ...APPROVAL,
              status: requestCount === 1 ? 'pending' : 'approved',
              execution_status: requestCount === 1 ? 'awaiting_approval' : 'completed',
            },
          },
        }),
      );
    }) as typeof fetch);

    const view = renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="progress"
        ackTemplate="Received"
      />,
    );
    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: APPROVAL.id },
    });
    fireEvent.click(screen.getByTestId('dingtalk-approval-load'));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Pending/i);
    expect(requestCount).toBe(1);

    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Approved/i);
    expect(screen.getByTestId('dingtalk-notification-preview')).toHaveTextContent(/Completed/i);
    expect(requestCount).toBe(2);
    expect(clearIntervalSpy).toHaveBeenCalled();

    await act(async () => vi.advanceTimersByTimeAsync(12000));
    expect(requestCount).toBe(2);
    view.unmount();
  });

  it('does not let a stale approval poll unlock polling for a newer approval id', async () => {
    vi.useFakeTimers();
    let requestCount = 0;
    let resolveOldPoll: ((response: Response) => void) | undefined;
    let resolveNewPoll: ((response: Response) => void) | undefined;
    vi.stubGlobal('fetch', (() => {
      requestCount += 1;
      if (requestCount === 2) {
        return new Promise<Response>((resolve) => {
          resolveOldPoll = resolve;
        });
      }
      if (requestCount === 4) {
        return new Promise<Response>((resolve) => {
          resolveNewPoll = resolve;
        });
      }
      return Promise.resolve(
        fakeResponse({
          body: {
            data: {
              ...APPROVAL,
              id: requestCount < 3 ? APPROVAL.id : 'approval-18',
            },
          },
        }),
      );
    }) as typeof fetch);

    const view = renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="progress"
        ackTemplate="Received"
      />,
    );
    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: APPROVAL.id },
    });
    fireEvent.click(screen.getByTestId('dingtalk-approval-load'));
    await act(async () => vi.advanceTimersByTimeAsync(0));

    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(requestCount).toBe(2);
    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: 'approval-18' },
    });
    fireEvent.click(screen.getByTestId('dingtalk-approval-load'));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(requestCount).toBe(4);

    await act(async () => {
      resolveOldPoll?.(fakeResponse({ body: { data: APPROVAL } }));
      await Promise.resolve();
    });
    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(requestCount).toBe(4);

    view.unmount();
    await act(async () => {
      resolveNewPoll?.(fakeResponse({ body: { data: { ...APPROVAL, id: 'approval-18' } } }));
      await Promise.resolve();
    });
  });

  it('does not start a queued old-id poll after the approval input changes', async () => {
    vi.useFakeTimers();
    const intervalSpy = vi.spyOn(window, 'setInterval');
    let requestCount = 0;
    vi.stubGlobal('fetch', (() => {
      requestCount += 1;
      return Promise.resolve(fakeResponse({ body: { data: APPROVAL } }));
    }) as typeof fetch);

    const view = renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="progress"
        ackTemplate="Received"
      />,
    );
    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: APPROVAL.id },
    });
    fireEvent.click(screen.getByTestId('dingtalk-approval-load'));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(requestCount).toBe(1);

    const oldPoll = intervalSpy.mock.calls.find(([, delay]) => delay === 4000)?.[0];
    expect(oldPoll).toBeTypeOf('function');
    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: 'approval-18' },
    });
    await act(async () => {
      if (typeof oldPoll === 'function') oldPoll();
      await Promise.resolve();
    });

    expect(requestCount).toBe(1);
    expect(screen.getByTestId('dingtalk-approval-empty')).toBeInTheDocument();
    view.unmount();
  });

  it('lets a manual refresh synchronously own the request before a queued poll runs', async () => {
    vi.useFakeTimers();
    const intervalSpy = vi.spyOn(window, 'setInterval');
    let requestCount = 0;
    let resolveManual: ((response: Response) => void) | undefined;
    vi.stubGlobal('fetch', (() => {
      requestCount += 1;
      if (requestCount === 2) {
        return new Promise<Response>((resolve) => {
          resolveManual = resolve;
        });
      }
      return Promise.resolve(fakeResponse({ body: { data: APPROVAL } }));
    }) as typeof fetch);

    const view = renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="progress"
        ackTemplate="Received"
      />,
    );
    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: APPROVAL.id },
    });
    fireEvent.click(screen.getByTestId('dingtalk-approval-load'));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    const queuedPoll = intervalSpy.mock.calls.find(([, delay]) => delay === 4000)?.[0];
    expect(queuedPoll).toBeTypeOf('function');

    fireEvent.click(screen.getByTestId('dingtalk-approval-refresh'));
    await act(async () => {
      if (typeof queuedPoll === 'function') queuedPoll();
      await Promise.resolve();
    });
    expect(requestCount).toBe(2);

    await act(async () => {
      resolveManual?.(
        fakeResponse({
          body: {
            data: {
              ...APPROVAL,
              status: 'approved',
              decided_at: '2026-08-01T10:03:00Z',
            },
          },
        }),
      );
      await Promise.resolve();
    });
    expect(screen.getByTestId('dingtalk-card-preview')).toHaveTextContent(/Approved/i);
    expect(screen.getByTestId('dingtalk-approval-refresh')).not.toHaveAttribute(
      'aria-busy',
      'true',
    );
    view.unmount();
  });

  it('clears pending approval polling when the guide unmounts', async () => {
    vi.useFakeTimers();
    let requestCount = 0;
    vi.stubGlobal('fetch', (() => {
      requestCount += 1;
      return Promise.resolve(fakeResponse({ body: { data: APPROVAL } }));
    }) as typeof fetch);

    const view = renderWithProviders(
      <DingTalkInteractionGuide
        workspaceId="ws-1"
        workspaceSlug="acme"
        verbosity="final_only"
        ackTemplate="Received"
      />,
    );
    fireEvent.change(screen.getByTestId('dingtalk-approval-id'), {
      target: { value: APPROVAL.id },
    });
    fireEvent.click(screen.getByTestId('dingtalk-approval-load'));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(requestCount).toBe(1);

    view.unmount();
    await act(async () => vi.advanceTimersByTimeAsync(8000));
    expect(requestCount).toBe(1);
  });
});
