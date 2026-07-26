/**
 * IssueDetailPage 组件测试(issue.md §4.1/§4.2/§4.3):
 * 详情渲染 / 标题乐观更新(If-Match,§6.14)/ 状态切换 / 依赖新增成环就地报错 /
 * 依赖乐观移除 + 失败回滚 / 错误态重试。
 * fetch 桩按调用序:GET issue → statuses / children / dependencies / activity / members。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch, headersOf } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import { IssueDetailPage } from '../IssueDetailPage';

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
  assignee_id: null,
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
    <MemoryRouter initialEntries={['/issues/iss-1']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <Routes>
              <Route path="/issues/:issueId" element={<IssueDetailPage />} />
            </Routes>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

const PROJECT_A = {
  id: 'prj-1', name: 'Apollo', key: 'APL', status: 'active', health: null,
  visibility: 'public', lead: null, lead_member_id: null, start_date: null,
  target_date: null, progress: 0, open_issues: 0, done_issues: 0, issue_seq: 1,
  archived: false, archived_at: null, my_role: 'lead',
  created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z',
};
const PROJECT_B = { ...PROJECT_A, id: 'prj-2', name: 'Borealis', key: 'BOR' };
const CYCLE_1 = {
  id: 'cyc-1', project_id: null, name: 'Sprint 1', starts_at: '2026-08-01',
  ends_at: '2026-08-14', state: 'active', auto_roll: false,
  created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z',
};
const MILESTONE_1 = {
  id: 'ms-1', project_id: 'prj-1', title: 'v1.0', description: null,
  target_date: '2026-09-30', state: 'open', overdue: false,
  created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z',
};
const MEMBERS_PAGE = {
  data: [
    { id: 'mem-1', member_type: 'human', role: 'owner', status: 'active',
      display_name: 'Owner', joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'o@c.com', avatar_url: null } },
  ],
  next_cursor: null,
};

/** 首轮加载 9 个响应:issue → (statuses, children, deps, activity, members, projects, cycles) → milestones。 */
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
    fakeResponse({ body: { data: [MILESTONE_1], next_cursor: null } }),
  ];
}

/** PATCH 后的整轮重取响应(issue → 7 并行 → milestones)。 */
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
    fakeResponse({ body: { data: [MILESTONE_1], next_cursor: null } }),
  ];
}

function queue(...extra: ReturnType<typeof fakeResponse>[]): FetchStub {
  const stub = stubFetch(...detailResponses(), ...extra);
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('IssueDetailPage', () => {
  it('renders header, description, dependencies and activity', async () => {
    queue();
    renderDetail();
    await screen.findByTestId('issue-detail');
    expect(screen.getByTestId('issue-detail-identifier').textContent).toBe('APL-1');
    expect(screen.getByTestId('issue-detail-version').textContent).toBe('v3');
    expect((screen.getByTestId('issue-detail-description') as HTMLTextAreaElement).value).toBe(
      'Detailed description',
    );
    expect(screen.getByTestId('issue-detail-deps')).toBeTruthy();
    expect(screen.getByText('WS-7')).toBeTruthy();
    expect(screen.getByTestId('issue-detail-activity')).toBeTruthy();
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
    fireEvent.change(screen.getByTestId('dep-target-input'), {
      target: { value: '22222222-2222-2222-2222-222222222222' },
    });
    fireEvent.click(screen.getByText('Add dependency'));
    await screen.findByTestId('dep-error');
    const posts = stub.calls.filter((c) => c.init?.method === 'POST');
    expect(posts.length).toBe(1);
  });

  it('removes a dependency optimistically and rolls back on failure', async () => {
    queue(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );
    renderDetail();
    await screen.findByText('WS-7');
    fireEvent.click(screen.getByText('Remove'));
    // rolled back after the failed DELETE
    await waitFor(() => {
      expect(screen.getByText('WS-7')).toBeTruthy();
    });
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
    // 每次 PATCH 消耗 1 个响应,随后整轮重取消耗 9 个
    const stub = queue(
      fakeResponse({ body: { data: DETAIL } }),
      ...reloadRound(),
      fakeResponse({ body: { data: DETAIL } }),
      ...reloadRound(),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    fireEvent.change(screen.getByTestId('issue-detail-estimate'), { target: { value: '5' } });
    await waitFor(() => {
      const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patchCalls.length).toBe(1);
      expect(JSON.parse(String(patchCalls[0].init?.body))).toEqual({ estimate: 5, version: 3 });
    });
    fireEvent.change(screen.getByTestId('issue-detail-start'), {
      target: { value: '2026-08-01' },
    });
    await waitFor(() => {
      const patchCalls = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patchCalls.length).toBe(2);
      expect(JSON.parse(String(patchCalls[1].init?.body))).toEqual({
        start_date: '2026-08-01',
        version: 3,
      });
    });
    // milestone / cycle selects are present with options
    expect(screen.getByTestId('issue-detail-milestone')).toBeTruthy();
    expect(screen.getByTestId('issue-detail-cycle')).toBeTruthy();
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
    };
    const moved = { ...DETAIL, project_id: 'prj-2', version: 4 };
    const stub = queue(
      fakeResponse({ body: { data: preview } }),
      fakeResponse({ body: { data: moved } }),
      ...reloadRound(moved),
    );
    renderDetail();
    await screen.findByTestId('issue-detail');
    fireEvent.change(screen.getByTestId('issue-detail-project'), { target: { value: 'prj-2' } });
    await screen.findByTestId('move-dialog');
    expect(screen.getByTestId('move-mapped')).toBeTruthy();
    expect(screen.getByTestId('move-cleared')).toBeTruthy();
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
    };
    const stub = queue(fakeResponse({ body: { data: preview } }));
    renderDetail();
    await screen.findByTestId('issue-detail');
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
    // 类型选择器存在(§4.2/§4.3:选类型)
    expect(screen.getByTestId('dep-type-select')).toBeTruthy();
    fireEvent.change(screen.getByTestId('dep-target-input'), { target: { value: 'WS-9' } });
    fireEvent.click(screen.getByText('Add dependency'));
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.init?.method === 'POST');
      expect(posts.length).toBe(1);
      expect(JSON.parse(String(posts[0].init?.body))).toEqual({
        depends_on_id: 'iss-9',
        type: 'blocked_by',
      });
    });
  });

  it('shows the error state with retry when the detail request fails', async () => {
    const stub = stubFetch(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
      ...detailResponses(),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDetail();
    const retry = await screen.findByText('Retry');
    fireEvent.click(retry);
    await screen.findByTestId('issue-detail');
  });
});
