/**
 * HomePage — 真实首页 / 工作区仪表盘(MES-107):
 * me 加载三态(骨架/错误重试/问候)、工作区卡片与空态向导、
 * issue 仪表盘分页/快捷创建/实时增量合并(含归属过滤)、错误 toast。
 * 桩 client + mock fetch,不触真实网络。
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import type { RealtimeEventFrame } from '../../types/realtime';
import { MeshApiClient } from '../../api/client';
import { HomePage } from '../pages/HomePage';
import { RealtimeContext } from '../AppShell';
import type { RealtimeContextValue } from '../AppShell';

const ME_PATH = '/api/v1/users/me';
const ISSUES_PATH = '/api/v1/workspaces/ws-1/issues';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ME_BODY = {
  data: {
    user: { id: 'u-1', email: 'jane@corp.com', display_name: 'Jane' },
    memberships: [
      {
        workspace_id: 'ws-1',
        workspace_name: 'Acme',
        workspace_slug: 'acme',
        role: 'admin',
        status: 'default',
        joined_at: '2026-07-01T00:00:00.000Z',
      },
      {
        workspace_id: 'ws-2',
        workspace_name: 'Globex',
        workspace_slug: 'globex',
        role: 'member',
        status: 'default',
        joined_at: '2026-07-02T00:00:00.000Z',
      },
    ],
  },
};

/** 仪表盘行最小形态(真实 API 同名字段子集;归属按 workspace_id)。 */
function issue(id: number, title: string, workspaceId = 'ws-1'): Record<string, unknown> {
  return {
    id: 'id-' + String(id),
    workspace_id: workspaceId,
    identifier: 'MESH-' + String(id),
    title,
    state_category: 'todo',
    updated_at: '2026-07-25T08:0' + String(id) + ':00.000Z',
  };
}

interface StubClient {
  subscribe: ReturnType<typeof vi.fn>;
  unsubscribe: ReturnType<typeof vi.fn>;
  onFrame: ReturnType<typeof vi.fn>;
  emit: (frame: RealtimeEventFrame) => void;
}

function createStubClient(): StubClient {
  const frameListeners = new Set<(frame: RealtimeEventFrame) => void>();
  return {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((cb: (frame: RealtimeEventFrame) => void) => {
      frameListeners.add(cb);
      return () => {
        frameListeners.delete(cb);
      };
    }),
    emit: (frame: RealtimeEventFrame) => {
      for (const cb of frameListeners) cb(frame);
    },
  };
}

function renderHome(client: StubClient | null): ReturnType<typeof renderWithProviders> {
  if (client === null) return renderWithProviders(<HomePage />);
  const value: RealtimeContextValue = { state: 'connected', client: client as never };
  const ui: ReactElement = (
    <RealtimeContext.Provider value={value}>
      <HomePage />
    </RealtimeContext.Provider>
  );
  return renderWithProviders(ui);
}

/** Input 设计组件把 data-testid 透传给原生 input;取原生元素以便 fireEvent.change。 */
function nativeInput(testId: string): HTMLInputElement {
  const element = screen.getByTestId(testId);
  return (
    element instanceof HTMLInputElement ? element : element.querySelector('input')
  ) as HTMLInputElement;
}

