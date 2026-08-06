import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { IssueExecutionsPanel } from '../IssueExecutionsPanel';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

const RUNNING = {
  id: 'exec-running',
  agent_id: 'agent-1',
  issue_id: 'issue-1',
  trigger: 'assign',
  status: 'running',
  priority: 100,
  required_capabilities: [],
  label_requirements: {},
  timeout_seconds: 120,
  max_attempts: 3,
  queued_at: '2026-08-05T08:00:00Z',
  finished_at: null,
  failure_reason: null,
  result: null,
  attempts: [
    {
      id: 'attempt-1',
      attempt_number: 1,
      runtime_id: 'runtime-1',
      runtime_name: 'runner-east',
      status: 'running',
      claimed_at: '2026-08-05T08:00:01Z',
      started_at: '2026-08-05T08:00:02Z',
      finished_at: null,
      working_branch: 'agent/exec-running/a1',
      result: null,
      failure_reason: null,
    },
  ],
};

const COMPLETED = {
  ...RUNNING,
  id: 'exec-completed',
  status: 'completed',
  queued_at: '2026-08-05T07:00:00Z',
  finished_at: '2026-08-05T07:01:00Z',
  result: { summary: 'Implemented the requested change' },
  attempts: [
    {
      ...RUNNING.attempts[0],
      id: 'attempt-2',
      status: 'completed',
      finished_at: '2026-08-05T07:01:00Z',
    },
  ],
};

const PRIVATE_QUEUED = {
  ...RUNNING,
  id: 'exec-private',
  status: 'queued',
  queued_at: '2026-08-05T09:00:00Z',
  attempts: [],
};

const FAILED_WITHOUT_ATTEMPTS = {
  ...RUNNING,
  id: 'exec-failed',
  status: 'failed',
  finished_at: '2026-08-05T08:02:00Z',
  failure_reason: 'provider_unavailable',
  attempts: undefined,
};

