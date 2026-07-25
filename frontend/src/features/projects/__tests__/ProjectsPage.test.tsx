/**
 * ProjectsPage + CreateProjectDialog 组件测试(project.md §4.1/§4.3)。
 * 以 fetch 桩驱动:卡片网格渲染(名称/状态徽章/健康度/进度/负责人/目标日)、筛选
 * (状态/已归档/我参与的,URL 同源)、Load more 游标分页、错误态重试、无工作区空态、
 * 实时列表帧合并;新建对话框:key 自动建议 + 客户端格式校验 + 409 内联错误。
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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
}

function makeFetch(projects: readonly ListProjectsProject[], opts: FetchOptions = {}) {
  const calls: RecordedCall[] = [];
  let listFailures = opts.failListOnce ? 1 : 0;
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, init });
    if (url.includes('/users/me')) {
      return fakeResponse({ body: { data: ME } });
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

  it('渲染项目卡片:名称/状态徽章/健康度/进度/负责人/目标日', async () => {
    stub([PROJECT_A, PROJECT_B]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Projects');

    const cardA = await screen.findByTestId('project-card-prj-1');
    expect(within(cardA).getByText('Apollo')).toBeInTheDocument();
    expect(within(cardA).getByText('Active')).toBeInTheDocument();
    expect(within(cardA).getByText('On track')).toBeInTheDocument();
    expect(within(cardA).getByText('Jane Doe')).toBeInTheDocument();
    expect(within(cardA).getByRole('progressbar', { name: '5/10 done' })).toBeInTheDocument();
    expect(screen.getByTestId('project-date-prj-1')).toHaveTextContent('Due');

    const cardB = screen.getByTestId('project-card-prj-2');
    expect(within(cardB).getByText('Borealis')).toBeInTheDocument();
    expect(within(cardB).getByText('Planning')).toBeInTheDocument();
    expect(within(cardB).getByText('No health set')).toBeInTheDocument();
    expect(within(cardB).queryByText('Jane Doe')).not.toBeInTheDocument();
    expect(screen.queryByTestId('project-date-prj-2')).not.toBeInTheDocument();
  });

  it('无项目时显示空态', async () => {
    stub([]);
    renderWithProviders(<ProjectsPage />, { route: '/projects' });
    // 两段加载(fetchMe → workspace 就绪后二次拉取)会瞬时替换空态节点;
    // 以「终态独有的描述文案 + 标题」组合作为稳定判据。
    await waitFor(() => {
      expect(screen.getByText('No projects match the current filters.')).toBeInTheDocument();
      expect(screen.getByText('Nothing here yet')).toBeInTheDocument();
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

  it('Load more 失败时提示错误 toast', async () => {
    const user = userEvent.setup();
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
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

    expect(
      await screen.findByText('Something went wrong. Please try again.'),
    ).toBeInTheDocument();
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
        calls.some(
          (c) =>
            c.init?.method === 'POST' && c.url.includes('/workspaces/ws-1/projects'),
        ),
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
    expect(await screen.findByText('Network error. Please check your connection and try again.')).toBeDefined();
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
});
