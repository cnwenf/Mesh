/**
 * ApprovalsPage 页面测试(README §6.10):三类 subject 卡片字段、批准/拒绝
 * (含留言 Dialog)、过期重新发起、agent 门控、空态、错误重试、双路由工作区
 * 解析(平直 /approvals 走 memberships,工作区路由走 Provider 上下文)。
 * MeshApiClient 经 vi.mock 以桩替代(与 InsightsPage 测试同约定);新增 i18n
 * 键在测试环境呈回退标记,故一律按 testid/role/href 断言,不断言文案。
 */
import { act, fireEvent, renderHook, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { ApprovalsPage } from '../ApprovalsPage';
import { useApprovalsWorkspace } from '../useApprovalsWorkspace';

const state = vi.hoisted(() => ({
  optionalContext: null as unknown,
  principal: null as unknown,
  me: null as unknown,
  requestCalls: [] as Array<{ method: string; path: string }>,
  listCalls: [] as Array<{ path: string; query?: Record<string, unknown> }>,
  postCalls: [] as Array<{ path: string; body?: unknown }>,
  listShouldFail: false,
  postShouldFail: false,
  /** 抛出非 MeshApiError 的普通错误(覆盖错误归一化回退分支) */
  listThrowPlain: false,
  postThrowPlain: false,
  meShouldFail: false,
  /** 列表请求挂起,由测试手动 resolve/reject(覆盖过期响应竞态) */
  listDeferred: false,
  listResolvers: [] as Array<{
    resolve: (envelope: unknown) => void;
    reject: (err: unknown) => void;
  }>,
  pending: [] as unknown[],
  byStatus: {} as Record<string, unknown[]>,
}));

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>();
  class StubMeshApiClient {
    async request(method: string, path: string, opts?: { body?: unknown }) {
      state.requestCalls.push({ method, path });
      if (method === 'GET' && path === '/api/v1/me') return state.principal;
      if (method === 'GET' && path === '/api/v1/users/me') {
        if (state.meShouldFail) {
          throw new actual.MeshApiError({ status: 500, code: 'internal_error', message: 'boom' });
        }
        return state.me;
      }
      if (path.endsWith('/approve') || path.endsWith('/reject')) {
        state.postCalls.push({ path, body: opts?.body });
        if (state.postThrowPlain) throw new Error('plain failure');
        if (state.postShouldFail) {
          throw new actual.MeshApiError({ status: 403, code: 'forbidden', message: 'no' });
        }
        return { id: path.split('/')[5] };
      }
      throw new Error(`unexpected request ${method} ${path}`);
    }

    async list(path: string, opts?: { query?: Record<string, unknown> }) {
      state.listCalls.push({ path, query: opts?.query });
      if (state.listDeferred) {
        return new Promise((resolve, reject) => {
          state.listResolvers.push({ resolve, reject });
        });
      }
      if (state.listThrowPlain) throw new Error('plain failure');
      if (state.listShouldFail) {
        throw new actual.MeshApiError({ status: 500, code: 'internal_error', message: 'boom' });
      }
      const query = opts?.query ?? {};
      if (query.role === 'mine') return { data: state.pending, next_cursor: null };
      const status = String(query.status ?? '');
      return { data: state.byStatus[status] ?? [], next_cursor: null };
    }
  }
  return { ...actual, MeshApiClient: StubMeshApiClient, getToken: () => 'token' };
});

vi.mock('../../../workspace/WorkspaceProvider', () => ({
  useOptionalWorkspace: () => state.optionalContext,
}));

const futureIso = (minutes: number): string => new Date(Date.now() + minutes * 60000).toISOString();
const pastIso = (): string => new Date(Date.now() - 5 * 60000).toISOString();

const TOOL_CALL = {
  id: 'ap-tool',
  subject_type: 'tool_call',
  subject_execution_id: 'ex1',
  subject_task_id: null,
  status: 'pending',
  action_summary: {
    action: 'shell.run',
    capability: 'shell',
    permission: 'execute',
    impact_scope: 'repo mesh',
    estimated_cost: '~2s',
    resume_context: { completed_steps: 3, pending_tool_call: 'rm -rf /tmp/x' },
  },
  requested_at: pastIso(),
  expires_at: futureIso(30),
  decided_at: null,
  decision_comment: null,
  execution_status: 'awaiting_approval',
};

