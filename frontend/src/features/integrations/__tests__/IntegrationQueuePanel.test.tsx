import { readFileSync } from 'node:fs';
import path from 'node:path';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { contrastRatio, WCAG_AA_RATIO } from '../../../design';
import { renderWithProviders } from '../../../test-utils/render';
import { IntegrationQueuePanel } from '../IntegrationQueuePanel';
import type { QueueRefreshRequest } from '../IntegrationQueuePanel';

const CONVERSATION = 'dingtalk:dingCorp01:cid6EUvB2O8qVF2RYQtHTKEsg==';
const INTEGRATIONS_CSS = readFileSync(
  path.resolve(process.cwd(), 'src/features/integrations/integrations.css'),
  'utf8',
);
const TOKENS_CSS = readFileSync(path.resolve(process.cwd(), 'src/design/tokens.css'), 'utf8');
const TOKENS_DARK_CSS = readFileSync(
  path.resolve(process.cwd(), 'src/design/tokens-dark.css'),
  'utf8',
);

const BASE_ITEMS = [
  {
    id: 'q-1',
    conversation_key: CONVERSATION,
    seq: 1,
    state: 'processing',
    dispatch_mode: 'serial_conversation',
    position: null,
    sender: { identity_key: 'dingtalk:dingCorp01:staff-me', display_name: 'Me', linked: true },
    target_agent: { id: 'agent-1', name: 'Mesh Coder' },
    message_excerpt: 'Investigate production alarm',
    message_text: 'SUPER SECRET FULL BODY',
    ack_sent_at: '2026-08-01T10:00:01Z',
    ack_merged_into: null,
    execution_id: 'exec-1',
    enqueued_at: '2026-08-01T10:00:00Z',
    started_at: '2026-08-01T10:00:02Z',
    finished_at: null,
  },
  {
    id: 'q-2',
    conversation_key: CONVERSATION,
    seq: 2,
    state: 'pending',
    dispatch_mode: 'serial_conversation',
    position: 1,
    sender: { identity_key: 'dingtalk:dingCorp01:staff-me', display_name: 'Me', linked: true },
    target_agent: { id: 'agent-1', name: 'Mesh Coder' },
    message_excerpt: 'deploy\u0007prod',
    ack_sent_at: null,
    ack_merged_into: 'q-1',
    execution_id: null,
    enqueued_at: '2026-08-01T10:00:03Z',
    started_at: null,
    finished_at: null,
  },
  {
    id: 'q-3',
    conversation_key: CONVERSATION,
    seq: 3,
    state: 'pending',
    dispatch_mode: 'serial_conversation',
    position: 2,
    sender: {
      identity_key: 'dingtalk:dingCorp01:staff-other',
      display_name: 'dingtalk:dingCorp01:staff-other',
      linked: false,
    },
    target_agent: null,
    message_excerpt: 'Check payment logs',
    ack_sent_at: null,
    ack_merged_into: null,
    execution_id: null,
    enqueued_at: '2026-08-01T10:00:04Z',
    started_at: null,
    finished_at: null,
  },
];

interface Recorded {
  readonly url: string;
  readonly method: string;
}

