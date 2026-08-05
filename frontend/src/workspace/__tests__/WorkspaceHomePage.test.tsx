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
  const list = vi.fn().mockResolvedValue({ data: [], next_cursor: null });
  for (const response of responses) {
    fetchImpl.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(response.status, response.body)),
    );
  }
  return {
    fetchImpl,
    client: {
      list,
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
    list,
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
                <Route
                  path="/w/:workspaceSlug"
                  element={<WorkspaceHomePage client={client as never} />}
                />
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

  it('用真实列表端点呈现最近项目、issue、收件箱与运行摘要', async () => {
    const { client, list } = stubClient({ status: 200, body: { data: DETAIL } });
    list.mockImplementation(async (path: string) => {
      if (path === '/api/v1/workspaces/ws-1/projects') {
        return {
          data: [{ id: 'project-1', name: 'Launch plan', key: 'LAUNCH', open_issues: 3 }],
          next_cursor: null,
        };
      }
      if (path === '/api/v1/workspaces/ws-1/issues') {
        return {
          data: [{ id: 'issue-1', identifier: 'LAUNCH-12', title: 'Prepare release notes' }],
          next_cursor: null,
        };
      }
      if (path === '/api/v1/inbox') {
        return {
          data: [
            {
              id: 'notification-1',
              title: 'Review requested',
              preview: 'Please review LAUNCH-12',
              read_at: null,
              archived_at: null,
            },
          ],
          next_cursor: null,
        };
      }
      if (path === '/api/v1/workspaces/ws-1/executions') {
        return {
          data: [{ id: 'execution-123456', trigger: 'assign', status: 'running' }],
          next_cursor: null,
        };
      }
      throw new Error(`Unexpected list path: ${path}`);
    });

    renderHome(client);

    expect(await screen.findByRole('heading', { name: 'Workspace activity' })).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: /Recent project Launch plan/ })).toHaveAttribute(
      'href',
      '/w/acme/projects/project-1',
    );
    expect(screen.getByText(/3 open/)).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: /Recent issue LAUNCH-12 Prepare release notes/ }),
    ).toHaveAttribute('href', '/w/acme/issues/issue-1');
    expect(screen.getByRole('link', { name: /Recent inbox Review requested/ })).toHaveAttribute(
      'href',
      '/w/acme/inbox/notification-1',
    );
    expect(screen.getByText('Unread')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: /Recent run Assign · executio Running/ }),
    ).toHaveAttribute('href', '/w/acme/executions/execution-123456');

    expect(list).toHaveBeenCalledWith(
      '/api/v1/workspaces/ws-1/projects',
      expect.objectContaining({ query: expect.objectContaining({ limit: 1 }) }),
    );
    expect(list).toHaveBeenCalledWith(
      '/api/v1/workspaces/ws-1/issues',
      expect.objectContaining({
        query: expect.objectContaining({ sort: 'created_at', order: 'desc', limit: 1 }),
      }),
    );
    expect(list).toHaveBeenCalledWith(
      '/api/v1/inbox',
      expect.objectContaining({
        query: expect.objectContaining({ workspace_id: 'ws-1', limit: 1 }),
      }),
    );
    expect(list).toHaveBeenCalledWith(
      '/api/v1/workspaces/ws-1/executions',
      expect.objectContaining({ query: expect.objectContaining({ limit: 1 }) }),
    );
  });

  it('区分空数据、已读数据与端点失败,不虚构零值', async () => {
    const { client, list } = stubClient({ status: 200, body: { data: DETAIL } });
    list.mockImplementation(async (path: string) => {
      if (path === '/api/v1/workspaces/ws-1/projects') {
        throw new Error('projects unavailable');
      }
      if (path === '/api/v1/inbox') {
        return {
          data: [
            {
              id: 'notification-read',
              title: 'Release published',
              preview: 'The release is already live',
              read_at: '2026-08-05T00:00:00Z',
              archived_at: null,
            },
          ],
          next_cursor: null,
        };
      }
      return { data: [], next_cursor: null };
    });

    renderHome(client);

    await waitFor(() =>
      expect(screen.getByTestId('ws-activity-project')).toHaveTextContent('Unavailable right now'),
    );
    expect(screen.getByTestId('ws-activity-project')).toHaveAttribute('href', '/w/acme/projects');
    expect(screen.getByTestId('ws-activity-issue')).toHaveTextContent('No recent issues');
    expect(screen.getByTestId('ws-activity-execution')).toHaveTextContent('No recent runs');
    expect(screen.getByTestId('ws-activity-execution')).toHaveAttribute('href', '/w/acme/runtimes');
    expect(screen.getByRole('link', { name: /Recent inbox Release published/ })).toHaveAttribute(
      'href',
      '/w/acme/inbox/notification-read',
    );
    expect(screen.queryByText('Unread')).not.toBeInTheDocument();
    expect(screen.queryByText(/0 open/)).not.toBeInTheDocument();
  });

  it('每张活动卡独立落定,单个挂起端点不阻塞其他卡', async () => {
    const { client, list } = stubClient({ status: 200, body: { data: DETAIL } });
    list.mockImplementation((path: string) => {
      if (path === '/api/v1/workspaces/ws-1/projects') {
        return Promise.resolve({
          data: [{ id: 'project-1', name: 'Independent result', key: 'IR', open_issues: 1 }],
          next_cursor: null,
        });
      }
      if (path === '/api/v1/workspaces/ws-1/executions') {
        return new Promise(() => {});
      }
      return Promise.resolve({ data: [], next_cursor: null });
    });

    const { container } = renderHome(client);

    expect(await screen.findByText('Independent result')).toBeInTheDocument();
    expect(screen.getByTestId('ws-activity-issue')).toHaveTextContent('No recent issues');
    expect(screen.getByTestId('ws-activity-inbox')).toHaveTextContent('No recent notifications');
    expect(
      screen.getByTestId('ws-activity-execution').querySelector('.mesh-skeleton'),
    ).not.toBeNull();
    expect(container.querySelectorAll('.mesh-skeleton')).toHaveLength(1);
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
