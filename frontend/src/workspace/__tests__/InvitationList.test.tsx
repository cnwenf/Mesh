import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { RealtimeContext } from '../../shell/AppShell';
import type { RealtimeContextValue } from '../../shell/AppShell';
import { InvitationList, effectiveStatus } from '../InvitationList';
import { workspaceChannel } from '../WorkspaceProvider';
import type { Invitation } from '../../api/invitations';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubClient(fetchImpl: ReturnType<typeof vi.fn>) {
  return {
    list: async (path: string, opts: { query?: Record<string, string> } = {}) => {
      const qs = opts.query?.cursor !== undefined ? `?cursor=${opts.query.cursor}` : '';
      const response = await fetchImpl(`http://localhost${path}${qs}`, { method: 'GET' });
      if (!response.ok) {
        const { MeshApiError } = await import('../../api/errors');
        throw new MeshApiError({ status: response.status, code: 'internal_error', message: 'x' });
      }
      return response.json();
    },
    request: async (method: string, path: string) => {
      const response = await fetchImpl(`http://localhost${path}`, { method });
      const body = (await response.json()) as {
        data?: unknown;
        error?: { code: string; message: string; details?: Record<string, unknown> };
      };
      if (!response.ok) {
        const { MeshApiError } = await import('../../api/errors');
        throw new MeshApiError({
          status: response.status,
          code: body.error?.code ?? 'internal_error',
          message: body.error?.message ?? '',
          details: body.error?.details,
        });
      }
      return body.data;
    },
  };
}

function stubFetch(...responses: Array<{ status: number; body: unknown }>): ReturnType<typeof vi.fn> {
  const fetchImpl = vi.fn();
  for (const response of responses) {
    fetchImpl.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(response.status, response.body)),
    );
  }
  return fetchImpl;
}

function makeInvitation(overrides: Partial<Invitation> = {}): Invitation {
  return {
    id: 'inv-1',
    email: 'jane@corp.com',
    role: 'member',
    status: 'active',
    max_uses: 10,
    used_count: 0,
    expires_at: '2026-08-01T00:00:00Z',
    token_prefix: 'invtk_Ab3Xy9zzzz',
    invited_by: 'mem-1',
    created_at: '2026-07-25T00:00:00Z',
    ...overrides,
  };
}

function renderList(
  fetchImpl: ReturnType<typeof vi.fn>,
  realtime?: RealtimeContextValue | null,
): ReturnType<typeof render> {
  const list = (
    <InvitationList workspaceId="ws-1" client={stubClient(fetchImpl) as never} />
  );
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            {realtime !== undefined ? (
              <RealtimeContext.Provider value={realtime}>{list}</RealtimeContext.Provider>
            ) : (
              list
            )}
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function createFakeRealtime(): { value: RealtimeContextValue; frames: Array<(f: unknown) => void> } {
  const frames: Array<(f: unknown) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((cb: (f: unknown) => void) => {
      frames.push(cb);
      return () => undefined;
    }),
    getCursor: vi.fn(() => undefined),
    ingestReconciledEvent: vi.fn(),
  };
  return { value: { state: 'connected', client: client as never }, frames };
}

describe('effectiveStatus(§4.4 惰性 exhausted 呈现)', () => {
  it('active 且用量达上限 → exhausted', () => {
    expect(effectiveStatus(makeInvitation({ used_count: 10, max_uses: 10 }))).toBe('exhausted');
  });
  it('其余状态原样', () => {
    expect(effectiveStatus(makeInvitation({ status: 'revoked' }))).toBe('revoked');
    expect(effectiveStatus(makeInvitation())).toBe('active');
  });
});

