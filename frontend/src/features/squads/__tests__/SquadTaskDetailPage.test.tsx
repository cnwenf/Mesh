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
import type { RealtimeClient } from '../../../realtime';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { SquadTaskDetailPage } from '../SquadTaskDetailPage';

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

function makeFakeRealtime(): {
  value: RealtimeContextValue;
  subscribe: ReturnType<typeof vi.fn>;
  unsubscribe: ReturnType<typeof vi.fn>;
  emit: (frame: RealtimeEventFrame) => void;
} {
  const listeners: Array<(frame: RealtimeEventFrame) => void> = [];
  const subscribe = vi.fn();
  const unsubscribe = vi.fn();
  const client = {
    subscribe,
    unsubscribe,
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
    subscribe,
    unsubscribe,
    emit: (frame) => {
      for (const listener of listeners) listener(frame);
    },
  };
}

function renderPage(realtime: RealtimeContextValue | null = null): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={['/squads/sq-1/tasks/tk-1']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime}>
              <Routes>
                <Route path="/squads/:squadId/tasks/:taskId" element={<SquadTaskDetailPage />} />
              </Routes>
            </RealtimeContext.Provider>
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
    // blocked_by「等待 X」
    expect(screen.getByTestId('squad-tree-blocked-tk-3').textContent).toContain('Write tests');
    // 非终态可取消
    expect(screen.getByTestId('squad-task-cancel')).toBeTruthy();
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
    // 方案 Markdown 经净化渲染
    expect(screen.getByTestId('squad-task-plan').textContent).toContain('My Plan');
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

  it('keeps a task cancellable and reports the API error when cancellation fails', async () => {
    let cancelCalls = 0;
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'http://mesh.test').pathname;
      const method = init?.method ?? 'GET';
      if (path === '/api/v1/users/me' && method === 'GET') {
        return fakeResponse({ body: { data: ME } });
      }
      if (path.endsWith('/tasks/tk-1/tree') && method === 'GET') {
        return fakeResponse({ body: { data: treeFixture() } });
      }
      if (path.endsWith('/tasks/tk-1/stream') && method === 'GET') {
        return fakeResponse({});
      }
      if (path.endsWith('/tasks/tk-1/cancel') && method === 'POST') {
        cancelCalls += 1;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'cancel failed' } },
        });
      }
      throw new Error(`Unexpected ${method} ${path}`);
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-page');

    fireEvent.click(screen.getByTestId('squad-task-cancel'));
    await screen.findByText('An internal error occurred. Please try again.');
    expect(cancelCalls).toBe(1);
    expect(screen.getByTestId('squad-task-cancel')).toBeTruthy();
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

  it('keeps the task visible when a status poll fails transiently', async () => {
    vi.useFakeTimers();
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture() } }),
      fakeResponse({}),
      fakeResponse({
        status: 503,
        body: { error: { code: 'unavailable', message: 'retry later' } },
      }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await vi.waitFor(() => {
      expect(screen.getByTestId('squad-task-title').textContent).toBe('Fix login');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(screen.getByTestId('squad-task-page')).toBeTruthy();
    expect(screen.queryByText('We could not load this content. Please try again.')).toBeNull();
    const statusCalls = stub.calls.filter((call) => String(call.url).includes('/status'));
    expect(statusCalls).toHaveLength(1);
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

  it('renders membership failures and no-workspace responses as terminal states', async () => {
    const failed = stubFetch(
      fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'membership failed' } },
      }),
    );
    vi.stubGlobal('fetch', failed.fetchImpl);
    const first = renderPage();
    await screen.findByText('We could not load this content. Please try again.');
    expect(screen.queryByTestId('squad-task-page')).toBeNull();
    first.unmount();

    const missing = stubFetch(fakeResponse({ body: { data: { ...ME, memberships: [] } } }));
    vi.stubGlobal('fetch', missing.fetchImpl);
    renderPage();
    await screen.findByText('Select a workspace to view its squads.');
    expect(screen.queryByTestId('squad-task-page')).toBeNull();
  });

  it('renders an approval task with omitted progress, plan, and children safely', async () => {
    const sparseTree = treeFixture({
      title_snapshot: null,
      status: 'awaiting_plan_approval',
      plan_markdown: '',
      children: undefined,
      progress: undefined,
    });
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: sparseTree } }),
      fakeResponse({}),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-page');

    expect(screen.getByTestId('squad-task-title')).toHaveTextContent('tk-1');
    expect(screen.getByTestId('squad-task-progress-label')).toHaveTextContent(
      '0 of 0 subtasks done',
    );
    expect(screen.getByTestId('squad-task-progress')).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByTestId('squad-task-approval')).toBeTruthy();
    expect(screen.queryByTestId('squad-task-plan')).toBeNull();
    expect(screen.getByText('No subtasks yet')).toBeTruthy();

    fireEvent.click(screen.getByTestId('squad-view-kanban'));
    expect(await screen.findByText('No subtasks yet')).toBeTruthy();
  });

  it('falls back to task ids for missing titles and unknown blockers', async () => {
    const fallbackChild = taskNode('tk-2', 'ignored', {
      title_snapshot: null,
      blocked_by: ['missing-task'],
      children: [taskNode('tk-3', 'Nested task')],
    });
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({
        body: {
          data: treeFixture({
            children: [fallbackChild],
            progress: { total: 2, done: 0, in_progress: 0, pending: 2, failed: 0 },
          }),
        },
      }),
      fakeResponse({}),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderPage();
    await screen.findByTestId('squad-task-page');

    expect(screen.getByTestId('squad-tree-node-tk-2')).toHaveTextContent('tk-2');
    expect(screen.getByTestId('squad-tree-blocked-tk-2')).toHaveTextContent('missing-task');
    expect(screen.getByTestId('squad-tree-node-tk-3')).toHaveTextContent('Nested task');
  });

  it('subscribes to realtime, ignores other channels, and reloads on the squad channel', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: { data: treeFixture({ title_snapshot: 'Before realtime' }) } }),
      fakeResponse({}),
      fakeResponse({ body: { data: treeFixture({ title_snapshot: 'After realtime' }) } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    const realtime = makeFakeRealtime();
    const page = renderPage(realtime.value);
    await screen.findByText('Before realtime');
    expect(realtime.subscribe).toHaveBeenCalledWith('squad:sq-1');

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'squad:another-squad',
        seq: 1,
        event: 'squad_task.updated',
        payload: { task_id: 'tk-other' },
      } as RealtimeEventFrame);
    });
    expect(screen.getByText('Before realtime')).toBeTruthy();

    await act(async () => {
      realtime.emit({
        op: 'event',
        channel: 'squad:sq-1',
        seq: 2,
        event: 'squad_task.updated',
        payload: { task_id: 'tk-1' },
      } as RealtimeEventFrame);
    });
    await screen.findByText('After realtime');
    page.unmount();
    expect(realtime.unsubscribe).toHaveBeenCalledWith('squad:sq-1');
  });
});
