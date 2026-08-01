/**
 * ProjectDetailPage 及其面板/对话框组件测试(project.md §4.1/§4.2/§4.3)。
 * 头部(状态/健康度/进度)、Tab 切换(概览/里程碑/更新动态)、健康度留痕对话框、
 * 里程碑创建/开合/删除(二次确认)、归档切换、删除(二次确认 + 回列表)、错误态。
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
import { ProjectDetailPage } from '../ProjectDetailPage';

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

const MILESTONE_OPEN = {
  id: 'ms-1',
  project_id: 'prj-1',
  title: 'GA launch',
  description: null,
  target_date: '2026-09-30',
  state: 'open',
  overdue: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
};

const MILESTONE_OVERDUE = {
  ...MILESTONE_OPEN,
  id: 'ms-2',
  title: 'Beta',
  target_date: '2026-01-15',
  overdue: true,
};

const UPDATE_1 = {
  id: 'upd-1',
  project_id: 'prj-1',
  author: { id: 'mem-lead', name: 'Jane Doe', member_type: 'human' },
  health: 'at_risk',
  status: null,
  message: 'vendor slipped',
  created_at: '2026-07-20T10:00:00Z',
};

function makeProject(overrides: Record<string, unknown> = {}) {
  return {
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
    milestones: [MILESTONE_OPEN, MILESTONE_OVERDUE],
    ...overrides,
  };
}

interface StubOptions {
  readonly project?: ReturnType<typeof makeProject> | null;
  readonly projectStatus?: number;
  readonly updates?: readonly unknown[];
  readonly archiveStatus?: number;
  readonly deleteStatus?: number;
}

function stubFetch(opts: StubOptions = {}) {
  const calls: RecordedCall[] = [];
  let project = opts.project === undefined ? makeProject() : opts.project;
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, init });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (method === 'GET' && url.includes('/updates')) {
      return fakeResponse({
        body: { data: opts.updates ?? [UPDATE_1], next_cursor: null },
      });
    }
    if (method === 'POST' && url.includes('/updates')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      // 服务端把 health 回写到 project;后续 GET 反映新值
      if (project !== null && typeof body.health === 'string') {
        project = { ...project, health: body.health };
      }
      return fakeResponse({
        status: 201,
        body: { data: { ...UPDATE_1, id: 'upd-new', ...body } },
      });
    }
    if (method === 'POST' && url.includes('/milestones')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return fakeResponse({
        status: 201,
        body: { data: { ...MILESTONE_OPEN, id: 'ms-new', ...body } },
      });
    }
    if (method === 'PATCH' && url.includes('/milestones/')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return fakeResponse({ body: { data: { ...MILESTONE_OPEN, ...body } } });
    }
    if (method === 'DELETE' && url.includes('/milestones/')) {
      return fakeResponse({ body: { data: { id: 'ms-1', deleted: true } } });
    }
    if (method === 'POST' && url.includes('/archive')) {
      return fakeResponse({
        status: opts.archiveStatus ?? 200,
        body:
          (opts.archiveStatus ?? 200) >= 400
            ? { error: { code: 'project_archived', message: 'archived' } }
            : { data: makeProject({ archived: true }) },
      });
    }
    if (method === 'DELETE' && url.match(/\/projects\/[^/]+$/)) {
      return fakeResponse({
        status: opts.deleteStatus ?? 200,
        body: { data: { id: 'prj-1', deleted: true } },
      });
    }
    if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
      if (project === null || (opts.projectStatus ?? 200) >= 400) {
        return fakeResponse({
          status: opts.projectStatus ?? 404,
          body: { error: { code: 'not_found', message: 'not found' } },
        });
      }
      return fakeResponse({ body: { data: project } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderDetail(): void {
  renderWithProviders(
    <Routes>
      <Route path="/projects" element={<div data-testid="projects-list-page" />} />
      <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
    </Routes>,
    { route: '/projects/prj-1' },
  );
}

const callsTo = (calls: RecordedCall[], method: string, needle: string): RecordedCall[] =>
  calls.filter((c) => (c.init?.method ?? 'GET') === method && c.url.includes(needle));

describe('ProjectDetailPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders header with name, status badge, health and progress', async () => {
    stubFetch();
    renderDetail();
    expect(await screen.findByText('Apollo')).toBeDefined();
    expect(screen.getByText('Active')).toBeDefined();
    expect(screen.getByText('On track')).toBeDefined();
    expect(screen.getByText('Moon landing')).toBeDefined();
  });

  it('opens and closes project export and import dialogs', async () => {
    stubFetch();
    const user = userEvent.setup();
    renderDetail();
    await screen.findByText('Apollo');

    await user.click(screen.getByTestId('export-project-button'));
    const exportDialog = await screen.findByRole('dialog', { name: 'Export data' });
    await user.click(within(exportDialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Export data' })).toBeNull());

    await user.click(screen.getByTestId('import-project-button'));
    const importDialog = await screen.findByRole('dialog', { name: 'Import data' });
    await user.click(within(importDialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Import data' })).toBeNull());
  });

  it('shows milestones with overdue marking on the milestones tab', async () => {
    stubFetch();
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('tab-milestones'));
    expect(await screen.findByTestId('milestone-list')).toBeDefined();
    expect(screen.getByText('GA launch')).toBeDefined();
    expect(screen.getByText('Beta')).toBeDefined();
    // Overdue badge (派生态:open + 过期 target_date)
    const overdueRow = screen.getByTestId('milestone-ms-2');
    expect(within(overdueRow).getByText(/Overdue/)).toBeDefined();
  });

  it('creates a milestone via the dialog', async () => {
    const calls = stubFetch();
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('tab-milestones'));
    await user.click(await screen.findByTestId('create-milestone-button'));
    await user.type(screen.getByTestId('milestone-title-input'), 'v2.0');
    await user.click(screen.getByTestId('create-milestone-submit'));
    await waitFor(() => {
      expect(callsTo(calls, 'POST', '/milestones').length).toBe(1);
    });
    expect(await screen.findByText('v2.0')).toBeDefined();
  });

  it('toggles milestone state and deletes with confirmation', async () => {
    const calls = stubFetch();
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('tab-milestones'));
    // 开合:open → PATCH state=closed
    await user.click(await screen.findByTestId('milestone-toggle-ms-1'));
    await waitFor(() => {
      const patches = callsTo(calls, 'PATCH', '/milestones/ms-1');
      expect(patches.length).toBe(1);
      expect(String(patches[0].init?.body)).toContain('"state":"closed"');
    });
    // 删除:二次确认后 DELETE
    await user.click(screen.getByTestId('milestone-delete-ms-1'));
    expect(await screen.findByTestId('milestone-delete-confirm-text')).toBeDefined();
    await user.click(screen.getByTestId('milestone-delete-confirm'));
    await waitFor(() => {
      expect(callsTo(calls, 'DELETE', '/milestones/ms-1').length).toBe(1);
    });
  });

  it('posts a health update via the dialog and writes it back', async () => {
    const calls = stubFetch();
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('update-status-button'));
    const dialog = await screen.findByTestId('health-update-form');
    await user.selectOptions(within(dialog).getByTestId('health-select'), 'off_track');
    await user.click(within(dialog).getByTestId('health-update-submit'));
    await waitFor(() => {
      const posts = callsTo(calls, 'POST', '/updates');
      expect(posts.length).toBe(1);
      expect(String(posts[0].init?.body)).toContain('"health":"off_track"');
    });
    expect(await screen.findByText('Off track')).toBeDefined();
  });

  it('posts an update from the updates tab form', async () => {
    const calls = stubFetch();
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('tab-updates'));
    expect(await screen.findByTestId('update-list')).toBeDefined();
    expect(screen.getByText('vendor slipped')).toBeDefined();
    await user.type(screen.getByLabelText('What changed?'), 'mitigation planned');
    await user.click(screen.getByTestId('update-submit'));
    await waitFor(() => {
      expect(callsTo(calls, 'POST', '/updates').length).toBe(1);
    });
  });

  it('archives and unarchives via the header toggle', async () => {
    const calls = stubFetch();
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('archive-toggle-button'));
    await waitFor(() => {
      expect(callsTo(calls, 'POST', '/archive').length).toBe(1);
    });
    // 归档后按钮变为取消归档
    expect(await screen.findByText('Unarchive')).toBeDefined();
  });

  it('deletes the project after confirmation and navigates to the list', async () => {
    const calls = stubFetch();
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('delete-project-button'));
    expect(await screen.findByTestId('delete-confirm-text')).toBeDefined();
    await user.click(screen.getByTestId('delete-confirm'));
    await waitFor(() => {
      expect(callsTo(calls, 'DELETE', '/projects/prj-1').length).toBe(1);
    });
    // 删除后路由回 /projects(占位列表页)
    expect(await screen.findByTestId('projects-list-page')).toBeDefined();
  });

  it('renders the error state when the project cannot be loaded', async () => {
    stubFetch({ project: null, projectStatus: 404 });
    renderDetail();
    expect(await screen.findByText('Something went wrong')).toBeDefined();
  });

  it('renders the no-workspace empty state for users without memberships', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: { ...ME, memberships: [] } } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderDetail();
    expect(await screen.findByText('You are not a member of any workspace yet.')).toBeDefined();
  });

  it('shows the dialog error when posting a health update fails', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && url.includes('/updates')) {
        return fakeResponse({
          status: 422,
          body: { error: { code: 'project_archived', message: 'archived' } },
        });
      }
      if (method === 'GET' && url.includes('/updates')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({ body: { data: makeProject() } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('update-status-button'));
    const dialog = await screen.findByTestId('health-update-form');
    await user.selectOptions(within(dialog).getByTestId('health-select'), 'at_risk');
    await user.click(within(dialog).getByTestId('health-update-submit'));
    expect(await screen.findByTestId('health-update-error')).toBeDefined();
  });

  it('surfaces errors from milestone create, toggle and delete', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'GET' && url.includes('/updates')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (method !== 'GET' && url.includes('/milestones')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({ body: { data: makeProject() } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('tab-milestones'));
    // 创建失败 → 对话框内错误
    await user.click(await screen.findByTestId('create-milestone-button'));
    await user.type(screen.getByTestId('milestone-title-input'), 'Doomed');
    await user.click(screen.getByTestId('create-milestone-submit'));
    expect(await screen.findByTestId('create-milestone-error')).toBeDefined();
    await user.keyboard('{Escape}');
    // 开合失败 → danger toast
    await user.click(await screen.findByTestId('milestone-toggle-ms-1'));
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
    // 删除失败 → danger toast
    await user.click(screen.getByTestId('milestone-delete-ms-1'));
    await user.click(await screen.findByTestId('milestone-delete-confirm'));
    await waitFor(() => {
      expect(
        screen.getAllByText('An internal error occurred. Please try again.').length,
      ).toBeGreaterThan(0);
    });
  });

  it('shows the update submit error when posting an update fails', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && url.includes('/updates')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (method === 'GET' && url.includes('/updates')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (method === 'GET' && url.match(/\/projects\/[^/]+$/)) {
        return fakeResponse({ body: { data: makeProject() } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('tab-updates'));
    await user.type(await screen.findByLabelText('What changed?'), 'note');
    await user.click(screen.getByTestId('update-submit'));
    expect(await screen.findByTestId('update-submit-error')).toBeDefined();
  });

  it('renders placeholders for a project without lead, description or milestones', async () => {
    stubFetch({
      project: makeProject({ lead: null, lead_member_id: null, description: null, milestones: [] }),
    });
    renderDetail();
    expect(await screen.findByText('No description yet.')).toBeDefined();
    await screen.findByText('No milestones yet.');
  });

  it('keeps the delete failure visible without navigating', async () => {
    stubFetch({ deleteStatus: 500 });
    renderDetail();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('delete-project-button'));
    await user.click(await screen.findByTestId('delete-confirm'));
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
    expect(screen.queryByTestId('projects-list-page')).toBeNull();
  });

  it('渲染项目 icon 与色块;健康度灯可点击打开更新对话框', async () => {
    stubFetch({ project: makeProject({ icon: '🛰️', color: '#00ff88' }) });
    const user = userEvent.setup();
    renderDetail();
    await screen.findByTestId('project-detail-header');

    expect(screen.getByTestId('project-icon').textContent).toBe('🛰️');
    expect(screen.getByTestId('project-color')).toBeDefined();

    await user.click(screen.getByTestId('health-light-button'));
    expect(await screen.findByTestId('health-update-form')).toBeDefined();
  });

  it('从里程碑 Tab 点回概览 Tab 移除 tab 参数', async () => {
    stubFetch();
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/projects" element={<div data-testid="projects-list-page" />} />
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
      </Routes>,
      { route: '/projects/prj-1?tab=milestones' },
    );
    await screen.findByTestId('project-detail-header');
    expect(screen.getByTestId('create-milestone-button')).toBeDefined();

    await user.click(screen.getByTestId('tab-overview'));

    await waitFor(() => expect(screen.queryByTestId('create-milestone-button')).toBeNull());
  });

  it('概览 Tab 里程碑:有目标日渲染日期,无目标日省略', async () => {
    stubFetch({
      project: makeProject({
        milestones: [
          MILESTONE_OPEN,
          { ...MILESTONE_OPEN, id: 'ms-3', title: 'No date', target_date: null, overdue: false },
        ],
      }),
    });
    const { container } = renderWithProviders(
      <Routes>
        <Route path="/projects" element={<div data-testid="projects-list-page" />} />
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
      </Routes>,
      { route: '/projects/prj-1' },
    );
    await screen.findByTestId('project-detail-header');

    const rows = container.querySelectorAll('.mesh-projects__milestone');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('2026-09-30');
    expect(rows[1].textContent).not.toContain('2026-09-30');
  });

  it('已归档项目头部按钮为 Unarchive 并调用 unarchive 端点', async () => {
    const calls = stubFetch({
      project: makeProject({ archived: true, archived_at: '2026-06-01T00:00:00Z' }),
    });
    const user = userEvent.setup();
    renderDetail();
    const toggle = await screen.findByTestId('archive-toggle-button');
    expect(toggle.textContent).toBe('Unarchive');

    await user.click(toggle);

    await waitFor(() => expect(callsTo(calls, 'POST', '/unarchive').length).toBe(1));
  });

  it('删除二次确认对话框可取消且不发起 DELETE', async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    renderDetail();
    await screen.findByTestId('project-detail-header');

    await user.click(screen.getByTestId('delete-project-button'));
    expect(screen.getByTestId('delete-confirm-text')).toBeDefined();
    await user.click(screen.getByText('Cancel'));

    expect(screen.queryByTestId('delete-confirm')).toBeNull();
    expect(callsTo(calls, 'DELETE', '/projects/').length).toBe(0);
  });
});

// ---- 实时帧合并(project:{id} 频道,§3.5/§6.7)----

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

function ToastLayer(props: { children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

interface FakeRealtime {
  readonly value: RealtimeContextValue;
  readonly client: { subscribe: ReturnType<typeof vi.fn>; unsubscribe: ReturnType<typeof vi.fn> };
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
  return {
    value: { state: 'connected', client: client as unknown as RealtimeClient },
    client,
    emit: (frame) => {
      for (const listener of listeners) listener(frame);
    },
  };
}

function renderDetailWithRealtime(realtime: FakeRealtime): void {
  render(
    <MemoryRouter initialEntries={['/projects/prj-1']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime.value}>
              <Routes>
                <Route path="/projects" element={<div data-testid="projects-list-page" />} />
                <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
              </Routes>
            </RealtimeContext.Provider>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('ProjectDetailPage 实时帧合并(MES-30 覆盖加固)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('订阅 project:{id} 频道,project.updated 帧合并头部字段', async () => {
    stubFetch();
    const realtime = makeFakeRealtime();
    renderDetailWithRealtime(realtime);
    await screen.findByTestId('project-detail-header');
    expect(realtime.client.subscribe).toHaveBeenCalledWith('project:prj-1');

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'project:prj-1',
        seq: 1,
        event: 'project.updated',
        payload: { id: 'prj-1', name: 'Apollo Renamed', updated_at: '2026-07-02T00:00:00Z' },
      });
    });

    expect(await screen.findByText('Apollo Renamed')).toBeDefined();
  });

  it('project.archived 帧把头部切为 Unarchive', async () => {
    stubFetch();
    const realtime = makeFakeRealtime();
    renderDetailWithRealtime(realtime);
    await screen.findByTestId('project-detail-header');

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'project:prj-1',
        seq: 2,
        event: 'project.archived',
        payload: {},
      });
    });

    await waitFor(() =>
      expect(screen.getByTestId('archive-toggle-button').textContent).toBe('Unarchive'),
    );
  });

  it('project.deleted 帧导航回项目列表', async () => {
    stubFetch();
    const realtime = makeFakeRealtime();
    renderDetailWithRealtime(realtime);
    await screen.findByTestId('project-detail-header');

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'project:prj-1',
        seq: 3,
        event: 'project.deleted',
        payload: {},
      });
    });

    expect(await screen.findByTestId('projects-list-page')).toBeDefined();
  });

  it('milestone.created 帧并入概览里程碑列表', async () => {
    stubFetch();
    const realtime = makeFakeRealtime();
    renderDetailWithRealtime(realtime);
    await screen.findByTestId('project-detail-header');

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'project:prj-1',
        seq: 4,
        event: 'milestone.created',
        payload: {
          milestone: { ...MILESTONE_OPEN, id: 'ms-rt', title: 'Realtime Milestone' },
        },
      });
    });

    expect(await screen.findByText('Realtime Milestone')).toBeDefined();
  });

  it('project_update.added 帧并入更新动态 Tab', async () => {
    stubFetch();
    const realtime = makeFakeRealtime();
    const user = userEvent.setup();
    renderDetailWithRealtime(realtime);
    await screen.findByTestId('project-detail-header');
    await user.click(screen.getByTestId('tab-updates'));

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'project:prj-1',
        seq: 5,
        event: 'project_update.added',
        payload: { update: { ...UPDATE_1, id: 'upd-rt', message: 'realtime note' } },
      });
    });

    expect(await screen.findByText('realtime note')).toBeDefined();
  });
});

describe('ProjectDetailPage 加载竞态守卫(MES-30 覆盖加固)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('组件卸载后到达的加载结果被丢弃(cancelled 守卫)', async () => {
    // 场景一:成功结果在卸载后到达
    let resolveProject: (response: Response) => void = () => undefined;
    const pendingOk = new Promise<Response>((resolve) => {
      resolveProject = resolve;
    });
    const implOk = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/updates')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return pendingOk;
    }) as typeof fetch;
    vi.stubGlobal('fetch', implOk);
    const first = renderWithProviders(
      <Routes>
        <Route path="/projects" element={<div data-testid="projects-list-page" />} />
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
      </Routes>,
      { route: '/projects/prj-1' },
    );
    await screen.findByText('Loading…');
    first.unmount();
    await act(async () => {
      resolveProject(fakeResponse({ body: { data: makeProject() } }));
    });
    expect(first.container.innerHTML).toBe('');

    // 场景二:失败结果在卸载后到达
    let rejectProject: (err: Error) => void = () => undefined;
    const pendingErr = new Promise<Response>((_, reject) => {
      rejectProject = reject;
    });
    // 防 flake:reject 触发的瞬间组件的 fetch 链可能尚未挂上 handler
    // (并行调度竞态),无兜底消费者会把本测试的有意延迟 reject 记成
    // unhandled rejection 拖红整套件;兜底消费者不影响组件自身的 catch 链。
    pendingErr.catch(() => undefined);
    const implErr = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/updates')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return pendingErr;
    }) as typeof fetch;
    vi.stubGlobal('fetch', implErr);
    const second = renderWithProviders(
      <Routes>
        <Route path="/projects" element={<div data-testid="projects-list-page" />} />
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
      </Routes>,
      { route: '/projects/prj-1' },
    );
    await screen.findByText('Loading…');
    second.unmount();
    await act(async () => {
      rejectProject(new Error('late failure'));
      await Promise.resolve();
    });
    expect(second.container.innerHTML).toBe('');
  });
});
