/**
 * IssueDetailPage 组件测试(issue.md §4.1/§4.2/§4.3):
 * 详情渲染 / 标题乐观更新(If-Match,§6.14)/ 状态切换 / 依赖新增成环就地报错 /
 * 依赖乐观移除 + 失败回滚 / 错误态重试。
 * fetch 桩按调用序:GET issue → statuses / children / dependencies / activity / members。
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, headersOf } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import type { RealtimeClient } from '../../../realtime';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { useSettingsStore } from '../../../state/settingsStore';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { IssueByIdRedirect } from '../IssueByIdRedirect';
import { IssueDetailPage, activityDescription, categoryTone } from '../IssueDetailPage';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

const DETAIL = {
  id: 'iss-1',
  workspace_id: 'ws-1',
  project_id: 'prj-1',
  project: { id: 'prj-1', name: 'Apollo', key: 'APL' },
  identifier_namespace_key: 'APL',
  number: 1,
  identifier: 'APL-1',
  title: 'First issue',
  description: 'Detailed description',
  status: {
    id: 'st-todo',
    project_id: null,
    name: 'Todo',
    category: 'todo',
    color: '#4c9aff',
    position: 1,
    is_default: true,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  },
  status_id: 'st-todo',
  state_category: 'todo',
  priority: 'medium',
  assignee: null,
  assignee_id: null as string | null,
  reporter: { id: 'mem-1', name: 'Owner', member_type: 'human' },
  reporter_id: 'mem-1',
  estimate: null,
  estimate_unit: null,
  due_date: null,
  start_date: null,
  milestone_id: null,
  cycle_id: null,
  parent_id: null,
  position: 0,
  completed_at: null,
  version: 3,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
  children_progress: { total: 1, done: 0 },
};

const STATUS_IN_PROGRESS = {
  id: 'st-wip',
  project_id: null,
  name: 'In Progress',
  category: 'in_progress',
  color: '#f2c94c',
  position: 2,
  is_default: false,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

function ToastLayer(props: { children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

function renderDetail(): void {
  render(
    <MemoryRouter initialEntries={['/w/ws/issues/iss-1']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <Routes>
              <Route path="/w/:workspaceSlug/issues/:issueId" element={<IssueDetailPage />} />
              <Route path="/w/:workspaceSlug/issues" element={<div data-testid="issue-list" />} />
            </Routes>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

interface FakeRealtime {
  readonly value: RealtimeContextValue;
  readonly client: {
    readonly subscribe: ReturnType<typeof vi.fn>;
    readonly unsubscribe: ReturnType<typeof vi.fn>;
    readonly onFrame: ReturnType<typeof vi.fn>;
  };
  readonly emit: (frame: RealtimeEventFrame) => void;
}

function makeFakeRealtime(): FakeRealtime {
  const listeners: Array<(frame: RealtimeEventFrame) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((listener: (frame: RealtimeEventFrame) => void) => {
      listeners.push(listener);
      return () => {
        const index = listeners.indexOf(listener);
        if (index >= 0) listeners.splice(index, 1);
      };
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

function renderDetailWithRealtime(realtime: RealtimeContextValue): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={['/w/ws/issues/iss-1']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime}>
              <Routes>
                <Route path="/w/:workspaceSlug/issues/:issueId" element={<IssueDetailPage />} />
              </Routes>
            </RealtimeContext.Provider>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

const PROJECT_A = {
  id: 'prj-1',
  name: 'Apollo',
  key: 'APL',
  status: 'active',
  health: null,
  visibility: 'public',
  lead: null,
  lead_member_id: null,
  start_date: null,
  target_date: null,
  progress: 0,
  open_issues: 0,
  done_issues: 0,
  issue_seq: 1,
  archived: false,
  archived_at: null,
  my_role: 'lead',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};
const PROJECT_B = { ...PROJECT_A, id: 'prj-2', name: 'Borealis', key: 'BOR' };
const CYCLE_1 = {
  id: 'cyc-1',
  project_id: null,
  name: 'Sprint 1',
  starts_at: '2026-08-01',
  ends_at: '2026-08-14',
  state: 'active',
  auto_roll: false,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};
const MILESTONE_1 = {
  id: 'ms-1',
  project_id: 'prj-1',
  title: 'v1.0',
  description: null,
  target_date: '2026-09-30',
  state: 'open',
  overdue: false,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};
const MEMBERS_PAGE = {
  data: [
    {
      id: 'mem-1',
      member_type: 'human',
      role: 'owner',
      status: 'active',
      display_name: 'Owner',
      joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'o@c.com', avatar_url: null },
    },
  ],
  next_cursor: null,
};
/** 当前用户(comments 集成:解析 currentMember 用)。 */
const ME_PAGE = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'WS',
      workspace_slug: 'ws',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};
/** 评论区首屏(comments 集成:面板挂载后拉取,空列表)。 */
const COMMENTS_EMPTY = { data: [], next_cursor: null };

/** 关联编辑器在 issue 加载后消费的 3 个列表请求(标签 / 标签目录 / 字段值)。 */
function associationResponses(): ReturnType<typeof fakeResponse>[] {
  return [
    fakeResponse({ body: { data: [], next_cursor: null } }),
    fakeResponse({ body: { data: [], next_cursor: null } }),
    fakeResponse({ body: { data: [], next_cursor: null } }),
  ];
}

/** 首轮加载 10 个响应:issue → (statuses, children, deps, activity, members, projects, cycles, me)
 * → milestones;随后关联编辑器消费 3 个列表请求、评论区首屏(comments 集成)消费 1 个
 * (同形空页,顺序不敏感;附件列表由 URL 感知桩带外供给,不消耗队列)。 */
function detailResponses(): ReturnType<typeof fakeResponse>[] {
  return [
    fakeResponse({ body: { data: DETAIL } }),
    fakeResponse({ body: { data: [DETAIL.status, STATUS_IN_PROGRESS], next_cursor: null } }),
    fakeResponse({ body: { data: [], next_cursor: null } }),
    fakeResponse({
      body: {
        data: [
          {
            id: 'dep-1',
            issue_id: 'iss-1',
            depends_on_id: 'iss-7',
            depends_on_identifier: 'WS-7',
            type: 'blocked_by',
            created_by: null,
            created_at: '2026-07-01T00:00:00Z',
          },
        ],
        next_cursor: null,
      },
    }),
    fakeResponse({
      body: {
        data: [
          {
            id: 'act-1',
            issue_id: 'iss-1',
            actor: { id: 'mem-1', name: 'Owner', member_type: 'human' },
            field: 'priority',
            old_value: 'low',
            new_value: 'medium',
            created_at: '2026-07-01T00:00:00Z',
          },
        ],
        next_cursor: null,
      },
    }),
    fakeResponse({ body: MEMBERS_PAGE }),
    fakeResponse({ body: { data: [PROJECT_A, PROJECT_B], next_cursor: null } }),
    fakeResponse({ body: { data: [CYCLE_1], next_cursor: null } }),
    fakeResponse({ body: { data: ME_PAGE } }),
    fakeResponse({ body: { data: [MILESTONE_1], next_cursor: null } }),
    ...associationResponses(),
    fakeResponse({ body: COMMENTS_EMPTY }),
  ];
}

