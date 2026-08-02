import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { DingTalkOverviewPanel } from '../DingTalkOverviewPanel';
import type { Integration } from '../types';

const DINGTALK: Integration = {
  id: 'int-dt',
  workspace_id: 'ws-1',
  kind: 'im_dingtalk',
  name: 'DingTalk R&D',
  status: 'active',
  config: {
    app_key: 'ding-app',
    corp_id: 'dingCorp01',
    receive_mode: 'stream',
    inbound_queue: 'serial_conversation',
    verbosity: 'final_only',
    ack_template: '✅ 已接收，处理中',
  },
  has_secret: true,
  health_state: 'healthy',
  last_error: null,
  last_success_at: '2026-08-01T10:00:00Z',
  events_7d: 8,
  created_by: 'member-1',
  created_at: '2026-08-01T09:00:00Z',
  updated_at: '2026-08-01T10:00:00Z',
};

interface Recorded {
  readonly url: string;
  readonly method: string;
  readonly body?: string;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function setupStream(status = 200): Recorded[] {
  const calls: Recorded[] = [];
  vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined });
    if (url.endsWith('/stream-status')) {
      if (status === 503) {
        return fakeResponse({
          status: 503,
          body: {
            error: {
              code: 'stream_channel_unavailable',
              message: 'down',
              details: {
                state: 'down',
                last_frame_at: '2026-08-01T09:55:00Z',
                last_attempt_at: '2026-08-01T10:00:00Z',
                backoff_seconds: 32,
              },
            },
          },
        });
      }
      return fakeResponse({
        body: {
          data: {
            state: 'connected',
            last_frame_at: '2026-08-01T09:59:00Z',
            last_attempt_at: '2026-08-01T09:58:00Z',
            backoff_seconds: null,
          },
        },
      });
    }
    if (url.endsWith('/test-send')) {
      return fakeResponse({ body: { data: { status: 'sent', conversation_ref: 'cid-test' } } });
    }
    if (url.endsWith('/integrations/int-dt:reconnect') && method === 'POST') {
      return fakeResponse({ status: 202, body: { data: { accepted: true } } });
    }
    return fakeResponse({ body: { data: {} } });
  }) as typeof fetch);
  return calls;
}