describe('HomePage(me 三态)', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) {
        return Promise.resolve(jsonResponse(ME_BODY));
      }
      if (url.includes(ISSUES_PATH) && (init?.method ?? 'GET') === 'GET') {
        const cursor = new URL(url).searchParams.get('cursor');
        if (cursor === null) {
          return Promise.resolve(
            jsonResponse({
              data: [1, 2, 3, 4, 5].map((n) => issue(n, 'Issue ' + String(n))),
              next_cursor: 'c2',
            }),
          );
        }
        return Promise.resolve(
          jsonResponse({
            data: [6, 7].map((n) => issue(n, 'Issue ' + String(n))),
            next_cursor: null,
          }),
        );
      }
      if (url.includes(ISSUES_PATH) && init?.method === 'POST') {
        return Promise.resolve(jsonResponse({ data: issue(99, 'Created issue') }, 201));
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('首载呈现骨架,me 返回后渲染问候语与工作区卡片', async () => {
    renderHome(null);
    expect(screen.getByTestId('home-loading')).toBeDefined();
    await waitFor(() => expect(screen.getByTestId('home-greeting')).toBeDefined());
    expect(screen.getByTestId('home-greeting').textContent).toContain('Jane');
    const list = screen.getByTestId('home-workspace-list');
    expect(within(list).getAllByRole('listitem').length).toBe(2);
    const acme = screen.getByTestId('home-workspace-acme');
    expect(acme.textContent).toContain('Acme');
    expect(acme.textContent).toContain('Admin');
    expect(acme.querySelector('a')?.getAttribute('href')).toBe('/w/acme');
    expect(screen.getByTestId('home-workspace-globex').textContent).toContain('Member');
  });

  it('display_name 为空时问候语回退邮箱', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) {
        return Promise.resolve(
          jsonResponse({
            data: {
              user: { id: 'u-1', email: 'jane@corp.com', display_name: '' },
              memberships: [],
            },
          }),
        );
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    renderHome(null);
    await waitFor(() => expect(screen.getByTestId('home-greeting')).toBeDefined());
    expect(screen.getByTestId('home-greeting').textContent).toContain('jane@corp.com');
  });

  it('me 失败呈错误态;重试成功后渲染内容', async () => {
    let failOnce = true;
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) {
        if (failOnce) {
          failOnce = false;
          return Promise.resolve(
            jsonResponse({ error: { code: 'internal_error', message: 'boom' } }, 500),
          );
        }
        return Promise.resolve(jsonResponse(ME_BODY));
      }
      if (url.includes(ISSUES_PATH)) {
        return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    renderHome(null);
    await waitFor(() => expect(screen.getByTestId('home-error')).toBeDefined());
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.getByTestId('home-workspace-list')).toBeDefined());
  });

  it('无成员身份:空态 + 创建工作区入口打开向导', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) {
        return Promise.resolve(
          jsonResponse({
            data: {
              user: { id: 'u-1', email: 'jane@corp.com', display_name: 'Jane' },
              memberships: [],
            },
          }),
        );
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    renderHome(null);
    await waitFor(() => expect(screen.getByTestId('home-no-workspaces')).toBeDefined());
    expect(screen.queryByTestId('home-dashboard')).toBeNull();
    fireEvent.click(screen.getByTestId('home-create-workspace'));
    await waitFor(() => expect(screen.getByTestId('ws-wizard-name-input')).toBeDefined());
  });

  it('仪表盘:首屏 5 条 + 加载更多补齐(游标分页),行深链指向详情', async () => {
    renderHome(null);
    const list = await screen.findByTestId('home-issue-list');
    await waitFor(() => expect(within(list).getAllByRole('listitem').length).toBe(5));
    expect(within(list).getByTestId('home-issue-MESH-1').textContent).toContain('Issue 1');
    fireEvent.click(screen.getByTestId('home-load-more'));
    await waitFor(() => expect(within(list).getAllByRole('listitem').length).toBe(7));
    expect(screen.queryByTestId('home-load-more')).toBeNull();
    const link = within(screen.getByTestId('home-issue-MESH-2')).getByRole('link');
    expect(link.getAttribute('href')).toBe('/issues/id-2');
  });

  it('快捷创建:POST 后新行出现;空标题不发请求', async () => {
    renderHome(null);
    await screen.findByTestId('home-issue-list');
    fireEvent.click(screen.getByTestId('home-create'));
    const postBodies = (): string[] =>
      fetchMock.mock.calls
        .filter((call) => (call[1] as RequestInit | undefined)?.method === 'POST')
        .map((call) => String((call[1] as RequestInit).body));
    expect(postBodies().length).toBe(0);

    fireEvent.change(nativeInput('home-new-title'), { target: { value: 'Created issue' } });
    fireEvent.click(screen.getByTestId('home-create'));
    await waitFor(() => expect(screen.getByTestId('home-issue-MESH-99')).toBeDefined());
    expect(postBodies()).toEqual([JSON.stringify({ title: 'Created issue' })]);
  });

  it('创建失败弹出错误 toast', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
      if (url.includes(ISSUES_PATH) && (init?.method ?? 'GET') === 'GET') {
        return Promise.resolve(jsonResponse({ data: [issue(1, 'Issue 1')], next_cursor: null }));
      }
      if (url.includes(ISSUES_PATH) && init?.method === 'POST') {
        return Promise.resolve(
          jsonResponse({ error: { code: 'validation_error', message: 'bad' } }, 400),
        );
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    renderHome(null);
    await screen.findByTestId('home-issue-list');
    fireEvent.change(nativeInput('home-new-title'), { target: { value: 'boom' } });
    fireEvent.click(screen.getByTestId('home-create'));
    await waitFor(() =>
      expect(document.body.textContent?.toLowerCase()).toContain('some fields are invalid'),
    );
  });

  it('仪表盘首载失败呈错误态,重试后恢复', async () => {
    let failFeed = true;
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
      if (url.includes(ISSUES_PATH)) {
        if (failFeed) {
          failFeed = false;
          return Promise.resolve(
            jsonResponse({ error: { code: 'internal_error', message: 'boom' } }, 500),
          );
        }
        return Promise.resolve(jsonResponse({ data: [issue(1, 'Issue 1')], next_cursor: null }));
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    renderHome(null);
    const dashboard = await screen.findByTestId('home-dashboard');
    await waitFor(() =>
      expect(within(dashboard).getByRole('button', { name: /retry/i })).toBeDefined(),
    );
    fireEvent.click(within(dashboard).getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(within(dashboard).getByTestId('home-issue-MESH-1')).toBeDefined());
  });

  it('空工作区:仪表盘呈空态且无加载更多', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
      if (url.includes(ISSUES_PATH)) {
        return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    renderHome(null);
    const dashboard = await screen.findByTestId('home-dashboard');
    await waitFor(() => expect(dashboard.textContent).toContain('No issues yet'));
    expect(screen.queryByTestId('home-load-more')).toBeNull();
  });
});