function setup(): Recorded[] {
  const calls: Recorded[] = [];
  let cancelled = false;
  vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/external-identities')) {
      return fakeResponse({
        body: {
          data: [
            {
              id: 'identity-1',
              provider: 'dingtalk',
              provider_tenant_key: 'dingCorp01',
              external_user_key: 'staff-me',
              user_id: 'user-1',
              created_in_workspace_id: 'ws-1',
              verified_at: '2026-08-01T09:00:00Z',
              created_at: '2026-08-01T09:00:00Z',
            },
          ],
          next_cursor: null,
        },
      });
    }
    if (url.endsWith('/queue/summary')) {
      return fakeResponse({
        body: {
          data: [
            {
              conversation_key: CONVERSATION,
              pending_count: cancelled ? 1 : 2,
              in_flight: [{ id: 'q-1', state: 'processing', seq: 1 }],
            },
          ],
          next_cursor: null,
        },
      });
    }
    if (url.includes('/integration-queue-audit')) {
      return fakeResponse({
        body: {
          data: [
            {
              id: 'orphan-1',
              binding_display: 'Deleted private project / old group',
              conversation_key: 'dingtalk:dingCorp01:deleted-private',
              sender_identity_key: 'dingtalk:dingCorp01:staff-old',
              sender: {
                identity_key: 'dingtalk:dingCorp01:staff-old',
                display_name: 'Former member',
                linked: false,
              },
              state: 'cancelled',
              project_id_snapshot: 'private-project-1',
              message_excerpt: 'Retained audit excerpt',
              enqueued_at: '2026-07-01T10:00:00Z',
              started_at: null,
              finished_at: '2026-07-01T10:01:00Z',
            },
          ],
          next_cursor: null,
        },
      });
    }
    if (url.endsWith('/q-2:cancel') && method === 'POST') {
      cancelled = true;
      return fakeResponse({ body: { data: { id: 'q-2', state: 'cancelled' } } });
    }
    if (url.includes('/queue') && method === 'GET') {
      const items = BASE_ITEMS.map((item) => {
        if (!cancelled) return item;
        if (item.id === 'q-2') return { ...item, state: 'cancelled', position: null };
        if (item.id === 'q-3') return { ...item, position: 1 };
        return item;
      });
      return fakeResponse({ body: { data: items, next_cursor: null } });
    }
    return fakeResponse({ body: { data: [], next_cursor: null } });
  }) as typeof fetch);
  return calls;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.querySelector('[data-integration-style-test]')?.remove();
  document.documentElement.removeAttribute('data-theme');
});

function resolveComputedColor(element: Element, property: 'color' | 'background-color'): string {
  const computed = getComputedStyle(element).getPropertyValue(property).trim();
  const variable = /^var\((--[\w-]+)(?:,\s*([^)]+))?\)$/.exec(computed);
  if (variable === null) return computed;
  const token = getComputedStyle(document.documentElement).getPropertyValue(variable[1]).trim();
  return token !== '' ? token : (variable[2]?.trim() ?? '');
}

function renderPanel(
  refreshRequest: QueueRefreshRequest = { key: 0, conversationKeys: [] },
  options: { isAdmin?: boolean; realtimeConnected?: boolean } = {},
) {
  return renderWithProviders(
    <IntegrationQueuePanel
      workspaceId="ws-1"
      integrationId="int-dt"
      isAdmin={options.isAdmin ?? false}
      realtimeConnected={options.realtimeConnected ?? true}
      refreshRequest={refreshRequest}
    />,
  );
}

function RefreshHarness(): React.JSX.Element {
  const [refresh, setRefresh] = useState<QueueRefreshRequest>({
    key: 0,
    conversationKeys: [],
  });
  return (
    <>
      <button
        type="button"
        onClick={() => setRefresh({ key: 1, conversationKeys: [CONVERSATION] })}
      >
        refresh conversation
      </button>
      <button type="button" onClick={() => setRefresh({ key: 2, conversationKeys: null })}>
        refresh project
      </button>
      <IntegrationQueuePanel
        workspaceId="ws-1"
        integrationId="int-dt"
        isAdmin={false}
        realtimeConnected
        refreshRequest={refresh}
      />
    </>
  );
}

function BurstRefreshHarness(): React.JSX.Element {
  const [refresh, setRefresh] = useState<QueueRefreshRequest>({
    key: 0,
    conversationKeys: [],
  });
  return (
    <>
      <button
        type="button"
        onClick={() =>
          setRefresh((current) => ({
            key: current.key + 1,
            conversationKeys: [CONVERSATION],
          }))
        }
      >
        burst refresh
      </button>
      <IntegrationQueuePanel
        workspaceId="ws-1"
        integrationId="int-dt"
        isAdmin={false}
        realtimeConnected
        refreshRequest={refresh}
        onRefreshConsumed={(key) =>
          setRefresh((current) =>
            current.key === key ? { key: current.key, conversationKeys: [] } : current,
          )
        }
      />
    </>
  );
}