function ToastLayer(props: { readonly children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

function renderPanel(
  props: Partial<React.ComponentProps<typeof IssueExecutionsPanel>> = {},
  realtime: RealtimeContextValue | null = null,
): ReturnType<typeof render> {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime}>
              <IssueExecutionsPanel
                workspaceId="ws-1"
                workspaceSlug="acme"
                issueId="issue-1"
                reviewable
                onApprove={vi.fn()}
                onRequestChanges={vi.fn()}
                {...props}
              />
            </RealtimeContext.Provider>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('IssueExecutionsPanel', () => {
  it('lists every execution with runtime, deep link and active progress controls', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), init });
        return fakeResponse({ body: { data: [RUNNING, COMPLETED], next_cursor: null } });
      }),
    );

    renderPanel();

    expect(await screen.findByTestId('issue-executions-panel')).toBeTruthy();
    expect(screen.getByTestId('issue-executions-count').textContent).toContain('2');
    expect(screen.getByTestId('issue-execution-runtime-exec-running').textContent).toContain(
      'runner-east',
    );
    expect(screen.getByTestId('issue-execution-link-exec-running')).toHaveAttribute(
      'href',
      '/w/acme/executions/exec-running',
    );
    expect(screen.getByTestId('issue-execution-progress-exec-running')).toHaveAttribute(
      'role',
      'progressbar',
    );
    expect(screen.getByTestId('issue-execution-cancel-exec-running')).toBeTruthy();
    expect(calls[0]?.url).toContain('issue_id=issue-1');
  });

  it('cancels an active execution and converges the row to cancelling', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
        if (url.endsWith('/executions/exec-running:cancel')) {
          return fakeResponse({ body: { data: { ...RUNNING, status: 'cancelling' } } });
        }
        return fakeResponse({ body: { data: [RUNNING, COMPLETED], next_cursor: null } });
      }),
    );

    renderPanel();
    fireEvent.click(await screen.findByTestId('issue-execution-cancel-exec-running'));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url.endsWith('/executions/exec-running:cancel') && call.init?.method === 'POST',
        ),
      ).toBe(true),
    );
    expect(screen.getByTestId('issue-execution-status-exec-running')).toHaveAttribute(
      'data-status',
      'cancelling',
    );
    expect(screen.queryByTestId('issue-execution-cancel-exec-running')).toBeNull();
  });

  it('surfaces a transport failure while cancelling and leaves the run actionable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith('/executions/exec-running:cancel')) {
          throw new TypeError('connection reset');
        }
        return fakeResponse({ body: { data: [RUNNING], next_cursor: null } });
      }),
    );

    renderPanel();
    fireEvent.click(await screen.findByTestId('issue-execution-cancel-exec-running'));

    expect(
      await screen.findByText('Network error. Please check your connection and try again.'),
    ).toBeTruthy();
    expect(screen.getByTestId('issue-execution-cancel-exec-running')).toBeEnabled();
  });

  it('offers output approval and change request only for a completed reviewable issue', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fakeResponse({ body: { data: [COMPLETED], next_cursor: null } })),
    );
    const onApprove = vi.fn();
    const onRequestChanges = vi.fn();
    renderPanel({ onApprove, onRequestChanges });

    fireEvent.click(await screen.findByTestId('issue-execution-approve-exec-completed'));
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith('exec-completed'));
    expect(onRequestChanges).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.queryByTestId('issue-execution-approve-exec-completed')).toBeNull(),
    );
  });

  it('makes a successful change request final in the current UI', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fakeResponse({ body: { data: [COMPLETED], next_cursor: null } })),
    );
    const onRequestChanges = vi.fn();
    renderPanel({ onRequestChanges });

    fireEvent.click(await screen.findByTestId('issue-execution-reject-exec-completed'));

    await waitFor(() => expect(onRequestChanges).toHaveBeenCalledWith('exec-completed'));
    await waitFor(() =>
      expect(screen.queryByTestId('issue-execution-reject-exec-completed')).toBeNull(),
    );
  });

  it('keeps output review actions available when the host callback rejects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fakeResponse({ body: { data: [COMPLETED], next_cursor: null } })),
    );
    const onApprove = vi.fn(async () => {
      throw new TypeError('host callback failed');
    });
    renderPanel({ onApprove });

    fireEvent.click(await screen.findByTestId('issue-execution-approve-exec-completed'));

    expect(
      await screen.findByText('We could not load this content. Please try again.'),
    ).toBeTruthy();
    expect(screen.getByTestId('issue-execution-approve-exec-completed')).toBeEnabled();
  });

  it('never offers review actions for an older completed execution', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fakeResponse({ body: { data: [RUNNING, COMPLETED], next_cursor: null } })),
    );
    renderPanel();

    expect(await screen.findByTestId('issue-executions-panel')).toBeTruthy();
    expect(screen.queryByTestId('issue-execution-approve-exec-completed')).toBeNull();
    expect(screen.queryByTestId('issue-execution-reject-exec-completed')).toBeNull();
  });

  it('keeps a persisted review decision final after a fresh load', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeResponse({
          body: {
            data: [
              {
                ...COMPLETED,
                output_review: {
                  decision: 'rejected',
                  decided_by_member_id: 'member-1',
                  decided_at: '2026-08-05T07:02:00Z',
                },
              },
            ],
            next_cursor: null,
          },
        }),
      ),
    );
    renderPanel();

    expect(await screen.findByTestId('issue-executions-panel')).toBeTruthy();
    expect(screen.queryByTestId('issue-execution-approve-exec-completed')).toBeNull();
    expect(screen.queryByTestId('issue-execution-reject-exec-completed')).toBeNull();
  });

  it('walks the cursor chain so every issue execution remains reachable', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.includes('cursor=page-2')) {
          return fakeResponse({ body: { data: [COMPLETED], next_cursor: null } });
        }
        return fakeResponse({ body: { data: [RUNNING], next_cursor: 'page-2' } });
      }),
    );
    renderPanel({ reviewable: false });

    fireEvent.click(await screen.findByTestId('issue-executions-load-more'));

    expect(await screen.findByTestId('issue-execution-link-exec-completed')).toBeTruthy();
    expect(screen.getByTestId('issue-executions-count')).toHaveTextContent('2');
    expect(calls.some((url) => url.includes('cursor=page-2'))).toBe(true);
    expect(screen.queryByTestId('issue-executions-load-more')).toBeNull();
  });

  it('renders a durable empty state when the issue has never run', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fakeResponse({ body: { data: [], next_cursor: null } })),
    );
    renderPanel({ reviewable: false });
    expect(await screen.findByTestId('issue-executions-empty')).toBeTruthy();
  });

  it('recovers from a non-API initial load error through the visible retry action', async () => {
    let attempt = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        attempt += 1;
        if (attempt === 1) throw new TypeError('connection reset');
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }),
    );

    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));

    expect(await screen.findByTestId('issue-executions-empty')).toBeTruthy();
    expect(attempt).toBe(2);
  });

  it('renders terminal failure metadata when an execution has no attempt rows', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeResponse({ body: { data: [FAILED_WITHOUT_ATTEMPTS], next_cursor: null } }),
      ),
    );

    renderPanel({ reviewable: false });

    expect(await screen.findByTestId('issue-execution-runtime-exec-failed')).toHaveTextContent('—');
    expect(screen.getByText(/provider_unavailable/)).toBeTruthy();
    expect(screen.queryByTestId('issue-execution-progress-exec-failed')).toBeNull();
  });

  it('subscribes known executions, ignores unrelated frames, reloads correlated frames, and cleans up', async () => {
    let frameListener: ((frame: RealtimeEventFrame) => void) | null = null;
    const client = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn((listener: (frame: RealtimeEventFrame) => void) => {
        frameListener = listener;
        return vi.fn();
      }),
    };
    const fetchMock = vi.fn(async () =>
      fakeResponse({ body: { data: [RUNNING, COMPLETED], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const rendered = renderPanel({}, {
      state: 'connected',
      client: client as never,
    } as RealtimeContextValue);

    await screen.findByTestId('issue-execution-link-exec-running');
    await waitFor(() => {
      expect(client.subscribe).toHaveBeenCalledWith('workspace:ws-1:executions');
      expect(client.subscribe).toHaveBeenCalledWith('issue:issue-1');
      expect(client.subscribe).toHaveBeenCalledWith('execution:exec-running');
    });
    const initialCalls = fetchMock.mock.calls.length;

    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'workspace:ws-1:executions',
        seq: 1,
        event: 'issue.updated',
        payload: {},
      });
      frameListener?.({
        op: 'event',
        channel: 'workspace:ws-1:executions',
        seq: 2,
        event: 'execution.started',
        payload: { execution_id: 42, issue_id: 'other' },
      });
    });
    expect(fetchMock).toHaveBeenCalledTimes(initialCalls);

    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'workspace:ws-1:executions',
        seq: 3,
        event: 'execution.queued',
        payload: { execution_id: 'new-execution', issue_id: 'issue-1' },
      });
    });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(initialCalls));
    const afterIssueFrame = fetchMock.mock.calls.length;

    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'execution:exec-running',
        seq: 4,
        event: 'execution.completed',
        payload: { execution_id: 'exec-running', issue_id: 'other' },
      });
    });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(afterIssueFrame));

    rendered.unmount();
    expect(client.unsubscribe).toHaveBeenCalledWith('workspace:ws-1:executions');
    expect(client.unsubscribe).toHaveBeenCalledWith('issue:issue-1');
    expect(client.unsubscribe).toHaveBeenCalledWith('execution:exec-running');
  });

  it('discovers private issue executions without discarding already loaded cursor pages', async () => {
    let frameListener: ((frame: RealtimeEventFrame) => void) | null = null;
    const client = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn((listener: (frame: RealtimeEventFrame) => void) => {
        frameListener = listener;
        return vi.fn();
      }),
    };
    let firstPageCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('cursor=page-2')) {
          return fakeResponse({ body: { data: [COMPLETED], next_cursor: null } });
        }
        firstPageCalls += 1;
        return fakeResponse({
          body: {
            data: firstPageCalls === 1 ? [RUNNING] : [PRIVATE_QUEUED, RUNNING],
            next_cursor: 'page-2',
          },
        });
      }),
    );

    renderPanel({ reviewable: false }, {
      state: 'connected',
      client: client as never,
    } as RealtimeContextValue);

    fireEvent.click(await screen.findByTestId('issue-executions-load-more'));
    expect(await screen.findByTestId('issue-execution-link-exec-completed')).toBeTruthy();
    expect(screen.queryByTestId('issue-executions-load-more')).toBeNull();
    await waitFor(() => expect(client.subscribe).toHaveBeenCalledWith('issue:issue-1'));

    act(() => {
      frameListener?.({
        op: 'event',
        channel: 'issue:issue-1',
        seq: 20,
        event: 'execution.queued',
        payload: { execution_id: 'exec-private', issue_id: 'issue-1' },
      });
    });

    expect(await screen.findByTestId('issue-execution-link-exec-private')).toBeTruthy();
    expect(screen.getByTestId('issue-execution-link-exec-completed')).toBeTruthy();
    expect(screen.getByTestId('issue-executions-count')).toHaveTextContent('3');
    expect(screen.queryByTestId('issue-executions-load-more')).toBeNull();
  });
});
