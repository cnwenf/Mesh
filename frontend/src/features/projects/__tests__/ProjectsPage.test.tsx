/**
 * ProjectsPage + CreateProjectDialog 组件测试(project.md §4.1/§4.3)。
 * 以 fetch 桩驱动:紧凑表格渲染(名称/状态徽章/健康度/进度/负责人/目标日)、筛选
 * (状态/已归档/我参与的,URL 同源)、Load more 游标分页、错误态重试、无工作区空态、
 * 实时列表帧合并;新建对话框:key 自动建议 + 客户端格式校验 + 409 内联错误。
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import type { RecordedCall } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import type { RealtimeClient } from '../../../realtime';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
import { CreateProjectDialog } from '../CreateProjectDialog';
import { ProjectsPage } from '../ProjectsPage';

const ME = {
  user: { id: 'usr-owner', email: 'owner@acme.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'Team',
      workspace_slug: 'team',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

const PROJECT_A = {
  id: 'prj-1',
  workspace_id: 'ws-1',
  name: 'Apollo',
  key: 'APL',
  description: 'Moon landing',
  icon: null,
  color: null,
  status: 'active',
  health: 'on_track',
  visibility: 'public',
  lead: { id: 'mem-lead', name: 'Jane Doe', member_type: 'human' },
  lead_member_id: 'mem-lead',
  start_date: '2026-01-01',
  target_date: '2026-09-30',
  progress: 0.5,
  open_issues: 5,
  done_issues: 5,
  issue_seq: 10,
  archived: false,
  archived_at: null,
  my_role: 'lead',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

const PROJECT_B = {
  ...PROJECT_A,
  id: 'prj-2',
  name: 'Borealis',
  key: 'BOR',
  description: null,
  status: 'planning',
  health: null,
  lead: null,
  lead_member_id: null,
  start_date: null,
  target_date: null,
  progress: 0,
  open_issues: 3,
  done_issues: 0,
  my_role: null,
};

const PROJECT_ARCHIVED = {
  ...PROJECT_A,
  id: 'prj-3',
  name: 'Ceres',
  key: 'CER',
  status: 'completed',
  health: 'off_track',
  archived: true,
  archived_at: '2026-06-01T00:00:00Z',
  my_role: null,
};

interface ListProjectsProject {
  readonly id: string;
  readonly name: string;
  readonly status: string;
  readonly archived: boolean;
  readonly my_role: string | null;
}

interface FetchOptions {
  /** POST /workspaces/{ws}/projects 的响应状态(默认 201) */
  readonly createStatus?: number;
  /** 409 时的错误信封 */
  readonly createError?: { readonly code: string; readonly message: string };
  /** GET 列表首次失败(错误态重试用) */
  readonly failListOnce?: boolean;
  /** POST 创建以网络错误 reject(非 MeshApiError 分支) */
  readonly createRejects?: boolean;
  /** 多工作区路由匹配场景的 /users/me 响应。 */
  readonly me?: unknown;
}

function makeFetch(projects: readonly ListProjectsProject[], opts: FetchOptions = {}) {
  const calls: RecordedCall[] = [];
  let listFailures = opts.failListOnce ? 1 : 0;
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, init });
    if (url.includes('/users/me')) {
      return fakeResponse({ body: { data: opts.me ?? ME } });
    }
    if (method === 'POST' && url.includes('/projects')) {
      if (opts.createRejects === true) {
        throw new TypeError('network down');
      }
      if (opts.createStatus !== undefined && opts.createStatus >= 400) {
        return fakeResponse({
          status: opts.createStatus,
          body: { error: opts.createError ?? { code: 'unknown', message: 'failed' } },
        });
      }
      const body = JSON.parse(String(init?.body)) as { name?: string; key?: string };
      return fakeResponse({
        status: 201,
        body: { data: { ...PROJECT_A, id: 'prj-new', name: body.name, key: body.key } },
      });
    }
    if (method === 'GET' && url.includes('/projects')) {
      if (listFailures > 0) {
        listFailures -= 1;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'server error' } },
        });
      }
      const params = new URL(url).searchParams;
      let data = [...projects];
      const status = params.get('status');
      if (status !== null) {
        data = data.filter((project) => project.status === status);
      }
      const archived = params.get('archived') === 'true';
      data = data.filter((project) => project.archived === archived);
      if (params.get('mine') === 'true') {
        data = data.filter((project) => project.my_role !== null);
      }
      return fakeResponse({ body: { data, next_cursor: null } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
  }) as typeof fetch;
  return { impl, calls };
}

