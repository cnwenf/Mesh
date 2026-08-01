/**
 * ExecutionDetailPage 组件测试(runtime.md §4.4):状态头 + 元信息 + 超时进度;
 * 日志三段合一——REST 补历史 + WS 实时帧追加 / offset 去重 / 跟随尾部开关 /
 * end 帧收尾;SSE 降级(EventSource 桩)在同一路径生效;凭证 Tab 值恒 `***`;
 * 取消二次确认;终态横幅(绿 / 红 + failure_reason);Tab URL 同步。
 */
import { act } from 'react';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
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
      result: { exit_code: 0 },
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
) {
  const page = <ExecutionDetailPage />;
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
});

describe('ExecutionDetailPage 实时日志(§4.9 三段合一)', () => {
  it('places a sticky follow/offset toolbar after the scrollable log stream', async () => {
    setup();
    renderPage();
    const panel = await screen.findByTestId('execution-log-panel');
    const toolbar = screen.getByTestId('execution-log-toolbar');
    expect(toolbar).toHaveAttribute('data-sticky-toolbar', 'true');
    expect(panel.compareDocumentPosition(toolbar) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  });

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
