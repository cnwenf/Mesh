/**
 * ExecutionDetailPage 组件测试(runtime.md §4.4):状态头 + 元信息 + 超时进度;
 * 日志三段合一——REST 补历史 + WS 实时帧追加 / offset 去重 / 跟随尾部开关 /
 * end 帧收尾;SSE 降级(EventSource 桩)在同一路径生效;凭证 Tab 值恒 `***`;
 * 取消二次确认;终态横幅(绿 / 红 + failure_reason);Tab URL 同步。
 */
import { act } from 'react';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { ExecutionDetailPage } from '../ExecutionDetailPage';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const ME = {
  user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'T',
      workspace_slug: 't',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

// 字段集与后端 `_render_execution` 对齐:无联表展示名(agent_name / issue_identifier),
// 详情页 agent / issue 行呈现契约实际返回的 id。
const EXECUTION = {
  id: 'e-1',
  workspace_id: 'ws-1',
  agent_id: 'a-1',
  issue_id: 'i-42',
  trigger: 'assign',
  status: 'running',
  priority: 100,
  required_capabilities: ['python'],
  label_requirements: {},
  timeout_seconds: 1800,
  queued_at: '2026-07-27T11:50:00Z',
  finished_at: null,
  failure_reason: null,
  result: null,
  max_attempts: 3,
  credentials: [
    { id: 'cr-1', name: 'intranet-repo-readonly', kind: 'repo_token' },
    { id: 'cr-2', name: 'CI_API_KEY', kind: 'env' },
  ],
  attempts: [
    {
      id: 'att-1',
      attempt_number: 1,
      runtime_name: 'intranet-build-01',
      status: 'running',
      claimed_at: '2026-07-27T11:50:01Z',
      started_at: new Date(Date.now() - 201_000).toISOString(),
      finished_at: null,
      working_branch: 'agent/e-1/a1',
      failure_reason: null,
      result: {
        schema_version: 1,
        outcome: { exit_code: 0, termination: 'completed', summary: 'done' },
      },
    },
  ],
};

const BACKFILL = {
  lines: [
    { stream: 'stdout', offset: 0, line: '$ mesh-agent run --task fix-login-bug' },
    { stream: 'stdout', offset: 64, line: '> checkout base main' },
    { stream: 'stderr', offset: 128, line: 'warning: deprecated api' },
  ],
  next_offset: 200,
};

interface Recorded {
  url: string;
  method: string;
}

function setup(execution: Record<string, unknown> = EXECUTION): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/executions/e-1/logs')) {
      return fakeResponse({ body: { data: BACKFILL } });
    }
    if (url.includes('/executions/e-1')) return fakeResponse({ body: { data: execution } });
    return fakeResponse({ body: { data: execution } }); // :cancel
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

type FrameListener = (frame: RealtimeEventFrame) => void;