describe('HomePage(实时增量合并)', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
      if (url.includes(ISSUES_PATH)) {
        return Promise.resolve(
          jsonResponse({ data: [issue(1, 'Issue 1'), issue(2, 'Issue 2')], next_cursor: null }),
        );
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('订阅 workspace 频道;created/updated/deleted 帧增量合并', async () => {
    const client = createStubClient();
    renderHome(client);
    await screen.findByTestId('home-issue-list');
    await waitFor(() => expect(client.subscribe).toHaveBeenCalledWith('workspace:ws-1:issues'));

    // created:嵌套 {issue} 载荷(后端同形)
    client.emit({
      op: 'event',
      channel: 'workspace:ws-1:issues',
      seq: 1,
      event: 'issue.created',
      payload: { issue: issue(3, 'Realtime new') },
    });
    await waitFor(() => expect(screen.getByTestId('home-issue-MESH-3')).toBeDefined());
    expect(screen.getByTestId('home-issue-MESH-3').textContent).toContain('Realtime new');

    // updated:扁平载荷就地合并
    client.emit({
      op: 'event',
      channel: 'workspace:ws-1:issues',
      seq: 2,
      event: 'issue.updated',
      payload: { id: 'id-1', title: 'Renamed by frame', updated_at: '2026-07-26T00:00:00.000Z' },
    });
    await waitFor(() =>
      expect(screen.getByTestId('home-issue-MESH-1').textContent).toContain('Renamed by frame'),
    );

    // deleted:行移除
    client.emit({
      op: 'event',
      channel: 'workspace:ws-1:issues',
      seq: 3,
      event: 'issue.deleted',
      payload: { id: 'id-2', updated_at: '2026-07-26T00:01:00.000Z' },
    });
    await waitFor(() => expect(screen.queryByTestId('home-issue-MESH-2')).toBeNull());
  });

  it('其他工作区的帧被归属过滤丢弃', async () => {
    const client = createStubClient();
    renderHome(client);
    await screen.findByTestId('home-issue-list');
    await waitFor(() => expect(client.subscribe).toHaveBeenCalled());
    client.emit({
      op: 'event',
      channel: 'workspace:ws-1:issues',
      seq: 1,
      event: 'issue.created',
      payload: { issue: issue(7, 'Foreign', 'ws-other') },
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByTestId('home-issue-MESH-7')).toBeNull();
  });

  it('卸载时取消订阅', async () => {
    const client = createStubClient();
    const result = renderHome(client);
    await screen.findByTestId('home-issue-list');
    await waitFor(() => expect(client.subscribe).toHaveBeenCalled());
    result.unmount();
    expect(client.unsubscribe).toHaveBeenCalledWith('workspace:ws-1:issues');
  });

  it('无 realtime 上下文时不订阅,仪表盘照常渲染', async () => {
    renderHome(null);
    await screen.findByTestId('home-issue-list');
    expect(screen.getByTestId('home-issue-MESH-1')).toBeDefined();
  });

  it('加载更多失败弹出错误 toast(列表不塌缩)', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
      if (url.includes(ISSUES_PATH) && (init?.method ?? 'GET') === 'GET') {
        if (new URL(url).searchParams.get('cursor') === null) {
          return Promise.resolve(jsonResponse({ data: [issue(1, 'Issue 1')], next_cursor: 'c2' }));
        }
        return Promise.resolve(
          jsonResponse({ error: { code: 'internal_error', message: 'boom' } }, 500),
        );
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    renderHome(null);
    await screen.findByTestId('home-issue-list');
    fireEvent.click(screen.getByTestId('home-load-more'));
    await waitFor(() =>
      expect(document.body.textContent?.toLowerCase()).toContain('an internal error occurred'),
    );
    expect(screen.getByTestId('home-issue-MESH-1')).toBeDefined();
  });

  it('创建响应晚于 created 帧到达时按 id 去重(不重复成行)', async () => {
    const client = createStubClient();
    renderHome(client);
    await screen.findByTestId('home-issue-list');
    await waitFor(() => expect(client.subscribe).toHaveBeenCalled());

    // 帧先到:仪表盘经实时合并出行
    client.emit({
      op: 'event',
      channel: 'workspace:ws-1:issues',
      seq: 1,
      event: 'issue.created',
      payload: { issue: issue(99, 'From frame') },
    });
    await waitFor(() => expect(screen.getByTestId('home-issue-MESH-99')).toBeDefined());

    // 再提交创建:POST 响应携带同一 id → 去重,不成双
    fireEvent.change(nativeInput('home-new-title'), { target: { value: 'Duplicate' } });
    fireEvent.click(screen.getByTestId('home-create'));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(2));
    expect(screen.getAllByTestId('home-issue-MESH-99').length).toBe(1);
  });
});