const AUTOPILOT = {
  id: 'ap-auto',
  subject_type: 'autopilot_action',
  subject_execution_id: null,
  subject_task_id: null,
  status: 'pending',
  action_summary: {
    action: 'autopilot_run',
    run_id: 'run1',
    impact_scope: { trigger_type: 'cron' },
  },
  requested_at: pastIso(),
  expires_at: futureIso(60),
  decided_at: null,
  decision_comment: null,
  execution_status: null,
};

const SQUAD_PLAN = {
  id: 'ap-squad',
  subject_type: 'squad_plan',
  subject_execution_id: null,
  subject_task_id: 't1',
  status: 'pending',
  action_summary: {
    plan_digest: 'Split into 3 subtasks',
    impact_scope: 'issue 12 subtree',
    subtask_count: 3,
    detail: { squad_id: 'sq1' },
  },
  requested_at: pastIso(),
  expires_at: futureIso(90),
  decided_at: null,
  decision_comment: null,
  execution_status: null,
};

const EXPIRED = {
  ...TOOL_CALL,
  id: 'ap-exp',
  subject_execution_id: 'ex9',
  status: 'expired',
  expires_at: pastIso(),
  action_summary: { action: 'shell.run' },
};

const APPROVED = {
  ...TOOL_CALL,
  id: 'ap-ok',
  status: 'approved',
  decided_at: new Date().toISOString(),
  decision_comment: 'fine by me',
};

const HUMAN_ME = {
  user: { id: 'u1', email: 'u1@x.io', display_name: 'U1' },
  memberships: [
    {
      workspace_id: 'ws1',
      workspace_name: 'WS',
      workspace_slug: 'ws',
      role: 'admin',
      status: 'active',
      joined_at: null,
    },
  ],
};

beforeEach(() => {
  state.optionalContext = null;
  state.principal = HUMAN_ME.user;
  state.me = HUMAN_ME;
  state.requestCalls = [];
  state.listCalls = [];
  state.postCalls = [];
  state.listShouldFail = false;
  state.postShouldFail = false;
  state.listThrowPlain = false;
  state.postThrowPlain = false;
  state.meShouldFail = false;
  state.listDeferred = false;
  state.listResolvers = [];
  state.pending = [TOOL_CALL, AUTOPILOT, SQUAD_PLAN];
  state.byStatus = { expired: [EXPIRED], approved: [APPROVED] };
});

