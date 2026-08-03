import { render, screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { RealtimeContext } from '../../shell/AppShell';
import type { RealtimeContextValue } from '../../shell/AppShell';
import { RolesMatrix } from '../RolesMatrix';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubClient(fetchImpl: ReturnType<typeof vi.fn>) {
  return {
    list: async (path: string) => {
      const response = await fetchImpl(`http://localhost${path}`, { method: 'GET' });
      if (!response.ok) {
        const { MeshApiError } = await import('../../api/errors');
        throw new MeshApiError({ status: response.status, code: 'not_found', message: 'x' });
      }
      return response.json();
    },
    request: async (method: string, path: string, opts: { body?: unknown } = {}) => {
      const response = await fetchImpl(`http://localhost${path}`, {
        method,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
      const body = (await response.json()) as {
        data?: unknown;
        error?: { code: string; message: string };
      };
      if (!response.ok) {
        const { MeshApiError } = await import('../../api/errors');
        throw new MeshApiError({
          status: response.status,
          code: body.error?.code ?? 'internal_error',
          message: body.error?.message ?? '',
        });
      }
      return body.data;
    },
  };
}

function stubFetch(
  ...responses: Array<{ status: number; body: unknown }>
): ReturnType<typeof vi.fn> {
  const fetchImpl = vi.fn();
  for (const response of responses) {
    fetchImpl.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(response.status, response.body)),
    );
  }
  return fetchImpl;
}

function renderMatrix(
  fetchImpl: ReturnType<typeof vi.fn>,
  realtime?: RealtimeContextValue,
): ReturnType<typeof render> {
  const matrix = <RolesMatrix workspaceId="ws-1" client={stubClient(fetchImpl) as never} />;
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <I18nProvider
          workspaceDefaultLocale={null}
          reporter={{ report: () => undefined, reported: [] }}
        >
          <ToastProvider regionLabel="notifications">
            {realtime === undefined ? (
              matrix
            ) : (
              <RealtimeContext.Provider value={realtime}>{matrix}</RealtimeContext.Provider>
            )}
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('RolesMatrix(角色矩阵 + 名册消费,§4 角色呈现)', () => {
  it('角色 × 能力矩阵按 RBAC 呈现(删除仅 owner,设置/邀请/成员 admin+)', async () => {
    const fetchImpl = stubFetch({
      status: 404,
      body: { error: { code: 'not_found', message: 'x' } },
    });
    renderMatrix(fetchImpl);

    await waitFor(() => expect(screen.getByTestId('roles-matrix')).toBeTruthy());
    // 表头四角色 + 四能力行
    expect(screen.getByText('Owner')).toBeTruthy();
    expect(screen.getByText('Guest')).toBeTruthy();
    expect(screen.getByText('Delete workspace')).toBeTruthy();
  });

  it('名册端点 404(MES-14 未合入)→ 优雅降级提示,非错误态', async () => {
    const fetchImpl = stubFetch({
      status: 404,
      body: { error: { code: 'not_found', message: 'x' } },
    });
    renderMatrix(fetchImpl);

    await waitFor(() => expect(screen.getByTestId('roles-roster-unavailable')).toBeTruthy());
  });

  it('名册端点 405 同样优雅降级', async () => {
    renderMatrix(
      stubFetch({ status: 405, body: { error: { code: 'method_not_allowed', message: 'x' } } }),
    );
    expect(await screen.findByTestId('roles-roster-unavailable')).toBeTruthy();
  });

  it('名册可用 → 行内角色变更 PATCH 并就地更新', async () => {
    const member = {
      id: 'mem-1',
      member_type: 'human',
      role: 'member',
      status: 'active',
      display_name: 'Jane',
    };
    const other = { ...member, id: 'mem-2', display_name: 'John', role: 'guest' };
    const fetchImpl = stubFetch(
      { status: 200, body: { data: [member, other], next_cursor: null } },
      { status: 200, body: { data: { ...member, role: 'admin' } } },
    );
    renderMatrix(fetchImpl);
    await waitFor(() => expect(screen.getAllByTestId('roles-roster-row')).toHaveLength(2));

    fireEvent.change(screen.getAllByTestId('roles-roster-select')[0], {
      target: { value: 'admin' },
    });
    await waitFor(() => {
      const [, init] = fetchImpl.mock.calls[1] as [string, { method: string; body: string }];
      expect(init.method).toBe('PATCH');
      expect(JSON.parse(init.body)).toEqual({ role: 'admin' });
    });
  });

  it('409 last_owner → 具名错误呈现', async () => {
    const member = {
      id: 'mem-1',
      member_type: 'human',
      role: 'owner',
      status: 'active',
      display_name: 'Jane',
    };
    const fetchImpl = stubFetch(
      { status: 200, body: { data: [member], next_cursor: null } },
      { status: 409, body: { error: { code: 'last_owner', message: 'x' } } },
    );
    renderMatrix(fetchImpl);
    await waitFor(() => screen.getByTestId('roles-roster-row'));

    fireEvent.change(screen.getByTestId('roles-roster-select'), { target: { value: 'member' } });
    await waitFor(() =>
      expect(screen.getByTestId('roles-change-error').textContent).toBe(
        'A workspace must keep at least one owner',
      ),
    );
  });

  it('agent 行的角色选项不含 owner(后端强校验前置)', async () => {
    const agent = {
      id: 'mem-2',
      member_type: 'agent',
      role: 'member',
      status: 'active',
      display_name: 'Bot',
    };
    const fetchImpl = stubFetch({ status: 200, body: { data: [agent], next_cursor: null } });
    renderMatrix(fetchImpl);
    await waitFor(() => screen.getByTestId('roles-roster-row'));

    const options = Array.from(
      (screen.getByTestId('roles-roster-select') as HTMLSelectElement).options,
    ).map((option) => option.value);
    expect(options).not.toContain('owner');
  });

  it('空名册呈现空态,无显示名时回退成员 id', async () => {
    const emptyFetch = stubFetch({ status: 200, body: { data: [], next_cursor: null } });
    const first = renderMatrix(emptyFetch);
    expect(await screen.findByTestId('roles-roster-empty')).toBeTruthy();
    first.unmount();

    const member = {
      id: 'mem-fallback',
      member_type: 'human',
      role: 'member',
      status: 'active',
      display_name: null,
    };
    renderMatrix(stubFetch({ status: 200, body: { data: [member], next_cursor: null } }));
    expect(await screen.findByText('mem-fallback')).toBeTruthy();
    expect(screen.getByLabelText('Role of mem-fallback')).toBeTruthy();
  });

  it('realtime 仅在同频道成员事件时重拉名册', async () => {
    const callbacks: Array<(frame: unknown) => void> = [];
    const realtimeClient = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn((callback: (frame: unknown) => void) => {
        callbacks.push(callback);
        return vi.fn();
      }),
    };
    const fetchImpl = stubFetch(
      { status: 200, body: { data: [], next_cursor: null } },
      { status: 200, body: { data: [], next_cursor: null } },
    );
    const rendered = renderMatrix(fetchImpl, {
      state: 'connected',
      client: realtimeClient as never,
    });
    await screen.findByTestId('roles-roster-empty');
    callbacks[0]({ channel: 'workspace:other', event: 'member.added' });
    callbacks[0]({ channel: 'workspace:ws-1', event: 'other' });
    callbacks[0]({ channel: 'workspace:ws-1', event: 'member.role_changed' });
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    rendered.unmount();
    expect(realtimeClient.unsubscribe).toHaveBeenCalledWith('workspace:ws-1');
  });
});
