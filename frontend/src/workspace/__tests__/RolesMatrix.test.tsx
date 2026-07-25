import { render, screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
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

function stubFetch(...responses: Array<{ status: number; body: unknown }>): ReturnType<typeof vi.fn> {
  const fetchImpl = vi.fn();
  for (const response of responses) {
    fetchImpl.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(response.status, response.body)),
    );
  }
  return fetchImpl;
}

function renderMatrix(fetchImpl: ReturnType<typeof vi.fn>): ReturnType<typeof render> {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            <RolesMatrix workspaceId="ws-1" client={stubClient(fetchImpl) as never} />
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('RolesMatrix(角色矩阵 + 名册消费,§4 角色呈现)', () => {
  it('角色 × 能力矩阵按 RBAC 呈现(删除仅 owner,设置/邀请/成员 admin+)', async () => {
    const fetchImpl = stubFetch({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
    renderMatrix(fetchImpl);

    await waitFor(() => expect(screen.getByTestId('roles-matrix')).toBeTruthy());
    // 表头四角色 + 四能力行
    expect(screen.getByText('Owner')).toBeTruthy();
    expect(screen.getByText('Guest')).toBeTruthy();
    expect(screen.getByText('Delete workspace')).toBeTruthy();
  });

  it('名册端点 404(MES-14 未合入)→ 优雅降级提示,非错误态', async () => {
    const fetchImpl = stubFetch({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
    renderMatrix(fetchImpl);

    await waitFor(() => expect(screen.getByTestId('roles-roster-unavailable')).toBeTruthy());
  });

  it('名册可用 → 行内角色变更 PATCH 并就地更新', async () => {
    const member = {
      id: 'mem-1',
      member_type: 'human',
      role: 'member',
      status: 'active',
      display_name: 'Jane',
    };
    const fetchImpl = stubFetch(
      { status: 200, body: { data: [member], next_cursor: null } },
      { status: 200, body: { data: { ...member, role: 'admin' } } },
    );
    renderMatrix(fetchImpl);
    await waitFor(() => expect(screen.getByTestId('roles-roster-row')).toBeTruthy());

    fireEvent.change(screen.getByTestId('roles-roster-select'), { target: { value: 'admin' } });
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
});
