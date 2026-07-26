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

/** 首轮加载 6 个响应:issue + (statuses, children, deps, activity, members)。 */
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
    fakeResponse({ body: { data: [], next_cursor: null } }),
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
    expect(screen.getByTestId('issue-detail-description').textContent).toBe(
      'Detailed description',
    );
    expect(screen.getByTestId('issue-detail-deps')).toBeTruthy();
    expect(screen.getByText('iss-7')).toBeTruthy();
    expect(screen.getByTestId('issue-detail-activity')).toBeTruthy();
  });

  it('patches the title with version and If-Match on blur (§3.4/§6.14)', async () => {
    const updated = { ...DETAIL, title: 'Renamed', version: 4 };
    const stub = queue(
      fakeResponse({ body: { data: updated } }),
      // reload round after the patch:
      fakeResponse({ body: { data: updated } }),
      fakeResponse({ body: { data: [DETAIL.status, STATUS_IN_PROGRESS], next_cursor: null } }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
    );
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
    fireEvent.change(screen.getByTestId('dep-target-input'), { target: { value: 'iss-7' } });
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
    await screen.findByText('iss-7');
    fireEvent.click(screen.getByText('Remove'));
    // rolled back after the failed DELETE
    await waitFor(() => {
      expect(screen.getByText('iss-7')).toBeTruthy();
    });
  });

  it('shows the error state with retry when the detail request fails', async () => {
    const stub = stubFetch(
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
      // retry round:
      ...detailResponses(),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderDetail();
    const retry = await screen.findByText('Retry');
    fireEvent.click(retry);
    await screen.findByTestId('issue-detail');
  });
});
