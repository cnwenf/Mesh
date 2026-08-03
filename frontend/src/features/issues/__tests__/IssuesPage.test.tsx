/**
 * IssuesPage 组件测试(DataView 标准化后;issue.md §4.1/§4.2/§4.3 + design-quality §3.2):
 * 列表渲染 / 骨架同形 / 错误态(影响+重试)/ 快速创建 / 勾选批量条(状态/优先级/删除确认)/
 * 表头排序循环 / 过滤 chips / 保存视图(localStorage)/ 键盘行选择 / 实时帧合并。
 * fetch 桩按调用序驱动:users/me → members → issues → statuses。
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import type { RealtimeClient } from '../../../realtime';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { requestOptimisticStepComplete } from '../../onboarding/notify';
import { SAVED_VIEWS_STORAGE_KEY } from '../issuesSavedViews';
import { IssuesPage } from '../IssuesPage';

vi.mock('../../onboarding/notify', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../onboarding/notify')>();
  return { ...actual, requestOptimisticStepComplete: vi.fn() };
});

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

const ME = {
  user: { id: 'usr-1', email: 'owner@acme.com', display_name: 'Owner' },
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

const MEMBERS = {
  data: [
    {
      id: 'mem-1',
      member_type: 'human',
      role: 'owner',
      status: 'active',
      display_name: 'Owner',
      joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'owner@acme.com', avatar_url: null },
    },
    {
      id: 'agent-1',
      member_type: 'agent',
      role: 'member',
      status: 'active',
      display_name: 'Planner',
      joined_at: null,
      profile: null,
    },
    {
      id: 'mem-inactive',
      member_type: 'human',
      role: 'member',
      status: 'suspended',
      display_name: 'Suspended member',
      joined_at: null,
      profile: null,
    },
  ],
  next_cursor: null,
};

const STATUS_TODO = {
  id: 'st-todo',
  project_id: null,
  name: 'Todo',
  category: 'todo',
  color: '#4c9aff',
  position: 1,
  is_default: true,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

function issueFixture(id: string, identifier: string, title: string) {
  return {
    id,
    workspace_id: 'ws-1',
    project_id: null,
    project: null,
    identifier_namespace_key: 'WS',
    number: Number(identifier.split('-')[1]),
    identifier,
    title,
    description: null,
    status: STATUS_TODO,
    status_id: 'st-todo',
    state_category: 'todo',
    priority: 'high',
    assignee: { id: 'mem-1', name: 'Owner', member_type: 'human' },
    assignee_id: 'mem-1',
    reporter: null,
    reporter_id: null,
    estimate: null,
    estimate_unit: null,
    due_date: '2026-08-15',
    start_date: null,
    milestone_id: null,
    cycle_id: null,
    parent_id: null,
    position: 0,
    completed_at: null,
    version: 1,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-02T00:00:00Z',
  };
}

const ISSUE_1 = issueFixture('iss-1', 'WS-1', 'Fix the login bug');
const ISSUE_2 = issueFixture('iss-2', 'WS-2', 'Ship the docs');

function ToastLayer(props: { children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

function makeFakeRealtime(): {
  value: RealtimeContextValue;
  subscribe: ReturnType<typeof vi.fn>;
  emit: (frame: RealtimeEventFrame) => void;
} {
  const listeners: Array<(frame: RealtimeEventFrame) => void> = [];
  const subscribe = vi.fn();
  const client = {
    subscribe,
    unsubscribe: vi.fn(),
    onFrame: vi.fn((listener: (frame: RealtimeEventFrame) => void) => {
      listeners.push(listener);
      return () => {
        const index = listeners.indexOf(listener);
        if (index >= 0) listeners.splice(index, 1);
      };
    }),
  };
  const value: RealtimeContextValue = {
    state: 'connected',
    client: client as unknown as RealtimeClient,
  };
  return {
    value,
    subscribe,
    emit: (frame) => {
      for (const listener of listeners) listener(frame);
    },
  };
}

function renderPage(realtime: RealtimeContextValue, route = '/w/team/issues'): void {
  render(
    <MemoryRouter initialEntries={[route]}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime}>
              <Routes>
                <Route path="/w/:workspaceSlug/issues" element={<IssuesPage />} />
              </Routes>
            </RealtimeContext.Provider>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** 带路由捕获的渲染(键盘 Enter 打开详情用)。 */
