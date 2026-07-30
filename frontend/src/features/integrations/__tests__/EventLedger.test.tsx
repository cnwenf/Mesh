/**
 * EventLedger 组件测试(integrations.md §4.2):签名/处理徽章、载荷预览(不可信数据)、
 * rejected/deduped 高亮与原因、过滤、空/错误态 + 重试。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { EventLedger } from '../EventLedger';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const RECEIVED_AT = '2026-07-29T10:00:00Z';

const EVT_DISPATCHED = {
  id: 'e-1',
  integration_id: 'int-1',
  external_event_id: 'x1',
  event_type: 'message.channels',
  payload: { text: 'hello' },
  signature_status: 'valid',
  process_status: 'dispatched',
  received_at: RECEIVED_AT,
};
const EVT_REJECTED = {
  id: 'e-2',
  integration_id: 'int-1',
  external_event_id: 'rejected:abc',
  event_type: 'message.channels',
  payload: { text: 'bad' },
  signature_status: 'invalid',
  process_status: 'rejected',
  received_at: RECEIVED_AT,
};
const EVT_DEDUPED = {
  id: 'e-3',
  integration_id: 'int-1',
  external_event_id: 'x1',
  event_type: 'message.channels',
  payload: {},
  signature_status: 'valid',
  process_status: 'deduped',
  received_at: RECEIVED_AT,
};

interface Recorded {
  url: string;
  method: string;
}

function setup(events: unknown[] = [EVT_DISPATCHED, EVT_REJECTED, EVT_DEDUPED]): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push({ url, method: 'GET' });
    if (/\/integrations\/int-1\/events/.test(url))
      return fakeResponse({ body: { data: events, next_cursor: null } });
    return fakeResponse({ body: { data: [], next_cursor: null } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderLedger() {
  return renderWithProviders(<EventLedger workspaceId="ws-1" integrationId="int-1" />);
}

describe('EventLedger', () => {
  it('renders events with signature and process badges', async () => {
    setup();
    renderLedger();
    await waitFor(() => expect(screen.getByTestId('event-row-e-1')).toBeInTheDocument());
    expect(screen.getByTestId('event-row-e-2')).toBeInTheDocument();
    expect(screen.getByTestId('event-row-e-3')).toBeInTheDocument();
  });

  it('shows reasons for rejected and deduped rows', async () => {
    setup();
    renderLedger();
    await waitFor(() => expect(screen.getByTestId('event-reason-e-2')).toBeInTheDocument());
    expect(screen.getByTestId('event-reason-e-3')).toBeInTheDocument();
    expect(screen.queryByTestId('event-reason-e-1')).toBeNull();
  });

  it('expands the payload with an untrusted-data banner', async () => {
    setup();
    renderLedger();
    await waitFor(() => expect(screen.getByTestId('event-toggle-e-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('event-toggle-e-1'));
    await waitFor(() => expect(screen.getByTestId('event-payload-row-e-1')).toBeInTheDocument());
    expect(screen.getByText(/Untrusted data/)).toBeInTheDocument();
    // collapse again
    await userEvent.click(screen.getByTestId('event-toggle-e-1'));
    await waitFor(() => expect(screen.queryByTestId('event-payload-row-e-1')).toBeNull());
  });

  it('filters by signature status', async () => {
    const calls = setup();
    renderLedger();
    await waitFor(() => expect(screen.getByTestId('event-filter-signature')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('event-filter-signature'), 'invalid');
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('signature_status=invalid'))).toBe(true),
    );
  });

  it('filters by process status', async () => {
    const calls = setup();
    renderLedger();
    await waitFor(() => expect(screen.getByTestId('event-filter-process')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('event-filter-process'), 'rejected');
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('process_status=rejected'))).toBe(true),
    );
  });

  it('shows the empty state without events', async () => {
    setup([]);
    renderLedger();
    await waitFor(() => expect(screen.getByText(/No inbound events/)).toBeInTheDocument());
  });

  it('shows the error state and retries', async () => {
    const calls: Recorded[] = [];
    let fail = true;
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push({ url, method: 'GET' });
      if (fail) {
        fail = false;
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
      }
      return fakeResponse({ body: { data: [EVT_DISPATCHED], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderLedger();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByTestId('event-row-e-1')).toBeInTheDocument());
  });
});