/**
 * PATCH 后的整轮重取响应(issue → 8 并行含 me → milestones)。关联编辑器不随
 * reloadKey 重取(由 issue.updated_at 变化驱动,桩响应 updated_at 恒定);
 * 评论区亦不随页面 reload 重取——故重取轮不含这两组请求。
 */
function reloadRound(issue = DETAIL): ReturnType<typeof fakeResponse>[] {
  return [
    fakeResponse({ body: { data: issue } }),
    fakeResponse({ body: { data: [DETAIL.status, STATUS_IN_PROGRESS], next_cursor: null } }),
    fakeResponse({ body: { data: [], next_cursor: null } }),
    fakeResponse({ body: { data: [], next_cursor: null } }),
    fakeResponse({ body: { data: [], next_cursor: null } }),
    fakeResponse({ body: MEMBERS_PAGE }),
    fakeResponse({ body: { data: [PROJECT_A, PROJECT_B], next_cursor: null } }),
    fakeResponse({ body: { data: [CYCLE_1], next_cursor: null } }),
    fakeResponse({ body: { data: ME_PAGE } }),
    fakeResponse({ body: { data: [MILESTONE_1], next_cursor: null } }),
  ];
}

/** 附件列表固定响应(空页)。 */
function attachmentsEmpty(): ReturnType<typeof fakeResponse> {
  return fakeResponse({ body: { data: [], next_cursor: null } });
}

/** issue 附件列表 GET:面板挂载/确认后重取各一次,与详情并行,到达顺序不确定。 */
function isAttachmentListCall(url: string, init?: RequestInit): boolean {
  return url.endsWith('/attachments') && (init?.method ?? 'GET') === 'GET';
}

/** 小队分派查询 GET(§4.3-2):挂载即发,与详情并行,到达顺序不确定。 */
function isAssignmentCall(url: string, init?: RequestInit): boolean {
  return url.includes('/squads/assignments/by-issue/') && (init?.method ?? 'GET') === 'GET';
}

/**
 * VCS 关联面板(integrations.md §4.2)挂载即发的两个列表请求(issue 关联 +
 * 工作区 VCS 集成),与详情并行、到达顺序不确定 —— 恒定回空页、不消耗队列。
 */
function isVcsPanelCall(url: string, init?: RequestInit): boolean {
  const method = init?.method ?? 'GET';
  return method === 'GET' && (/\/vcs-links/.test(url) || /\/integrations/.test(url));
}

/** VCS 关联面板列表固定响应(空页)。 */
function vcsPanelEmpty(): ReturnType<typeof fakeResponse> {
  return fakeResponse({ body: { data: [], next_cursor: null } });
}

/** 默认无活跃小队分派(data:null);分派相关测试自带专用桩,不经此 helper。 */
function assignmentEmpty(): ReturnType<typeof fakeResponse> {
  return fakeResponse({ body: { data: null } });
}

/** Issue 运行反查面板列表；URL 感知返回，避免与详情并发队列竞争。 */
function isIssueExecutionsCall(url: string, init?: RequestInit): boolean {
  return (
    (init?.method ?? 'GET') === 'GET' &&
    url.includes('/workspaces/ws-1/executions') &&
    url.includes('issue_id=iss-1')
  );
}

function issueExecutionsEmpty(): ReturnType<typeof fakeResponse> {
  return fakeResponse({ body: { data: [], next_cursor: null } });
}

/**
 * URL 感知的顺序桩:附件列表 / 小队分派查询恒定回固定响应、**不消耗队列**
 * (消除与并行详情请求的到达顺序竞争 —— 盲队列在这些 fetch 插队时会整体错位,
 * CI 上间歇红);其余请求按顺序消耗响应,超出后复用最后一个(与 stubFetch 语义一致)。
 */
function detailStub(...responses: Response[]): FetchStub {
  const calls: FetchStub['calls'] = [];
  let index = 0;
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (isAttachmentListCall(url, init)) return attachmentsEmpty();
    if (isAssignmentCall(url, init)) return assignmentEmpty();
    if (isVcsPanelCall(url, init)) return vcsPanelEmpty();
    if (isIssueExecutionsCall(url, init)) return issueExecutionsEmpty();
    const response = responses[Math.min(index, responses.length - 1)];
    index += 1;
    return response;
  }) as typeof fetch;
  return { fetchImpl, calls };
}

function queue(...extra: ReturnType<typeof fakeResponse>[]): FetchStub {
  const stub = detailStub(...detailResponses(), ...extra);
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  useSettingsStore.getState().setLocale(null);
});