function renderPageWithRoutes(realtime: RealtimeContextValue, route = '/w/team/issues'): void {
  render(
    <MemoryRouter initialEntries={[route]}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime}>
              <Routes>
                <Route path="/w/:workspaceSlug/issues" element={<IssuesPage />} />
                <Route
                  path="/w/:workspaceSlug/issues/by-identifier/:identifier"
                  element={<div data-testid="nav-detail" />}
                />
              </Routes>
            </RealtimeContext.Provider>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function queueInitialLoad(...extra: ReturnType<typeof fakeResponse>[]): FetchStub {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
    fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    ...extra,
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(requestOptimisticStepComplete).mockClear();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('IssuesPage', () => {
  it('renders issue rows after loading (identifier / title / status / assignee)', async () => {
    queueInitialLoad();
    const rt = makeFakeRealtime();
    renderPage(rt.value);
    await screen.findByText('WS-1');
    expect(screen.getByText('Fix the login bug')).toBeTruthy();
    expect(screen.getByText('WS-2')).toBeTruthy();
    expect(screen.getAllByText('Todo').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Owner').length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: 'Fix the login bug' })).toHaveAttribute(
      'href',
      '/w/team/issues/by-identifier/WS-1',
    );
    expect(rt.subscribe).toHaveBeenCalledWith('workspace:ws-1:issues');
  });

  it('renders DataView with a single h1 title (页面模板唯一 h1,§4.4)', async () => {
    queueInitialLoad();
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    const headings = screen.getAllByRole('heading', { level: 1 });
    expect(headings.length).toBe(1);
    expect(screen.getByTestId('data-view')).toBeTruthy();
  });

  it('routes list, filter, selection and quick-create controls through the design foundation', async () => {
    queueInitialLoad();
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');

    expect(screen.getByTestId('issue-filter-q')).toHaveAttribute('data-slot', 'input');
    expect(screen.getByTestId('issue-filter-mine').closest('.mesh-checkbox')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Key' })).toHaveAttribute('data-slot', 'button');
    expect(
      screen.getByTestId('issue-status-iss-1').querySelector('[data-slot="badge"]'),
    ).not.toBeNull();

    fireEvent.click(screen.getByTestId('issue-open-create'));
    expect(screen.getByTestId('issue-create-title')).toHaveAttribute('data-slot', 'input');
  });

  it('shows a same-shape skeleton before the first load (骨架同形,§13.3)', async () => {
    // issues 请求挂起:工作区解析后、列表返回前,呈现与行同形的骨架。
    let resolveIssues: (response: Response) => void = () => undefined;
    const pendingIssues = new Promise<Response>((resolve) => {
      resolveIssues = resolve;
    });
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/members')) return fakeResponse({ body: MEMBERS });
      if (url.includes('/statuses')) {
        return fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } });
      }
      return pendingIssues;
    }) as typeof fetch);
    renderPage(makeFakeRealtime().value);
    expect(await screen.findByTestId('issues-skeleton')).toBeTruthy();
    // 放行挂起请求,干净收尾(避免未处理 rejection)
    resolveIssues(fakeResponse({ body: { data: [ISSUE_1], next_cursor: null } }));
    await screen.findByText('WS-1');
  });

  it('renders due dates localized via Intl, not raw ISO strings (LOW-2)', async () => {
    queueInitialLoad();
    const rt = makeFakeRealtime();
    renderPage(rt.value);
    await screen.findByText('WS-1');
    // en + UTC + dateStyle medium → "Aug 15, 2026"(纯日期值锁 UTC,日历日不随时区漂移)
    expect(screen.getAllByText('Aug 15, 2026').length).toBe(2);
    expect(screen.queryByText('2026-08-15')).toBeNull();
  });

  it('falls back to the raw value for an unparsable due date without breaking the row (LOW-2)', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [{ ...ISSUE_1, due_date: 'not-a-date' }], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    const rt = makeFakeRealtime();
    renderPage(rt.value);
    await screen.findByText('WS-1');
    expect(screen.getByText('not-a-date')).toBeTruthy();
  });

  it('shows the error state with impact and retry when the list request fails (§7.7 四部分)', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
      // retry round (workspace effect ran once; reload fetches issues+statuses):
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    // 错误态含影响说明(issues.errorImpact 新键)与恢复动作
    expect(
      await screen.findByText('Your filters and selection are kept; the list could not be loaded.'),
    ).toBeTruthy();
    const retry = await screen.findByText('Retry');
    fireEvent.click(retry);
    await screen.findByText('WS-1');
  });

  it('creates an issue via the quick create form', async () => {
    const created = issueFixture('iss-3', 'WS-3', 'Brand new');
    const stub = queueInitialLoad(fakeResponse({ status: 201, body: { data: created } }));
    void stub;
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-open-create'));
    fireEvent.change(screen.getByTestId('issue-create-title'), {
      target: { value: 'Brand new' },
    });
    fireEvent.submit(screen.getByTestId('issue-create-form'));
    await screen.findByText('WS-3');
    expect(screen.getByRole('link', { name: 'Brand new' })).toHaveAttribute(
      'href',
      '/w/team/issues/by-identifier/WS-3',
    );
    // 建 issue 成功 → 乐观推进清单步骤 3(onboarding.md §1.2.2)
    expect(requestOptimisticStepComplete).toHaveBeenCalledWith('create_first_issue');
  });

  it('opens the bulk bar on selection and bulk-deletes after confirmation (§5.5/§13.3)', async () => {
    queueInitialLoad(
      fakeResponse({ body: { data: { succeeded: 1, failed: 0, errors: [] } } }),
      // reload after bulk (issues + statuses only):
      fakeResponse({ body: { data: [ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('bulk-bar');
    // 取消选择 → 批量条消失;再勾选继续删除流程
    fireEvent.click(screen.getByText('Clear selection'));
    await waitFor(() => expect(screen.queryByTestId('bulk-bar')).toBeNull());
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('bulk-bar');
    // destructive 需确认(§13.3):Delete → 确认对话框 → 确认
    fireEvent.click(screen.getByText('Delete'));
    await screen.findByTestId('bulk-delete-confirm-body');
    fireEvent.click(screen.getByTestId('bulk-delete-confirm'));
    await waitFor(() => {
      expect(screen.queryByText('WS-1')).toBeNull();
    });
  });

  it('bulk set status via menu sends changes.status_id (§1.2.5)', async () => {
    const stub = queueInitialLoad(
      fakeResponse({ body: { data: { succeeded: 1, failed: 0, errors: [] } } }),
      fakeResponse({ body: { data: [ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(screen.getByRole('button', { name: 'Set status…' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Todo' }));
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.init?.method === 'POST');
      expect(posts.length).toBe(1);
      expect(JSON.parse(String(posts[0].init?.body))).toEqual({
        issue_ids: ['iss-1'],
        changes: { status_id: 'st-todo' },
      });
    });
  });

  it('bulk set priority via menu sends changes.priority (§1.2.5)', async () => {
    const stub = queueInitialLoad(
      fakeResponse({ body: { data: { succeeded: 2, failed: 0, errors: [] } } }),
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    // 表头全选
    fireEvent.click(screen.getByTestId('issue-select-all'));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(screen.getByRole('button', { name: 'Set priority…' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Urgent' }));
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.init?.method === 'POST');
      expect(JSON.parse(String(posts[0].init?.body))).toEqual({
        issue_ids: ['iss-1', 'iss-2'],
        changes: { priority: 'urgent' },
      });
    });
    // 批量成功后 onDone 清空选择 → 批量条(selectedCount 0)消失
    await waitFor(() => expect(screen.queryByTestId('bulk-bar')).toBeNull());
  });

  it('bulk failure (non-partial) shows a danger toast and keeps rows', async () => {
    queueInitialLoad(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(screen.getByText('Delete'));
    fireEvent.click(await screen.findByTestId('bulk-delete-confirm'));
    await waitFor(() => {
      expect(document.querySelector('.mesh-toast--danger')).not.toBeNull();
    });
    expect(screen.getByText('WS-1')).toBeTruthy();
  });

  it('merges realtime frames into the list (§3.6 incremental merge)', async () => {
    queueInitialLoad();
    const rt = makeFakeRealtime();
    renderPage(rt.value);
    await screen.findByText('WS-1');
    // created frame adds a row
    await act(async () => {
      rt.emit({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 2,
        event: 'issue.created',
        payload: { issue: issueFixture('iss-9', 'WS-9', 'Realtime born') },
      } as RealtimeEventFrame);
    });
    await screen.findByText('WS-9');
    // deleted frame removes it
    await act(async () => {
      rt.emit({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 3,
        event: 'issue.deleted',
        payload: { id: 'iss-9' },
      } as RealtimeEventFrame);
    });
    await waitFor(() => {
      expect(screen.queryByText('WS-9')).toBeNull();
    });
  });

  it('does not re-insert filtered-out issues via realtime frames (§3.6 水位含 q)', async () => {
    // 过滤切换触发的重拉:仅 WS-2 命中 'docs'
    queueInitialLoad(
      fakeResponse({ body: { data: [ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    const rt = makeFakeRealtime();
    renderPage(rt.value);
    await screen.findByText('WS-1');
    // 搜索 docs:只有 WS-2 命中
    fireEvent.change(screen.getByTestId('issue-filter-q'), { target: { value: 'docs' } });
    await waitFor(() => expect(screen.queryByTestId('issue-row-WS-1')).toBeNull());
    // 迟到的 issue.created 帧不得把 WS-1 重新塞回当前过滤视图
    await act(async () => {
      rt.emit({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 9,
        event: 'issue.created',
        payload: { issue: ISSUE_1 },
      } as RealtimeEventFrame);
    });
    expect(screen.queryByTestId('issue-row-WS-1')).toBeNull();
    // 命中过滤的帧仍会合并
    await act(async () => {
      rt.emit({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 10,
        event: 'issue.updated',
        payload: {
          id: 'iss-2',
          changes: { title: 'Ship the docs v2' },
          updated_at: '2026-07-03T00:00:00Z',
        },
      } as RealtimeEventFrame);
    });
    await screen.findByText('Ship the docs v2');
  });

  it('rejects realtime rows that miss the active category or priority watermark', async () => {
    queueInitialLoad();
    const rt = makeFakeRealtime();
    renderPage(rt.value, '/w/team/issues?category=todo&priority=high');
    await screen.findByText('WS-1');

    await act(async () => {
      rt.emit({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 11,
        event: 'issue.created',
        payload: {
          issue: { ...issueFixture('iss-done', 'WS-10', 'Done elsewhere'), state_category: 'done' },
        },
      } as RealtimeEventFrame);
      rt.emit({
        op: 'event',
        channel: 'workspace:ws-1:issues',
        seq: 12,
        event: 'issue.created',
        payload: {
          issue: { ...issueFixture('iss-urgent', 'WS-11', 'Wrong priority'), priority: 'urgent' },
        },
      } as RealtimeEventFrame);
    });

    expect(screen.queryByText('WS-10')).toBeNull();
    expect(screen.queryByText('WS-11')).toBeNull();
  });

  it('expands the quick create form with project and assignee (§4.3 MEDIUM-3)', async () => {
    const created = { ...issueFixture('iss-4', 'WS-4', 'Expanded create'), project_id: 'prj-9' };
    const projectsResp = fakeResponse({
      body: {
        data: [
          {
            id: 'prj-9',
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
          },
        ],
        next_cursor: null,
      },
    });
    const stub = queueInitialLoad(
      projectsResp,
      fakeResponse({ status: 201, body: { data: created } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-open-create'));
    fireEvent.click(screen.getByTestId('issue-create-expand'));
    await screen.findByTestId('issue-create-project');
    await screen.findByTestId('issue-create-assignee');
    expect(screen.getByRole('option', { name: 'Planner (agent)' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'Suspended member' })).toBeNull();
    fireEvent.change(screen.getByTestId('issue-create-title'), {
      target: { value: 'Expanded create' },
    });
    fireEvent.change(screen.getByTestId('issue-create-project'), { target: { value: 'prj-9' } });
    fireEvent.change(screen.getByTestId('issue-create-assignee'), { target: { value: 'mem-1' } });
    fireEvent.submit(screen.getByTestId('issue-create-form'));
    await screen.findByText('WS-4');
    const posts = stub.calls.filter((c) => c.init?.method === 'POST');
    expect(posts.length).toBe(1);
    expect(JSON.parse(String(posts[0].init?.body))).toEqual({
      title: 'Expanded create',
      priority: 'none',
      project_id: 'prj-9',
      assignee_id: 'mem-1',
    });
  });

  it('passes assignee_id on first load under ?mine=true (B4)', async () => {
    const mine = { ...ISSUE_1, assignee_id: 'mem-1' };
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [mine], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value, '/w/team/issues?mine=true');
    await screen.findByText('WS-1');
    // 首载 issues 请求即携带 assignee_id(修复前首载无过滤参数)
    const issueCalls = stub.calls.filter((c) => String(c.url).includes('/issues?'));
    expect(issueCalls.length).toBeGreaterThan(0);
    expect(String(issueCalls[0].url)).toContain('assignee_id=mem-1');
    // mine 过滤生成 chip(§3.2 过滤 chips)
    expect(screen.getByTestId('filter-chip-mine')).toBeTruthy();
  });

  it('quick create under a priority filter respects the watermark (F3)', async () => {
    const created = issueFixture('iss-5', 'WS-5', 'Off-filter create');
    const stub = queueInitialLoad(
      fakeResponse({ status: 201, body: { data: created } }),
      // F3:不匹配当前过滤 → 触发重拉而非前置渲染
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value, '/w/team/issues?priority=urgent');
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-open-create'));
    fireEvent.change(screen.getByTestId('issue-create-title'), {
      target: { value: 'Off-filter create' },
    });
    fireEvent.submit(screen.getByTestId('issue-create-form'));
    await waitFor(() => {
      expect(stub.calls.filter((c) => c.init?.method === 'POST').length).toBe(1);
    });
    // 创建的 WS-5(priority none)不匹配 priority=urgent 过滤,不前置渲染
    expect(screen.queryByText('WS-5')).toBeNull();
  });

  it('opens the quick create form via ?create=1 (M12)', async () => {
    queueInitialLoad();
    renderPage(makeFakeRealtime().value, '/w/team/issues?create=1');
    await screen.findByTestId('issue-create-form');
    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => expect(screen.queryByTestId('issue-create-form')).toBeNull());
  });

  it('keeps the quick-create draft and renders an inline error after a rejected create', async () => {
    queueInitialLoad(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-open-create'));
    const title = screen.getByTestId('issue-create-title') as HTMLInputElement;
    fireEvent.change(title, { target: { value: 'Keep this draft' } });
    fireEvent.submit(screen.getByTestId('issue-create-form'));
    await screen.findByRole('alert');
    expect(title.value).toBe('Keep this draft');
  });

  it('bulk partial failure toast surfaces per-item codes (F4)', async () => {
    queueInitialLoad(
      fakeResponse({
        status: 422,
        body: {
          error: {
            code: 'bulk_partial_failure',
            message: 'partial',
            details: {
              succeeded: 1,
              failed: 1,
              errors: [{ issue_id: 'iss-2', code: 'forbidden', message: 'no' }],
            },
          },
        },
      }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(screen.getByText('Delete'));
    fireEvent.click(await screen.findByTestId('bulk-delete-confirm'));
    await screen.findByText(/forbidden/);
  });

  it('renders the empty state with a primary action when there are no issues (§7.7)', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    await screen.findByText('No issues yet');
    // 主操作 = 快速创建(权限匹配)
    fireEvent.click(screen.getAllByText('+ New issue')[0]);
    await screen.findByTestId('issue-create-form');
  });

  it('header sort cycles asc → desc → none with aria-sort (§7.6)', async () => {
    queueInitialLoad();
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    const titleHeader = screen.getByRole('button', { name: /Title/ });
    // 升序:Fix < Ship
    fireEvent.click(titleHeader);
    let rows = screen.getAllByTestId(/^issue-row-/);
    expect(rows[0].getAttribute('data-testid')).toBe('issue-row-WS-1');
    expect(rows[0].closest('table')?.querySelector('th[aria-sort="ascending"]')).not.toBeNull();
    // 降序
    fireEvent.click(titleHeader);
    rows = screen.getAllByTestId(/^issue-row-/);
    expect(rows[0].getAttribute('data-testid')).toBe('issue-row-WS-2');
    // 再点 → 清除排序(aria-sort none,恢复服务端序)
    fireEvent.click(titleHeader);
    expect(screen.getByTestId('issue-table')).toHaveAttribute('data-slot', 'table');
    expect(screen.getByTestId('issue-table').querySelector('th[aria-sort="ascending"]')).toBeNull();
    expect(
      screen.getByTestId('issue-table').querySelector('th[aria-sort="descending"]'),
    ).toBeNull();
  });

  it('sorts by priority header (rank compare)', async () => {
    const lowIssue = { ...ISSUE_2, priority: 'urgent' };
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [ISSUE_1, lowIssue], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByRole('button', { name: /Priority/ }));
    const rows = screen.getAllByTestId(/^issue-row-/);
    // urgent(WS-2)排到 high(WS-1)前
    expect(rows[0].getAttribute('data-testid')).toBe('issue-row-WS-2');
  });

  it('filter chips are removable and clear-all appears at ≥2 chips (§3.2)', async () => {
    queueInitialLoad(
      // q 移除后重拉
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value, '/w/team/issues?q=docs&category=todo');
    await screen.findByText('WS-2');
    expect(screen.getByTestId('filter-chip-q')).toBeTruthy();
    expect(screen.getByTestId('filter-chip-category')).toBeTruthy();
    // ≥2 chips → 清除全部
    fireEvent.click(screen.getByText('Clear all'));
    await waitFor(() => {
      expect(screen.queryByTestId('filter-chip-q')).toBeNull();
      expect(screen.queryByTestId('filter-chip-category')).toBeNull();
    });
  });

  it('removing a single chip clears only that filter param', async () => {
    const stub = queueInitialLoad(
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value, '/w/team/issues?priority=urgent&category=todo');
    await screen.findByText('WS-1');
    const chip = screen.getByTestId('filter-chip-priority');
    fireEvent.click(within(chip).getByRole('button'));
    await waitFor(() => {
      expect(screen.queryByTestId('filter-chip-priority')).toBeNull();
    });
    // category chip 仍在
    expect(screen.getByTestId('filter-chip-category')).toBeTruthy();
    // 重拉不再带 priority
    const lastIssues = stub.calls.filter((c) => String(c.url).includes('/issues?')).pop();
    expect(String(lastIssues?.url)).not.toContain('priority=');
  });

  it('saves, applies and deletes a named view via localStorage (§3.2 保存视图)', async () => {
    const stub = queueInitialLoad(
      // 应用视图(写 ?priority=high)后的重拉
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    // 打开保存视图菜单 → 保存当前视图
    fireEvent.click(screen.getByRole('button', { name: 'Saved views' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Save current view' }));
    const nameInput = await screen.findByTestId('saved-view-name');
    expect(nameInput).toHaveAttribute('data-slot', 'input');
    fireEvent.change(nameInput, { target: { value: 'High board' } });
    fireEvent.click(screen.getByTestId('saved-view-save'));
    // 持久化到 localStorage
    const stored = JSON.parse(window.localStorage.getItem(SAVED_VIEWS_STORAGE_KEY) ?? '[]');
    expect(stored).toEqual([{ name: 'High board', params: {} }]);
    // 再次打开菜单 → 预设项可见,点击应用
    fireEvent.click(screen.getByRole('button', { name: 'Saved views' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'High board' }));
    await waitFor(() => {
      expect(screen.queryByText('High board')).toBeNull(); // 菜单关闭
    });
    // 删除预设(en 目录文案为 Delete view "{name}",触发器 name 为空)
    fireEvent.click(screen.getByRole('button', { name: 'Delete view ""' }));
    const deleteItems = await screen.findAllByRole('menuitem', {
      name: 'Delete view "High board"',
    });
    fireEvent.click(deleteItems[0]);
    expect(JSON.parse(window.localStorage.getItem(SAVED_VIEWS_STORAGE_KEY) ?? '[]')).toEqual([]);
    void stub;
  });

  it('keyboard selection: arrows move focus, space toggles, enter opens (§3.2/§10.2)', async () => {
    queueInitialLoad();
    renderPageWithRoutes(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    const row0 = screen.getByTestId('issue-row-WS-1');
    const row1 = screen.getByTestId('issue-row-WS-2');
    row0.focus();
    // ↓ 移到第二行并真实移焦
    fireEvent.keyDown(row0, { key: 'ArrowDown' });
    await waitFor(() => expect(document.activeElement).toBe(row1));
    // 空格切换选中 → 批量条出现
    fireEvent.keyDown(row1, { key: ' ' });
    await screen.findByTestId('bulk-bar');
    // End/Home 边界
    fireEvent.keyDown(row1, { key: 'Home' });
    await waitFor(() => expect(document.activeElement).toBe(row0));
    // Enter 打开详情
    fireEvent.keyDown(row0, { key: 'Enter' });
    await screen.findByTestId('nav-detail');
  });

  it('row secondary menu opens the issue (次要操作经行菜单,§7.6)', async () => {
    queueInitialLoad();
    renderPageWithRoutes(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    const row = screen.getByTestId('issue-row-WS-1');
    fireEvent.click(within(row).getByRole('button', { name: 'Row actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open' }));
    await screen.findByTestId('nav-detail');
  });

  it('warns when a successful bulk reports failed > 0 (§5.5 成功失败计数)', async () => {
    queueInitialLoad(
      fakeResponse({ body: { data: { succeeded: 1, failed: 1, errors: [] } } }),
      fakeResponse({ body: { data: [ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(screen.getByRole('button', { name: 'Set status…' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Todo' }));
    await waitFor(() => {
      expect(document.querySelector('.mesh-toast--warn')).not.toBeNull();
    });
  });

  it('partial failure without per-item errors still reports the tally', async () => {
    queueInitialLoad(
      fakeResponse({
        status: 422,
        body: {
          error: {
            code: 'bulk_partial_failure',
            message: 'partial',
            details: { succeeded: 0, failed: 1 },
          },
        },
      }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(screen.getByText('Delete'));
    fireEvent.click(await screen.findByTestId('bulk-delete-confirm'));
    // 计数 toast(无逐条明细)
    await screen.findByText(/0 succeeded, 1 failed/);
  });

  it('uses safe count defaults when a partial-failure envelope omits tally details', async () => {
    queueInitialLoad(
      fakeResponse({
        status: 422,
        body: {
          error: {
            code: 'bulk_partial_failure',
            message: 'partial',
            details: {},
          },
        },
      }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    fireEvent.click(await screen.findByText('Delete'));
    fireEvent.click(await screen.findByTestId('bulk-delete-confirm'));
    await screen.findByText(/0 succeeded, 1 failed/);
  });

  it('maps a transport-level bulk failure to the generic danger feedback', async () => {
    const stub = queueInitialLoad();
    const queuedFetch = stub.fetchImpl;
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') throw new Error('network unavailable');
      return queuedFetch(input, init);
    }) as typeof fetch);
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    fireEvent.click(await screen.findByText('Delete'));
    fireEvent.click(await screen.findByTestId('bulk-delete-confirm'));
    await waitFor(() => expect(document.querySelector('.mesh-toast--danger')).not.toBeNull());
  });

  it('cancelling the delete confirm dialog performs no bulk call', async () => {
    const stub = queueInitialLoad();
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(screen.getByText('Delete'));
    await screen.findByTestId('bulk-delete-confirm-body');
    // 经对话框关闭按钮(×)取消 → 触发 Dialog onClose
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('bulk-delete-confirm-body')).toBeNull());
    // 再开一次,经对话框内 Cancel 按钮取消
    fireEvent.click(screen.getByText('Delete'));
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(stub.calls.filter((c) => c.init?.method === 'POST').length).toBe(0);
  });

  it('quick create supports create-and-continue then cancel (§9.3 连续创建)', async () => {
    const created = issueFixture('iss-8', 'WS-8', 'Keep going');
    const stub = queueInitialLoad(fakeResponse({ status: 201, body: { data: created } }));
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-open-create'));
    const titleInput = screen.getByTestId('issue-create-title');
    fireEvent.change(titleInput, { target: { value: 'Keep going' } });
    // 优先级选择(覆盖 create 表单 priority onChange)
    fireEvent.change(within(screen.getByTestId('issue-create-form')).getByLabelText('Priority'), {
      target: { value: 'high' },
    });
    fireEvent.click(screen.getByText('Create & add another'));
    await screen.findByText('WS-8');
    // 连续创建:表单仍在、标题已清空
    expect(screen.getByTestId('issue-create-form')).toBeTruthy();
    expect((screen.getByTestId('issue-create-title') as HTMLInputElement).value).toBe('');
    expect(stub.calls.filter((c) => c.init?.method === 'POST').length).toBe(1);
    // 取消关闭表单
    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => expect(screen.queryByTestId('issue-create-form')).toBeNull());
  });

  it('cancelling the save-view dialog stores nothing', async () => {
    queueInitialLoad();
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByRole('button', { name: 'Saved views' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Save current view' }));
    fireEvent.change(await screen.findByTestId('saved-view-name'), { target: { value: 'Nope' } });
    // 经对话框关闭按钮(×)取消 → 触发 Dialog onClose
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('saved-view-form')).toBeNull());
    // 再开一次,经对话框内 Cancel 按钮取消
    fireEvent.click(screen.getByRole('button', { name: 'Saved views' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Save current view' }));
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(window.localStorage.getItem(SAVED_VIEWS_STORAGE_KEY)).toBeNull();
  });

  it('changes filters via the toolbar selects and mine checkbox', async () => {
    const stub = queueInitialLoad(
      // category 变更重拉 + priority 变更重拉 + mine 变更重拉
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'todo' } });
    await waitFor(() => {
      const last = stub.calls.filter((c) => String(c.url).includes('/issues?')).pop();
      expect(String(last?.url)).toContain('state_category=todo');
    });
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'urgent' } });
    await waitFor(() => {
      const last = stub.calls.filter((c) => String(c.url).includes('/issues?')).pop();
      expect(String(last?.url)).toContain('priority=urgent');
    });
    fireEvent.click(screen.getByTestId('issue-filter-mine'));
    await waitFor(() => {
      const last = stub.calls.filter((c) => String(c.url).includes('/issues?')).pop();
      expect(String(last?.url)).toContain('assignee_id=mem-1');
    });
  });

  it('clears active select and mine filters through their toolbar controls', async () => {
    const stub = queueInitialLoad(
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value, '/w/team/issues?category=todo&priority=high&mine=true');
    await screen.findByText('WS-1');

    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'all' } });
    await waitFor(() => expect(screen.queryByTestId('filter-chip-category')).toBeNull());
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'all' } });
    await waitFor(() => expect(screen.queryByTestId('filter-chip-priority')).toBeNull());
    fireEvent.click(screen.getByTestId('issue-filter-mine'));
    await waitFor(() => expect(screen.queryByTestId('filter-chip-mine')).toBeNull());

    const last = stub.calls.filter((call) => String(call.url).includes('/issues?')).pop();
    expect(String(last?.url)).not.toContain('assignee_id=');
  });

  it('removing the q chip clears the search filter', async () => {
    const stub = queueInitialLoad(
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value, '/w/team/issues?q=login');
    await screen.findByText('WS-1');
    const chip = screen.getByTestId('filter-chip-q');
    fireEvent.click(within(chip).getByRole('button'));
    await waitFor(() => expect(screen.queryByTestId('filter-chip-q')).toBeNull());
    const last = stub.calls.filter((c) => String(c.url).includes('/issues?')).pop();
    expect(String(last?.url)).not.toContain('q=');
  });

  it('saves a view with active filter + sort params and applies it later', async () => {
    const stub = queueInitialLoad(
      // 移除 chip 重拉 + 应用视图(priority=high + sort=title)后重拉
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value, '/w/team/issues?priority=high&sort=title&order=desc');
    await screen.findByText('WS-1');
    // 保存当前视图(含 priority + sort 快照)
    fireEvent.click(screen.getByRole('button', { name: 'Saved views' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Save current view' }));
    fireEvent.change(await screen.findByTestId('saved-view-name'), { target: { value: 'Titled' } });
    fireEvent.click(screen.getByTestId('saved-view-save'));
    const stored = JSON.parse(window.localStorage.getItem(SAVED_VIEWS_STORAGE_KEY) ?? '[]');
    expect(stored).toEqual([
      { name: 'Titled', params: { priority: 'high', sort: 'title', order: 'desc' } },
    ]);
    // 清掉过滤后应用视图 → URL 写回 priority=high
    fireEvent.click(within(screen.getByTestId('filter-chip-priority')).getByRole('button'));
    await waitFor(() => expect(screen.queryByTestId('filter-chip-priority')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Saved views' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Titled' }));
    await waitFor(() => {
      const last = stub.calls.filter((c) => String(c.url).includes('/issues?')).pop();
      expect(String(last?.url)).toContain('priority=high');
    });
  });

  it('renders unassigned / status-less / undated rows without crashing (长内容缺字段)', async () => {
    const bare = {
      ...ISSUE_1,
      id: 'iss-7',
      identifier: 'WS-7',
      title: 'Bare issue',
      assignee: null,
      assignee_id: null,
      status: null,
      due_date: null,
    };
    const agentAssigned = {
      ...ISSUE_1,
      id: 'iss-6',
      identifier: 'WS-6',
      title: 'Agent issue',
      assignee: { id: 'mem-9', name: 'Bot', member_type: 'agent' },
      assignee_id: 'mem-9',
    };
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [bare, agentAssigned], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-7');
    // 无负责人 → Unassigned;无 status → 回退 category 名;无截止日 → 空
    expect(screen.getAllByText('Unassigned').length).toBeGreaterThan(0);
    // agent 负责人 → 名称带 (agent) 后缀 + agent 头像
    expect(screen.getByText(/Bot \(agent\)/)).toBeTruthy();
    const row = screen.getByTestId('issue-row-WS-7');
    expect(row.textContent).not.toContain('Aug 15');
    // 行菜单的「选择」次要操作可勾选
    fireEvent.click(within(row).getByRole('button', { name: 'Row actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
  });

  it('opens quick create from the empty-state primary action', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    await screen.findByText('No issues yet');
    // EmptyState 主操作(第二个 '+ New issue',位于空态区)
    const buttons = screen.getAllByText('+ New issue');
    fireEvent.click(buttons[buttons.length - 1]);
    await screen.findByTestId('issue-create-form');
  });

  it('loads more rows via the footer cursor action', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: 'cur-1' } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
      fakeResponse({ body: { data: [ISSUE_1, ISSUE_2], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(
      makeFakeRealtime().value,
      '/w/team/issues?q=login&category=todo&priority=high&mine=true',
    );
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByText('Load more'));
    await screen.findByText('WS-2');
    expect(screen.getAllByText('WS-1')).toHaveLength(1);
    // 第二页请求保留全部过滤水位并携带 cursor。
    const lastCall = stub.calls.filter((c) => String(c.url).includes('/issues?')).pop();
    expect(String(lastCall?.url)).toContain('cursor=cur-1');
    expect(String(lastCall?.url)).toContain('q=login');
    expect(String(lastCall?.url)).toContain('state_category=todo');
    expect(String(lastCall?.url)).toContain('priority=high');
    expect(String(lastCall?.url)).toContain('assignee_id=mem-1');
  });

  it('loads an unfiltered next page and supports deselect-all branches', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [ISSUE_1], next_cursor: 'cur-plain' } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
      fakeResponse({ body: { data: [ISSUE_2], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');

    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await waitFor(() => expect(screen.queryByTestId('bulk-bar')).toBeNull());
    fireEvent.click(screen.getByTestId('issue-select-all'));
    fireEvent.click(screen.getByTestId('issue-select-all'));
    await waitFor(() => expect(screen.queryByTestId('bulk-bar')).toBeNull());

    fireEvent.click(screen.getByText('Load more'));
    await screen.findByText('WS-2');
    const lastCall = stub.calls.filter((call) => String(call.url).includes('/issues?')).pop();
    expect(String(lastCall?.url)).toContain('cursor=cur-plain');
    expect(String(lastCall?.url)).not.toContain('state_category=');
    expect(String(lastCall?.url)).not.toContain('priority=');
    expect(String(lastCall?.url)).not.toContain('assignee_id=');
  });

  it('renders the no-workspace empty state without issuing scoped list requests', async () => {
    const noWorkspaceMe = { ...ME, memberships: [] };
    const stub = stubFetch(fakeResponse({ body: { data: noWorkspaceMe } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    await screen.findByText('No active workspace — create or join one to manage issues.');
    expect(stub.calls.some((call) => String(call.url).includes('/issues?'))).toBe(false);
  });
});
