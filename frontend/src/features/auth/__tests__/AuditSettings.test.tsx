import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api/client';
import { AuditSettings } from '../AuditSettings';
import { renderWithProviders } from '../../../test-utils/render';

const ENTRY = {
  id: 'aud-1',
  actor_member_id: 'mem-1',
  actor_kind: 'member',
  action: 'token.created',
  resource_type: 'api_token',
  resource_id: 'tok-1',
  ip_address: '127.0.0.1',
  metadata: {},
  created_at: '2026-07-25T10:00:00Z',
};

function routingClient(routes: Record<string, { status?: number; body: unknown }>): MeshApiClient {
  const fetchImpl = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    const match = Object.keys(routes).find((key) => url.includes(key));
    const response = match !== undefined ? routes[match] : { status: 200, body: { data: [] } };
    return new Response(JSON.stringify(response.body), {
      status: response.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://t', getToken: () => 'tok', fetchImpl });
}

describe('AuditSettings(auth.md §4.4 / §3.3 / §5.3)', () => {
  it('列出审计条目;system 行为者本地化', async () => {
    const client = routingClient({
      '/api/v1/workspaces/ws-1/audit-logs': {
        body: {
          data: [ENTRY, { ...ENTRY, id: 'aud-2', actor_kind: 'system', actor_member_id: null }],
          next_cursor: null,
        },
      },
    });
    renderWithProviders(<AuditSettings client={client} workspaceId="ws-1" />);

    await waitFor(() => expect(screen.getByTestId('audit-aud-1')).toBeTruthy());
    expect(screen.getByText('System')).toBeTruthy();
    expect(screen.queryByTestId('audit-load-more')).toBeNull();
  });

  it('应用 action/before/after 过滤并发起带参请求', async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: [ENTRY], next_cursor: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ) as unknown as typeof fetch;
    const client = new MeshApiClient({ baseUrl: 'http://t', getToken: () => 'tok', fetchImpl });
    renderWithProviders(<AuditSettings client={client} workspaceId="ws-1" />);

    await user.type(screen.getByTestId('audit-action'), 'token.created');
    await user.type(screen.getByTestId('audit-after'), '2026-07-01T00:00:00Z');
    await user.type(screen.getByTestId('audit-before'), '2026-08-01T00:00:00Z');
    await user.click(screen.getByTestId('audit-apply'));

    await waitFor(() => {
      const lastCall = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls.at(-1) as [string];
      expect(String(lastCall[0])).toContain('action=token.created');
      expect(String(lastCall[0])).toContain('after=2026-07-01T00%3A00%3A00Z');
      expect(String(lastCall[0])).toContain('before=2026-08-01T00%3A00%3A00Z');
    });
  });

  it('有 next_cursor 时呈现加载更多', async () => {
    const client = routingClient({
      '/api/v1/workspaces/ws-1/audit-logs': {
        body: { data: [ENTRY], next_cursor: 'cur-1' },
      },
    });
    renderWithProviders(<AuditSettings client={client} workspaceId="ws-1" />);
    await waitFor(() => expect(screen.getByTestId('audit-load-more')).toBeTruthy());
  });
});