function stub(projects: readonly ListProjectsProject[], opts: FetchOptions = {}) {
  const { impl, calls } = makeFetch(projects, opts);
  vi.stubGlobal('fetch', impl);
  return calls;
}

const listCalls = (calls: RecordedCall[]): RecordedCall[] =>
  calls.filter((c) => (c.init?.method ?? 'GET') === 'GET' && c.url.includes('/projects'));

function deferredResponse(): {
  readonly promise: Promise<Response>;
  readonly resolve: (response: Response) => void;
} {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

/** ToastProvider 需要经 useT 提供 regionLabel(render.tsx 同款)。 */
function ToastLayer(props: { children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

interface FakeRealtime {
  readonly value: RealtimeContextValue;
  readonly client: {
    subscribe: ReturnType<typeof vi.fn>;
    unsubscribe: ReturnType<typeof vi.fn>;
    onFrame: ReturnType<typeof vi.fn>;
  };
  readonly emit: (frame: RealtimeEventFrame) => void;
}

function makeFakeRealtime(): FakeRealtime {
  const listeners: Array<(frame: RealtimeEventFrame) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((cb: (frame: RealtimeEventFrame) => void) => {
      listeners.push(cb);
      return () => undefined;
    }),
  };
  const value: RealtimeContextValue = {
    state: 'connected',
    client: client as unknown as RealtimeClient,
  };
  return {
    value,
    client,
    emit: (frame) => {
      for (const listener of listeners) listener(frame);
    },
  };
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染项目表格行:名称/状态徽章/健康度/进度/负责人/目标日', async () => {
    stub([PROJECT_A, PROJECT_B]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Projects');
    expect(screen.getByTestId('data-view')).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);

    const table = await screen.findByRole('table', { name: 'Projects' });
    expect(table).toHaveClass('mesh-projects__table');
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((header) => header.textContent),
    ).toEqual(['Name', 'Status', 'Health', 'Progress', 'Lead', 'Target date']);
    expect(screen.queryByTestId('projects-grid')).not.toBeInTheDocument();

    const cardA = await screen.findByTestId('project-card-prj-1');
    expect(cardA).toHaveRole('row');
    expect(within(cardA).getByRole('link', { name: 'Apollo' })).toHaveAttribute(
      'href',
      '/w/team/projects/prj-1',
    );
    expect(within(cardA).getByText('Active')).toBeInTheDocument();
    expect(within(cardA).getByText('On track')).toBeInTheDocument();
    expect(within(cardA).getByRole('img', { name: 'Jane Doe' })).toHaveClass('mesh-avatar--20');
    expect(within(cardA).getByRole('progressbar', { name: '5/10 done' })).toBeInTheDocument();
    expect(screen.getByTestId('project-date-prj-1')).toHaveTextContent('Due');

    const cardB = screen.getByTestId('project-card-prj-2');
    expect(within(cardB).getByText('Borealis')).toBeInTheDocument();
    expect(within(cardB).getByText('Planning')).toBeInTheDocument();
    expect(within(cardB).getByText('No health set')).toBeInTheDocument();
    expect(within(cardB).queryByText('Jane Doe')).not.toBeInTheDocument();
    expect(screen.queryByTestId('project-date-prj-2')).not.toBeInTheDocument();
  });

  it('规范深链按 route slug 请求对应工作区,且卡片保留同一 slug', async () => {
    const betaMembership = {
      ...ME.memberships[0],
      workspace_id: 'ws-2',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
    };
    const calls = stub([PROJECT_A], {
      me: { ...ME, memberships: [ME.memberships[0], betaMembership] },
    });
    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/projects" element={<ProjectsPage />} />
      </Routes>,
      { route: '/w/beta/projects' },
    );

    const card = await screen.findByTestId('project-card-prj-1');
    expect(listCalls(calls).some((call) => call.url.includes('/workspaces/ws-2/projects'))).toBe(
      true,
    );
    expect(within(card).getByRole('link', { name: 'Apollo' })).toHaveAttribute(
      'href',
      '/w/beta/projects/prj-1',
    );
  });

  it('生产 /w 路由只使用 WorkspaceProvider 当前工作区,不再读取 memberships', async () => {
    const detail = {
      id: 'ws-2',
      name: 'Beta',
      slug: 'beta',
      logo_url: null,
      timezone: 'UTC',
      settings: {},
      my_role: 'owner' as const,
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
    const providerClient = {
      request: vi.fn(async () => detail),
    };
    const betaProject = { ...PROJECT_B, workspace_id: 'ws-2' };
    const calls: RecordedCall[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/workspaces/ws-2/projects')) {
        return fakeResponse({ body: { data: [betaProject], next_cursor: null } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <WorkspaceProvider slug="beta" client={providerClient as never}>
        <Routes>
          <Route path="/w/:workspaceSlug/projects" element={<ProjectsPage />} />
        </Routes>
      </WorkspaceProvider>,
      { route: '/w/beta/projects' },
    );

    const card = await screen.findByTestId('project-card-prj-2');
    expect(within(card).getByRole('link', { name: 'Borealis' })).toHaveAttribute(
      'href',
      '/w/beta/projects/prj-2',
    );
    expect(calls.some((call) => call.url.includes('/users/me'))).toBe(false);
    expect(listCalls(calls).every((call) => call.url.includes('/workspaces/ws-2/projects'))).toBe(
      true,
    );
  });

  it('route slug 无对应 membership 时不误读其他工作区项目', async () => {
    const calls = stub([PROJECT_A]);
    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/projects" element={<ProjectsPage />} />
      </Routes>,
      { route: '/w/missing/projects' },
    );

    expect(
      await screen.findByText('You are not a member of any workspace yet.'),
    ).toBeInTheDocument();
    expect(listCalls(calls)).toHaveLength(0);
  });

  it('切换 route slug 后忽略旧工作区迟到的首屏,不覆盖新网格/游标/加载态', async () => {
    const user = userEvent.setup();
    const alphaPage = deferredResponse();
    const betaPage = deferredResponse();
    let betaRequested = false;
    const betaMembership = {
      ...ME.memberships[0],
      workspace_id: 'ws-2',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
    };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({
          body: { data: { ...ME, memberships: [ME.memberships[0], betaMembership] } },
        });
      }
      if (url.includes('/workspaces/ws-1/projects')) return alphaPage.promise;
      if (url.includes('/workspaces/ws-2/projects')) {
        betaRequested = true;
        return betaPage.promise;
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);

    renderWithProviders(
      <Routes>
        <Route
          path="/w/:workspaceSlug/projects"
          element={
            <>
              <Link to="/w/beta/projects">Switch to Beta</Link>
              <ProjectsPage />
            </>
          }
        />
      </Routes>,
      { route: '/w/team/projects' },
    );

    await waitFor(() => expect(screen.getByText('Loading…')).toBeInTheDocument());
    await user.click(screen.getByRole('link', { name: 'Switch to Beta' }));
    await waitFor(() => expect(betaRequested).toBe(true));

    await act(async () => {
      alphaPage.resolve(fakeResponse({ body: { data: [PROJECT_A], next_cursor: 'alpha-cursor' } }));
      await alphaPage.promise;
    });

    expect(screen.queryByText('Apollo')).not.toBeInTheDocument();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.queryByTestId('projects-load-more')).not.toBeInTheDocument();

    await act(async () => {
      betaPage.resolve(
        fakeResponse({
          body: {
            data: [{ ...PROJECT_B, workspace_id: 'ws-2' }],
            next_cursor: null,
          },
        }),
      );
      await betaPage.promise;
    });

    const betaCard = await screen.findByTestId('project-card-prj-2');
    expect(within(betaCard).getByRole('link', { name: 'Borealis' })).toHaveAttribute(
      'href',
      '/w/beta/projects/prj-2',
    );
    expect(screen.queryByText('Apollo')).not.toBeInTheDocument();
    expect(screen.queryByTestId('projects-load-more')).not.toBeInTheDocument();
  });

  it('创建成功后进入同一工作区的规范项目详情深链', async () => {
    stub([PROJECT_A]);
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/w/:workspaceSlug/projects" element={<ProjectsPage />} />
        <Route
          path="/w/:workspaceSlug/projects/:projectId"
          element={<div data-testid="created-project-route" />}
        />
      </Routes>,
      { route: '/w/team/projects' },
    );

    await user.click(await screen.findByTestId('new-project-button'));
    await user.type(screen.getByTestId('create-project-name'), 'Tiny');
    await user.click(screen.getByTestId('create-project-submit'));

    expect(await screen.findByTestId('created-project-route')).toBeInTheDocument();
  });

  it('无项目时显示空态(onboarding 四要素)', async () => {
    stub([]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    // 两段加载(fetchMe → workspace 就绪后二次拉取)会瞬时替换空态节点;
    // 以「终态独有的描述文案 + 标题」组合作为稳定判据。
    await waitFor(() => {
      expect(screen.getByText('Group related issues with a project.')).toBeInTheDocument();
      expect(screen.getByText('No projects yet')).toBeInTheDocument();
    });
  });

  it('加载失败显示错误态,点击 Retry 重新加载', async () => {
    const user = userEvent.setup();
    stub([PROJECT_A], { failListOnce: true });
    renderWithProviders(<ProjectsPage />, { route: '/projects' });

    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('server error')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Apollo')).toBeInTheDocument();
  });

  it('无工作区成员身份时提示无工作区', async () => {
    const impl = (async () =>
      fakeResponse({
        body: { data: { user: ME.user, memberships: [] } },
      })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    expect(
      await screen.findByText('You are not a member of any workspace yet.'),
    ).toBeInTheDocument();
  });

  it('读取本人工作区失败时显示可重试错误态', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('network down'))),
    );
    renderWithProviders(<ProjectsPage />, { route: '/projects' });

    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    expect(
      screen.getByText('We could not load this content. Please try again.'),
    ).toBeInTheDocument();
  });

  it('状态筛选经 URL 同源,重拉带 status 参数的列表', async () => {
    const user = userEvent.setup();
    const calls = stub([PROJECT_A, PROJECT_B]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await screen.findByText('Apollo');

    await user.selectOptions(screen.getByTestId('projects-status-filter'), 'planning');

    await waitFor(() =>
      expect(listCalls(calls).some((c) => c.url.includes('status=planning'))).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText('Apollo')).not.toBeInTheDocument());
    expect(screen.getByText('Borealis')).toBeInTheDocument();
  });

  it('「已归档」勾选切换 archived=true 视图', async () => {
    const user = userEvent.setup();
    const calls = stub([PROJECT_A, PROJECT_ARCHIVED]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await screen.findByText('Apollo');

    await user.click(screen.getByTestId('projects-archived-filter'));

    await waitFor(() =>
      expect(listCalls(calls).some((c) => c.url.includes('archived=true'))).toBe(true),
    );
    expect(await screen.findByText('Ceres')).toBeInTheDocument();
    expect(screen.queryByText('Apollo')).not.toBeInTheDocument();

    await user.click(screen.getByTestId('projects-archived-filter'));
    await waitFor(() => {
      const latest = listCalls(calls).at(-1);
      expect(latest?.url.includes('archived=true') ?? true).toBe(false);
    });
    await waitFor(() => expect(screen.getByText('Apollo')).toBeInTheDocument());
  });

  it('「我参与的」勾选仅显示 my_role 非空的项目(mine=true)', async () => {
    const user = userEvent.setup();
    const calls = stub([PROJECT_A, PROJECT_B]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await screen.findByText('Apollo');

    await user.click(screen.getByTestId('projects-mine-filter'));

    await waitFor(() =>
      expect(listCalls(calls).some((c) => c.url.includes('mine=true'))).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText('Borealis')).not.toBeInTheDocument());
    expect(screen.getByText('Apollo')).toBeInTheDocument();
  });

  it('Load more 以游标分页追加项目', async () => {
    const user = userEvent.setup();
    const calls: RecordedCall[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: ME } });
      }
      if (url.includes('/projects') && url.includes('cursor=c1')) {
        return fakeResponse({ body: { data: [PROJECT_B], next_cursor: null } });
      }
      if (url.includes('/projects')) {
        return fakeResponse({ body: { data: [PROJECT_A], next_cursor: 'c1' } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await screen.findByText('Apollo');

    await user.click(screen.getByTestId('projects-load-more'));

    expect(await screen.findByText('Borealis')).toBeInTheDocument();
    await waitFor(() =>
      expect(listCalls(calls).some((c) => c.url.includes('cursor=c1'))).toBe(true),
    );
    expect(screen.queryByTestId('projects-load-more')).not.toBeInTheDocument();
  });

  it('切换 route slug 后忽略旧工作区迟到的 Load more,并释放新工作区分页态', async () => {
    const user = userEvent.setup();
    const alphaMore = deferredResponse();
    const betaMembership = {
      ...ME.memberships[0],
      workspace_id: 'ws-2',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
    };
    const staleAlphaProject = { ...PROJECT_A, id: 'prj-alpha-more', name: 'Alpha more' };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({
          body: { data: { ...ME, memberships: [ME.memberships[0], betaMembership] } },
        });
      }
      if (url.includes('/workspaces/ws-1/projects') && url.includes('cursor=alpha-cursor')) {
        return alphaMore.promise;
      }
      if (url.includes('/workspaces/ws-1/projects')) {
        return fakeResponse({ body: { data: [PROJECT_A], next_cursor: 'alpha-cursor' } });
      }
      if (url.includes('/workspaces/ws-2/projects')) {
        return fakeResponse({
          body: {
            data: [{ ...PROJECT_B, workspace_id: 'ws-2' }],
            next_cursor: 'beta-cursor',
          },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);

    renderWithProviders(
      <Routes>
        <Route
          path="/w/:workspaceSlug/projects"
          element={
            <>
              <Link to="/w/beta/projects">Switch to Beta</Link>
              <ProjectsPage />
            </>
          }
        />
      </Routes>,
      { route: '/w/team/projects' },
    );

    await screen.findByText('Apollo');
    await user.click(screen.getByTestId('projects-load-more'));
    await user.click(screen.getByRole('link', { name: 'Switch to Beta' }));

    expect(await screen.findByText('Borealis')).toBeInTheDocument();
    expect(screen.getByTestId('projects-load-more')).toBeEnabled();

    await act(async () => {
      alphaMore.resolve(fakeResponse({ body: { data: [staleAlphaProject], next_cursor: null } }));
      await alphaMore.promise;
    });

    expect(screen.queryByText('Alpha more')).not.toBeInTheDocument();
    expect(screen.getByText('Borealis')).toBeInTheDocument();
    expect(screen.getByTestId('projects-load-more')).toBeEnabled();
  });

  it('A→B 切换后健康度弹窗保持关闭且不会向 A 项目提交', async () => {
    const user = userEvent.setup();
    const calls: RecordedCall[] = [];
    const betaMembership = {
      ...ME.memberships[0],
      workspace_id: 'ws-2',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
    };
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.includes('/users/me')) {
        return fakeResponse({
          body: { data: { ...ME, memberships: [ME.memberships[0], betaMembership] } },
        });
      }
      if (url.includes('/workspaces/ws-1/projects')) {
        return fakeResponse({ body: { data: [PROJECT_A], next_cursor: null } });
      }
      if (url.includes('/workspaces/ws-2/projects')) {
        return fakeResponse({
          body: { data: [{ ...PROJECT_B, workspace_id: 'ws-2' }], next_cursor: null },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <Routes>
        <Route
          path="/w/:workspaceSlug/projects"
          element={
            <>
              <Link to="/w/beta/projects">Switch to Beta</Link>
              <ProjectsPage />
            </>
          }
        />
      </Routes>,
      { route: '/w/team/projects' },
    );

    const alphaCard = await screen.findByTestId('project-card-prj-1');
    await user.click(within(alphaCard).getByRole('button', { name: 'Update status' }));
    expect(await screen.findByTestId('health-update-form')).toBeInTheDocument();

    await user.click(screen.getByRole('link', { name: 'Switch to Beta' }));
    expect(await screen.findByTestId('project-card-prj-2')).toBeInTheDocument();
    expect(screen.queryByTestId('health-update-form')).not.toBeInTheDocument();
    expect(calls.some((call) => (call.init?.method ?? 'GET') === 'POST')).toBe(false);
  });

  it('A→B 切换后新建项目弹窗不会在 B 工作区重开', async () => {
    const user = userEvent.setup();
    const betaMembership = {
      ...ME.memberships[0],
      workspace_id: 'ws-2',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
    };
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({
          body: { data: { ...ME, memberships: [ME.memberships[0], betaMembership] } },
        });
      }
      if (url.includes('/workspaces/ws-1/projects')) {
        return fakeResponse({ body: { data: [PROJECT_A], next_cursor: null } });
      }
      if (url.includes('/workspaces/ws-2/projects')) {
        return fakeResponse({
          body: { data: [{ ...PROJECT_B, workspace_id: 'ws-2' }], next_cursor: null },
        });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch);

    renderWithProviders(
      <Routes>
        <Route
          path="/w/:workspaceSlug/projects"
          element={
            <>
              <Link to="/w/beta/projects">Switch to Beta</Link>
              <ProjectsPage />
            </>
          }
        />
      </Routes>,
      { route: '/w/team/projects' },
    );

    await screen.findByTestId('project-card-prj-1');
    await user.click(screen.getByTestId('new-project-button'));
    expect(await screen.findByRole('dialog', { name: 'New project' })).toBeInTheDocument();

    await user.click(screen.getByRole('link', { name: 'Switch to Beta' }));
    expect(await screen.findByTestId('project-card-prj-2')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'New project' })).not.toBeInTheDocument();
  });

  it('Load more 失败时提示错误 toast', async () => {
    const user = userEvent.setup();
    const impl = (async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: ME } });
      }
      if (url.includes('/projects') && url.includes('cursor=c1')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      }
      if (url.includes('/projects')) {
        return fakeResponse({ body: { data: [PROJECT_A], next_cursor: 'c1' } });
      }
      return fakeResponse({ status: 404 });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await screen.findByText('Apollo');

    await user.click(screen.getByTestId('projects-load-more'));

    expect(await screen.findByText('Something went wrong. Please try again.')).toBeInTheDocument();
  });

  it('实时帧 project.created 按当前筛选合并进列表', async () => {
    stub([PROJECT_A]);
    const realtime = makeFakeRealtime();
    render(
      <MemoryRouter initialEntries={['/projects']}>
        <ThemeProvider>
          <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
            <ToastLayer>
              <RealtimeContext.Provider value={realtime.value}>
                <ProjectsPage />
              </RealtimeContext.Provider>
            </ToastLayer>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    await screen.findByText('Apollo');
    expect(realtime.client.subscribe).toHaveBeenCalledWith('workspace:ws-1:projects');

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'workspace:ws-1:projects',
        seq: 1,
        event: 'project.created',
        payload: { project: { ...PROJECT_B, id: 'prj-rt', name: 'Realtime Project' } },
      });
    });

    expect(await screen.findByText('Realtime Project')).toBeInTheDocument();

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'workspace:ws-1:projects',
        seq: 2,
        event: 'project.created',
        payload: { project: PROJECT_ARCHIVED },
      });
    });
    expect(screen.queryByText('Ceres')).not.toBeInTheDocument();
  });

  it('实时列表忽略 foreign channel 与当前频道中的 foreign workspace payload', async () => {
    stub([PROJECT_A]);
    const realtime = makeFakeRealtime();
    render(
      <MemoryRouter initialEntries={['/projects']}>
        <ThemeProvider>
          <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
            <ToastLayer>
              <RealtimeContext.Provider value={realtime.value}>
                <ProjectsPage />
              </RealtimeContext.Provider>
            </ToastLayer>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    await screen.findByText('Apollo');

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'workspace:ws-2:projects',
        seq: 1,
        event: 'project.created',
        payload: { project: { ...PROJECT_B, id: 'foreign-channel', name: 'Foreign channel' } },
      });
      realtime.emit({
        op: 'event',
        channel: 'workspace:ws-1:projects',
        seq: 2,
        event: 'project.created',
        payload: {
          project: {
            ...PROJECT_B,
            id: 'foreign-workspace',
            workspace_id: 'ws-2',
            name: 'Foreign workspace',
          },
        },
      });
    });

    expect(screen.queryByText('Foreign channel')).not.toBeInTheDocument();
    expect(screen.queryByText('Foreign workspace')).not.toBeInTheDocument();
    expect(screen.getByText('Apollo')).toBeInTheDocument();
  });
});

