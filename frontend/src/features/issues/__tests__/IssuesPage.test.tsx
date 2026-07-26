/**
 * IssuesPage 组件测试(issue.md §4.1/§4.2/§4.3):
 * 列表渲染 / 骨架 / 错误态重试 / 快速创建 / 勾选批量工具条 / 实时帧合并。
 * fetch 桩按调用序驱动:users/me → members → issues → statuses。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
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
import { IssuesPage } from '../IssuesPage';

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

function renderPage(realtime: RealtimeContextValue): void {
  render(
    <MemoryRouter initialEntries={['/issues']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime}>
              <IssuesPage />
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
    expect(rt.subscribe).toHaveBeenCalledWith('workspace:ws-1:issues');
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

  it('shows the error state with retry when the list request fails', async () => {
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
  });

  it('opens the bulk bar on selection and bulk-deletes (§5.5)', async () => {
    queueInitialLoad(
      fakeResponse({ body: { data: { succeeded: 1, failed: 0, errors: [] } } }),
      // reload after bulk (issues + statuses only):
      fakeResponse({ body: { data: [ISSUE_2], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-select-iss-1'));
    await screen.findByTestId('issue-bulkbar');
    fireEvent.click(screen.getByText('Delete'));
    await waitFor(() => {
      expect(screen.queryByText('WS-1')).toBeNull();
    });
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
        payload: { id: 'iss-2', changes: { title: 'Ship the docs v2' }, updated_at: '2026-07-03T00:00:00Z' },
      } as RealtimeEventFrame);
    });
    await screen.findByText('Ship the docs v2');
  });

  it('expands the quick create form with project and assignee (§4.3 MEDIUM-3)', async () => {
    const created = { ...issueFixture('iss-4', 'WS-4', 'Expanded create'), project_id: 'prj-9' };
    const projectsResp = fakeResponse({
      body: {
        data: [
          { id: 'prj-9', name: 'Apollo', key: 'APL', status: 'active', health: null,
            visibility: 'public', lead: null, lead_member_id: null, start_date: null,
            target_date: null, progress: 0, open_issues: 0, done_issues: 0, issue_seq: 1,
            archived: false, archived_at: null, my_role: 'lead',
            created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-01T00:00:00Z' },
        ],
        next_cursor: null,
      },
    });
    const stub = queueInitialLoad(
      // 展开按需加载项目名册(成员名册由页面下传);created 收尾,
      // 无论展开 effect 触发一次还是两次,创建调用都落在 created 上(stub 复用末尾)
      projectsResp,
      fakeResponse({ status: 201, body: { data: created } }),
    );
    renderPage(makeFakeRealtime().value);
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-open-create'));
    fireEvent.click(screen.getByTestId('issue-create-expand'));
    await screen.findByTestId('issue-create-project');
    await screen.findByTestId('issue-create-assignee');
    fireEvent.change(screen.getByTestId('issue-create-title'), { target: { value: 'Expanded create' } });
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
    render(
      <MemoryRouter initialEntries={['/issues?mine=true']}>
        <ThemeProvider>
          <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
            <ToastLayer>
              <RealtimeContext.Provider value={makeFakeRealtime().value}>
                <IssuesPage />
              </RealtimeContext.Provider>
            </ToastLayer>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    await screen.findByText('WS-1');
    // 首载 issues 请求即携带 assignee_id(修复前首载无过滤参数)
    const issueCalls = stub.calls.filter((c) => String(c.url).includes('/issues?'));
    expect(issueCalls.length).toBeGreaterThan(0);
    expect(String(issueCalls[0].url)).toContain('assignee_id=mem-1');
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
    render(
      <MemoryRouter initialEntries={['/issues?priority=urgent']}>
        <ThemeProvider>
          <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
            <ToastLayer>
              <RealtimeContext.Provider value={makeFakeRealtime().value}>
                <IssuesPage />
              </RealtimeContext.Provider>
            </ToastLayer>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    await screen.findByText('WS-1');
    fireEvent.click(screen.getByTestId('issue-open-create'));
    fireEvent.change(screen.getByTestId('issue-create-title'), { target: { value: 'Off-filter create' } });
    fireEvent.submit(screen.getByTestId('issue-create-form'));
    await waitFor(() => {
      expect(stub.calls.filter((c) => c.init?.method === 'POST').length).toBe(1);
    });
    // 创建的 WS-5(priority none)不匹配 priority=urgent 过滤,不前置渲染
    expect(screen.queryByText('WS-5')).toBeNull();
  });

  it('opens the quick create form via ?create=1 (M12)', async () => {
    queueInitialLoad();
    render(
      <MemoryRouter initialEntries={['/issues?create=1']}>
        <ThemeProvider>
          <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
            <ToastLayer>
              <RealtimeContext.Provider value={makeFakeRealtime().value}>
                <IssuesPage />
              </RealtimeContext.Provider>
            </ToastLayer>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    await screen.findByTestId('issue-create-form');
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
    await screen.findByTestId('issue-bulkbar');
    fireEvent.click(screen.getByText('Delete'));
    await screen.findByText(/forbidden/);
  });

  it('renders the empty state when there are no issues', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
      fakeResponse({ body: { data: [STATUS_TODO], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage(makeFakeRealtime().value);
    await screen.findByText('No issues yet');
  });
});