describe('ApprovalsPage pending inbox', () => {
  it('resolves the workspace from memberships on the flat route and lists pending approvals', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByTestId('approvals-list')).toBeInTheDocument();
    });
    // 平直路由:经 /users/me 的 memberships 解析出 ws1
    expect(state.listCalls[0]?.path).toBe('/api/v1/workspaces/ws1/approvals');
    expect(state.listCalls[0]?.query).toEqual({ role: 'mine' });
    expect(screen.getByTestId('approval-card-ap-tool')).toBeInTheDocument();
    expect(screen.getByTestId('approval-card-ap-auto')).toBeInTheDocument();
    expect(screen.getByTestId('approval-card-ap-squad')).toBeInTheDocument();
  });

  it('shows subject deep links, permission chips, impact/cost and the resume hint', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByTestId('approvals-list')).toBeInTheDocument();
    });
    expect(screen.getByTestId('approval-link-ap-tool')).toHaveAttribute('href', '/executions/ex1');
    expect(screen.getByTestId('approval-link-ap-auto')).toHaveAttribute(
      'href',
      '/autopilots/runs/run1',
    );
    expect(screen.getByTestId('approval-link-ap-squad')).toHaveAttribute(
      'href',
      '/squads/sq1/tasks/t1',
    );
    const toolCard = within(screen.getByTestId('approval-card-ap-tool'));
    expect(toolCard.getByTestId('approval-impact-ap-tool')).toBeInTheDocument();
    expect(toolCard.getByTestId('approval-resume-ap-tool')).toBeInTheDocument();
    expect(toolCard.getByTestId('approval-expires-ap-tool')).toBeInTheDocument();
    // autopilot 无 resume 续跑提示(协议仅 tool_call/squad_plan)
    expect(
      within(screen.getByTestId('approval-card-ap-auto')).queryByTestId('approval-resume-ap-auto'),
    ).toBeNull();
  });

  it('approve posts to /approve and flips the row without reload', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByTestId('approval-approve-ap-tool')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('approval-approve-ap-tool'));
    await waitFor(() => {
      expect(state.postCalls.some((c) => c.path.endsWith('/approvals/ap-tool/approve'))).toBe(true);
    });
    await waitFor(() => {
      expect(screen.getByTestId('approval-status-ap-tool')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('approval-approve-ap-tool')).toBeNull();
  });

  it('rolls the row back when the approve call fails', async () => {
    state.postShouldFail = true;
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByTestId('approval-approve-ap-tool')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('approval-approve-ap-tool'));
    await waitFor(() => {
      expect(state.postCalls.length).toBeGreaterThan(0);
    });
    // 回滚:行仍是 pending(批准按钮仍在),并给出可见失败反馈(toast 区存在)
    await waitFor(() => {
      expect(screen.getByTestId('approval-approve-ap-tool')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('approval-status-ap-tool')).toBeNull();
  });

  it('reject collects an optional comment through the dialog and posts it', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByTestId('approval-reject-ap-tool')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('approval-reject-ap-tool'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByTestId('approval-reject-comment'), {
      target: { value: 'not safe' },
    });
    fireEvent.click(within(dialog).getByTestId('approval-reject-confirm'));
    await waitFor(() => {
      const rejectCall = state.postCalls.find((c) => c.path.endsWith('/reject'));
      expect(rejectCall?.body).toEqual({ comment: 'not safe' });
    });
    await waitFor(() => {
      expect(screen.getByTestId('approval-status-ap-tool')).toBeInTheDocument();
    });
  });

  it('renders decided rows read-only with the decision comment', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals?status=approved' });
    await waitFor(() => {
      expect(screen.getByTestId('approval-card-ap-ok')).toBeInTheDocument();
    });
    const card = within(screen.getByTestId('approval-card-ap-ok'));
    expect(card.queryByTestId('approval-approve-ap-ok')).toBeNull();
    expect(card.getByTestId('approval-status-ap-ok')).toBeInTheDocument();
  });
});

