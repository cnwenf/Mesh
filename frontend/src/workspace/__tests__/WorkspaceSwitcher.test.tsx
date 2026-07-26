import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { WorkspaceProvider } from '../WorkspaceProvider';
import { WorkspaceSwitcher } from '../WorkspaceSwitcher';
import type { ReactNode } from 'react';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
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

/** 桩客户端:list 走游标包络;request 走单对象包络。 */
function stubClient(fetchImpl: ReturnType<typeof vi.fn>) {
  return {
    list: async (path: string, opts: { query?: Record<string, string> } = {}) => {
      const qs = opts.query?.cursor !== undefined ? `?cursor=${opts.query.cursor}` : '';
      const response = await fetchImpl(`http://localhost${path}${qs}`, { method: 'GET' });
      return response.json();
    },
    request: async (method: string, path: string, opts: { body?: unknown } = {}) => {
      const response = await fetchImpl(`http://localhost${path}`, {
        method,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
      const body = (await response.json()) as { data?: unknown };
      return body.data;
    },
  };
}

const WS_A = {
  id: 'ws-1',
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  my_role: 'owner',
  created_at: '2026-07-25T00:00:00Z',
};
const WS_B = {
  id: 'ws-2',
  name: 'Beta',
  slug: 'beta',
  logo_url: null,
  my_role: 'member',
  created_at: '2026-07-24T00:00:00Z',
};

function renderSwitcher(client: unknown, withProvider = false): ReturnType<typeof render> {
  const tree = (children: ReactNode): React.JSX.Element => (
    <MemoryRouter initialEntries={[withProvider ? '/w/acme' : '/']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            {withProvider ? (
              <WorkspaceProvider slug="acme" client={client as never}>
                {children}
              </WorkspaceProvider>
            ) : (
              children
            )}
            <Routes>
              <Route path="/" element={<span data-testid="at-root" />} />
              <Route path="/w/:workspaceSlug" element={<span data-testid="nav-target" />} />
            </Routes>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>
  );
  return render(tree(<WorkspaceSwitcher client={client as never} />));
}

describe('WorkspaceSwitcher(切换器,§4.2)', () => {
  it('工作区上下文外按钮显示通用标签', () => {
    renderSwitcher(stubClient(stubFetch()));
    expect(screen.getByTestId('ws-switcher-button').textContent).toBe('Workspaces');
  });

  it('工作区上下文内按钮显示当前工作区名', async () => {
    const fetchImpl = stubFetch({ status: 200, body: { data: WS_A } });
    renderSwitcher(stubClient(fetchImpl), true);
    await waitFor(() =>
      expect(screen.getByTestId('ws-switcher-button').textContent).toBe('Acme'),
    );
  });

  it('打开后列出全部工作区并标记当前项', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      // provider 的 by-slug
      { status: 200, body: { data: WS_A } },
      // 切换器列表
      { status: 200, body: { data: [WS_A, WS_B], next_cursor: null } },
    );
    renderSwitcher(stubClient(fetchImpl), true);
    await waitFor(() => screen.getByTestId('ws-switcher-button'));

    await user.click(screen.getByTestId('ws-switcher-button'));
    await waitFor(() => expect(screen.getByTestId('ws-switcher-item-acme')).toBeTruthy());
    expect(screen.getByTestId('ws-switcher-item-beta')).toBeTruthy();
    expect(screen.getByTestId('ws-switcher-current')).toBeTruthy();
  });

  it('点击列表项切换到对应工作区路由', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 200,
      body: { data: [WS_A, WS_B], next_cursor: null },
    });
    renderSwitcher(stubClient(fetchImpl));

    await user.click(screen.getByTestId('ws-switcher-button'));
    await waitFor(() => screen.getByTestId('ws-switcher-item-beta'));
    await user.click(screen.getByTestId('ws-switcher-item-beta'));
    await waitFor(() => expect(screen.getByTestId('nav-target')).toBeTruthy());
  });

  it('列表为空显示空态', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 200, body: { data: [], next_cursor: null } });
    renderSwitcher(stubClient(fetchImpl));

    await user.click(screen.getByTestId('ws-switcher-button'));
    await waitFor(() => expect(screen.getByTestId('ws-switcher-empty')).toBeTruthy());
  });

  it('列表加载失败显示错误态', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
    renderSwitcher(stubClient(fetchImpl));

    await user.click(screen.getByTestId('ws-switcher-button'));
    await waitFor(() => expect(screen.getByTestId('ws-switcher-error')).toBeTruthy());
  });

  it('创建入口打开向导', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 200, body: { data: [], next_cursor: null } });
    renderSwitcher(stubClient(fetchImpl));

    await user.click(screen.getByTestId('ws-switcher-button'));
    await user.click(await screen.findByTestId('ws-switcher-create'));
    await waitFor(() => expect(screen.getByTestId('ws-wizard-name')).toBeTruthy());
  });
});