describe('DingTalkOverviewPanel', () => {
  it('keeps outbound test-send and inbound Stream diagnostics as separate actions', async () => {
    const calls = setupStream();
    renderWithProviders(
      <DingTalkOverviewPanel workspaceId="ws-1" integration={DINGTALK} isAdmin onEdit={vi.fn()} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-stream-state')).toHaveTextContent(/Connected/i),
    );
    expect(screen.getByTestId('dingtalk-receive-mode')).toHaveTextContent(/Stream/i);
    expect(screen.getByTestId('dingtalk-last-frame')).toHaveTextContent('2026-08-01T09:59:00Z');
    expect(screen.getByTestId('dingtalk-test-send')).toBeInTheDocument();
    expect(screen.getByTestId('dingtalk-diagnose')).toBeInTheDocument();

    await userEvent.click(screen.getByTestId('dingtalk-test-send'));
    await userEvent.type(screen.getByTestId('dingtalk-test-conversation-ref'), 'cid-test');
    await userEvent.click(screen.getByTestId('dingtalk-test-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-test-result')).toHaveTextContent(/sent/i),
    );

    const outbound = calls.find((call) => call.url.endsWith('/test-send'));
    expect(JSON.parse(outbound?.body ?? '{}')).toEqual({
      conversation_ref: 'cid-test',
      conversation_type: 'group',
    });
    expect(calls.some((call) => call.url.endsWith('/integrations/int-dt:test'))).toBe(false);

    const initialDiagnostics = calls.filter((call) => call.url.endsWith('/stream-status')).length;
    await userEvent.click(screen.getByTestId('dingtalk-diagnose'));
    await waitFor(() =>
      expect(calls.filter((call) => call.url.endsWith('/stream-status')).length).toBeGreaterThan(
        initialDiagnostics,
      ),
    );
  });

  it('renders a persisted down state and reconnect backoff from a 503 diagnostic envelope', async () => {
    const calls = setupStream(503);
    const onEdit = vi.fn();
    renderWithProviders(
      <DingTalkOverviewPanel workspaceId="ws-1" integration={DINGTALK} isAdmin onEdit={onEdit} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-stream-state')).toHaveTextContent(/Down/i),
    );
    expect(screen.getByTestId('dingtalk-stream-alert')).toHaveTextContent('32');
    const reconnect = screen.getByTestId('dingtalk-reconnect');
    const edit = screen.getByTestId('dingtalk-edit-config');
    expect(reconnect).toHaveTextContent(/Reconnect/i);
    expect(edit).toHaveTextContent(/Edit configuration/i);
    expect(reconnect).not.toBe(edit);

    await userEvent.click(reconnect);
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url.endsWith('/api/v1/workspaces/ws-1/integrations/int-dt:reconnect') &&
            call.method === 'POST',
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.getByText(/Reconnect requested/i)).toBeInTheDocument());

    await userEvent.click(edit);
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it('keeps reconnect failures actionable without opening edit configuration', async () => {
    const onEdit = vi.fn();
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/stream-status')) {
        return fakeResponse({
          status: 503,
          body: {
            error: {
              code: 'stream_channel_unavailable',
              message: 'down',
              details: { state: 'down', backoff_seconds: 8 },
            },
          },
        });
      }
      if (url.endsWith('/integrations/int-dt:reconnect') && init?.method === 'POST') {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'reconnect failed' } },
        });
      }
      return fakeResponse({ body: { data: {} } });
    }) as typeof fetch);

    renderWithProviders(
      <DingTalkOverviewPanel workspaceId="ws-1" integration={DINGTALK} isAdmin onEdit={onEdit} />,
    );
    await waitFor(() => expect(screen.getByTestId('dingtalk-reconnect')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('dingtalk-reconnect'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
    expect(onEdit).not.toHaveBeenCalled();
    expect(screen.getByTestId('dingtalk-edit-config')).toBeEnabled();
  });

  it('shows the HTTP callback without requesting Stream diagnostics and copies it', async () => {
    const calls = setupStream();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    renderWithProviders(
      <DingTalkOverviewPanel
        workspaceId="ws-1"
        integration={{ ...DINGTALK, config: { ...DINGTALK.config, receive_mode: 'http' } }}
        isAdmin
        onEdit={vi.fn()}
      />,
    );

    expect(screen.getByTestId('dingtalk-http-callback')).toHaveTextContent(
      '/api/v1/integrations/dingtalk/events',
    );
    expect(screen.queryByTestId('dingtalk-diagnose')).toBeNull();
    expect(calls.some((call) => call.url.endsWith('/stream-status'))).toBe(false);
    await userEvent.click(screen.getByTestId('dingtalk-copy-callback'));
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/integrations/dingtalk/events'),
    );
  });

  it('includes the direct-message recipient in an outbound test', async () => {
    const calls = setupStream();
    renderWithProviders(
      <DingTalkOverviewPanel workspaceId="ws-1" integration={DINGTALK} isAdmin onEdit={vi.fn()} />,
    );
    await userEvent.click(screen.getByTestId('dingtalk-test-send'));
    await userEvent.selectOptions(screen.getByTestId('dingtalk-test-conversation-type'), 'direct');
    await userEvent.type(screen.getByTestId('dingtalk-test-conversation-ref'), 'staff-1');
    await userEvent.type(screen.getByTestId('dingtalk-test-user-key'), 'staff-1');
    await userEvent.click(screen.getByTestId('dingtalk-test-submit'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/test-send'))).toBe(true));
    const outbound = calls.find((call) => call.url.endsWith('/test-send'));
    expect(JSON.parse(outbound?.body ?? '{}')).toMatchObject({
      conversation_type: 'direct',
      user_key: 'staff-1',
    });
  });

  it('renders reconnecting diagnostics with safe defaults for optional values', async () => {
    vi.stubGlobal('fetch', (async () =>
      fakeResponse({
        body: {
          data: {
            state: 'reconnecting',
            last_frame_at: null,
            last_attempt_at: null,
            backoff_seconds: null,
          },
        },
      })) as typeof fetch);

    renderWithProviders(
      <DingTalkOverviewPanel
        workspaceId="ws-1"
        integration={{
          ...DINGTALK,
          config: {
            app_key: 'ding-app',
            corp_id: 'dingCorp01',
            receive_mode: 'stream',
          },
        }}
        isAdmin={false}
        onEdit={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-stream-state')).toHaveTextContent(/Reconnecting/i),
    );
    expect(screen.getByTestId('dingtalk-last-frame')).toHaveTextContent(/Never/i);
    expect(screen.getByTestId('dingtalk-stream-alert')).toHaveTextContent('0');
    expect(screen.queryByTestId('dingtalk-test-send')).toBeNull();
  });

  it('handles sparse unavailable diagnostics and resets the test-send dialog', async () => {
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/stream-status')) {
        return fakeResponse({
          status: 503,
          body: {
            error: {
              code: 'stream_channel_unavailable',
              message: 'down',
              details: {},
            },
          },
        });
      }
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'send failed' } },
      });
    }) as typeof fetch);

    renderWithProviders(
      <DingTalkOverviewPanel workspaceId="ws-1" integration={DINGTALK} isAdmin onEdit={vi.fn()} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-stream-state')).toHaveTextContent(/Down/i),
    );
    expect(screen.getByTestId('dingtalk-last-frame')).toHaveTextContent(/Never/i);
    await userEvent.click(screen.getByTestId('dingtalk-test-send'));
    await userEvent.selectOptions(screen.getByTestId('dingtalk-test-conversation-type'), 'direct');
    await userEvent.selectOptions(screen.getByTestId('dingtalk-test-conversation-type'), 'group');
    await userEvent.type(screen.getByTestId('dingtalk-test-conversation-ref'), 'cid-failed');
    await userEvent.click(screen.getByTestId('dingtalk-test-submit'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.queryByTestId('dingtalk-test-conversation-ref')).toBeNull();
  });

  it('reports clipboard failures for the HTTP callback', async () => {
    setupStream();
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    renderWithProviders(
      <DingTalkOverviewPanel
        workspaceId="ws-1"
        integration={{ ...DINGTALK, config: { ...DINGTALK.config, receive_mode: 'http' } }}
        isAdmin={false}
        onEdit={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByTestId('dingtalk-copy-callback'));
    await waitFor(() => expect(screen.getByText(/unexpected error/i)).toBeInTheDocument());
  });

  it('replaces a failed diagnostic skeleton with a retryable error state', async () => {
    let attempt = 0;
    vi.stubGlobal('fetch', (async () => {
      attempt += 1;
      if (attempt === 1) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      }
      return fakeResponse({
        body: {
          data: {
            state: 'connected',
            last_frame_at: null,
            last_attempt_at: null,
            backoff_seconds: null,
          },
        },
      });
    }) as typeof fetch);

    renderWithProviders(
      <DingTalkOverviewPanel workspaceId="ws-1" integration={DINGTALK} isAdmin onEdit={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-diagnostic-error')).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Reading Stream connection status/i)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() =>
      expect(screen.getByTestId('dingtalk-stream-state')).toHaveTextContent(/Connected/i),
    );
  });
});
