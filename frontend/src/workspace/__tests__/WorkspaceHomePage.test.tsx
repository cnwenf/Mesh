import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiError } from '../../api/errors';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { WorkspaceProvider } from '../WorkspaceProvider';
import { WorkspaceHomePage } from '../pages/WorkspaceHomePage';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubClient(...responses: Array<{ status: number; body: unknown }>) {
  const fetchImpl = vi.fn();
  for (const response of responses) {
    fetchImpl.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(response.status, response.body)),
    );
  }
  return {
    fetchImpl,
    client: {
      request: async (method: string, path: string) => {
        const response = await fetchImpl(`http://localhost${path}`, { method });
        const body = (await response.json()) as {
          data?: unknown;
          error?: { code: string; message: string };
        };
        if (!response.ok) {
          throw new MeshApiError({
            status: response.status,
            code: body.error?.code ?? 'internal_error',
            message: body.error?.message ?? '',
          });
        }
        return body.data;
      },
    },
  };
}

const DETAIL = {
  id: 'ws-1',
  name: 'Acme Team',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'zh-CN' },
  my_role: 'owner',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

function renderHome(client: unknown): ReturnType<typeof render> {
  const wrapper = (): React.JSX.Element => (
    <MemoryRouter initialEntries={['/w/acme']}>
      <ThemeProvider>
        <I18nProvider
          workspaceDefaultLocale={null}
          reporter={{ report: () => undefined, reported: [] }}
        >
          <ToastProvider regionLabel="notifications">
            <WorkspaceProvider slug="acme" client={client as never}>
              <Routes>
                <Route path="/w/:workspaceSlug" element={<WorkspaceHomePage />} />
              </Routes>
            </WorkspaceProvider>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>
  );
  return render(wrapper());
}

describe('WorkspaceHomePage(工作区概览,§4.1)', () => {
  it('加载中呈现 loading 态', () => {
    // 永不 resolve 的桩 → 停在 loading
    const fetchImpl = vi.fn(
      (_url: string, _init: unknown) => new Promise<Response>(() => undefined),
    );
    renderHome({ request: () => fetchImpl('x', {}) });
    expect(screen.getByTestId('ws-loading')).toBeTruthy();
  });

  it('owner 视角:名称/元信息 + 设置入口', async () => {
    const { client } = stubClient({ status: 200, body: { data: DETAIL } });
    renderHome(client);

    await waitFor(() => expect(screen.getByTestId('ws-home-name').textContent).toBe('Acme Team'));
    expect(screen.getByTestId('ws-home-meta').textContent).toContain('acme');
    expect(screen.getByTestId('ws-home-meta').textContent).toContain('zh-CN');
    expect(screen.getByTestId('ws-settings-link')).toBeTruthy();
  });

  it('用统一页头建立唯一主标题,并提供工作区内的快速入口', async () => {
    const { client } = stubClient({ status: 200, body: { data: DETAIL } });
    const { container } = renderHome(client);

    await screen.findByTestId('ws-home-name');
    expect(container.querySelector('.mesh-page-header')).not.toBeNull();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByText('UTC')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Projects' })).toHaveAttribute(
      'href',
      '/w/acme/projects',
    );
    expect(screen.getByRole('link', { name: 'Issues' })).toHaveAttribute('href', '/w/acme/issues');
    expect(screen.getByRole('link', { name: 'Board' })).toHaveAttribute('href', '/w/acme/board');
    expect(screen.getByRole('link', { name: 'Members' })).toHaveAttribute(
      'href',
      '/w/acme/members',
    );
  });

  it('快速入口逐段编码 workspace slug 中的保留字符', async () => {
    const { client } = stubClient({
      status: 200,
      body: { data: { ...DETAIL, slug: 'blue team/ops' } },
    });
    renderHome(client);

    await screen.findByTestId('ws-home-name');
    expect(screen.getByRole('link', { name: 'Projects' })).toHaveAttribute(
      'href',
      '/w/blue%20team%2Fops/projects',
    );
    expect(screen.getByRole('link', { name: 'Issues' })).toHaveAttribute(
      'href',
      '/w/blue%20team%2Fops/issues',
    );
    expect(screen.getByRole('link', { name: 'Board' })).toHaveAttribute(
      'href',
      '/w/blue%20team%2Fops/board',
    );
    expect(screen.getByRole('link', { name: 'Members' })).toHaveAttribute(
      'href',
      '/w/blue%20team%2Fops/members',
    );
    expect(screen.getByTestId('ws-settings-link')).toHaveAttribute(
      'href',
      '/w/blue%20team%2Fops/settings',
    );
  });

  it('member 视角:设置入口隐藏,提示可见性', async () => {
    const { client } = stubClient({
      status: 200,
      body: { data: { ...DETAIL, my_role: 'member' } },
    });
    renderHome(client);

    await waitFor(() => expect(screen.getByTestId('ws-home-name')).toBeTruthy());
    expect(screen.queryByTestId('ws-settings-link')).toBeNull();
  });

  it('工作区未配置默认语言时回退为 en', async () => {
    const { client } = stubClient({
      status: 200,
      body: { data: { ...DETAIL, settings: {} } },
    });
    renderHome(client);

    await screen.findByTestId('ws-home-name');
    expect(screen.getByTestId('ws-home-meta')).toHaveTextContent('default language: en');
  });

  it('404 → not-found 门控呈现(与不存在同形)', async () => {
    const { client } = stubClient({
      status: 404,
      body: { error: { code: 'not_found', message: 'workspace not found' } },
    });
    renderHome(client);

    await waitFor(() => expect(screen.getByTestId('ws-not-found')).toBeTruthy());
  });

  it('500 → 错误态 + 重试恢复', async () => {
    const { client } = stubClient(
      { status: 500, body: { error: { code: 'internal_error', message: 'boom' } } },
      { status: 200, body: { data: DETAIL } },
    );
    renderHome(client);

    await waitFor(() => expect(screen.getByTestId('ws-error')).toBeTruthy());
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByTestId('ws-home-name')).toBeTruthy());
  });
});