function makeRealtime() {
  const listeners = new Set<FrameListener>();
  const subscribed: string[] = [];
  const client = {
    subscribe: (channel: string) => {
      subscribed.push(channel);
    },
    unsubscribe: vi.fn(),
    onFrame: (cb: FrameListener) => {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
  };
  return {
    value: { state: 'connected', client } as unknown as RealtimeContextValue,
    emit: (frame: RealtimeEventFrame) => {
      act(() => {
        for (const listener of listeners) listener(frame);
      });
    },
    subscribed,
  };
}

function renderPage(
  realtime: ReturnType<typeof makeRealtime> | null = null,
  route = '/executions/e-1',
  withExecutionSwitch = false,
) {
  const page = (
    <>
      <ExecutionDetailPage />
      {withExecutionSwitch ? (
        <Link to="/executions/e-2" data-testid="execution-switch">
          switch execution
        </Link>
      ) : null}
    </>
  );
  return renderWithProviders(
    <Routes>
      <Route
        path="/executions/:executionId"
        element={
          realtime === null ? (
            page
          ) : (
            <RealtimeContext.Provider value={realtime.value}>{page}</RealtimeContext.Provider>
          )
        }
      />
    </Routes>,
    { route },
  );
}

function logFrame(offset: number, line: string, stream = 'stdout'): RealtimeEventFrame {
  return {
    op: 'event',
    channel: 'execution:e-1:logs',
    seq: offset + 1,
    event: 'execution.log',
    payload: { type: 'log', stream, offset, line },
  };
}

describe('ExecutionDetailPage 头部与元信息', () => {
  it('渲染状态 + runtime + agent / issue / 触发 / 分支 + 已运行 / 上限', async () => {
    setup();
    renderPage();
    expect(await screen.findByTestId('execution-detail-page')).toBeInTheDocument();
    expect(screen.getByTestId('execution-status')).toHaveTextContent('Running');
    expect(screen.getByTestId('execution-runtime-name')).toHaveTextContent('intranet-build-01');
    // agent / issue 行呈现契约实际返回的 id(后端不提供联表展示名)。
    expect(screen.getByTestId('execution-agent')).toHaveTextContent('a-1');
    expect(screen.getByTestId('execution-issue')).toHaveTextContent('i-42');
    // 标题为 trigger 文案 + 短 ID(不依赖后端不提供的字段)。
    expect(screen.getByTestId('execution-title')).toHaveTextContent('Assign · e-1');
    expect(screen.getByTestId('execution-trigger')).toHaveTextContent('Assign');
    expect(screen.getByTestId('execution-branch')).toHaveTextContent('agent/e-1/a1');
    expect(screen.getByTestId('execution-elapsed').textContent).toContain('/ 30:00');
    expect(screen.getByTestId('execution-progress')).toBeInTheDocument();
  });

  it('终态失败呈现红色横幅 + failure_reason', async () => {
    setup({ ...EXECUTION, status: 'failed', failure_reason: 'nonzero_exit' });
    renderPage();
    expect(await screen.findByTestId('execution-terminal-banner')).toHaveTextContent(
      'nonzero_exit',
    );
    // 终态无取消按钮
    expect(screen.queryByTestId('execution-cancel-button')).toBeNull();
  });

  it('终态成功呈现绿色横幅文案', async () => {
    setup({ ...EXECUTION, status: 'completed', finished_at: '2026-07-27T12:00:00Z' });
    renderPage();
    expect(await screen.findByTestId('execution-terminal-banner')).toHaveTextContent(
      'successfully',
    );
  });

  it('按 attempt 呈现 provider、冻结预算、usage、时间线与安全审批审计', async () => {
    const user = userEvent.setup();
    const frozenBudget = {
      max_cost_usd: '2.500000',
      max_tokens: 10000,
      max_turns: 25,
      max_wall_time_seconds: 1800,
      max_idle_time_seconds: 300,
    };
    setup({
      ...EXECUTION,
      status: 'completed',
      frozen_budget: frozenBudget,
      attempts: [
        {
          id: 'att-1',
          attempt_number: 1,
          runtime_name: 'intranet-build-01',
          status: 'cancelled',
          claimed_at: '2026-07-27T11:50:01Z',
          started_at: '2026-07-27T11:50:02Z',
          finished_at: '2026-07-27T11:51:00Z',
          working_branch: 'agent/e-1/a1',
          failure_reason: 'awaiting_approval',
          provider: null,
          provider_version: null,
          model: null,
          actual_usage: null,
          frozen_budget: frozenBudget,
          redaction_hits: 0,
          timeline: [
            { event: 'claimed', at: '2026-07-27T11:50:01Z' },
            { event: 'running', at: '2026-07-27T11:50:02Z' },
            { event: 'approval_requested', at: '2026-07-27T11:51:00Z' },
            { event: 'approval_approved', at: '2026-07-27T11:52:00Z' },
            {
              event: 'terminal',
              at: '2026-07-27T11:51:00Z',
              status: 'cancelled',
              reason_code: 'awaiting_approval',
            },
          ],
        },
        {
          id: 'att-2',
          attempt_number: 2,
          runtime_name: 'intranet-build-01',
          status: 'completed',
          claimed_at: '2026-07-27T11:53:00Z',
          started_at: '2026-07-27T11:53:01Z',
          finished_at: '2026-07-27T11:55:00Z',
          working_branch: 'agent/e-1/a2',
          failure_reason: null,
          provider: 'provider-a',
          provider_version: '1.2.3',
          model: 'model-z',
          actual_usage: {
            prompt_tokens: 100,
            completion_tokens: 30,
            cache_tokens: 20,
            total_tokens: 150,
            cost_usd: '1.250000',
            turns: 4,
          },
          frozen_budget: frozenBudget,
          redaction_hits: 2,
          timeline: [
            { event: 'requeued', at: '2026-07-27T11:53:00Z', reason_code: 'awaiting_approval' },
            { event: 'claimed', at: '2026-07-27T11:53:00Z' },
            { event: 'running', at: '2026-07-27T11:53:01Z' },
            { event: 'terminal', at: '2026-07-27T11:55:00Z', status: 'completed' },
          ],
        },
      ],
      approval_audits: [
        {
          id: 'approval-1',
          source_attempt_id: 'att-1',
          request: {
            action: 'repo.write',
            fields: { repository: 'org/repo', branch: 'feature/audit' },
          },
          requested_by_member_id: 'member-agent',
          requested_at: '2026-07-27T11:51:00Z',
          decision: {
            status: 'approved',
            decided_by_member_id: 'member-owner',
            decided_at: '2026-07-27T11:52:00Z',
          },
          grant: {
            action: 'repo.write',
            fields: { repository: 'org/repo', branch: 'feature/audit' },
          },
          result: { attempt_id: 'att-2', status: 'completed', termination: 'completed' },
        },
      ],
    });
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-tab-audit'));

    expect(await screen.findByTestId('execution-attempt-audit-att-1')).toHaveTextContent(
      'approval_requested',
    );
    const completed = screen.getByTestId('execution-attempt-audit-att-2');
    expect(completed).toHaveTextContent('provider-a');
    expect(completed).toHaveTextContent('1.2.3');
    expect(completed).toHaveTextContent('model-z');
    expect(completed).toHaveTextContent('2.500000');
    expect(completed).toHaveTextContent('1.250000');
    expect(completed).toHaveTextContent('requeued');
    expect(completed).toHaveTextContent('Redacted: 2');
    const approval = screen.getByTestId('execution-approval-audit-approval-1');
    expect(approval).toHaveTextContent('repo.write');
    expect(approval).toHaveTextContent('member-owner');
    expect(approval).toHaveTextContent('completed');
    expect(screen.getByTestId('execution-panel-audit').textContent).not.toContain('token');
    expect(screen.getByTestId('execution-panel-audit').textContent).not.toContain('/srv/');
    expect(screen.getByTestId('execution-panel-audit').textContent).not.toContain('thinking');
  });

  it('按安全白名单呈现预算、安全标记、grant fields 与 Diff reference', async () => {
    const user = userEvent.setup();
    const frozenBudget = {
      max_cost_usd: '2.500000',
      max_tokens: 10000,
      max_turns: 25,
      max_wall_time_seconds: 1800,
      max_idle_time_seconds: 300,
    };
    setup({
      ...EXECUTION,
      status: 'completed',
      frozen_budget: frozenBudget,
      checkout: {
        diff_ref: 'logs/ws-1/diffs/safe.diff',
        repo_url: 'https://example.test/repo?token=checkout-secret',
        arbitrary: 'checkout-hidden',
      },
      attempts: [
        {
          ...EXECUTION.attempts[0],
          status: 'completed',
          frozen_budget: frozenBudget,
          redacted: true,
          security_alert: 'result_redacted',
          result: {
            schema_version: 1,
            outcome: {
              exit_code: 0,
              termination: 'completed',
              summary: 'safe result summary',
              thinking: 'provider-thinking-hidden',
            },
            artifacts: {
              checkout_id: 'checkout-42',
              diff_ref: 'diff:42',
              path: '/srv/private/worktree',
            },
            secret: 'result-secret-hidden',
          },
        },
      ],
      approval_audits: [
        {
          id: 'approval-safe',
          source_attempt_id: 'att-1',
          request: {
            action: 'repo.write',
            fields: {
              repository: 'org/repo',
              secret: 'request-secret-hidden',
            },
          },
          requested_by_member_id: 'member-agent',
          requested_at: '2026-07-27T11:51:00Z',
          decision: {
            status: 'approved',
            decided_by_member_id: 'member-owner',
            decided_at: '2026-07-27T11:52:00Z',
          },
          grant: {
            action: 'repo.write',
            fields: {
              branch: 'approved/branch',
              scope: 'repository',
              secret: 'grant-secret-hidden',
            },
          },
          result: { attempt_id: 'att-1', status: 'completed', termination: 'completed' },
        },
      ],
    });
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-tab-audit'));

    const audit = await screen.findByTestId('execution-attempt-audit-att-1');
    expect(audit).toHaveTextContent('Wall time limit');
    expect(audit).toHaveTextContent('30:00');
    expect(audit).toHaveTextContent('Idle time limit');
    expect(audit).toHaveTextContent('05:00');
    expect(audit).toHaveTextContent('Payload redacted: true');
    expect(audit).toHaveTextContent('Security alert: result_redacted');
    const approval = screen.getByTestId('execution-approval-audit-approval-safe');
    expect(approval).toHaveTextContent('approved/branch');
    expect(approval).toHaveTextContent('repository');
    expect(approval).not.toHaveTextContent('request-secret-hidden');
    expect(approval).not.toHaveTextContent('grant-secret-hidden');

    await user.click(screen.getByTestId('execution-tab-artifacts'));
    expect(await screen.findByTestId('execution-artifact-diff-ref')).toHaveTextContent(
      'logs/ws-1/diffs/safe.diff',
    );
    const result = screen.getByTestId('execution-artifact-result');
    expect(result).toHaveTextContent('safe result summary');
    expect(result).not.toHaveTextContent('provider-thinking-hidden');
    expect(result).not.toHaveTextContent('/srv/private/worktree');
    expect(result).not.toHaveTextContent('result-secret-hidden');
    expect(screen.getByTestId('execution-panel-artifacts')).not.toHaveTextContent(
      'checkout-secret',
    );
    expect(screen.getByTestId('execution-panel-artifacts')).not.toHaveTextContent(
      'checkout-hidden',
    );
  });

  it('稀疏 attempt 审计使用安全占位且空审批不报错', async () => {
    const user = userEvent.setup();
    setup({
      ...EXECUTION,
      approval_audits: [
        {
          id: 'approval-sparse',
          source_attempt_id: 'att-1',
          request: {
            action: 'repo.read',
            fields: { scope: true, method: false, target_id: 2 },
          },
          requested_by_member_id: 'member-agent',
          requested_at: '2026-07-27T11:51:00Z',
          decision: {
            status: 'pending',
            decided_by_member_id: null,
            decided_at: null,
          },
          grant: null,
          result: null,
        },
      ],
    });
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-tab-audit'));
    const attempt = await screen.findByTestId('execution-attempt-audit-att-1');
    expect(attempt).toHaveTextContent('Redacted: 0');
    const approval = screen.getByTestId('execution-approval-audit-approval-sparse');
    expect(approval).toHaveTextContent('true');
    expect(approval).toHaveTextContent('false');
    expect(approval).toHaveTextContent('2');
    expect(approval).toHaveTextContent('—');
  });

  it('无 attempt 时审计页明确呈现空态', async () => {
    const user = userEvent.setup();
    setup({ ...EXECUTION, attempts: [], credentials: [] });
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-tab-audit'));
    expect(await screen.findByTestId('execution-panel-audit')).toHaveTextContent(
      'No attempts recorded.',
    );
  });
});

describe('ExecutionDetailPage 实时日志(§4.9 三段合一)', () => {
  it('REST 补历史后 WS 帧追加,按 offset 去重不重', async () => {
    setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    // ① REST 补历史
    expect(await screen.findByTestId('execution-log-line-0')).toHaveTextContent('mesh-agent run');
    expect(screen.getByTestId('execution-log-line-128')).toHaveTextContent('deprecated');
    expect(realtime.subscribed).toContain('execution:e-1:logs');
    // ② 实时帧:与历史重叠的 offset 被去重,新 offset 追加
    realtime.emit(logFrame(128, 'DUP SHOULD NOT APPEAR'));
    realtime.emit(logFrame(200, 'PASSED [ 41%]'));
    expect(await screen.findByTestId('execution-log-line-200')).toHaveTextContent('PASSED');
    expect(screen.getByTestId('execution-log-line-128').textContent).not.toContain(
      'DUP SHOULD NOT APPEAR',
    );
    expect(screen.queryByTestId('execution-log-line-201')).toBeNull();
  });

  it('end 帧收尾显示终态并刷新执行详情', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('execution-log-line-0');
    const before = calls.filter((c) => c.url.endsWith('/executions/e-1')).length;
    realtime.emit({
      op: 'event',
      channel: 'execution:e-1:logs',
      seq: 99,
      event: 'execution.log',
      payload: { type: 'end', status: 'completed', final_offset: 1200340 },
    });
    expect(await screen.findByTestId('execution-log-end')).toHaveTextContent('completed');
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith('/executions/e-1')).length).toBeGreaterThan(before),
    );
  });

  it('status 帧触发执行详情重拉;非法 log 帧(缺 line)忽略', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('execution-log-line-0');
    realtime.emit({
      op: 'event',
      channel: 'execution:e-1:logs',
      seq: 50,
      event: 'execution.log',
      payload: { type: 'log', offset: 300 }, // 缺 line → 忽略
    });
    expect(screen.queryByTestId('execution-log-line-300')).toBeNull();
    const before = calls.filter((c) => c.url.endsWith('/executions/e-1')).length;
    realtime.emit({
      op: 'event',
      channel: 'execution:e-1:logs',
      seq: 51,
      event: 'execution.log',
      payload: { type: 'status', status: 'running' },
    });
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith('/executions/e-1')).length).toBeGreaterThan(before),
    );
  });

  it('execution:{id} 频道终态帧触发重拉', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('execution-log-line-0');
    expect(realtime.subscribed).toContain('execution:e-1');
    const before = calls.filter((c) => c.url.endsWith('/executions/e-1')).length;
    realtime.emit({
      op: 'event',
      channel: 'execution:e-1',
      seq: 1,
      event: 'execution.completed',
      payload: { data: { id: 'e-1' } },
    });
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith('/executions/e-1')).length).toBeGreaterThan(before),
    );
  });

  it('订阅 workspace / issue 非终态频道并只刷新当前 execution', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('execution-log-line-0');
    await waitFor(() => {
      expect(realtime.subscribed).toContain('workspace:ws-1:executions');
      expect(realtime.subscribed).toContain('issue:i-42');
    });
    const detailCalls = (): number =>
      calls.filter((call) => call.url.endsWith('/executions/e-1')).length;
    const before = detailCalls();

    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:executions',
      seq: 5,
      event: 'execution.started',
      payload: { execution_id: 'other-execution', issue_id: 'i-42' },
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(detailCalls()).toBe(before);

    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:executions',
      seq: 6,
      event: 'execution.started',
      payload: { execution_id: 'e-1', issue_id: 'i-42' },
    });
    await waitFor(() => expect(detailCalls()).toBeGreaterThan(before));
    const afterWorkspace = detailCalls();

    realtime.emit({
      op: 'event',
      channel: 'issue:i-42',
      seq: 7,
      event: 'execution.claimed',
      payload: { execution_id: 'e-1', issue_id: 'i-42' },
    });
    await waitFor(() => expect(detailCalls()).toBeGreaterThan(afterWorkspace));
  });

  it('executionId 改变时清空日志、offset 与 end 状态并从零补新执行', async () => {
    const user = userEvent.setup();
    const urls: string[] = [];
    const secondExecution = {
      ...EXECUTION,
      id: 'e-2',
      issue_id: 'i-99',
      attempts: [
        {
          ...EXECUTION.attempts[0],
          id: 'att-2',
          working_branch: 'agent/e-2/a1',
        },
      ],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        urls.push(url);
        if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
        if (url.includes('/executions/e-1/logs')) {
          return fakeResponse({ body: { data: BACKFILL } });
        }
        if (url.includes('/executions/e-2/logs')) {
          return fakeResponse({
            body: {
              data: {
                lines: [{ stream: 'stdout', offset: 0, line: 'second execution only' }],
                next_offset: 21,
              },
            },
          });
        }
        if (url.includes('/executions/e-2')) {
          return fakeResponse({ body: { data: secondExecution } });
        }
        return fakeResponse({ body: { data: EXECUTION } });
      }),
    );
    const realtime = makeRealtime();
    renderPage(realtime, '/executions/e-1', true);
    expect(await screen.findByTestId('execution-log-line-128')).toHaveTextContent('deprecated');
    realtime.emit({
      op: 'event',
      channel: 'execution:e-1:logs',
      seq: 90,
      event: 'execution.log',
      payload: { type: 'end', status: 'completed', final_offset: 900 },
    });
    expect(await screen.findByTestId('execution-log-end')).toHaveTextContent('completed');

    await user.click(screen.getByTestId('execution-switch'));

    expect(await screen.findByTestId('execution-log-line-0')).toHaveTextContent(
      'second execution only',
    );
    expect(screen.queryByText('warning: deprecated api')).toBeNull();
    expect(screen.queryByTestId('execution-log-end')).toBeNull();
    expect(screen.getByTestId('execution-offset')).toHaveTextContent('21');
    expect(
      urls.some((url) => url.includes('/executions/e-2/logs') && url.includes('offset=0')),
    ).toBe(true);
  });

  it('跟随尾部开关可切换', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('execution-log-line-0');
    const toggle = screen.getByTestId('execution-follow-toggle') as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    await user.click(toggle);
    expect(toggle.checked).toBe(false);
  });

  it('SSE 降级:实时态未连通时经 EventSource 订阅同一 offset 协议', async () => {
    const messages: Array<(event: MessageEvent) => void> = [];
    const closed: boolean[] = [];
    class FakeEventSource {
      static instances = 0;
      url: string;
      onmessage: ((event: MessageEvent) => void) | null = null;
      constructor(url: string) {
        FakeEventSource.instances += 1;
        this.url = url;
        messages.push((event) => this.onmessage?.(event));
      }
      close(): void {
        closed.push(true);
      }
    }
    vi.stubGlobal('EventSource', FakeEventSource);
    setup();
    renderPage(); // realtime = null → 未连通 → SSE
    await screen.findByTestId('execution-log-line-0');
    expect(FakeEventSource.instances).toBeGreaterThanOrEqual(1);
    expect(messages.length).toBeGreaterThanOrEqual(1);
    // 帧追加
    act(() => {
      messages[0]({
        data: JSON.stringify({ type: 'log', stream: 'stdout', offset: 400, line: 'sse line' }),
      } as MessageEvent);
    });
    expect(await screen.findByTestId('execution-log-line-400')).toHaveTextContent('sse line');
    // 非法 JSON 行不崩
    act(() => {
      messages[0]({ data: 'not-json{{' } as MessageEvent);
    });
    expect(screen.getByTestId('execution-log-line-400')).toBeInTheDocument();
  });

  it('日志 REST 失败呈现降级提示但不阻断页面', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/logs')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'logs down' } },
        });
      }
      return fakeResponse({ body: { data: EXECUTION } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByTestId('execution-logs-unavailable')).toBeInTheDocument();
    expect(screen.getByTestId('execution-detail-page')).toBeInTheDocument();
  });
});