const PROJECTS_PATH = '/api/v1/workspaces/ws-1/projects';

function project(id: number, key: string, openIssues: number): Record<string, unknown> {
  return {
    id: 'proj-' + String(id),
    workspace_id: 'ws-1',
    name: 'Project ' + key,
    key,
    description: null,
    icon: null,
    color: null,
    status: 'active',
    health: null,
    visibility: 'workspace',
    lead: null,
    lead_member_id: null,
    start_date: null,
    target_date: null,
    progress: 0,
    open_issues: openIssues,
    done_issues: 0,
    issue_seq: 1,
    archived: false,
    archived_at: null,
    my_role: 'member',
    created_at: '2026-07-01T00:00:00.000Z',
    updated_at: '2026-07-25T00:00:00.000Z',
  };
}

/** 路由 ME / ISSUES / PROJECTS 的 fetch mock;projects 行为可配。 */
function routedFetch(projectsResponse: () => Response): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
    if (url.includes(PROJECTS_PATH)) return Promise.resolve(projectsResponse());
    if (url.includes(ISSUES_PATH) && (init?.method ?? 'GET') === 'GET') {
      return Promise.resolve(jsonResponse({ data: [issue(1, 'Issue 1')], next_cursor: null }));
    }
    return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
  });
}

describe('HomePage(最近项目小组件 + onboarding)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('有项目时渲染最近项目小组件,卡片深链指向项目详情', async () => {
    vi.stubGlobal(
      'fetch',
      routedFetch(() =>
        jsonResponse({ data: [project(1, 'ALPHA', 3), project(2, 'BETA', 0)], next_cursor: null }),
      ),
    );
    renderHome(null);
    const section = await screen.findByTestId('home-projects');
    expect(within(section).getByTestId('home-project-ALPHA').textContent).toContain(
      'Project ALPHA',
    );
    expect(within(section).getByTestId('home-project-ALPHA').textContent).toContain('3 open');
    const link = within(within(section).getByTestId('home-project-BETA')).getByRole('link');
    expect(link.getAttribute('href')).toBe('/projects/proj-2');
  });

  it('项目为空时不渲染小组件(有数据才呈现,无演示内容)', async () => {
    vi.stubGlobal(
      'fetch',
      routedFetch(() => jsonResponse({ data: [], next_cursor: null })),
    );
    renderHome(null);
    await screen.findByTestId('home-issue-list');
    expect(screen.queryByTestId('home-projects')).toBeNull();
  });

  it('项目加载失败安静隐藏小组件,不阻断工作台', async () => {
    vi.stubGlobal(
      'fetch',
      routedFetch(() => jsonResponse({ error: { code: 'internal_error', message: 'boom' } }, 500)),
    );
    renderHome(null);
    await screen.findByTestId('home-issue-list');
    expect(screen.queryByTestId('home-projects')).toBeNull();
    // 工作台其余部分照常:问候语与工作区列表仍在。
    expect(screen.getByTestId('home-greeting')).toBeDefined();
  });

  it('空工作区(无 issue)呈现 onboarding 区域', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
        if (url.includes(PROJECTS_PATH)) {
          return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
        }
        if (url.includes(ISSUES_PATH)) {
          return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
        }
        return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
      }),
    );
    renderHome(null);
    await waitFor(() => expect(screen.getByTestId('home-onboarding')).toBeDefined());
  });
});

