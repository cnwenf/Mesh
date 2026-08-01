/**
 * SquadTaskDetailPage 组件测试(squad.md §4.4):
 * 状态/进度条 / 拆解树(缩进子任务·负责人·阶段·blocked_by 等待)/ 计划审批横幅(批准·驳回)/
 * 取消 / 非终态 3s 轮询 getTaskStatus(状态变化即重拉整树)/ 错误态重试。
 * fetch 桩按调用序驱动:users/me → tree(→ 审批/取消写 → tree;轮询 → status…)。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import { SquadTaskDetailPage } from '../SquadTaskDetailPage';

const realtimeHarness = vi.hoisted(() => ({
  current: null as null | {
    client: {
      subscribe: ReturnType<typeof vi.fn>;
      unsubscribe: ReturnType<typeof vi.fn>;
      onFrame: ReturnType<typeof vi.fn>;
    };
  },
}));

vi.mock('../../../shell/AppShell', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../shell/AppShell')>();
  return { ...actual, useRealtimeContext: () => realtimeHarness.current };
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

const AGENT_SNAP = { member_id: 'mem-2', member_type: 'agent', name: 'Builder' };
const HUMAN_SNAP = { member_id: 'mem-1', member_type: 'human', name: 'Owner' };

function taskNode(id: string, title: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    squad_id: 'sq-1',
    issue_id: 'iss-1',
    parent_task_id: 'tk-1',
    root_task_id: 'tk-1',
    depth: 1,
    title_snapshot: title,
    status: 'pending',
    assignee: null,
    stage: null,
    execution_id: null,
    plan_markdown: null,
    result_summary: null,
    failure_reason: null,
    depends_on: [],
    blocked_by: [],
    dispatched_at: null,
    started_at: null,
    finished_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function treeFixture(overrides: Record<string, unknown> = {}) {
  return {
    ...taskNode('tk-1', 'Fix login', { parent_task_id: null, root_task_id: null, depth: 0 }),
    status: 'in_progress',
    children: [
      taskNode('tk-2', 'Write tests', {
        status: 'done',
        assignee: AGENT_SNAP,
        stage: 1,
      }),
      taskNode('tk-3', 'Ship it', {
        status: 'pending',
        stage: 2,
        depends_on: ['tk-2'],
        blocked_by: ['tk-2'],
      }),
    ],
    progress: { total: 2, done: 1, in_progress: 0, pending: 1, failed: 0 },
    ...overrides,
  };
}

function ToastLayer(props: { children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

function renderPage(): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={['/squads/sq-1/tasks/tk-1']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <Routes>
              <Route path="/squads/:squadId/tasks/:taskId" element={<SquadTaskDetailPage />} />
            </Routes>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  realtimeHarness.current = null;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('SquadTaskDetailPage', () => {
  it('renders status, progress and the decomposition tree with blocked indicators', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture() } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-page');
    expect(screen.getByTestId('squad-task-title').textContent).toBe('Fix login');
    // 进度:1/2 完成 → 50%
    expect(screen.getByTestId('squad-task-progress-label').textContent).toBe(
      '1 of 2 subtasks done',
    );
    expect(screen.getByTestId('squad-task-progress').getAttribute('aria-valuenow')).toBe('50');
    // 子任务节点
    expect(screen.getByTestId('squad-tree-node-tk-2')).toBeTruthy();
    expect(screen.getByTestId('squad-tree-node-tk-3')).toBeTruthy();
    // 负责人 / 阶段
    expect(screen.getByText('Builder')).toBeTruthy();
    expect(screen.getByText('Stage 1')).toBeTruthy();
    expect(screen.getByTestId('squad-tree-assignee-tk-2')).toHaveTextContent('Builder');
    // Dependencies remain readable after the blocker finishes; active blockers retain explicit waiting copy.
    expect(screen.getByTestId('squad-tree-dependencies-tk-3')).toHaveTextContent('Write tests');
    // blocked_by「等待 X」
    expect(screen.getByTestId('squad-tree-blocked-tk-3').textContent).toContain('Write tests');
    // 非终态可取消
    expect(screen.getByTestId('squad-task-cancel')).toBeTruthy();
  });

  it('subscribes to the squad realtime channel when a connection is available', async () => {
    const subscribe = vi.fn();
    const unsubscribe = vi.fn();
    const off = vi.fn();
    const onFrame = vi.fn().mockReturnValue(off);
    realtimeHarness.current = { client: { subscribe, unsubscribe, onFrame } };
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture({ status: 'done' }) } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    const view = renderPage();

    await screen.findByTestId('squad-task-page');
    expect(subscribe).toHaveBeenCalledWith('squad:sq-1');
    view.unmount();
    expect(off).toHaveBeenCalled();
    expect(unsubscribe).toHaveBeenCalledWith('squad:sq-1');
  });

  it('renders fallback titles, human ownership, unknown dependencies, and failure context', async () => {
    const richChild = taskNode('tk-human', 'ignored', {
      title_snapshot: null,
      assignee: HUMAN_SNAP,
      blocked_by: ['missing-blocker'],
      depends_on: ['missing-dependency'],
      failure_reason: 'Dependency failed',
      children: [taskNode('tk-nested', 'Nested', { failure_reason: '' })],
    });
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({
        body: {
          data: treeFixture({
            title_snapshot: null,
            status: 'done',
            progress: undefined,
            children: [richChild],
          }),
        },
      }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();

    await screen.findByTestId('squad-task-page');
    expect(screen.getByTestId('squad-task-title')).toHaveTextContent('tk-1');
    expect(screen.getByTestId('squad-task-progress-label')).toHaveTextContent('0 of 0');
    expect(screen.getByTestId('squad-tree-node-tk-human')).toHaveTextContent('tk-human');
    expect(screen.getByTestId('squad-tree-assignee-tk-human')).toHaveTextContent('Owner');
    expect(screen.getByTestId('squad-tree-blocked-tk-human')).toHaveTextContent('missing-blocker');
    expect(screen.getByTestId('squad-tree-dependencies-tk-human')).toHaveTextContent(
      'missing-dependency',
    );
    expect(screen.getByTestId('squad-tree-node-tk-human')).toHaveTextContent('Dependency failed');
    expect(screen.getByTestId('squad-tree-node-tk-nested')).toBeInTheDocument();
  });

  it('shows empty states in both task views when children are omitted', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({
        body: {
          data: treeFixture({ status: 'done', children: undefined }),
        },
      }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();

    await screen.findByTestId('squad-task-page');
    expect(screen.getByText('No subtasks yet')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('squad-view-kanban'));
    expect(screen.getByText('No subtasks yet')).toBeInTheDocument();
  });

  it('shows the error state with retry when the tree request fails', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } }),
      fakeResponse({ body: { data: treeFixture() } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    const retry = await screen.findByText('Retry');
    fireEvent.click(retry);
    await screen.findByTestId('squad-task-page');
  });

  it('maps an unexpected tree transport failure to the generic error copy', async () => {
    const impl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(fakeResponse({ body: { data: ME } }))
      .mockRejectedValueOnce(new Error('socket closed'));
    vi.stubGlobal('fetch', impl);
    renderPage();

    expect(
      await screen.findByText('Network error. Please check your connection and try again.'),
    ).toBeTruthy();
  });

  it('approves a pending plan and reloads the tree', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({
        body: {
          data: treeFixture({
            status: 'awaiting_plan_approval',
            plan_markdown: '# My Plan',
            children: [],
            progress: { total: 0, done: 0, in_progress: 0, pending: 0, failed: 0 },
          }),
        },
      }),
      fakeResponse({ body: { data: { id: 'ap-1', status: 'approved' } } }),
      fakeResponse({
        body: {
          data: treeFixture({
            status: 'dispatching',
            children: [],
            progress: { total: 0, done: 0, in_progress: 0, pending: 0, failed: 0 },
          }),
        },
      }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-approval');
    expect(screen.getByTestId('squad-task-approval')).toHaveClass('mesh-squads__decision-card');
    // 方案 Markdown 经净化渲染
    expect(screen.getByTestId('squad-task-plan').textContent).toContain('My Plan');
    expect(screen.getByTestId('squad-task-plan').closest('aside')).not.toBeNull();
    fireEvent.click(screen.getByTestId('squad-task-approve'));
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.init?.method === 'POST');
      expect(String(posts[0].url)).toContain('/tasks/tk-1/plan/approve');
    });
    await screen.findByText('Dispatching');
    expect(screen.queryByTestId('squad-task-approval')).toBeNull();
  });

  it('rejects a pending plan via the reject endpoint', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({
        body: {
          data: treeFixture({
            status: 'awaiting_plan_approval',
            plan_markdown: '# My Plan',
            children: [],
            progress: { total: 0, done: 0, in_progress: 0, pending: 0, failed: 0 },
          }),
        },
      }),
      fakeResponse({ body: { data: { id: 'ap-1', status: 'rejected' } } }),
      fakeResponse({
        body: {
          data: treeFixture({
            status: 'decomposing',
            children: [],
            progress: { total: 0, done: 0, in_progress: 0, pending: 0, failed: 0 },
          }),
        },
      }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-approval');
    fireEvent.click(screen.getByTestId('squad-task-reject'));
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.init?.method === 'POST');
      expect(String(posts[0].url)).toContain('/tasks/tk-1/plan/reject');
    });
    await screen.findByText('Decomposing');
  });

  it('cancels a non-terminal task and hides the cancel button once cancelled', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture() } }),
      fakeResponse({ body: { data: { cancelled: true, task_id: 'tk-1' } } }),
      fakeResponse({ body: { data: treeFixture({ status: 'cancelled' }) } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-page');
    fireEvent.click(screen.getByTestId('squad-task-cancel'));
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.init?.method === 'POST');
      expect(String(posts[0].url)).toContain('/tasks/tk-1/cancel');
    });
    await screen.findByText('Cancelled');
    expect(screen.queryByTestId('squad-task-cancel')).toBeNull();
  });

  it('polls status every 3s while non-terminal and reloads the tree on change', async () => {
    vi.useFakeTimers();
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture() } }),
      // 编排流尝试:无主体 → 不可用,静默退出,由轮询兜底(§3.5)。不消耗后续队列语义。
      fakeResponse({}),
      // poll #1: no change
      fakeResponse({
        body: { data: { task_id: 'tk-1', status: 'in_progress', result_summary: null } },
      }),
      // poll #2: changed → reload tree
      fakeResponse({
        body: { data: { task_id: 'tk-1', status: 'done', result_summary: 'All good' } },
      }),
      fakeResponse({ body: { data: treeFixture({ status: 'done', result_summary: 'All good' }) } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await vi.waitFor(() => {
      expect(screen.getByTestId('squad-task-title').textContent).toBe('Fix login');
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await vi.waitFor(() => {
      expect(screen.getByTestId('squad-task-status').textContent).toContain('Done');
    });
    expect(screen.getByTestId('squad-task-result').textContent).toBe('All good');
    // 终态:取消按钮消失,且轮询恰为 2 次
    expect(screen.queryByTestId('squad-task-cancel')).toBeNull();
    const statusCalls = stub.calls.filter((c) => String(c.url).includes('/status'));
    expect(statusCalls.length).toBe(2);
  });

  it('toggles to the kanban view and buckets subtasks into status columns', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture() } }),
      fakeResponse({}),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-page');
    // 默认树视图:无看板
    expect(screen.queryByTestId('squad-kanban')).toBeNull();
    fireEvent.click(screen.getByTestId('squad-view-kanban'));
    await screen.findByTestId('squad-kanban');
    // 五列齐备
    for (const key of ['pending', 'in_progress', 'blocked', 'done', 'failed']) {
      expect(screen.getByTestId(`squad-kanban-col-${key}`)).toBeTruthy();
    }
    // tk-2(done)归 Done 列,tk-3(pending)归 Pending 列
    expect(screen.getByTestId('squad-kanban-card-tk-2')).toBeTruthy();
    expect(screen.getByTestId('squad-kanban-card-tk-3')).toBeTruthy();
    expect(screen.getByTestId('squad-kanban-count-done').textContent).toBe('1');
    expect(screen.getByTestId('squad-kanban-count-pending').textContent).toBe('1');
    // 切回树视图:看板消失,树复现
    fireEvent.click(screen.getByTestId('squad-view-tree'));
    await screen.findByTestId('squad-tree-node-tk-2');
    expect(screen.queryByTestId('squad-kanban')).toBeNull();
  });

  it('fires moveTaskStatus and refetches when a card is dropped onto a column', async () => {
    const moved = taskNode('tk-3', 'Ship it', { status: 'in_progress' });
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture() } }),
      fakeResponse({}),
      fakeResponse({ body: { data: moved } }),
      fakeResponse({
        body: {
          data: treeFixture({ children: [taskNode('tk-3', 'Ship it', { status: 'in_progress' })] }),
        },
      }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-page');
    fireEvent.click(screen.getByTestId('squad-view-kanban'));
    await screen.findByTestId('squad-kanban');
    // 把 tk-3(pending)拖到 In progress 列
    fireEvent.drop(screen.getByTestId('squad-kanban-col-in_progress'), {
      dataTransfer: { getData: () => 'tk-3' },
    });
    await waitFor(() => {
      const patches = stub.calls.filter((c) => c.init?.method === 'PATCH');
      expect(patches.length).toBe(1);
      expect(String(patches[0].url)).toContain('/tasks/tk-3/status');
      expect(JSON.parse(String(patches[0].init?.body))).toEqual({ status: 'in_progress' });
    });
  });

  it('shows a warning and does not call the API for a nonsensical drop onto Pending', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture() } }),
      fakeResponse({}),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-page');
    fireEvent.click(screen.getByTestId('squad-view-kanban'));
    await screen.findByTestId('squad-kanban');
    // tk-2(done)拖回 Pending 列:客户端判定无效,仅提示不发请求
    fireEvent.drop(screen.getByTestId('squad-kanban-col-pending'), {
      dataTransfer: { getData: () => 'tk-2' },
    });
    await screen.findByText('That move is not allowed.');
    const patches = stub.calls.filter((c) => c.init?.method === 'PATCH');
    expect(patches.length).toBe(0);
  });

  it('keeps the task usable when cancel, approval and kanban mutations fail', async () => {
    const calls: { url: string; method: string }[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/stream')) return fakeResponse({});
      if (method !== 'GET') {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'mutation failed' } },
        });
      }
      if (url.includes('/status')) {
        return fakeResponse({
          body: { data: { task_id: 'tk-1', status: 'awaiting_plan_approval' } },
        });
      }
      return fakeResponse({
        body: {
          data: treeFixture({ status: 'awaiting_plan_approval', plan_markdown: null }),
        },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();

    await screen.findByTestId('squad-task-approval');
    expect(screen.queryByTestId('squad-task-plan')).toBeNull();
    fireEvent.click(screen.getByTestId('squad-task-cancel'));
    fireEvent.click(screen.getByTestId('squad-task-approve'));
    fireEvent.click(screen.getByTestId('squad-view-kanban'));
    fireEvent.drop(await screen.findByTestId('squad-kanban-col-in_progress'), {
      dataTransfer: { getData: () => 'tk-3' },
    });

    await waitFor(() => expect(calls.filter((call) => call.method !== 'GET')).toHaveLength(3));
    expect(await screen.findByTestId('squad-task-page')).toBeTruthy();
  });

  it('silently retries after a transient polling failure', async () => {
    vi.useFakeTimers();
    let statusCalls = 0;
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/stream')) return fakeResponse({});
      if (url.includes('/status')) {
        statusCalls += 1;
        throw new Error('temporary network failure');
      }
      return fakeResponse({ body: { data: treeFixture() } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await vi.waitFor(() => expect(screen.getByTestId('squad-task-page')).toBeTruthy());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(statusCalls).toBe(1);
    expect(screen.getByTestId('squad-task-page')).toBeTruthy();
  });
});
