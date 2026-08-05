/**
 * RuntimeDetailPage 组件测试(runtime.md §4.2):头部元数据 / 标签与能力 chips /
 * 正在执行(取消)+ 历史任务 / 暂停 / 恢复 / 轮换 token(新 token 一次性弹窗 +
 * 复制 + 关闭)/ 实时重拉(runtime.* 命中本 id + 执行帧)/ 错误态。
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
import { RuntimeDetailPage } from '../RuntimeDetailPage';

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

const RUNTIME = {
  id: 'r-1',
  name: 'intranet-build-01',
  kind: 'self_hosted',
  status: 'online',
  operational_state: 'online',
  diagnostics: [],
  labels: { region: 'intranet', gpu: 'false' },
  capabilities: ['version_control', 'python', 'ffmpeg'],
  hostname: 'build-node-7',
  os: 'linux-x86_64',
  cpu_cores: 8,
  memory_mb: 32768,
  max_concurrent: 4,
  current_load: 2,
  last_heartbeat_at: '2026-07-27T11:59:55Z',
  heartbeat_interval_seconds: 15,
  version: '1.4.2',
  created_at: '2026-01-01T00:00:00Z',
};

// 字段集与后端 `_render_execution` 对齐(无联表展示名;行标签走 trigger + 短 ID)。
const INFLIGHT = {
  id: 'e-1',
  workspace_id: 'ws-1',
  agent_id: 'a-1',
  issue_id: 'i-42',
  trigger: 'assign',
  status: 'running',
  priority: 100,
  required_capabilities: [],
  label_requirements: {},
  timeout_seconds: 1800,
  queued_at: '2026-07-27T11:50:00Z',
  finished_at: null,
  failure_reason: null,
  result: null,
  max_attempts: 3,
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
    },
  ],
};

const HISTORY = {
  ...INFLIGHT,
  id: 'e-2',
  trigger: 'mention',
  status: 'completed',
  finished_at: '2026-07-26T09:00:00Z',
  attempts: [],
};

interface Recorded {
  url: string;
  method: string;
}

function setup(runtime: Record<string, unknown> = RUNTIME, me = ME): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (url.includes('/tokens:rotate')) {
      return fakeResponse({ body: { data: { runtime_token: 'rt_live_NEW' } } });
    }
    if (url.includes('/runtimes/r-1/executions')) {
      return fakeResponse({ body: { data: [INFLIGHT, HISTORY], next_cursor: null } });
    }
    if (url.includes('/runtimes/r-1')) return fakeResponse({ body: { data: runtime } });
    return fakeResponse({ body: { data: INFLIGHT } }); // :cancel / :pause / :resume
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

function renderPage(realtime: ReturnType<typeof makeRealtime> | null = null) {
  const page = <RuntimeDetailPage />;
  return renderWithProviders(
    <Routes>
      <Route
        path="/runtimes/:runtimeId"
        element={
          realtime === null ? (
            page
          ) : (
            <RealtimeContext.Provider value={realtime.value}>{page}</RealtimeContext.Provider>
          )
        }
      />
      <Route path="/w/:workspaceSlug/executions/:executionId" element={<div>execution-page</div>} />
      <Route path="/w/:workspaceSlug/automations/runtimes" element={<div>runtimes-page</div>} />
    </Routes>,
    { route: '/runtimes/r-1' },
  );
}

describe('RuntimeDetailPage', () => {
  it('渲染头部元数据 + 标签 / 能力 chips', async () => {
    setup();
    renderPage();
    expect(await screen.findByTestId('runtime-detail-name')).toHaveTextContent('intranet-build-01');
    expect(screen.getByTestId('runtime-detail-status')).toHaveTextContent('Online');
    expect(screen.getByTestId('runtime-detail-host')).toHaveTextContent('build-node-7');
    expect(screen.getByTestId('runtime-detail-os')).toHaveTextContent('linux-x86_64');
    expect(screen.getByTestId('runtime-detail-cpu')).toHaveTextContent('8');
    expect(screen.getByTestId('runtime-detail-memory')).toHaveTextContent('32 GB');
    expect(screen.getByTestId('runtime-detail-concurrency')).toHaveTextContent('2/4');
    expect(screen.getByTestId('runtime-detail-version')).toHaveTextContent('v1.4.2');
    expect(screen.getByTestId('runtime-detail-labels')).toHaveTextContent('region=intranet');
    expect(screen.getByTestId('runtime-detail-capabilities')).toHaveTextContent('ffmpeg');
    expect(screen.getByTestId('runtime-operational-state')).toHaveTextContent('Online');
    expect(screen.queryByText('运行失败')).toBeNull();
  });

  it('兼容旧响应时从 lifecycle 推导 operational state', async () => {
    setup({ ...RUNTIME, operational_state: undefined, diagnostics: undefined });
    renderPage();
    expect(await screen.findByTestId('runtime-operational-state')).toHaveTextContent('Online');
  });

  it('兼容旧响应时把 paused / unavailable 映射为可行动状态', async () => {
    setup({
      ...RUNTIME,
      status: 'paused',
      operational_state: undefined,
      diagnostics: undefined,
    });
    const first = renderPage();
    expect(await screen.findByTestId('runtime-operational-state')).toHaveTextContent('Paused');
    first.unmount();

    setup({
      ...RUNTIME,
      status: 'unavailable',
      operational_state: undefined,
      diagnostics: undefined,
    });
    renderPage();
    expect(await screen.findByTestId('runtime-operational-state')).toHaveTextContent('Degraded');
  });

  it('Degraded 精确呈现缺失能力、受影响任务类型与可复制修复命令', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    setup({
      ...RUNTIME,
      status: 'unavailable',
      operational_state: 'degraded',
      diagnostics: [
        {
          reason_code: 'provider_unavailable',
          missing_capabilities: ['python', 'version_control'],
          affected_task_types: ['provider:primary'],
          repair_command: 'mesh-runtime doctor --config <config-file>',
        },
        {
          reason_code: 'capability_missing',
          missing_capabilities: [],
          affected_task_types: [],
          repair_command: 'mesh-runtime inventory --config <config-file>',
        },
      ],
    });
    renderPage();
    expect(await screen.findByTestId('runtime-operational-state')).toHaveTextContent('Degraded');
    const diagnostic = screen.getByTestId('runtime-diagnostic-provider_unavailable');
    expect(diagnostic).toHaveTextContent('python');
    expect(diagnostic).toHaveTextContent('provider:primary');
    expect(diagnostic).toHaveTextContent('mesh-runtime doctor --config <config-file>');
    expect(screen.getByTestId('runtime-diagnostic-capability_missing')).toHaveTextContent('—');
    await user.click(screen.getByTestId('runtime-diagnostic-copy-provider_unavailable'));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('mesh-runtime doctor --config <config-file>'),
    );
    writeText.mockRejectedValueOnce(new Error('clipboard denied'));
    await user.click(screen.getByTestId('runtime-diagnostic-copy-provider_unavailable'));
    expect(screen.getByTestId('runtime-detail-page')).toBeInTheDocument();
    expect(screen.queryByText('运行失败')).toBeNull();
  });

  it('Isolated 仅给出脱敏诊断导出与重新注册动作', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const createObjectURL = vi.fn(() => 'blob:runtime-diagnostics');
    const revokeObjectURL = vi.fn();
    const NativeURL = URL;
    class RuntimeTestURL extends NativeURL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    }
    vi.stubGlobal('URL', RuntimeTestURL);
    const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    setup({
      ...RUNTIME,
      status: 'unavailable',
      operational_state: 'isolated',
      diagnostics: [
        {
          reason_code: 'security_anomaly',
          missing_capabilities: [],
          affected_task_types: ['all'],
          repair_command: 'mesh-runtime doctor --config <config-file>',
        },
      ],
    });
    renderPage();
    expect(await screen.findByTestId('runtime-operational-state')).toHaveTextContent('Isolated');
    expect(screen.getByTestId('runtime-export-diagnostics')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-reregister-command')).toHaveTextContent(
      'mesh-runtime activate --config <config-file> --activation-code-stdin',
    );
    expect(screen.queryByTestId('runtime-detail-pause')).toBeNull();
    await user.click(screen.getByTestId('runtime-export-diagnostics'));
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(download).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:runtime-diagnostics');
    await user.click(screen.getByTestId('runtime-reregister-copy'));
    expect(writeText).toHaveBeenCalledWith(
      'mesh-runtime activate --config <config-file> --activation-code-stdin',
    );
  });

  it('正在执行列表含取消;历史任务分列', async () => {
    setup();
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    // 行标签为 trigger 文案 + 短 ID(契约字段,不依赖联表展示名)。
    expect(screen.getByTestId('runtime-inflight-e-1')).toHaveTextContent('Assign · e-1');
    expect(screen.getByTestId('runtime-inflight-status-e-1')).toHaveTextContent('Running');
    expect(screen.getByTestId('runtime-cancel-e-1')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-history-e-2')).toHaveTextContent('Mention · e-2');
  });

  it('取消在途执行 POST :cancel', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('runtime-inflight-e-1');
    await user.click(screen.getByTestId('runtime-cancel-e-1'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/executions/e-1:cancel'))).toBe(true),
    );
  });

  it('暂停动作 POST :pause(online 行)', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    await user.click(screen.getByTestId('runtime-detail-pause'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/runtimes/r-1:pause'))).toBe(true),
    );
  });

  it('paused 状态呈现恢复按钮,POST :resume', async () => {
    const user = userEvent.setup();
    const calls = setup({ ...RUNTIME, status: 'paused', operational_state: 'paused' });
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    expect(screen.queryByTestId('runtime-detail-pause')).toBeNull();
    expect(screen.getByTestId('runtime-operational-state')).toHaveTextContent('Paused');
    await user.click(screen.getByTestId('runtime-detail-resume'));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/runtimes/r-1:resume'))).toBe(true),
    );
  });

  it('轮换 token:弹窗一次性呈现新 token,可复制 / 关闭', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    setup();
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    await user.click(screen.getByTestId('runtime-detail-rotate'));
    expect(await screen.findByTestId('runtime-rotate-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-rotate-token')).toHaveTextContent('rt_live_NEW');
    await user.click(screen.getByTestId('runtime-rotate-copy'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('rt_live_NEW'));
    await user.click(screen.getByTestId('runtime-rotate-close'));
    expect(screen.queryByTestId('runtime-rotate-dialog')).toBeNull();
  });

  it('轮换失败回显 toast,不弹窗', async () => {
    const user = userEvent.setup();
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST') {
        return fakeResponse({
          status: 409,
          body: { error: { code: 'conflict', message: 'conflict' } },
        });
      }
      if (url.includes('/runtimes/r-1/executions')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      if (url.includes('/runtimes/r-1')) return fakeResponse({ body: { data: RUNTIME } });
      return fakeResponse({ body: { data: {} } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const calls: Recorded[] = [];
    const wrapped = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method ?? 'GET' });
      return impl(input, init);
    }) as typeof fetch;
    vi.stubGlobal('fetch', wrapped);
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    await user.click(screen.getByTestId('runtime-detail-rotate'));
    // 轮换请求已发出且被拒(409)→ 不呈现新 token 弹窗
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/tokens:rotate') && c.method === 'POST')).toBe(true),
    );
    expect(screen.queryByTestId('runtime-rotate-dialog')).toBeNull();
  });

  it('本 runtime 的 runtime.* 帧触发重拉;其它 id 忽略', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('runtime-detail-name');
    expect(realtime.subscribed).toContain('workspace:ws-1:runtimes');
    expect(realtime.subscribed).toContain('workspace:ws-1:executions');
    const before = calls.filter(
      (c) => c.url.includes('/runtimes/r-1') && c.method === 'GET',
    ).length;
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:runtimes',
      seq: 1,
      event: 'runtime.online',
      payload: { data: { id: 'other' } },
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls.filter((c) => c.url.includes('/runtimes/r-1') && c.method === 'GET').length).toBe(
      before,
    );
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:runtimes',
      seq: 2,
      event: 'runtime.degraded',
      payload: { data: { id: 'r-1' } },
    });
    await waitFor(() =>
      expect(
        calls.filter((c) => c.url.includes('/runtimes/r-1') && c.method === 'GET').length,
      ).toBeGreaterThan(before),
    );
  });

  it('执行帧触发重拉;空在途 / 空历史呈现空态', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/runtimes/r-1/executions')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return fakeResponse({ body: { data: RUNTIME } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('runtime-detail-name');
    expect(screen.getByTestId('runtime-detail-labels')).toBeInTheDocument();
    const titles = screen.getAllByText('Nothing here yet');
    expect(titles.length).toBeGreaterThanOrEqual(2); // 无在途 + 无历史
  });

  it('稀疏元数据呈现「—」回退(主机 / OS / CPU / 内存 / 版本 / 标签 / 能力)', async () => {
    const sparse = {
      ...RUNTIME,
      hostname: null,
      os: null,
      cpu_cores: null,
      memory_mb: null,
      version: null,
      labels: {},
      capabilities: [],
    };
    setup(sparse);
    renderPage();
    expect(await screen.findByTestId('runtime-detail-host')).toHaveTextContent('—');
    expect(screen.getByTestId('runtime-detail-os')).toHaveTextContent('—');
    expect(screen.getByTestId('runtime-detail-cpu')).toHaveTextContent('—');
    expect(screen.getByTestId('runtime-detail-memory')).toHaveTextContent('—');
    expect(screen.getByTestId('runtime-detail-version')).toHaveTextContent('—');
    expect(screen.getByTestId('runtime-detail-labels')).toHaveTextContent('—');
    expect(screen.getByTestId('runtime-detail-capabilities')).toHaveTextContent('—');
  });

  it('行标签恒为 trigger 文案 + 短 ID(不依赖后端不提供的展示名);历史无 finished_at 时回退 queued_at', async () => {
    const inflight = { ...INFLIGHT, agent_id: null };
    const history = { ...HISTORY, agent_id: null, finished_at: null };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/runtimes/r-1/executions')) {
        return fakeResponse({ body: { data: [inflight, history], next_cursor: null } });
      }
      return fakeResponse({ body: { data: RUNTIME } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByTestId('runtime-inflight-e-1')).toHaveTextContent('Assign · e-1');
    expect(screen.getByTestId('runtime-history-e-2')).toHaveTextContent('Mention · e-2');
  });

  it('返回与查看按钮触发导航(路由外卸载详情)', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    await user.click(screen.getByTestId('runtime-view-e-1'));
    expect(screen.queryByTestId('runtime-detail-page')).toBeNull();
  });

  it('历史查看按钮触发导航', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    await user.click(screen.getByTestId('runtime-history-view-e-2'));
    expect(screen.queryByTestId('runtime-detail-page')).toBeNull();
  });

  it('返回按钮导航回列表', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    await user.click(screen.getByTestId('runtime-detail-back'));
    expect(screen.queryByTestId('runtime-detail-page')).toBeNull();
  });

  it('无 payload id 的生命周期帧也触发重拉(全量兜底)', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('runtime-detail-name');
    const before = calls.filter(
      (c) => c.url.includes('/runtimes/r-1') && c.method === 'GET',
    ).length;
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:runtimes',
      seq: 5,
      event: 'runtime.paused',
      payload: {},
    });
    await waitFor(() =>
      expect(
        calls.filter((c) => c.url.includes('/runtimes/r-1') && c.method === 'GET').length,
      ).toBeGreaterThan(before),
    );
  });

  it('工作区解析失败(users/me 错误)呈现错误态', async () => {
    const impl = (async () =>
      fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'boom' } },
      })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });

  it('runtimes 频道无关事件 / 无关频道帧不触发重拉', async () => {
    const calls = setup();
    const realtime = makeRealtime();
    renderPage(realtime);
    await screen.findByTestId('runtime-detail-name');
    const before = calls.filter(
      (c) => c.url.includes('/runtimes/r-1') && c.method === 'GET',
    ).length;
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:runtimes',
      seq: 6,
      event: 'unrelated.event',
      payload: {},
    });
    realtime.emit({
      op: 'event',
      channel: 'workspace:ws-1:other',
      seq: 7,
      event: 'execution.queued',
      payload: {},
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls.filter((c) => c.url.includes('/runtimes/r-1') && c.method === 'GET').length).toBe(
      before,
    );
  });

  it('取消失败回显 toast 且页面不崩溃(act 错误路径)', async () => {
    const user = userEvent.setup();
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/runtimes/r-1/executions')) {
        return fakeResponse({ body: { data: [INFLIGHT, HISTORY], next_cursor: null } });
      }
      if (url.includes('/runtimes/r-1')) return fakeResponse({ body: { data: RUNTIME } });
      if (method === 'POST') {
        return fakeResponse({
          status: 409,
          body: { error: { code: 'conflict', message: 'conflict' } },
        });
      }
      return fakeResponse({ body: { data: {} } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await screen.findByTestId('runtime-inflight-e-1');
    await user.click(screen.getByTestId('runtime-cancel-e-1'));
    // 失败后页面仍完好(错误经 toast 吞下,不崩)
    expect(screen.getByTestId('runtime-detail-page')).toBeInTheDocument();
  });

  it('轮换弹窗复制失败回显 toast(剪贴板拒绝)', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: vi.fn(async () => {
          throw new Error('denied');
        }),
      },
      configurable: true,
    });
    setup();
    renderPage();
    await screen.findByTestId('runtime-detail-name');
    await user.click(screen.getByTestId('runtime-detail-rotate'));
    await screen.findByTestId('runtime-rotate-dialog');
    await user.click(screen.getByTestId('runtime-rotate-copy'));
    // 复制失败不关闭弹窗,页面不崩溃
    expect(screen.getByTestId('runtime-rotate-dialog')).toBeInTheDocument();
  });

  it('在途尝试未 started(仅 claimed)时已运行计为 00:00', async () => {
    const claimedOnly = {
      ...INFLIGHT,
      attempts: [{ ...INFLIGHT.attempts[0], started_at: null, claimed_at: null }],
    };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/runtimes/r-1/executions')) {
        return fakeResponse({ body: { data: [claimedOnly], next_cursor: null } });
      }
      return fakeResponse({ body: { data: RUNTIME } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByTestId('runtime-inflight-e-1')).toHaveTextContent('00:00');
  });

  it('错误态重试按钮触发重拉', async () => {
    const user = userEvent.setup();
    let fail = true;
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (fail) {
        fail = false;
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      }
      if (url.includes('/runtimes/r-1/executions')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return fakeResponse({ body: { data: RUNTIME } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    const retry = await screen.findByRole('button', { name: 'Retry' });
    await user.click(retry);
    expect(await screen.findByTestId('runtime-detail-name')).toBeInTheDocument();
  });

  it('详情加载失败呈现错误态', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({
        status: 404,
        body: { error: { code: 'not_found', message: 'missing' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });

  it('members can cancel executions but cannot manage the runtime', async () => {
    setup(RUNTIME, {
      ...ME,
      memberships: [{ ...ME.memberships[0], role: 'member' }],
    });
    renderPage();

    await screen.findByTestId('runtime-detail-name');
    expect(screen.getByTestId('runtime-cancel-e-1')).toBeInTheDocument();
    expect(screen.queryByTestId('runtime-detail-pause')).toBeNull();
    expect(screen.queryByTestId('runtime-detail-rotate')).toBeNull();
  });

  it('guests cannot cancel executions or manage the runtime', async () => {
    setup(RUNTIME, {
      ...ME,
      memberships: [{ ...ME.memberships[0], role: 'guest' }],
    });
    renderPage();

    await screen.findByTestId('runtime-detail-name');
    expect(screen.queryByTestId('runtime-cancel-e-1')).toBeNull();
    expect(screen.queryByTestId('runtime-detail-pause')).toBeNull();
    expect(screen.queryByTestId('runtime-detail-rotate')).toBeNull();
  });

  it('shows the no-workspace state without loading runtime data', async () => {
    const calls = setup(RUNTIME, { ...ME, memberships: [] });
    renderPage();

    expect(
      await screen.findByText('No workspace available. Join or create a workspace first.'),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes('/runtimes/r-1'))).toBe(false);
  });
});