describe('ApprovalsPage states', () => {
  it('shows the expired badge and a relaunch deep link on expired cards', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals?status=expired' });
    await waitFor(() => {
      expect(screen.getByTestId('approval-card-ap-exp')).toBeInTheDocument();
    });
    expect(screen.getByTestId('approval-expired-ap-exp')).toBeInTheDocument();
    expect(screen.getByTestId('approval-link-ap-exp')).toHaveAttribute('href', '/executions/ex9');
    expect(screen.queryByTestId('approval-approve-ap-exp')).toBeNull();
  });

  it('switches tabs via the URL status param', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByTestId('approvals-list')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('tab', { name: 'Expired' }));
    await waitFor(() => {
      expect(state.listCalls.some((c) => c.query?.status === 'expired')).toBe(true);
    });
    await waitFor(() => {
      expect(screen.getByTestId('approval-card-ap-exp')).toBeInTheDocument();
    });
  });

  it('gates the flat route from the active agent principal without listing approvals', async () => {
    state.principal = {
      kind: 'agent',
      id: 'member-agent',
      member_type: 'agent',
      workspace_id: 'ws1',
      role: 'member',
      name: 'Builder',
      scopes: ['approval:read'],
    };
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByTestId('approvals-agent-gated')).toBeInTheDocument();
    });
    expect(state.listCalls).toHaveLength(0); // 门控前置,不发列表请求
    expect(state.requestCalls).not.toContainEqual({ method: 'GET', path: '/api/v1/users/me' });
  });

  it('gates the workspace route from its matching agent principal without listing approvals', async () => {
    state.optionalContext = {
      status: 'ready',
      workspace: { id: 'ws2', slug: 'team-b' },
      error: null,
      isAdmin: false,
      isOwner: false,
      refresh: vi.fn(),
      patch: vi.fn(),
    };
    state.principal = {
      kind: 'agent',
      id: 'member-agent',
      member_type: 'agent',
      workspace_id: 'ws2',
      role: 'member',
      name: 'Builder',
      scopes: ['approval:read'],
    };

    renderWithProviders(<ApprovalsPage />, { route: '/w/team-b/approvals' });

    await waitFor(() => {
      expect(screen.getByTestId('approvals-agent-gated')).toBeInTheDocument();
    });
    expect(state.listCalls).toHaveLength(0);
    expect(state.requestCalls).not.toContainEqual({ method: 'GET', path: '/api/v1/users/me' });
  });

  it('shows the empty state when the inbox has nothing pending', async () => {
    state.pending = [];
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByRole('tablist')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('approvals-list')).toBeNull();
    expect(screen.getByText('No approvals waiting for you')).toBeInTheDocument();
  });

  it('shows the error state with retry when the list fails', async () => {
    state.listShouldFail = true;
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    });
    state.listShouldFail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => {
      expect(screen.getByTestId('approvals-list')).toBeInTheDocument();
    });
  });

  it('resolves the workspace from the provider context on workspace routes', async () => {
    state.optionalContext = {
      status: 'ready',
      workspace: { id: 'ws2', slug: 'team-b' },
      error: null,
      isAdmin: true,
      isOwner: false,
      refresh: vi.fn(),
      patch: vi.fn(),
    };
    state.me = {
      ...HUMAN_ME,
      memberships: [
        HUMAN_ME.memberships[0],
        {
          ...HUMAN_ME.memberships[0],
          workspace_id: 'ws2',
          workspace_slug: 'team-b',
        },
      ],
    };
    renderWithProviders(<ApprovalsPage />, { route: '/w/team-b/approvals' });
    await waitFor(() => {
      expect(screen.getByTestId('approvals-list')).toBeInTheDocument();
    });
    expect(state.listCalls[0]?.path).toBe('/api/v1/workspaces/ws2/approvals');
    // Provider 只提供路由工作区;human principal 仍需按 workspace_id 匹配 active membership。
    expect(state.requestCalls).toContainEqual({ method: 'GET', path: '/api/v1/users/me' });
  });

  it('shows the error state when the provider workspace is unavailable', async () => {
    state.optionalContext = { status: 'not_found', workspace: null, error: null };
    renderWithProviders(<ApprovalsPage />, { route: '/w/gone/approvals' });
    await waitFor(() => {
      expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    });
  });

  it('keeps the workspace error view when its retry is clicked', async () => {
    state.optionalContext = { status: 'not_found', workspace: null, error: null };
    renderWithProviders(<ApprovalsPage />, { route: '/w/gone/approvals' });
    const retry = await screen.findByRole('button', { name: 'Retry' });
    fireEvent.click(retry);
    // 工作区解析失败下的重试只重置 reloadKey,仍停留在错误态,且不发列表请求
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    expect(state.listCalls).toHaveLength(0);
  });

  it('shows the no-workspace empty state when the user has no memberships', async () => {
    state.me = { ...HUMAN_ME, memberships: [] };
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    expect(await screen.findByText('No workspace available.')).toBeInTheDocument();
    expect(screen.queryByTestId('approvals-list')).toBeNull();
    expect(state.listCalls).toHaveLength(0);
  });

  it('shows the workspace error state when the profile request fails', async () => {
    state.meShouldFail = true;
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    expect(state.listCalls).toHaveLength(0);
  });

  it('shows the skeleton while the workspace provider is still loading', async () => {
    state.optionalContext = { status: 'loading', workspace: null, error: null };
    renderWithProviders(<ApprovalsPage />, { route: '/w/team/approvals' });
    expect(await screen.findByTestId('approvals-loading')).toBeInTheDocument();
    expect(state.listCalls).toHaveLength(0);
  });

  it('shows the unknown-error description when listing throws a non-API error and recovers on retry', async () => {
    state.listThrowPlain = true;
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    // 普通错误被归一为 code=unknown 的 MeshApiError → error.unknown 文案
    expect(await screen.findByText(/An unexpected error occurred/)).toBeInTheDocument();
    state.listThrowPlain = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('approvals-list')).toBeInTheDocument();
  });

  it('clears the status param when switching back to the pending tab', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals?status=approved' });
    expect(await screen.findByTestId('approval-card-ap-ok')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Pending' }));
    // pending 视图清空 ?status= 并回到 role=mine 收件箱口径
    await waitFor(() => {
      expect(state.listCalls.at(-1)?.query).toEqual({ role: 'mine' });
    });
    expect(await screen.findByTestId('approval-card-ap-tool')).toBeInTheDocument();
  });

  it('rejects with a null comment when the dialog textarea is left blank', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    fireEvent.click(await screen.findByTestId('approval-reject-ap-tool'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByTestId('approval-reject-comment'), {
      target: { value: '   ' },
    });
    fireEvent.click(within(dialog).getByTestId('approval-reject-confirm'));
    await waitFor(() => {
      const rejectCall = state.postCalls.find((c) => c.path.endsWith('/reject'));
      expect(rejectCall?.body).toEqual({ comment: null });
    });
  });

  it('dismisses the reject dialog via the cancel button and the close control', async () => {
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    fireEvent.click(await screen.findByTestId('approval-reject-ap-tool'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByText('Cancel'));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
    // 重新打开后经标题栏关闭按钮(onClose)关闭
    fireEvent.click(screen.getByTestId('approval-reject-ap-tool'));
    const reopened = await screen.findByRole('dialog');
    fireEvent.click(within(reopened).getByRole('button', { name: 'Close dialog' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
  });

  it('toasts the unknown-error message when a decision throws a non-API error', async () => {
    state.postThrowPlain = true;
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    fireEvent.click(await screen.findByTestId('approval-approve-ap-tool'));
    await waitFor(() => {
      expect(state.postCalls.length).toBeGreaterThan(0);
    });
    // 普通错误 → toastKeyForError 回退 error.unknown;乐观更新回滚
    expect(await screen.findByText(/An unexpected error occurred/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('approval-approve-ap-tool')).toBeInTheDocument();
    });
  });
});

describe('ApprovalsPage list races', () => {
  it('ignores a list failure that arrives after the status filter changed', async () => {
    state.listDeferred = true;
    renderWithProviders(<ApprovalsPage />, { route: '/approvals' });
    await waitFor(() => {
      expect(state.listCalls.length).toBe(1);
    });
    // 切换页签使首个请求作废;其迟到失败不得进入错误态(旧响应不覆盖新查询)
    fireEvent.click(screen.getByRole('tab', { name: 'Expired' }));
    await waitFor(() => {
      expect(state.listCalls.length).toBe(2);
    });
    await act(async () => {
      state.listResolvers[0]?.reject(new Error('stale failure'));
    });
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
    await act(async () => {
      state.listResolvers[1]?.resolve({ data: [EXPIRED], next_cursor: null });
    });
    expect(await screen.findByTestId('approval-card-ap-exp')).toBeInTheDocument();
  });
});

describe('useApprovalsWorkspace resolution races', () => {
  it('ignores a stale profile resolution after the client identity changes', async () => {
    let resolveStale!: (me: unknown) => void;
    const staleClient = {
      request: vi.fn(
        () =>
          new Promise((resolve) => {
            resolveStale = resolve;
          }),
      ),
    } as unknown as MeshApiClient;
    const freshMe = {
      ...HUMAN_ME,
      memberships: [{ ...HUMAN_ME.memberships[0], workspace_id: 'ws9' }],
    };
    const freshClient = { request: vi.fn(async () => freshMe) } as unknown as MeshApiClient;

    const { result, rerender } = renderHook((client) => useApprovalsWorkspace(client), {
      initialProps: staleClient,
    });
    expect(result.current).toEqual({ kind: 'loading' });

    // 切换 client → 首个 effect 清理(cancelled),新请求解析 ws9
    rerender(freshClient);
    await waitFor(() => {
      expect(result.current).toEqual({
        kind: 'ready',
        workspaceId: 'ws9',
        isAgentPrincipal: false,
      });
    });

    // 迟到的旧响应被 cancelled 守卫丢弃,不覆盖新状态
    await act(async () => {
      resolveStale(HUMAN_ME);
    });
    expect(result.current).toEqual({
      kind: 'ready',
      workspaceId: 'ws9',
      isAgentPrincipal: false,
    });
  });
});