describe('CreateProjectDialog(经 ProjectsPage 打开)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function openDialog(): Promise<RecordedCall[]> {
    const calls = stub([PROJECT_A]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await screen.findByText('Apollo');
    const user = userEvent.setup();
    await user.click(screen.getByTestId('new-project-button'));
    await screen.findByRole('dialog', { name: 'New project' });
    return calls;
  }

  it('名称输入自动建议大写 key,手改后按格式即时校验', async () => {
    const user = userEvent.setup();
    await openDialog();

    await user.type(screen.getByTestId('create-project-name'), 'Website Redesign');
    expect(screen.getByTestId('create-project-key')).toHaveValue('WEBSITE_REDE');
    expect(screen.getByText('Looks good — this key is valid.')).toBeInTheDocument();

    await user.clear(screen.getByTestId('create-project-key'));
    await user.type(screen.getByTestId('create-project-key'), '9bad');
    expect(screen.getByTestId('create-project-key')).toHaveValue('9BAD');
    expect(
      screen.getByText(
        'Keys use 2–12 uppercase letters, digits or underscores, starting with a letter.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId('create-project-submit')).toBeDisabled();

    await user.clear(screen.getByTestId('create-project-key'));
    await user.type(screen.getByTestId('create-project-key'), 'WEB');
    expect(screen.getByText('Looks good — this key is valid.')).toBeInTheDocument();
    expect(screen.getByTestId('create-project-submit')).toBeEnabled();
  });

  it('名称为空时不可提交,对话框经 Cancel 关闭', async () => {
    const user = userEvent.setup();
    await openDialog();

    expect(screen.getByTestId('create-project-submit')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('提交成功:POST /workspaces/{ws}/projects + toast + 列表重载', async () => {
    const user = userEvent.setup();
    const calls = await openDialog();

    await user.type(screen.getByTestId('create-project-name'), 'Website Redesign');
    await user.selectOptions(screen.getByTestId('create-project-visibility'), 'private');
    // type=date 输入经 change 事件设值(jsdom 对逐字符输入做值消毒)
    fireEvent.change(screen.getByTestId('create-project-target-date'), {
      target: { value: '2026-12-31' },
    });
    await user.click(screen.getByTestId('create-project-submit'));

    await waitFor(() =>
      expect(
        calls.some((c) => c.init?.method === 'POST' && c.url.includes('/workspaces/ws-1/projects')),
      ).toBe(true),
    );
    const post = calls.find((c) => c.init?.method === 'POST');
    const body = String(post?.init?.body);
    expect(body).toContain('"name":"Website Redesign"');
    expect(body).toContain('"key":"WEBSITE_REDE"');
    expect(body).toContain('"visibility":"private"');
    expect(body).toContain('"target_date":"2026-12-31"');

    expect(await screen.findByText('Project created.')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // onCreated → reloadKey++ → 列表二次拉取
    await waitFor(() => expect(listCalls(calls).length).toBeGreaterThanOrEqual(2));
  });

  it('409 project_key_taken 就地内联错误,不关闭对话框', async () => {
    const user = userEvent.setup();
    const calls = stub([PROJECT_A], {
      createStatus: 409,
      createError: { code: 'project_key_taken', message: 'taken' },
    });
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await screen.findByText('Apollo');
    await user.click(screen.getByTestId('new-project-button'));
    await screen.findByRole('dialog', { name: 'New project' });

    await user.type(screen.getByTestId('create-project-name'), 'Website Redesign');
    await user.click(screen.getByTestId('create-project-submit'));

    expect(await screen.findByTestId('create-project-error')).toHaveTextContent(
      'That project key is already taken in this workspace',
    );
    expect(screen.getByRole('dialog', { name: 'New project' })).toBeInTheDocument();
    expect(listCalls(calls).length).toBe(1);
  });
});

describe('CreateProjectDialog(独立渲染:client 注入)', () => {
  it('描述为空时省略 description 字段,仅提交必填项', async () => {
    const user = userEvent.setup();
    const calls: RecordedCall[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return fakeResponse({
        status: 201,
        body: { data: { ...PROJECT_A, id: 'prj-new', name: 'Tiny', key: 'TNY' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);

    const client = new MeshApiClient({ baseUrl: 'http://127.0.0.1:8901', getToken: () => null });
    renderWithProviders(
      <CreateProjectDialog
        open
        onClose={() => undefined}
        client={client}
        workspaceId="ws-1"
        onCreated={() => undefined}
      />,
    );

    await user.type(screen.getByTestId('create-project-name'), 'Tiny');
    await user.click(screen.getByTestId('create-project-submit'));

    await waitFor(() => expect(calls.some((c) => c.init?.method === 'POST')).toBe(true));
    const body = String(calls.find((c) => c.init?.method === 'POST')?.init?.body);
    expect(body).toContain('"name":"Tiny"');
    expect(body).toContain('"key":"TINY"');
    expect(body).not.toContain('description');
  });

  it('新建失败(网络错误)就地呈现 unknownError 文案', async () => {
    stub([], { createRejects: true });
    const user = userEvent.setup();
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await user.click(await screen.findByTestId('new-project-button'));
    const nameInput = await screen.findByLabelText('Name');
    await user.type(nameInput, 'Broken');
    await user.click(screen.getByTestId('create-project-submit'));
    expect(
      await screen.findByText('Network error. Please check your connection and try again.'),
    ).toBeDefined();
  });

  it('名称为空时提交被禁用,不发请求', async () => {
    const calls = stub([]);
    const user = userEvent.setup();
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await user.click(await screen.findByTestId('new-project-button'));
    const submit = await screen.findByTestId('create-project-submit');
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    expect(
      calls.filter((c) => (c.init?.method ?? 'GET') === 'POST' && c.url.includes('/projects')),
    ).toHaveLength(0);
  });

  it('卡片渲染项目 icon 与主题色色块(§4)', async () => {
    stub([
      { ...PROJECT_A, id: 'prj-9', icon: '🚀', color: '#ff0044' },
    ] as unknown as readonly ListProjectsProject[]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    expect(await screen.findByTestId('project-card-prj-9')).toBeDefined();
    expect(screen.getByTestId('project-icon-prj-9').textContent).toBe('🚀');
    expect(screen.getByTestId('project-color-prj-9').style.background).not.toBe('');
  });

  it('状态筛选重置为 All 时移除 URL 参数并重拉全量列表', async () => {
    const calls = stub([PROJECT_A, PROJECT_B] as unknown as readonly ListProjectsProject[]);
    const user = userEvent.setup();
    renderWithProviders(<ProjectsPage />, { route: '/projects?status=active' });
    await screen.findByTestId('project-card-prj-1');
    expect(screen.queryByTestId('project-card-prj-2')).toBeNull();

    await user.selectOptions(screen.getByTestId('projects-status-filter'), 'all');

    await waitFor(() => {
      expect(listCalls(calls).some((c) => !new URL(c.url).searchParams.has('status'))).toBe(true);
    });
    expect(await screen.findByTestId('project-card-prj-2')).toBeDefined();
  });

  it('卡片健康度灯打开页面级更新对话框,留痕成功后重载列表并关闭', async () => {
    const calls = stub([PROJECT_A] as unknown as readonly ListProjectsProject[]);
    const user = userEvent.setup();
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    await screen.findByTestId('project-card-prj-1');
    const listCountBefore = listCalls(calls).length;

    const card = screen.getByTestId('project-card-prj-1');
    expect(card.querySelector('[role="presentation"]')).toBeNull();
    const healthButton = within(card).getByRole('button', { name: 'Update status' });
    expect(healthButton).toHaveAttribute('data-testid', 'project-health-prj-1');
    await user.click(healthButton);
    expect(await screen.findByTestId('health-update-form')).toBeDefined();

    await user.click(screen.getByTestId('health-update-submit'));

    expect(await screen.findByText('Status update posted.')).toBeDefined();
    await waitFor(() => expect(listCalls(calls).length).toBeGreaterThan(listCountBefore));
    await waitFor(() => expect(screen.queryByText('Update status')).toBeNull());
  });
});