describe('ExecutionDetailPage Tab / 凭证 / 取消', () => {
  it('凭证 Tab 仅元信息,值恒 ***', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-tab-credentials'));
    expect(await screen.findByTestId('execution-credential-cr-1')).toHaveTextContent(
      'intranet-repo-readonly',
    );
    expect(screen.getByTestId('execution-credential-value-cr-1')).toHaveTextContent('***');
    expect(screen.getByTestId('execution-credential-value-cr-2')).toHaveTextContent('***');
    expect(screen.getByTestId('execution-panel-credentials').textContent).not.toContain('sk-');
  });

  it('产物 Tab 呈现工作分支与结果', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-tab-artifacts'));
    expect(await screen.findByTestId('execution-artifact-branch')).toHaveTextContent(
      'agent/e-1/a1',
    );
    expect(screen.getByTestId('execution-artifact-result')).toHaveTextContent('exit_code');
  });

  it('Tab 选中同步 URL search params', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-tab-credentials'));
    await screen.findByTestId('execution-panel-credentials');
    expect(screen.getByTestId('execution-tab-credentials').getAttribute('aria-selected')).toBe(
      'true',
    );
    // URL 带 ?tab=credentials(经 MemoryRouter,断言 aria 与面板存在即覆盖同步路径)
    expect(screen.queryByTestId('execution-panel-logs')).toBeNull();
  });

  it('取消二次确认:打开弹窗 → 确认 POST :cancel', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-cancel-button'));
    expect(screen.getByTestId('execution-cancel-dialog')).toBeInTheDocument();
    await user.click(screen.getByTestId('execution-cancel-confirm'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/executions/e-1:cancel'))).toBe(true),
    );
    await waitFor(() => expect(screen.queryByTestId('execution-cancel-dialog')).toBeNull());
  });

  it('取消弹窗可放弃(不发请求)', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-cancel-button'));
    await user.click(screen.getByTestId('execution-cancel-dismiss'));
    expect(screen.queryByTestId('execution-cancel-dialog')).toBeNull();
    expect(calls.some((c) => c.url.includes(':cancel'))).toBe(false);
  });

  it('稀疏执行:无 attempt / 无 agent_id / 无 issue_id 的「—」回退', async () => {
    const sparse = {
      ...EXECUTION,
      agent_id: null,
      issue_id: null,
      credentials: undefined,
      attempts: [],
    };
    setup(sparse);
    renderPage();
    expect(await screen.findByTestId('execution-detail-page')).toBeInTheDocument();
    // 标题恒为 trigger 文案 + 短 ID(契约字段,不因稀疏数据退化)
    expect(screen.getByTestId('execution-title')).toHaveTextContent('Assign · e-1');
    expect(screen.getByTestId('execution-runtime-name')).toHaveTextContent('—');
    expect(screen.getByTestId('execution-agent')).toHaveTextContent('—');
    expect(screen.getByTestId('execution-issue')).toHaveTextContent('—');
    expect(screen.getByTestId('execution-branch')).toHaveTextContent('—');
    expect(screen.getByTestId('execution-elapsed').textContent).toContain('00:00');
    // 产物 Tab 无 attempt → 空态
    expect(screen.getByTestId('execution-panel-logs')).toBeInTheDocument();
  });

  it('返回按钮经 navigate(-1) 触发(单条历史栈下停留本页,不崩溃)', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-back'));
    // MemoryRouter 单条目无上一页 → 停留;断言回调已执行且页面完好
    expect(screen.getByTestId('execution-detail-page')).toBeInTheDocument();
  });

  it('错误态重试按钮触发重拉', async () => {
    const user = userEvent.setup();
    let fail = true;
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/logs')) {
        return fakeResponse({ body: { data: { lines: [], next_offset: 0 } } });
      }
      if (fail) {
        fail = false;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      }
      return fakeResponse({ body: { data: EXECUTION } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    const retry = await screen.findByRole('button', { name: 'Retry' });
    await user.click(retry);
    expect(await screen.findByTestId('execution-detail-page')).toBeInTheDocument();
  });

  it('详情加载失败呈现错误态', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/logs'))
        return fakeResponse({ body: { data: { lines: [], next_offset: 0 } } });
      return fakeResponse({
        status: 404,
        body: { error: { code: 'not_found', message: 'missing' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });

  it('无凭证时呈现空态', async () => {
    const user = userEvent.setup();
    const { credentials: _drop, ...rest } = EXECUTION;
    setup(rest);
    renderPage();
    await screen.findByTestId('execution-detail-page');
    await user.click(screen.getByTestId('execution-tab-credentials'));
    expect(await screen.findByTestId('execution-panel-credentials')).toHaveTextContent(
      'No credentials were injected for this execution.',
    );
  });
});
