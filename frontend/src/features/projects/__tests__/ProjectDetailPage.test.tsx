/**
 * ProjectDetailPage 及其面板/对话框组件测试(project.md §4.1/§4.2/§4.3)。
 * 头部(状态/健康度/进度)、Tab 切换(概览/里程碑/更新动态)、健康度留痕对话框、
 * 里程碑创建/开合/删除(二次确认)、归档切换、删除(二次确认 + 回列表)、错误态。
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import type { RecordedCall } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
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
    expect(
      await screen.findByText('You are not a member of any workspace yet.'),
    ).toBeDefined();
  });
});