const EXECUTIONS_PATH = '/api/v1/workspaces/ws-1/executions';
const APPROVALS_PATH = '/api/v1/workspaces/ws-1/approvals';

/** 执行行最小形态:与后端 `_render_execution` 同字段(无联表展示名,标签走 trigger+短ID)。 */
function execRow(id: number, status: string): Record<string, unknown> {
  return {
    id: 'exec-' + String(id),
    workspace_id: 'ws-1',
    agent_id: 'agent-1',
    issue_id: 'issue-' + String(id),
    trigger: 'assign',
    status,
    priority: 0,
    task_spec: { kind: 'issue_assignment', untrusted_context: {} },
    label_requirements: {},
    required_capabilities: [],
    config_snapshot: {},
    max_attempts: 3,
    queued_at: '2026-07-30T00:00:00.000Z',
    finished_at: null,
    timeout_seconds: 600,
    failure_reason: null,
    result: null,
    cancel_requested_at: null,
  };
}

function approvalRow(id: number, executionId: string | null): Record<string, unknown> {
  return {
    id: 'appr-' + String(id),
    subject_type: 'execution',
    subject_execution_id: executionId,
    subject_task_id: null,
    status: 'pending',
    action_summary: 'Approve deploy of MESH-' + String(id),
    requested_at: '2026-07-30T00:00:00.000Z',
    expires_at: '2026-07-31T00:00:00.000Z',
    decided_at: null,
    decision_comment: null,
    execution_status: 'awaiting_approval',
  };
}