describe('IssueDetailPage', () => {
  it('renders header, description, dependencies and activity', async () => {
    const stub = queue();
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    expect(screen.getByTestId('issue-detail-identifier').textContent).toBe('APL-1');
    expect(screen.getByRole('heading', { level: 1, name: 'First issue' })).toHaveClass('sr-only');
    expect(screen.getByTestId('issue-detail-version').textContent).toBe('v3');
    expect((screen.getByTestId('issue-detail-description') as HTMLTextAreaElement).value).toBe(
      'Detailed description',
    );
    expect(screen.getByTestId('issue-detail-title')).toHaveAttribute('data-slot', 'input');
    expect(screen.getByTestId('issue-detail-description')).toHaveAttribute('data-slot', 'textarea');
    expect(screen.getByTestId('dep-target-input')).toHaveAttribute('data-slot', 'input');
    expect(screen.getByTestId('issue-detail-estimate')).toHaveAttribute('data-slot', 'input');
    expect(screen.getByTestId('issue-detail-start')).toHaveAttribute('data-slot', 'input');
    expect(screen.getByTestId('issue-detail-due')).toHaveAttribute('data-slot', 'input');
    expect(screen.getByTestId('issue-detail-deps')).toBeTruthy();
    expect(screen.getByTestId('issue-executions-panel')).toBeTruthy();
    expect(
      stub.calls.some(
        (call) =>
          call.url.includes('/workspaces/ws-1/executions') && call.url.includes('issue_id=iss-1'),
      ),
    ).toBe(true);
    expect(screen.getByText('WS-7')).toBeTruthy();
    expect(screen.getByTestId('dep-link-dep-1')).toHaveAttribute(
      'href',
      '/w/ws/issues/by-identifier/WS-7',
    );
    // DetailLayout + summary chips(§8.3:关键状态保留在标题下)
    expect(screen.getByTestId('detail-layout')).toBeTruthy();
    expect(screen.getByTestId('issue-chip-status').textContent).toContain('Todo');
    expect(screen.getByTestId('issue-chip-priority').textContent).toContain('Medium');
    expect(screen.getByTestId('issue-chip-assignee').textContent).toContain('Unassigned');
    expect(screen.getByTestId('issue-chip-due')).toBeTruthy();
    // 活动流在「活动」Tab 内(默认评论 Tab,§3.2 活动/评论可切换)
    expect(screen.queryByTestId('issue-detail-activity')).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Activity' }));
    expect(screen.getByTestId('issue-detail-activity')).toBeTruthy();
    // 切回评论 Tab(草稿经 localStorage 持久化,切换安全)
    fireEvent.click(screen.getByRole('tab', { name: 'Comments' }));
    expect(screen.queryByTestId('issue-detail-activity')).toBeNull();
  });

  it('renders sparse issue/member/activity envelopes through their defensive fallbacks', async () => {
    const sparseIssue = {
      ...DETAIL,
      project_id: null,
      project: null,
      status: null,
      status_id: null,
      state_category: 'backlog',
      description: null,
      due_date: '2026-10-01',
      assignee: { id: 'mem-email', name: 'Email match', member_type: 'human' },
      assignee_id: 'mem-email',
    };
    const child = {
      ...DETAIL,
      id: 'iss-child',
      identifier: 'APL-2',
      number: 2,
      title: 'Child issue',
      state_category: 'done',
    };
    const members = {
      data: [
        {
          ...MEMBERS_PAGE.data[0],
          id: 'agent-1',
          member_type: 'agent',
          display_name: 'Robot',
          profile: { id: 'usr-1', full_name: 'Robot', email: 'o@c.com', avatar_url: null },
        },
        {
          ...MEMBERS_PAGE.data[0],
          id: 'mem-no-profile',
          display_name: 'No profile',
          profile: null,
        },
        {
          ...MEMBERS_PAGE.data[0],
          id: 'mem-email',
          display_name: 'Unmatched human',
          profile: {
            id: 'usr-other',
            full_name: 'Unmatched human',
            email: 'other@c.com',
            avatar_url: null,
          },
        },
      ],
      next_cursor: null,
    };
    const stub = detailStub(
      fakeResponse({ body: { data: sparseIssue } }),
      fakeResponse({ body: { data: [STATUS_IN_PROGRESS], next_cursor: null } }),
      fakeResponse({ body: { data: [child], next_cursor: null } }),
      fakeResponse({
        body: {
          data: [
            {
              id: 'dep-fallback',
              issue_id: 'iss-1',
              depends_on_id: '12345678-1234-1234-1234-123456789012',
              depends_on_identifier: null,
              type: 'relates_to',
              created_by: null,
              created_at: '2026-07-01T00:00:00Z',
            },
          ],
          next_cursor: null,
        },
      }),
      fakeResponse({
        body: {
          data: [
            {
              id: null,
              issue_id: 'iss-1',
              actor: null,
              field: 'status',
              old_value: null,
              new_value: 'backlog',
              created_at: '2026-07-01T00:00:00Z',
            },
          ],
          next_cursor: null,
        },
      }),
      fakeResponse({ body: members }),
      fakeResponse({ body: { data: [PROJECT_A, PROJECT_B], next_cursor: null } }),
      fakeResponse({ body: { data: [CYCLE_1], next_cursor: null } }),
      fakeResponse({ body: { data: ME_PAGE } }),
      ...associationResponses(),
      fakeResponse({ body: COMMENTS_EMPTY }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);

    renderDetail();
    await screen.findByTestId('issue-detail');
    expect(screen.getByTestId('issue-chip-status').textContent).toContain('Backlog');
    expect(screen.getByTestId('issue-chip-assignee').textContent).toContain('Email match');
    expect(screen.getByTestId('issue-chip-due').textContent).toContain('2026-10-01');
    expect((screen.getByTestId('issue-detail-description') as HTMLTextAreaElement).value).toBe('');
    expect(screen.getByTestId('issue-detail-children').textContent).toContain('Child issue');
    expect(screen.getByRole('link', { name: /APL-2/ })).toHaveAttribute(
      'href',
      '/w/ws/issues/by-identifier/APL-2',
    );
    expect(screen.getByTestId('dep-link-dep-fallback').textContent).toBe('12345678');
    expect(screen.getByTestId('dep-link-dep-fallback')).toHaveAttribute(
      'href',
      '/w/ws/issues/12345678-1234-1234-1234-123456789012',
    );

    const title = screen.getByTestId('issue-detail-title');
    fireEvent.change(title, { target: { value: '   ' } });
    fireEvent.blur(title);
    fireEvent.blur(screen.getByTestId('issue-detail-description'));
    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: '' } });

    fireEvent.click(screen.getByRole('tab', { name: 'Activity' }));
    expect(screen.getByTestId('issue-detail-activity').textContent).toContain('System');
  });

  it('renders the empty activity state when the issue has no activity entries', async () => {
    const responses = detailResponses();
    responses[4] = fakeResponse({ body: { data: [], next_cursor: null } });
    const stub = detailStub(...responses);
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDetail();
    await screen.findByTestId('issue-detail');
    fireEvent.click(screen.getByRole('tab', { name: 'Activity' }));
    expect(screen.getByText('No activity yet.')).toBeTruthy();
  });

  it('生产 UUID 深链经 IssueByIdRedirect 挂载详情并保留 workspace slug', async () => {
    queue();
    render(
      <MemoryRouter initialEntries={['/w/ws/issues/550e8400-e29b-41d4-a716-446655440000']}>
        <ThemeProvider>
          <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
            <ToastLayer>
              <Routes>
                <Route path="/w/:workspaceSlug/issues/:issueId" element={<IssueByIdRedirect />} />
              </Routes>
            </ToastLayer>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    await screen.findByTestId('issue-detail');
    expect(screen.getByTestId('dep-link-dep-1')).toHaveAttribute(
      'href',
      '/w/ws/issues/by-identifier/WS-7',
    );
  });

  it('rejects a workspace-scoped deep link whose slug does not own the issue', async () => {
    const responses = detailResponses();
    responses[8] = fakeResponse({
      body: {
        data: {
          ...ME_PAGE,
          memberships: [{ ...ME_PAGE.memberships[0], workspace_slug: 'another-workspace' }],
        },
      },
    });
    const stub = detailStub(...responses);
    vi.stubGlobal('fetch', stub.fetchImpl);

    renderDetail();

    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByTestId('issue-detail')).toBeNull();
  });

  it('subscribes to the detail channel, merges a fresh frame and cleans up', async () => {
    queue();
    const realtime = makeFakeRealtime();
    const view = renderDetailWithRealtime(realtime.value);
    await screen.findByTestId('issue-detail');
    await waitFor(() => expect(realtime.client.subscribe).toHaveBeenCalledWith('issue:iss-1'));

    act(() => {
      realtime.emit({
        op: 'event',
        channel: 'issue:iss-1',
        seq: 7,
        event: 'issue.updated',
        payload: {
          id: 'iss-1',
          changes: { due_date: '2026-11-30' },
          version: 4,
          updated_at: '2099-01-01T00:00:00Z',
        },
      });
    });
    expect(screen.getByTestId('issue-chip-due').textContent).toContain('2026-11-30');

    view.unmount();
    expect(realtime.client.unsubscribe).toHaveBeenCalledWith('issue:iss-1');
  });

  it('reverts the title draft on Escape and saves on Enter (§3.2 标题可内联编辑)', async () => {
    const updated = { ...DETAIL, title: 'Via enter', version: 4 };
    const stub = queue(fakeResponse({ body: { data: updated } }), ...reloadRound(updated));
    renderDetail();
    const title = (await screen.findByTestId('issue-detail-title')) as HTMLInputElement;
    // Esc 还原为服务端标题
    fireEvent.change(title, { target: { value: 'Scratch' } });
    fireEvent.keyDown(title, { key: 'Escape' });
    expect(title.value).toBe('First issue');
    // Enter 保存
    fireEvent.change(title, { target: { value: 'Via enter' } });
    fireEvent.keyDown(title, { key: 'Enter' });
    await waitFor(() => {
      const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patchCalls.length).toBe(1);
      expect(JSON.parse(String(patchCalls[0].init?.body))).toEqual({
        title: 'Via enter',
        version: 3,
      });
    });
  });

  it('surfaces a transient conflict notice after 409 convergence (§3.2 保存与冲突状态清楚)', async () => {
    const server = { ...DETAIL, title: 'Someone else', description: null, version: 9 };
    queue(
      // PATCH → 409 conflict → 收敛 GET(服务端最新)→ 整轮重取
      fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'stale' } } }),
      fakeResponse({ body: { data: server } }),
      ...reloadRound(server as unknown as typeof DETAIL),
    );
    renderDetail();
    const title = await screen.findByTestId('issue-detail-title');
    fireEvent.change(title, { target: { value: 'Renamed' } });
    fireEvent.blur(title);
    // 冲突弱提示:role=status + conflictNotice 文案 + data-phase=conflict
    const indicator = await screen.findByTestId('issue-save-indicator');
    await waitFor(() => expect(indicator.getAttribute('data-phase')).toBe('conflict'));
    expect(indicator.textContent).toContain('Updated by someone else; latest version loaded');
  });

  it('deletes the issue only after the confirm dialog (§13.3 destructive 确认)', async () => {
    const stub = queue(fakeResponse({ body: { data: { id: 'iss-1', deleted: true } } }));
    renderDetail();
    await screen.findByTestId('issue-detail');
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), { timeout: 5000 });
    // 动作菜单 → 删除 → 确认
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    // 未确认前不发 DELETE;先经对话框关闭按钮(×)取消(触发 Dialog onClose)
    expect(stub.calls.filter((c) => c.init?.method === 'DELETE').length).toBe(0);
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // 对话框内 Cancel 走独立回调，同样不得发 DELETE。
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(stub.calls.filter((c) => c.init?.method === 'DELETE').length).toBe(0);
    // 再次打开并确认删除
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    fireEvent.click(await screen.findByTestId('issue-delete-confirm'));
    await waitFor(() => {
      expect(stub.calls.filter((c) => c.init?.method === 'DELETE').length).toBe(1);
    });
    expect(await screen.findByTestId('issue-list')).toBeTruthy();
  });

  it('renders with a malformed children_progress envelope instead of blanking the page (defensive default)', async () => {
    // 回归:children_progress 缺失/为 null 的畸形信封曾在渲染期抛 TypeError,
    // 无错误边界兜底时整页空白。防御默认值后仅该计数回退 0,页面照常渲染。
    for (const malformed of [null, { total: 'x' }, 'garbage']) {
      const stub = detailStub(
        fakeResponse({ body: { data: { ...DETAIL, children_progress: malformed } } }),
        ...detailResponses().slice(1),
      );
      vi.stubGlobal('fetch', stub.fetchImpl);
      const { unmount } = render(
        <MemoryRouter initialEntries={['/w/ws/issues/iss-1']}>
          <ThemeProvider>
            <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
              <ToastLayer>
                <Routes>
                  <Route path="/w/:workspaceSlug/issues/:issueId" element={<IssueDetailPage />} />
                </Routes>
              </ToastLayer>
            </I18nProvider>
          </ThemeProvider>
        </MemoryRouter>,
      );
      expect(await screen.findByTestId('issue-detail')).toBeTruthy();
      expect((screen.getByTestId('issue-detail-title') as HTMLInputElement).value).toBe(
        'First issue',
      );
      unmount();
      vi.unstubAllGlobals();
    }
  });

  it('patches the title with version and If-Match on blur (§3.4/§6.14)', async () => {
    const updated = { ...DETAIL, title: 'Renamed', version: 4 };
    const stub = queue(fakeResponse({ body: { data: updated } }), ...reloadRound(updated));
    renderDetail();
    const title = await screen.findByTestId('issue-detail-title');
    fireEvent.change(title, { target: { value: 'Renamed' } });
    fireEvent.blur(title);
    await waitFor(() => {
      const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patchCalls.length).toBe(1);
      const body = JSON.parse(String(patchCalls[0].init?.body));
      expect(body).toEqual({ title: 'Renamed', version: 3 });
      expect(headersOf(patchCalls[0])['If-Match']).toBe('2026-07-02T00:00:00Z');
    });
    // 保存成功弱提示:phase → saved(随后自动淡出,§3.2)
    await waitFor(() =>
      expect(screen.getByTestId('issue-save-indicator').getAttribute('data-phase')).toBe('saved'),
    );
  });

  it('reports circular dependency inline without creating the edge (§5.3)', async () => {
    const stub = queue(
      fakeResponse({
        status: 409,
        body: {
          error: {
            code: 'circular_dependency',
            message: 'cycle',
            details: { path: ['iss-1', 'iss-7', 'iss-1'] },
          },
        },
      }),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('dep-target-input'), {
      target: { value: '22222222-2222-2222-2222-222222222222' },
    });
    fireEvent.click(screen.getByText('Add dependency'));
    await screen.findByTestId('dep-error');
    const posts = stub.calls.filter((c) => c.init?.method === 'POST');
    expect(posts.length).toBe(1);
  });

  it('removes a dependency optimistically and rolls back on failure', async () => {
    const stub = queue(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );
    renderDetail();
    await screen.findByText('WS-7');
    // 等关联编辑器挂载请求发出,避免后续操作抢跑导致响应队列错位。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.click(screen.getByText('Remove'));
    // rolled back after the failed DELETE
    await waitFor(() => {
      expect(screen.getByText('WS-7')).toBeTruthy();
    });
  });

  it('maps transport failures for dependency add/remove, delete and move preview', async () => {
    const base = detailStub(...detailResponses(), ...reloadRound());
    const failures = { add: 0, remove: 0, delete: 0, move: 0 };
    const unreadableResponse = (): Response =>
      ({
        ok: true,
        status: 200,
        text: async () => {
          throw new Error('response body read failed');
        },
      }) as unknown as Response;
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'POST' && url.endsWith('/dependencies')) {
        failures.add += 1;
        return unreadableResponse();
      }
      if (method === 'DELETE' && url.includes('/dependencies/')) {
        failures.remove += 1;
        return unreadableResponse();
      }
      if (method === 'DELETE' && url.endsWith('/issues/iss-1')) {
        failures.delete += 1;
        return unreadableResponse();
      }
      if (method === 'POST' && url.endsWith('/move-preview')) {
        failures.move += 1;
        return unreadableResponse();
      }
      return base.fetchImpl(input, init);
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    renderDetail();
    await screen.findByTestId('issue-detail');
    await waitFor(() => expect(base.calls.length).toBeGreaterThanOrEqual(12), { timeout: 5000 });

    fireEvent.change(screen.getByTestId('dep-target-input'), {
      target: { value: '33333333-3333-3333-3333-333333333333' },
    });
    fireEvent.click(screen.getByText('Add dependency'));
    await screen.findByTestId('dep-error');
    expect(failures.add).toBe(1);

    fireEvent.click(screen.getByText('Remove'));
    await waitFor(() => expect(screen.getByText('WS-7')).toBeTruthy());
    expect(failures.remove).toBe(1);

    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    fireEvent.click(await screen.findByTestId('issue-delete-confirm'));
    await waitFor(() => expect(failures.delete).toBe(1));

    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: 'prj-2' } });
    await waitFor(() => expect(failures.move).toBe(1));
  });

  it('saves the description on blur (§4.1 描述可编辑)', async () => {
    // PATCH 响应 + 整轮重取
    const stub = queue(fakeResponse({ body: { data: DETAIL } }), ...reloadRound());
    renderDetail();
    const textarea = await screen.findByTestId('issue-detail-description');
    fireEvent.change(textarea, { target: { value: 'New description body' } });
    fireEvent.blur(textarea);
    await waitFor(() => {
      const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patchCalls.length).toBe(1);
      expect(JSON.parse(String(patchCalls[0].init?.body))).toEqual({
        description: 'New description body',
        version: 3,
      });
    });
  });

  it('edits estimate and start date from the sidebar (§4.2 MEDIUM-1)', async () => {
    // 每次 PATCH 消耗 1 个响应,随后整轮重取消耗 9 个。
    // 连续变更以「可观察请求数」同步收敛,不依赖被动副作用的逐次刷新时序:
    // React 19 下两次 reloadKey 更新可能在同一批处理中合并(0→2 单次副作用执行),
    // 第一轮整轮重取未发出就触发第二次变更会使响应队列错位;先等第一轮落定
    // (12 初始(9 页面 + 3 编辑器挂载) + 1 PATCH + 9 重取 = 22)再发第二次变更。末次成功后必有收敛重取
    // (reloadKey 终值触发一次副作用),不丢最终一致性。
    const stub = queue(
      fakeResponse({ body: { data: DETAIL } }),
      ...reloadRound(),
      fakeResponse({ body: { data: DETAIL } }),
      ...reloadRound(),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('issue-detail-estimate'), { target: { value: '5' } });
    await waitFor(
      () => {
        const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
        expect(patchCalls.length).toBe(1);
        expect(JSON.parse(String(patchCalls[0].init?.body))).toEqual({ estimate: 5, version: 3 });
      },
      { timeout: 5000 },
    );
    await waitFor(
      () => {
        expect(stub.calls.length).toBeGreaterThanOrEqual(22);
      },
      { timeout: 5000 },
    );
    fireEvent.change(screen.getByTestId('issue-detail-start'), {
      target: { value: '2026-08-01' },
    });
    await waitFor(
      () => {
        const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
        expect(patchCalls.length).toBe(2);
        expect(JSON.parse(String(patchCalls[1].init?.body))).toEqual({
          start_date: '2026-08-01',
          version: 3,
        });
      },
      { timeout: 5000 },
    );
    // milestone / cycle selects are present with options(第二轮整轮重取收敛后)
    expect(await screen.findByTestId('issue-detail-milestone', {}, { timeout: 5000 })).toBeTruthy();
    expect(await screen.findByTestId('issue-detail-cycle', {}, { timeout: 5000 })).toBeTruthy();
  });

  it('opens the move preview dialog on project change and confirms (§4.3 MEDIUM-2)', async () => {
    const preview = {
      issue_id: 'iss-1',
      identifier: 'APL-1',
      from_project_id: 'prj-1',
      target_project_id: 'prj-2',
      mapped_fields: [
        { field: 'status', from: { name: 'Dev' }, to: { name: 'Todo' }, reason: 'private' },
      ],
      cleared_fields: [{ field: 'milestone_id', reason: '项目私有里程碑' }],
      kept_fields: ['title', 'identifier'],
      version: 3,
    };
    const moved = { ...DETAIL, project_id: 'prj-2', version: 4 };
    const stub = queue(
      fakeResponse({ body: { data: preview } }),
      fakeResponse({ body: { data: moved } }),
      ...reloadRound(moved),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: 'prj-2' } });
    await screen.findByTestId('move-dialog');
    expect(screen.getByTestId('move-mapped')).toBeTruthy();
    expect(screen.getByTestId('move-cleared')).toBeTruthy();
    // §4.3/§3.8:对话框须标明迁移去向(目标项目名,自 projects 列表解析)
    expect(screen.getByTestId('move-target').textContent).toContain('Borealis');
    fireEvent.click(screen.getByTestId('move-confirm'));
    await waitFor(() => {
      const movePosts = stub.calls.filter(
        (c) => c.init?.method === 'POST' && String(c.url).endsWith('/move'),
      );
      expect(movePosts.length).toBe(1);
      expect(JSON.parse(String(movePosts[0].init?.body))).toEqual({
        target_project_id: 'prj-2',
        confirm: true,
        version: 3,
      });
    });
  });

  it('cancels the move dialog without calling move', async () => {
    const preview = {
      issue_id: 'iss-1',
      identifier: 'APL-1',
      from_project_id: 'prj-1',
      target_project_id: 'prj-2',
      mapped_fields: [],
      cleared_fields: [],
      kept_fields: [],
      version: 3,
    };
    const stub = queue(fakeResponse({ body: { data: preview } }));
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: 'prj-2' } });
    await screen.findByTestId('move-dialog');
    fireEvent.click(screen.getByTestId('move-cancel'));
    await waitFor(() => expect(screen.queryByTestId('move-dialog')).toBeNull());
    expect(stub.calls.filter((c) => String(c.url).endsWith('/move')).length).toBe(0);
  });

  it('resolves a dependency target by identifier and posts its UUID (M7)', async () => {
    const dep = {
      id: 'dep-2',
      issue_id: 'iss-1',
      depends_on_id: 'iss-9',
      depends_on_identifier: 'WS-9',
      type: 'blocked_by',
      created_by: null,
      created_at: '2026-07-01T00:00:00Z',
    };
    const stub = queue(
      // 标识符解析:WS-9 → iss-9
      fakeResponse({ body: { data: { ...DETAIL, id: 'iss-9', identifier: 'WS-9' } } }),
      // 依赖创建
      fakeResponse({ status: 201, body: { data: dep } }),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    // 类型选择器存在(§4.2/§4.3:选类型)并切换类型(覆盖 dep-type onChange)
    expect(screen.getByTestId('dep-type-select')).toBeTruthy();
    fireEvent.change(screen.getByTestId('dep-type-select'), { target: { value: 'blocks' } });
    fireEvent.change(screen.getByTestId('dep-target-input'), { target: { value: 'WS-9' } });
    fireEvent.click(screen.getByText('Add dependency'));
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.init?.method === 'POST');
      expect(posts.length).toBe(1);
      expect(JSON.parse(String(posts[0].init?.body))).toEqual({
        depends_on_id: 'iss-9',
        type: 'blocks',
      });
    });
  });

  it('shows the error state with retry when the detail request fails', async () => {
    const stub = detailStub(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
      ...detailResponses(),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDetail();
    const retry = await screen.findByText('Retry');
    fireEvent.click(retry);
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
  });

  it('labels the workspace inbox as the move target when target is null (§4.3)', async () => {
    const preview = {
      issue_id: 'iss-1',
      identifier: 'APL-1',
      from_project_id: 'prj-1',
      target_project_id: null,
      mapped_fields: [],
      cleared_fields: [{ field: 'milestone_id', reason: '项目私有里程碑' }],
      kept_fields: ['title', 'identifier'],
      version: 3,
    };
    const stub = queue(fakeResponse({ body: { data: preview } }));
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: '' } });
    await screen.findByTestId('move-dialog');
    // 目标为收件箱(null)时,目标名取收件箱本地化文案而非空/原始 id
    expect(screen.getByTestId('move-target').textContent).toContain('Inbox');
    // 仅清除场景:mapped 区不渲染,cleared 区渲染(与截图 ev-m5 同形态)
    expect(screen.queryByTestId('move-mapped')).toBeNull();
    expect(screen.getByTestId('move-cleared')).toBeTruthy();
  });

  it('rolls the status select back in place on a strict-mode rejection, no reload/skeleton (§4.4/§5.2)', async () => {
    // 严格模式 409 invalid_status_transition:仅返回错误,不排队任何 reload 响应 ——
    // 若实现仍整页重取,fetch 桩会复用本错误响应并把它当 issue 渲染而崩溃。
    const stub = queue(
      fakeResponse({
        status: 409,
        body: {
          error: {
            code: 'invalid_status_transition',
            message: 'strict',
            details: { from: 'st-todo', to: 'st-wip', allowed: [] },
          },
        },
      }),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(13), {
      timeout: 5000,
    });
    const statusSelect = screen.getByTestId('issue-detail-status') as HTMLSelectElement;
    expect(statusSelect.value).toBe('st-todo');
    fireEvent.change(statusSelect, { target: { value: 'st-wip' } });
    // 经 i18n key 渲染的危险 toast(en)
    expect(
      await screen.findByText('This status transition is not allowed (strict mode)'),
    ).toBeTruthy();
    // 就地回落原值、不保留被禁目标值
    await waitFor(() =>
      expect((screen.getByTestId('issue-detail-status') as HTMLSelectElement).value).toBe(
        'st-todo',
      ),
    );
    // 不触发整页 reload:详情 GET 仅首轮一次(关联编辑器/评论区/附件拉取不计入
    // 详情请求),被拒 PATCH 一次(无骨架闪烁来源)。
    const detailGets = stub.calls.filter(
      (c) => String(c.url).endsWith('/issues/iss-1') && (c.init?.method ?? 'GET') === 'GET',
    );
    expect(detailGets.length).toBe(1);
    expect(stub.calls.filter((c) => c.init?.method === 'PATCH').length).toBe(1);
  });

  it('shows the strict-mode rejection toast in Chinese under zh-CN (§4.4 i18n)', async () => {
    act(() => {
      useSettingsStore.getState().setLocale('zh-CN');
    });
    const stub = queue(
      fakeResponse({
        status: 409,
        body: {
          error: {
            code: 'invalid_status_transition',
            message: 'strict',
            details: { from: 'st-todo', to: 'st-wip', allowed: [] },
          },
        },
      }),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('issue-detail-status') as HTMLSelectElement, {
      target: { value: 'st-wip' },
    });
    // zh-CN 须显示中文译文,而非原始 key / 硬编码英文
    expect(await screen.findByText('严格模式下不允许该状态转换')).toBeTruthy();
  });

  it('renders move preview field/reason with readable i18n labels, not technical keys (LOW-2)', async () => {
    const preview = {
      issue_id: 'iss-1',
      identifier: 'APL-1',
      from_project_id: 'prj-1',
      target_project_id: 'prj-missing',
      mapped_fields: [
        {
          field: 'status',
          from: { name: 'Dev' },
          to: { name: 'Todo' },
          reason: '项目私有 status → 目标项目同 category 默认 status',
        },
        {
          field: 'labels',
          reason: '项目级标签',
        },
      ],
      cleared_fields: [
        { field: 'milestone_id', reason: '项目私有里程碑' },
        { field: 'cycle_id', reason: '项目绑定的周期' },
        // 未知键/原因:回退原值渲染(后端新增词汇不中断 UI)
        { field: 'labels_v2', reason: 'brand_new_reason' },
      ],
      kept_fields: [],
    };
    const stub = queue(fakeResponse({ body: { data: preview } }));
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: 'prj-2' } });
    await screen.findByTestId('move-dialog');
    const mapped = screen.getByTestId('move-mapped').textContent ?? '';
    const cleared = screen.getByTestId('move-cleared').textContent ?? '';
    // 字段技术键 → 本地化字段名
    expect(mapped).toContain('Status: Dev → Todo');
    expect(mapped).toContain('Labels: ? → ?');
    expect(mapped).not.toContain('status:');
    // 预览返回当前项目目录之外的目标时,名称回退为稳定 id。
    expect(screen.getByTestId('move-target').textContent).toContain('prj-missing');
    expect(cleared).toContain('Milestone(Project-private milestone)');
    expect(cleared).toContain('Cycle(Project-bound cycle)');
    expect(cleared).not.toContain('milestone_id');
    expect(cleared).not.toContain('cycle_id');
    // 未知键回退原始值
    expect(cleared).toContain('labels_v2(brand_new_reason)');
  });

  /**
   * 两次侧栏字段变更的通用流程(沿用 estimate/start 测试的时序收敛:先等第一轮
   * 重取落定 calls≥22 再发第二次变更,避免 React 19 批处理合并 reloadKey 错位队列)。
   */
  async function twoSidebarChanges(
    first: { testId: string; value: string; body: unknown },
    second: { testId: string; value: string; body: unknown },
  ): Promise<void> {
    const stub = queue(
      fakeResponse({ body: { data: DETAIL } }),
      ...reloadRound(),
      fakeResponse({ body: { data: DETAIL } }),
      ...reloadRound(),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), { timeout: 5000 });
    fireEvent.change(screen.getByTestId(first.testId), { target: { value: first.value } });
    await waitFor(
      () => {
        const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
        expect(patchCalls.length).toBe(1);
        expect(JSON.parse(String(patchCalls[0].init?.body))).toEqual(first.body);
      },
      { timeout: 5000 },
    );
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(22), { timeout: 5000 });
    fireEvent.change(screen.getByTestId(second.testId), { target: { value: second.value } });
    await waitFor(
      () => {
        const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
        expect(patchCalls.length).toBe(2);
        expect(JSON.parse(String(patchCalls[1].init?.body))).toEqual(second.body);
      },
      { timeout: 5000 },
    );
  }

  it('patches priority and assignee from the sidebar (§4.2)', async () => {
    await twoSidebarChanges(
      { testId: 'issue-detail-priority', value: 'high', body: { priority: 'high', version: 3 } },
      {
        testId: 'issue-detail-assignee',
        value: 'mem-1',
        body: { assignee_id: 'mem-1', version: 3 },
      },
    );
  });

  it('patches due date and estimate unit from the sidebar (§4.2)', async () => {
    await twoSidebarChanges(
      {
        testId: 'issue-detail-due',
        value: '2026-09-01',
        body: { due_date: '2026-09-01', version: 3 },
      },
      {
        testId: 'issue-detail-estimate-unit',
        value: 'points',
        body: { estimate_unit: 'points', version: 3 },
      },
    );
  });

  it('patches milestone and cycle from the sidebar (§4.2)', async () => {
    await twoSidebarChanges(
      {
        testId: 'issue-detail-milestone',
        value: 'ms-1',
        body: { milestone_id: 'ms-1', version: 3 },
      },
      { testId: 'issue-detail-cycle', value: 'cyc-1', body: { cycle_id: 'cyc-1', version: 3 } },
    );
  });

  it('clears milestone and cycle back to null with empty select values (§4.2 三态清空)', async () => {
    await twoSidebarChanges(
      { testId: 'issue-detail-milestone', value: '', body: { milestone_id: null, version: 3 } },
      { testId: 'issue-detail-cycle', value: '', body: { cycle_id: null, version: 3 } },
    );
  });

  it('renders a fully-populated issue with all sidebar fields non-null (缺字段分支对照)', async () => {
    const FULL = {
      ...DETAIL,
      estimate: 5,
      estimate_unit: 'points',
      start_date: '2026-08-01',
      due_date: '2026-09-01',
      milestone_id: 'ms-1',
      cycle_id: 'cyc-1',
      assignee: { id: 'mem-1', name: 'Owner', member_type: 'human' },
      assignee_id: 'mem-1',
    };
    const stub = detailStub(fakeResponse({ body: { data: FULL } }), ...detailResponses().slice(1));
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDetail();
    await screen.findByTestId('issue-detail');
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), { timeout: 5000 });
    expect((screen.getByTestId('issue-detail-estimate') as HTMLInputElement).value).toBe('5');
    expect((screen.getByTestId('issue-detail-estimate-unit') as HTMLSelectElement).value).toBe(
      'points',
    );
    expect((screen.getByTestId('issue-detail-start') as HTMLInputElement).value).toBe('2026-08-01');
    expect((screen.getByTestId('issue-detail-due') as HTMLInputElement).value).toBe('2026-09-01');
    expect((screen.getByTestId('issue-detail-milestone') as HTMLSelectElement).value).toBe('ms-1');
    expect((screen.getByTestId('issue-detail-cycle') as HTMLSelectElement).value).toBe('cyc-1');
    // chips 呈现已填值(负责人 / 截止日)
    expect(screen.getByTestId('issue-chip-assignee').textContent).toContain('Owner');
    expect(screen.getByTestId('issue-chip-due').textContent).toContain('2026-09-01');
  });

  it('clears assignee back to unassigned with an empty select value', async () => {
    const assigned = { ...DETAIL, assignee_id: 'mem-1' };
    const stub = queue(fakeResponse({ body: { data: assigned } }), ...reloadRound(assigned));
    renderDetail();
    await screen.findByTestId('issue-detail');
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), { timeout: 5000 });
    fireEvent.change(screen.getByTestId('issue-detail-assignee'), { target: { value: '' } });
    await waitFor(() => {
      const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patchCalls.length).toBe(1);
      expect(JSON.parse(String(patchCalls[0].init?.body))).toEqual({
        assignee_id: null,
        version: 3,
      });
    });
  });

  it('shows Unassigned when the reporter is missing (报告人回退分支)', async () => {
    const noReporter = { ...DETAIL, reporter: null, reporter_id: null };
    const stub = detailStub(
      fakeResponse({ body: { data: noReporter } }),
      ...detailResponses().slice(1),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDetail();
    await screen.findByTestId('issue-detail');
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), { timeout: 5000 });
    expect(document.querySelector('.mesh-issues-detail__meta')?.textContent).toContain(
      'Unassigned',
    );
  });

  it('localizes estimate unit options (LOW-2:points/hours 不再硬编码英文)', async () => {
    const stub = queue();
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    const options = (screen.getByTestId('issue-detail-estimate-unit') as HTMLSelectElement).options;
    expect(options[1].value).toBe('points');
    expect(options[1].text).toBe('Points');
    expect(options[2].value).toBe('hours');
    expect(options[2].text).toBe('Hours');
  });

  it('refreshes the preview and keeps the dialog open on 422 move_confirmation_required (LOW-3)', async () => {
    const stalePreview = {
      issue_id: 'iss-1',
      identifier: 'APL-1',
      from_project_id: 'prj-1',
      target_project_id: 'prj-2',
      mapped_fields: [],
      cleared_fields: [{ field: 'milestone_id', reason: '项目私有里程碑' }],
      kept_fields: [],
    };
    // 预览过期:服务端以 details.preview 下发最新清单(issue.md §3.8/README §6.14)
    const freshPreview = {
      ...stalePreview,
      cleared_fields: [{ field: 'cycle_id', reason: '项目绑定的周期' }],
    };
    const moved = { ...DETAIL, project_id: 'prj-2', version: 4 };
    const stub = queue(
      fakeResponse({ body: { data: stalePreview } }),
      fakeResponse({
        status: 422,
        body: {
          error: {
            code: 'move_confirmation_required',
            message: 'confirm required',
            details: { preview: freshPreview },
          },
        },
      }),
      fakeResponse({ body: { data: moved } }),
      ...reloadRound(moved),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: 'prj-2' } });
    await screen.findByTestId('move-dialog');
    fireEvent.click(screen.getByTestId('move-confirm'));
    // 对话框保持,预览按 details.preview 重渲染(清除清单由 milestone 更新为 cycle)
    await waitFor(() =>
      expect(screen.getByTestId('move-cleared').textContent).toContain(
        'Cycle(Project-bound cycle)',
      ),
    );
    expect(screen.getByTestId('move-dialog')).toBeTruthy();
    // 再次确认成功落库
    fireEvent.click(screen.getByTestId('move-confirm'));
    await waitFor(() => {
      const movePosts = stub.calls.filter(
        (c) => c.init?.method === 'POST' && String(c.url).endsWith('/move'),
      );
      expect(movePosts.length).toBe(2);
    });
    await waitFor(() => expect(screen.queryByTestId('move-dialog')).toBeNull());
  });

  it('falls back to toast and closes the dialog when 422 carries no usable preview (LOW-3)', async () => {
    const preview = {
      issue_id: 'iss-1',
      identifier: 'APL-1',
      from_project_id: 'prj-1',
      target_project_id: 'prj-2',
      mapped_fields: [],
      cleared_fields: [],
      kept_fields: [],
    };
    const stub = queue(
      fakeResponse({ body: { data: preview } }),
      // details 缺合法 preview → 走既有错误路径(toast + 关闭),不静默吞错
      fakeResponse({
        status: 422,
        body: { error: { code: 'move_confirmation_required', message: 'confirm required' } },
      }),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    // 等关联编辑器挂载时的 3 个列表请求发出(9 页面 + 3 编辑器 = 12),
    // 避免后续操作抢跑编辑器请求导致响应队列错位(coverage 下 effect 调度更慢)。
    await waitFor(() => expect(stub.calls.length).toBeGreaterThanOrEqual(12), {
      timeout: 5000,
    });
    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: 'prj-2' } });
    await screen.findByTestId('move-dialog');
    fireEvent.click(screen.getByTestId('move-confirm'));
    await waitFor(() => expect(screen.queryByTestId('move-dialog')).toBeNull());
    expect(
      await screen.findByText(
        'Moving this item changes some fields. Please review and confirm the move.',
      ),
    ).toBeTruthy();
  });
});

describe('categoryTone(状态类别 → 徽标 tone 纯映射)', () => {
  it('覆盖全部 7 个类别', () => {
    expect(categoryTone('backlog')).toBe('neutral');
    expect(categoryTone('todo')).toBe('info');
    expect(categoryTone('in_progress')).toBe('accent');
    expect(categoryTone('in_review')).toBe('warning');
    expect(categoryTone('blocked')).toBe('danger');
    expect(categoryTone('done')).toBe('success');
    expect(categoryTone('cancelled')).toBe('neutral');
  });
});

describe('activityDescription(execution activity projection)', () => {
  it('renders the started-processing timeline copy instead of a generic field change', () => {
    const t = (key: string): string => key;
    expect(
      activityDescription(
        {
          id: 'activity-run',
          issue_id: 'iss-1',
          actor: { id: 'agent-member', name: 'Builder', member_type: 'agent' },
          field: 'execution',
          old_value: null,
          new_value: { state: 'started', execution_id: 'exec-1' },
          created_at: '2026-08-05T08:00:00Z',
        },
        t,
      ),
    ).toBe('issues.activity.executionStarted');
  });
});