describe('InvitationList(邀请列表,§4.2/§4.5)', () => {
  it('渲染行:对象/角色/状态徽标/用量/过期时间;active 有撤销按钮', async () => {
    const fetchImpl = stubFetch({
      status: 200,
      body: { data: [makeInvitation()], next_cursor: null },
    });
    renderList(fetchImpl);

    await waitFor(() => expect(screen.getByTestId('invitation-row')).toBeTruthy());
    expect(screen.getByText('jane@corp.com')).toBeTruthy();
    expect(screen.getByText('active')).toBeTruthy();
    expect(screen.getByTestId('invitation-uses').textContent).toBe('0/10');
    expect(screen.getByTestId('invitation-revoke')).toBeTruthy();
  });

  it('链接邀请以 token 前缀呈现', async () => {
    const fetchImpl = stubFetch({
      status: 200,
      body: { data: [makeInvitation({ email: null })], next_cursor: null },
    });
    renderList(fetchImpl);
    await waitFor(() => expect(screen.getByText(/invtk_Ab3Xy9/)).toBeTruthy());
  });

  it('终态行无撤销按钮', async () => {
    const fetchImpl = stubFetch({
      status: 200,
      body: { data: [makeInvitation({ status: 'expired' })], next_cursor: null },
    });
    renderList(fetchImpl);
    await waitFor(() => expect(screen.getByText('expired')).toBeTruthy());
    expect(screen.queryByTestId('invitation-revoke')).toBeNull();
  });

  it('撤销成功 → 行更新为 revoked', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 200, body: { data: [makeInvitation()], next_cursor: null } },
      { status: 200, body: { data: makeInvitation({ status: 'revoked' }) } },
    );
    renderList(fetchImpl);
    await waitFor(() => screen.getByTestId('invitation-revoke'));

    await user.click(screen.getByTestId('invitation-revoke'));
    await waitFor(() => expect(screen.getByText('revoked')).toBeTruthy());
  });

  it('撤销 409 conflict → 重拉对齐', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 200, body: { data: [makeInvitation()], next_cursor: null } },
      {
        status: 409,
        body: { error: { code: 'conflict', message: 'x', details: { status: 'expired' } } },
      },
      { status: 200, body: { data: [makeInvitation({ status: 'expired' })], next_cursor: null } },
    );
    renderList(fetchImpl);
    await waitFor(() => screen.getByTestId('invitation-revoke'));

    await user.click(screen.getByTestId('invitation-revoke'));
    await waitFor(() => expect(screen.getByText('expired')).toBeTruthy());
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it('空列表呈现空态', async () => {
    const fetchImpl = stubFetch({ status: 200, body: { data: [], next_cursor: null } });
    renderList(fetchImpl);
    await waitFor(() => expect(screen.getByTestId('invitation-list-empty')).toBeTruthy());
  });

  it('加载失败 → 错误态 + 重试', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 500, body: { error: { code: 'internal_error', message: 'x' } } },
      { status: 200, body: { data: [], next_cursor: null } },
    );
    renderList(fetchImpl);
    await waitFor(() => expect(screen.getByTestId('invitation-list-error')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByTestId('invitation-list-empty')).toBeTruthy());
  });

  it('next_cursor → load more 追加', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 200, body: { data: [makeInvitation()], next_cursor: 'c1' } },
      {
        status: 200,
        body: { data: [makeInvitation({ id: 'inv-2', email: 'b@x.com' })], next_cursor: null },
      },
    );
    renderList(fetchImpl);
    await waitFor(() => screen.getByTestId('invitation-load-more'));

    await user.click(screen.getByTestId('invitation-load-more'));
    await waitFor(() => expect(screen.getAllByTestId('invitation-row')).toHaveLength(2));
  });

  it('realtime invitation.redeemed → used_count 合并(达上限呈 exhausted)', async () => {
    const fetchImpl = stubFetch({
      status: 200,
      body: { data: [makeInvitation({ max_uses: 1 })], next_cursor: null },
    });
    const realtime = createFakeRealtime();
    renderList(fetchImpl, realtime.value);
    await waitFor(() => expect(screen.getByTestId('invitation-uses').textContent).toBe('0/1'));

    act(() => {
      for (const cb of realtime.frames) {
        cb({
          op: 'event',
          channel: workspaceChannel('ws-1'),
          seq: 1,
          event: 'invitation.redeemed',
          payload: { invitation_id: 'inv-1', member_id: 'mem-9', used_count: 1 },
        });
      }
    });

    await waitFor(() => expect(screen.getByTestId('invitation-uses').textContent).toBe('1/1'));
    await waitFor(() => expect(screen.getByText('exhausted')).toBeTruthy());
  });
});