describe('IntegrationQueuePanel', () => {
  it('uses defined semantic tokens and keeps queue-card text WCAG AA in a 390x844 dark viewport', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });
    document.documentElement.dataset.theme = 'dark';
    const style = document.createElement('style');
    style.dataset.integrationStyleTest = 'true';
    style.textContent = `${TOKENS_CSS}\n${TOKENS_DARK_CSS}\n${INTEGRATIONS_CSS}`;
    document.head.append(style);

    setup();
    renderPanel();
    const card = await screen.findByTestId(`queue-conversation-${CONVERSATION}`);
    document.documentElement.dataset.theme = 'dark';
    const meta = card.querySelector('.mesh-integrations__queue-meta');
    expect(meta).not.toBeNull();
    expect(window.innerWidth).toBe(390);
    expect(window.innerHeight).toBe(844);
    expect(INTEGRATIONS_CSS).not.toMatch(/var\(--mesh-/);

    const background = resolveComputedColor(card, 'background-color');
    const text = resolveComputedColor(card, 'color');
    const muted = resolveComputedColor(meta as Element, 'color');
    expect(background).not.toBe('');
    expect(text).not.toBe('');
    expect(muted).not.toBe('');
    expect(contrastRatio(text, background)).toBeGreaterThanOrEqual(WCAG_AA_RATIO);
    expect(contrastRatio(muted, background)).toBeGreaterThanOrEqual(WCAG_AA_RATIO);
  });

  it('groups authorized queue items by conversation and exposes only sanitized excerpts', async () => {
    setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument());

    expect(screen.getByTestId(`queue-conversation-${CONVERSATION}`)).toHaveTextContent(
      'cid6EUvB2O8qVF2RYQtHTKEsg==',
    );
    expect(screen.getByTestId('queue-item-q-1')).toHaveTextContent('Mesh Coder');
    expect(screen.getByTestId('queue-duration-q-1')).toHaveTextContent(/Runtime:/i);
    expect(screen.getByTestId('queue-execution-q-1')).toHaveAttribute('href', '/executions/exec-1');
    expect(screen.getByTestId('queue-item-q-2')).toHaveTextContent('deployprod');
    expect(screen.getByTestId('queue-position-q-2')).toHaveTextContent('1');
    expect(screen.getByTestId('queue-item-q-3')).toHaveTextContent(/Not linked/i);
    expect(screen.getByTestId('queue-item-q-3')).toHaveTextContent(/External DingTalk user/i);
    expect(screen.getByTestId('queue-item-q-3')).not.toHaveTextContent('staff-other');
    expect(screen.queryByText('SUPER SECRET FULL BODY')).toBeNull();
  });

  it('allows the mapped sender to cancel pending work and refetches that conversation', async () => {
    const calls = setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('queue-cancel-q-2')).toBeInTheDocument());
    expect(screen.getByTestId('queue-cancel-q-1')).toBeDisabled();
    expect(screen.getByTestId('queue-cancel-q-1')).toHaveAttribute(
      'title',
      expect.stringMatching(/stop/i),
    );
    expect(screen.queryByTestId('queue-cancel-q-3')).toBeNull();

    await userEvent.click(screen.getByTestId('queue-cancel-q-2'));
    await waitFor(() => expect(screen.getByTestId('queue-position-q-3')).toHaveTextContent('1'));
    expect(calls.some((call) => call.url.endsWith('/q-2:cancel') && call.method === 'POST')).toBe(
      true,
    );
    expect(
      calls.some(
        (call) =>
          call.method === 'GET' &&
          call.url.includes('/queue?') &&
          call.url.includes(`conversation_key=${encodeURIComponent(CONVERSATION)}`),
      ),
    ).toBe(true);
  });

  it('does not grant cancellation from a matching user key on another provider', async () => {
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/external-identities')) {
        return fakeResponse({
          body: {
            data: [
              {
                id: 'identity-github',
                provider: 'github',
                provider_tenant_key: 'dingCorp01',
                external_user_key: 'staff-me',
                user_id: 'user-1',
                created_in_workspace_id: 'ws-1',
                verified_at: '2026-08-01T09:00:00Z',
                created_at: '2026-08-01T09:00:00Z',
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (url.endsWith('/queue/summary')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/queue?')) {
        return fakeResponse({ body: { data: [BASE_ITEMS[1]], next_cursor: null } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);

    renderPanel();
    await waitFor(() => expect(screen.getByTestId('queue-item-q-2')).toBeInTheDocument());
    expect(screen.queryByTestId('queue-cancel-q-2')).toBeNull();
  });

  it('refetches a disclosed conversation on workspace invalidation and all authorized data for project invalidation', async () => {
    const calls = setup();
    renderWithProviders(<RefreshHarness />);
    await waitFor(() => expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument());
    const before = calls.length;
    await userEvent.click(screen.getByRole('button', { name: 'refresh conversation' }));
    await waitFor(() =>
      expect(
        calls
          .slice(before)
          .some((call) =>
            call.url.includes(`conversation_key=${encodeURIComponent(CONVERSATION)}`),
          ),
      ).toBe(true),
    );

    const beforeProject = calls.length;
    await userEvent.click(screen.getByRole('button', { name: 'refresh project' }));
    await waitFor(() =>
      expect(
        calls
          .slice(beforeProject)
          .some((call) => call.url.includes('/queue?') && !call.url.includes('conversation_key=')),
      ).toBe(true),
    );
  });

  it('coalesces duplicate invalidations that arrive while a conversation refresh is slow', async () => {
    let targetedLoads = 0;
    let resolveSlowTarget: ((response: Response) => void) | undefined;
    const slowTarget = new Promise<Response>((resolve) => {
      resolveSlowTarget = resolve;
    });
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/queue/summary') || url.includes('/external-identities')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('conversation_key=')) {
        targetedLoads += 1;
        if (targetedLoads === 1) return slowTarget;
      }
      if (url.includes('/queue?')) {
        return fakeResponse({ body: { data: [BASE_ITEMS[0]], next_cursor: null } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);

    renderWithProviders(<BurstRefreshHarness />);
    await waitFor(() => expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'burst refresh' }));
    await waitFor(() => expect(targetedLoads).toBe(1));
    for (let index = 0; index < 5; index += 1) {
      await userEvent.click(screen.getByRole('button', { name: 'burst refresh' }));
    }
    expect(targetedLoads).toBe(1);

    await act(async () => {
      resolveSlowTarget?.(fakeResponse({ body: { data: [BASE_ITEMS[0]], next_cursor: null } }));
    });
    await waitFor(() => expect(targetedLoads).toBe(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(targetedLoads).toBe(2);
  });

  it('keeps orphan audit behind an admin-only explicit entry point', async () => {
    const calls = setup();
    renderPanel({ key: 0, conversationKeys: [] }, { isAdmin: true });
    await waitFor(() => expect(screen.getByTestId('queue-audit-open')).toBeInTheDocument());
    expect(calls.some((call) => call.url.includes('/integration-queue-audit'))).toBe(false);
    await userEvent.click(screen.getByTestId('queue-audit-open'));
    await waitFor(() => expect(screen.getByTestId('queue-audit-orphan-1')).toBeInTheDocument());
    expect(screen.getByTestId('queue-audit-orphan-1')).toHaveTextContent('Retained audit excerpt');
  });

  it('does not expose the orphan audit entry point to ordinary members', async () => {
    setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument());
    expect(screen.queryByTestId('queue-audit-open')).toBeNull();
    expect(screen.queryByText(/Deleted private project/)).toBeNull();
  });

  it('installs and removes the polling fallback while realtime is disconnected', async () => {
    setup();
    const setIntervalSpy = vi.spyOn(window, 'setInterval');
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
    const view = renderPanel({ key: 0, conversationKeys: [] }, { realtimeConnected: false });

    await waitFor(() => expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument());
    await waitFor(() => expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 4000));
    const pollingCallIndex = setIntervalSpy.mock.calls.findIndex((call) => call[1] === 4000);
    const intervalId = setIntervalSpy.mock.results[pollingCallIndex]?.value;
    view.unmount();
    expect(clearIntervalSpy).toHaveBeenCalledWith(intervalId);
  });

  it('does not let fallback polling overtake a slow initial full load', async () => {
    let resolveQueue: ((response: Response) => void) | undefined;
    const pendingQueue = new Promise<Response>((resolve) => {
      resolveQueue = resolve;
    });
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/queue?')) return pendingQueue;
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);
    const setIntervalSpy = vi.spyOn(window, 'setInterval');

    renderPanel({ key: 0, conversationKeys: [] }, { realtimeConnected: false });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(setIntervalSpy.mock.calls.some((call) => call[1] === 4000)).toBe(false);

    await act(async () => {
      resolveQueue?.(fakeResponse({ body: { data: [BASE_ITEMS[0]], next_cursor: null } }));
    });
    await waitFor(() => expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 4000));
    expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument();
  });

  it('recovers from an initial queue error through the error-state retry', async () => {
    let queueAttempts = 0;
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/queue') && !url.endsWith('/queue/summary')) {
        queueAttempts += 1;
        if (queueAttempts === 1) {
          return fakeResponse({
            status: 500,
            body: { error: { code: 'internal_error', message: 'boom' } },
          });
        }
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);

    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByTestId('integration-queue-panel')).toBeInTheDocument());
    expect(queueAttempts).toBe(2);
  });

  it('keeps the initial full load as a barrier before applying targeted invalidations', async () => {
    const otherConversation = 'dingtalk:dingCorp01:cid-other';
    let resolveFull: ((response: Response) => void) | undefined;
    const fullResponse = new Promise<Response>((resolve) => {
      resolveFull = resolve;
    });
    const calls: string[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith('/queue/summary') || url.includes('/external-identities')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/queue?') && !url.includes('conversation_key=')) return fullResponse;
      if (url.includes('/queue?')) {
        return fakeResponse({ body: { data: [BASE_ITEMS[0]], next_cursor: null } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);

    renderPanel({ key: 1, conversationKeys: [CONVERSATION] });
    await waitFor(() =>
      expect(
        calls.some((url) => url.includes('/queue?') && !url.includes('conversation_key=')),
      ).toBe(true),
    );
    expect(calls.some((url) => url.includes('conversation_key='))).toBe(false);

    await act(async () => {
      resolveFull?.(
        fakeResponse({
          body: {
            data: [
              BASE_ITEMS[0],
              { ...BASE_ITEMS[2], id: 'q-other', conversation_key: otherConversation },
            ],
            next_cursor: null,
          },
        }),
      );
    });

    await waitFor(() => expect(screen.getByTestId('queue-item-q-other')).toBeInTheDocument());
    await waitFor(() => expect(calls.some((url) => url.includes('conversation_key='))).toBe(true));
    expect(screen.getByTestId('queue-item-q-other')).toBeInTheDocument();
  });

  it('paginates queue and admin audit results without dropping prior rows', async () => {
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/queue/summary') || url.includes('/external-identities')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/integration-queue-audit')) {
        const secondPage = url.includes('cursor=audit-next');
        return fakeResponse({
          body: {
            data: [
              {
                id: secondPage ? 'audit-2' : 'audit-1',
                binding_display: secondPage ? 'Second binding' : 'First binding',
                conversation_key: CONVERSATION,
                sender_identity_key: 'dingtalk:dingCorp01:staff-old',
                sender: {
                  identity_key: 'dingtalk:dingCorp01:staff-old',
                  display_name: 'Former member',
                  linked: false,
                },
                state: 'cancelled',
                project_id_snapshot: null,
                message_excerpt: secondPage ? 'Second audit row' : 'First audit row',
                enqueued_at: '2026-08-01T10:00:00Z',
                started_at: null,
                finished_at: '2026-08-01T10:01:00Z',
              },
            ],
            next_cursor: secondPage ? null : 'audit-next',
          },
        });
      }
      if (url.includes('/queue?')) {
        const secondPage = url.includes('cursor=queue-next');
        return fakeResponse({
          body: {
            data: [secondPage ? BASE_ITEMS[1] : BASE_ITEMS[0]],
            next_cursor: secondPage ? null : 'queue-next',
          },
        });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);

    renderPanel({ key: 0, conversationKeys: [] }, { isAdmin: true });
    await waitFor(() => expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('queue-load-more'));
    await waitFor(() => expect(screen.getByTestId('queue-item-q-2')).toBeInTheDocument());
    expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument();
    expect(screen.queryByTestId('queue-load-more')).toBeNull();

    await userEvent.click(screen.getByTestId('queue-audit-open'));
    await waitFor(() => expect(screen.getByTestId('queue-audit-audit-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('queue-audit-load-more'));
    await waitFor(() => expect(screen.getByTestId('queue-audit-audit-2')).toBeInTheDocument());
    expect(screen.getByTestId('queue-audit-audit-1')).toBeInTheDocument();
  });

  it('shows retryable inline errors when a refresh or audit request fails', async () => {
    let fullLoads = 0;
    let auditLoads = 0;
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/queue/summary') || url.includes('/external-identities')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/integration-queue-audit')) {
        auditLoads += 1;
        if (auditLoads === 1) {
          return fakeResponse({
            status: 500,
            body: { error: { code: 'internal_error', message: 'audit failed' } },
          });
        }
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/queue?')) {
        fullLoads += 1;
        if (fullLoads === 2) {
          return fakeResponse({
            status: 500,
            body: { error: { code: 'internal_error', message: 'refresh failed' } },
          });
        }
        return fakeResponse({ body: { data: [BASE_ITEMS[0]], next_cursor: null } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);

    renderPanel({ key: 0, conversationKeys: [] }, { isAdmin: true });
    await waitFor(() => expect(screen.getByTestId('queue-item-q-1')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/internal error/i));
    await userEvent.click(
      within(screen.getByRole('alert')).getByRole('button', { name: /retry/i }),
    );
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());

    await userEvent.click(screen.getByTestId('queue-audit-open'));
    await waitFor(() =>
      expect(
        within(screen.getByTestId('queue-audit-panel')).getByText(/internal error/i),
      ).toBeInTheDocument(),
    );
    await userEvent.click(
      within(screen.getByTestId('queue-audit-panel')).getByRole('button', { name: /retry/i }),
    );
    await waitFor(() =>
      expect(screen.getByTestId('queue-audit-panel')).toHaveTextContent(
        /No retained orphan items/i,
      ),
    );
  });

  it('covers summary fallbacks, failed cancellation, empty audit, and toolbar callbacks', async () => {
    const item = {
      ...BASE_ITEMS[1],
      id: 'q-fallback',
      conversation_key: 'dingtalk:dingCorp01:summary-missing',
      enqueued_at: 'not-a-date',
    };
    let queueLoads = 0;
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.endsWith('/q-fallback:cancel') && method === 'POST') {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'cancel failed' } },
        });
      }
      if (url.includes('/integration-queue-audit')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.endsWith('/queue/summary') || url.includes('/external-identities')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/queue') && method === 'GET') {
        queueLoads += 1;
        return fakeResponse({ body: { data: [item], next_cursor: null } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch);

    renderPanel({ key: 0, conversationKeys: [] }, { isAdmin: true });
    await waitFor(() => expect(screen.getByTestId('queue-item-q-fallback')).toBeInTheDocument());
    expect(screen.getByTestId('queue-item-q-fallback')).toHaveTextContent('not-a-date');

    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(queueLoads).toBeGreaterThan(1));
    await userEvent.click(screen.getByTestId('queue-cancel-q-fallback'));
    await waitFor(() => expect(screen.getByText(/internal error/i)).toBeInTheDocument());

    await userEvent.click(screen.getByTestId('queue-audit-open'));
    await waitFor(() => expect(screen.getByTestId('queue-audit-panel')).toBeInTheDocument());
    expect(screen.getByTestId('queue-audit-panel')).toHaveTextContent(/No retained orphan items/i);
    await userEvent.click(
      within(screen.getByTestId('queue-audit-panel')).getByRole('button', { name: /close/i }),
    );
    expect(screen.queryByTestId('queue-audit-panel')).toBeNull();
  });
});