describe('HomePage(等待确认 / AI 运行 小组件)', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  function install(
    executions: Record<string, unknown>[],
    approvals: Record<string, unknown>[],
  ): void {
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
      if (url.includes(ISSUES_PATH)) {
        return Promise.resolve(jsonResponse({ data: [issue(1, 'Issue 1')], next_cursor: null }));
      }
      if (url.includes(PROJECTS_PATH)) {
        return Promise.resolve(jsonResponse({ data: [], next_cursor: null }));
      }
      if (url.includes(EXECUTIONS_PATH)) {
        return Promise.resolve(jsonResponse({ data: executions, next_cursor: null }));
      }
      if (url.includes(APPROVALS_PATH)) {
        return Promise.resolve(jsonResponse({ data: approvals, next_cursor: null }));
      }
      return Promise.resolve(jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('有数据时渲染两块,过滤执行终态,行深链正确', async () => {
    install(
      [execRow(1, 'running'), execRow(2, 'awaiting_approval'), execRow(3, 'completed')],
      [approvalRow(7, 'exec-9')],
    );
    renderHome(null);

    const runs = await screen.findByTestId('home-ai-runs');
    // completed 被过滤,仅剩 running + awaiting_approval 两行。
    await waitFor(() => expect(within(runs).getAllByRole('listitem').length).toBe(2));
    // 标签由契约实际字段组成:trigger 文案 + 短 ID;元信息为状态 + 相对时间。
    const firstRow = within(runs).getByTestId('home-ai-run-exec-1').textContent ?? '';
    expect(firstRow).toMatch(/分派|Assign/);
    expect(firstRow).toContain('exec-1');
    // 两个分隔符:标签(trigger · 短ID)+ 元信息(状态 · 相对时间)。
    expect(firstRow.split(' · ').length - 1).toBe(2);
    const runLink = within(within(runs).getByTestId('home-ai-run-exec-1')).getByRole('link');
    expect(runLink.getAttribute('href')).toBe('/executions/exec-1');

    const waiting = screen.getByTestId('home-waiting');
    const waitingItem = within(waiting).getByTestId('home-waiting-appr-7');
    expect(waitingItem.textContent).toContain('Approve deploy of MESH-7');
    expect(within(waitingItem).getByRole('link').getAttribute('href')).toBe('/executions/exec-9');
  });

  it('审批无关联执行(subject_execution_id=null)时呈现为不可链接条目(per-file 分支门禁补测)', async () => {
    install([], [approvalRow(8, null)]);
    renderHome(null);
    const waiting = await screen.findByTestId('home-waiting');
    const item = within(waiting).getByTestId('home-waiting-appr-8');
    expect(item.textContent).toContain('Approve deploy of MESH-8');
    // 无执行关联 → 纯文本条目,不渲染深链
    expect(within(item).queryByRole('link')).toBeNull();
  });

  it('执行/审批为空时不渲染对应小组件(无演示内容)', async () => {
    install([], []);
    renderHome(null);
    await screen.findByTestId('home-issue-list');
    expect(screen.queryByTestId('home-ai-runs')).toBeNull();
    expect(screen.queryByTestId('home-waiting')).toBeNull();
    expect(screen.queryByTestId('home-projects')).toBeNull();
  });

  it('执行/审批接口失败安静隐藏,不阻断工作台', async () => {
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes(ME_PATH)) return Promise.resolve(jsonResponse(ME_BODY));
      if (url.includes(ISSUES_PATH)) {
        return Promise.resolve(jsonResponse({ data: [issue(1, 'Issue 1')], next_cursor: null }));
      }
      // projects/executions/approvals 全部 500。
      return Promise.resolve(
        jsonResponse({ error: { code: 'internal_error', message: 'boom' } }, 500),
      );
    });
    vi.stubGlobal('fetch', fetchMock);
    renderHome(null);
    await screen.findByTestId('home-issue-list');
    expect(screen.queryByTestId('home-ai-runs')).toBeNull();
    expect(screen.queryByTestId('home-waiting')).toBeNull();
    expect(screen.getByTestId('home-greeting')).toBeDefined();
  });
});

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('HomePage(错误分流与竞态守卫)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('me 请求为非 API 异常 → 呈现网络错误文案', async () => {
    const client = { request: vi.fn().mockRejectedValue(new Error('boom')) };
    renderWithProviders(<HomePage client={client as never} />);

    await waitFor(() => expect(screen.getByTestId('home-error')).toBeDefined());
    expect(screen.getByTestId('home-error').textContent).toContain('Network error');
  });

  it('me 请求在卸载后才被拒绝 → 不向已拆除的渲染树派发错误态', async () => {
    const pending = createDeferred<Response>();
    const fetchMock = vi.fn(() => pending.promise);
    vi.stubGlobal('fetch', fetchMock);

    const result = renderWithProviders(<HomePage />);
    expect(screen.getByTestId('home-loading')).toBeDefined();
    result.unmount();

    await act(async () => {
      pending.reject(new TypeError('Failed to fetch'));
      await Promise.resolve();
    });
    expect(document.body.textContent).not.toContain('Retry');
  });

  it('仪表盘首载在卸载后才落定(resolve)→ 过期响应被丢弃', async () => {
    const pending = createDeferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(ME_PATH)) return jsonResponse(ME_BODY);
      if (url.includes(ISSUES_PATH)) return pending.promise;
      return jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = renderWithProviders(<HomePage />);
    await waitFor(() => expect(screen.getByTestId('home-dashboard')).toBeDefined());
    result.unmount();

    await act(async () => {
      pending.resolve(jsonResponse({ data: [issue(1, 'Late arrival')], next_cursor: null }));
      await Promise.resolve();
    });
    expect(document.body.textContent).not.toContain('Late arrival');
  });

  it('仪表盘首载在卸载后才被拒绝 → 不派发错误态', async () => {
    const pending = createDeferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(ME_PATH)) return jsonResponse(ME_BODY);
      if (url.includes(ISSUES_PATH)) return pending.promise;
      return jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = renderWithProviders(<HomePage />);
    await waitFor(() => expect(screen.getByTestId('home-dashboard')).toBeDefined());
    result.unmount();

    await act(async () => {
      pending.reject(new TypeError('Failed to fetch'));
      await Promise.resolve();
    });
    expect(document.body.textContent).not.toContain('Retry');
  });

  it('仪表盘首载为非 API 异常 → 区内呈现网络错误文案', async () => {
    const client = {
      request: vi.fn().mockResolvedValue(ME_BODY.data),
      list: vi.fn().mockRejectedValue(new Error('boom')),
    };
    renderWithProviders(<HomePage client={client as never} />);

    const dashboard = await screen.findByTestId('home-dashboard');
    await waitFor(() => expect(dashboard.textContent).toContain('Network error'));
  });

  it('首载未完成时到达的实时帧被丢弃(初始 null 守卫),随后首载照常渲染', async () => {
    const pending = createDeferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(ME_PATH)) return jsonResponse(ME_BODY);
      if (url.includes(ISSUES_PATH)) return pending.promise;
      return jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);
    const client = createStubClient();
    renderHome(client);

    await waitFor(() => expect(client.subscribe).toHaveBeenCalledWith('workspace:ws-1:issues'));
    // issues 仍为 null(首载未完成):帧到达不得报错、不得成行
    client.emit({
      op: 'event',
      channel: 'workspace:ws-1:issues',
      seq: 1,
      event: 'issue.created',
      payload: { issue: issue(50, 'Early frame') },
    });

    await act(async () => {
      pending.resolve(jsonResponse({ data: [issue(1, 'Issue 1')], next_cursor: null }));
    });
    await waitFor(() => expect(screen.getByTestId('home-issue-MESH-1')).toBeDefined());
    expect(screen.queryByTestId('home-issue-MESH-50')).toBeNull();
  });

  it('快捷创建失败为非 API 异常 → 通用错误 toast', async () => {
    const client = {
      request: vi.fn(async (method: string, path: string) => {
        if (method === 'POST' && path.includes(ISSUES_PATH)) {
          throw new Error('boom');
        }
        return ME_BODY.data;
      }),
      list: vi.fn().mockResolvedValue({ data: [issue(1, 'Issue 1')], next_cursor: null }),
    };
    renderWithProviders(<HomePage client={client as never} />);
    await screen.findByTestId('home-issue-list');

    fireEvent.change(nativeInput('home-new-title'), { target: { value: 'boom' } });
    fireEvent.click(screen.getByTestId('home-create'));

    await waitFor(() =>
      expect(document.body.textContent).toContain('An unexpected error occurred'),
    );
  });

  it('创建响应携带列表中已有 id(无帧先到)→ 按 id 去重不重复成行', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes(ME_PATH)) return jsonResponse(ME_BODY);
      if (url.includes(ISSUES_PATH) && init?.method === 'POST') {
        // 与首屏 id-3 同 id:模拟 created 帧已先行合并后的重复响应
        return jsonResponse({ data: issue(3, 'Duplicate of 3') }, 201);
      }
      if (url.includes(ISSUES_PATH)) {
        return jsonResponse({
          data: [1, 2, 3, 4, 5].map((n) => issue(n, 'Issue ' + String(n))),
          next_cursor: null,
        });
      }
      return jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWithProviders(<HomePage />);
    const list = await screen.findByTestId('home-issue-list');
    await waitFor(() => expect(within(list).getAllByRole('listitem').length).toBe(5));

    fireEvent.change(nativeInput('home-new-title'), { target: { value: 'Duplicate' } });
    fireEvent.click(screen.getByTestId('home-create'));
    // 输入框清空 = 创建成功路径已跑完
    await waitFor(() => expect(nativeInput('home-new-title').value).toBe(''));
    expect(screen.getAllByTestId('home-issue-MESH-3').length).toBe(1);
    expect(within(list).getAllByRole('listitem').length).toBe(5);
  });

  it('client 实例切换后旧实例的过期分页响应被丢弃(初始 null 守卫)', async () => {
    const deferredOldPage = createDeferred<Response>();
    const deferredNewFirstPage = createDeferred<Response>();
    const fetchOld = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(ME_PATH)) return jsonResponse(ME_BODY);
      if (url.includes(ISSUES_PATH)) {
        return new URL(url).searchParams.get('cursor') === null
          ? jsonResponse({
              data: [1, 2, 3].map((n) => issue(n, 'Old ' + String(n))),
              next_cursor: 'c2',
            })
          : deferredOldPage.promise;
      }
      return jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404);
    });
    const fetchNew = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(ME_PATH)) return jsonResponse(ME_BODY);
      if (url.includes(ISSUES_PATH)) return deferredNewFirstPage.promise;
      return jsonResponse({ error: { code: 'not_found', message: 'nf' } }, 404);
    });
    const clientOld = new MeshApiClient({
      baseUrl: 'http://old.test',
      getToken: () => 'tok',
      fetchImpl: fetchOld as unknown as typeof fetch,
    });
    const clientNew = new MeshApiClient({
      baseUrl: 'http://new.test',
      getToken: () => 'tok',
      fetchImpl: fetchNew as unknown as typeof fetch,
    });

    function ClientSwapper(): ReactElement {
      const [client, setClient] = useState<MeshApiClient>(clientOld);
      return (
        <>
          <button type="button" data-testid="swap-client" onClick={() => setClient(clientNew)}>
            swap
          </button>
          <HomePage client={client} />
        </>
      );
    }

    renderWithProviders(<ClientSwapper />);
    const list = await screen.findByTestId('home-issue-list');
    await waitFor(() => expect(within(list).getAllByRole('listitem').length).toBe(3));

    // 旧实例的分页请求在途时切换 client → 列表先复位 null,再载入新实例首页
    fireEvent.click(screen.getByTestId('home-load-more'));
    fireEvent.click(screen.getByTestId('swap-client'));
    await waitFor(() => expect(fetchNew).toHaveBeenCalled());

    await act(async () => {
      deferredOldPage.resolve(
        jsonResponse({ data: [6, 7].map((n) => issue(n, 'Stale ' + String(n))), next_cursor: null }),
      );
      await Promise.resolve();
    });
    // 过期页不得混入(此刻列表仍为 null 或新实例数据)
    expect(document.body.textContent).not.toContain('Stale 6');

    await act(async () => {
      deferredNewFirstPage.resolve(
        jsonResponse({ data: [issue(9, 'Fresh 9')], next_cursor: null }),
      );
    });
    const freshList = await screen.findByTestId('home-issue-list');
    await waitFor(() => expect(within(freshList).getAllByRole('listitem').length).toBe(1));
    expect(screen.getByTestId('home-issue-MESH-9').textContent).toContain('Fresh 9');
    expect(document.body.textContent).not.toContain('Stale 6');
    expect(document.body.textContent).not.toContain('Old 1');
  });
});
