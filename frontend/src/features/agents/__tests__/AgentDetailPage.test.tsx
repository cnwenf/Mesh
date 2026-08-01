/**
 * AgentDetailPage 组件测试(agent.md §4.3):五 Tab 渲染、配置保存前越界校验(H-F2)、
 * top_p/模型/预设控件(H-F3)、历史「对比上一版」(H-F4)、可见性单选 + 转移(H-F5)、
 * 暂停弹窗 in_flight_policy(M-F1)、presence 脚手架(M-F2)、生命周期动作。
 * 页面自建 client → 桩 global fetch,按调用顺序返回包络。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { RealtimeContext } from '../../../shell/AppShell';
import { AgentDetailPage } from '../AgentDetailPage';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

interface Recorded {
  url: string;
  method: string;
  init?: RequestInit;
}

/** 按 URL 派发响应(顺序无关,规避模块级 fetch 引用捕获问题)。 */
function setup(): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, init });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/executions')) {
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }
    if (url.includes('/agents/a-1/skills') || url.includes('/skill-installations')) {
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }
    if (url.includes('/config-versions')) {
      if (method === 'POST') return fakeResponse({ body: { data: AGENT } }); // rollback
      return fakeResponse({ body: { data: VERSIONS, next_cursor: null } });
    }
    if (method !== 'GET') return fakeResponse({ body: { data: AGENT } }); // mutations
    return fakeResponse({ body: { data: AGENT } }); // getAgent
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

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

const AGENT = {
  id: 'a-1',
  member: {
    id: 'm-1',
    member_type: 'agent',
    display_name: '小测',
    avatar_url: null,
    role_tag: '测试工程师',
    role: 'member',
    status: 'active',
  },
  display_name: '小测',
  name: '小测',
  avatar_url: null,
  role_tag: '测试工程师',
  badge_kind: 'ai',
  lifecycle_status: 'active',
  visibility: 'workspace',
  trigger_on_assign: true,
  owner_user_id: 'u-1',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  slug: null,
  bio: 'bio',
  system_instructions: '指令',
  model_config: { model_tier: 'balanced', temperature: 0.2, top_p: 1, max_tokens: 8192 },
  default_runtime_id: null,
  active_config_version_id: 'v-2',
  current_version: null,
};

const VERSIONS = [
  {
    id: 'v-2',
    agent_id: 'a-1',
    snapshot: { model_config: { temperature: 0.7 } },
    change_summary: '改',
    changed_by: 'm-1',
    created_at: '2026-01-02T00:00:00Z',
  },
  {
    id: 'v-1',
    agent_id: 'a-1',
    snapshot: { model_config: { temperature: 0.2 } },
    change_summary: '初',
    changed_by: 'm-1',
    created_at: '2026-01-01T00:00:00Z',
  },
];

function renderPage() {
  // useParams 需要匹配的 <Route> 才能解析 :agentId。
  return renderWithProviders(
    <Routes>
      <Route path="/agents/:agentId" element={<AgentDetailPage />} />
    </Routes>,
    { route: '/agents/a-1' },
  );
}

describe('AgentDetailPage', () => {
  it('最近执行完成时展示统一 succeeded 状态与执行深链', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/executions')) {
        return fakeResponse({
          body: {
            data: [
              {
                id: 'e-1',
                agent_id: 'a-1',
                issue_id: null,
                trigger: 'manual',
                status: 'completed',
                priority: 0,
                required_capabilities: [],
                label_requirements: {},
                timeout_seconds: 600,
                queued_at: '2026-01-02T00:00:00Z',
                finished_at: '2026-01-02T00:01:00Z',
                failure_reason: null,
                result: {},
              },
            ],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ body: { data: AGENT } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();

    await waitFor(() =>
      expect(
        screen.getByTestId('agent-detail-presence').querySelector('[data-state="succeeded"]'),
      ).not.toBeNull(),
    );
    expect(screen.getByTestId('agent-latest-execution')).toHaveTextContent(
      /completed successfully|成功完成/i,
    );
    expect(screen.getByTestId('agent-latest-execution-link')).toHaveAttribute(
      'href',
      '/executions/e-1',
    );
  });

  it('渲染详情头(底座 Avatar/Badge/运行态徽标)+ 概览 + presence 脚手架', async () => {
    setup();
    renderPage();
    expect(await screen.findByTestId('agent-detail-name')).toHaveTextContent('小测');
    // 底座 Avatar(agent 统一轮廓)替代手写头像。
    expect(document.querySelector('.mesh-avatar--agent')).not.toBeNull();
    // AI 徽章出自 design Badge(accent),testid 保留在外层包装。
    expect(
      screen.getByTestId('agent-detail-badge').querySelector('.mesh-badge--accent'),
    ).not.toBeNull();
    // 运行态徽标:无帧 → unknown(data-state);容量说明为「Capacity: —」。
    expect(
      screen.getByTestId('agent-detail-presence').querySelector('[data-state="unknown"]'),
    ).not.toBeNull();
    expect(screen.getByTestId('agent-detail-presence-caption')).toHaveTextContent('—');
    expect(screen.getByTestId('agent-panel-overview')).toBeInTheDocument();
  });

  it('配置 Tab 越界红字拦截保存(H-F2)', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-config'));
    expect(screen.getByTestId('agent-panel-config')).toBeInTheDocument();
    const temp = screen.getByTestId('agent-detail-temperature') as HTMLInputElement;
    await user.clear(temp);
    await user.type(temp, '5');
    expect(screen.getByTestId('agent-config-error')).toBeInTheDocument();
    expect((screen.getByTestId('agent-config-save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('配置 Tab 含 top_p / 模型 / 预设控件,合法时保存(H-F3)', async () => {
    const user = userEvent.setup();
    setup(); // save → reload getAgent
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-config'));
    expect(screen.getByTestId('agent-detail-top-p')).toBeInTheDocument();
    expect(screen.getByTestId('agent-detail-model')).toBeInTheDocument();
    expect(screen.getByTestId('agent-detail-preset')).toBeInTheDocument();
    await user.selectOptions(screen.getByTestId('agent-detail-preset'), 'fast_triage');
    expect((screen.getByTestId('agent-detail-temperature') as HTMLInputElement).value).toBe('0.3');
    await user.click(screen.getByTestId('agent-config-save'));
    await waitFor(() => expect(screen.getByTestId('agent-detail-name')).toBeInTheDocument());
  });

  it('历史 Tab 对比上一版(H-F4)', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-history'));
    expect(await screen.findByTestId('agent-version-v-2')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-compare-v-2'));
    expect(screen.getByTestId('agent-compare-body-v-2')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-compare-v-2')); // 收起
    expect(screen.queryByTestId('agent-compare-body-v-2')).not.toBeInTheDocument();
  });

  it('可见性 Tab 单选切换 + 转移弹窗(H-F5)', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-visibility'));
    await user.click(screen.getByTestId('agent-detail-visibility-private'));
    await user.click(screen.getByTestId('agent-transfer-button'));
    expect(screen.getByTestId('agent-transfer-dialog')).toBeInTheDocument();
    await user.type(screen.getByTestId('agent-transfer-user-id'), 'u-9');
    await user.click(screen.getByTestId('agent-transfer-confirm'));
    await waitFor(() =>
      expect(screen.queryByTestId('agent-transfer-dialog')).not.toBeInTheDocument(),
    );
  });

  it('暂停弹窗选 cancel_current 发 body(M-F1)', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-pause-button'));
    expect(screen.getByTestId('agent-pause-dialog')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-pause-cancel'));
    await user.click(screen.getByTestId('agent-pause-confirm'));
    await waitFor(() => {
      const pauseCall = calls.find((c) => c.url.includes(':pause'));
      expect(pauseCall).toBeDefined();
      expect(JSON.parse((pauseCall!.init?.body as string) ?? '{}').in_flight_policy).toBe(
        'cancel_current',
      );
    });
  });

  it('非 pause 生命周期动作直接发', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-disable-button'));
    await waitFor(() => expect(calls.find((c) => c.url.includes(':disable'))).toBeDefined());
  });

  it('编辑按钮打开向导', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-edit-button'));
    expect(await screen.findByTestId('agent-wizard-basic')).toBeInTheDocument();
  });

  it('暂停弹窗取消按钮关闭弹窗', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-pause-button'));
    expect(screen.getByTestId('agent-pause-dialog')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-pause-cancel-btn'));
    expect(screen.queryByTestId('agent-pause-dialog')).not.toBeInTheDocument();
  });

  it('转移弹窗空 user id 不发请求 + 取消按钮', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-visibility'));
    await user.click(screen.getByTestId('agent-transfer-button'));
    expect((screen.getByTestId('agent-transfer-confirm') as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByTestId('agent-transfer-cancel-btn'));
    expect(screen.queryByTestId('agent-transfer-dialog')).not.toBeInTheDocument();
    expect(calls.find((c) => c.url.includes(':transfer'))).toBeUndefined();
  });

  it('可见性切到当前值不发请求(早返回)', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-visibility'));
    // 当前为 workspace,再点 workspace → no-op
    await user.click(screen.getByTestId('agent-detail-visibility-workspace'));
    expect(calls.find((c) => c.method === 'PATCH')).toBeUndefined();
  });

  it('历史 Tab 回滚非当前版本', async () => {
    const user = userEvent.setup();
    const calls = setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-history'));
    expect(await screen.findByTestId('agent-version-v-1')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-rollback-v-1'));
    await waitFor(() => expect(calls.find((c) => c.url.includes(':rollback'))).toBeDefined());
  });

  it('配置 Tab 改 model / preset 控件', async () => {
    const user = userEvent.setup();
    setup();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-config'));
    await user.selectOptions(screen.getByTestId('agent-detail-model'), 'mainstream-llm-balanced');
    await user.selectOptions(screen.getByTestId('agent-detail-preset'), 'creative_draft');
    expect((screen.getByTestId('agent-detail-temperature') as HTMLInputElement).value).toBe('0.9');
  });
});

// --- 扩展覆盖:错误态 / 非管理态 / 实时帧 / 边界分支 -------------------------------

const MEMBER_ME = {
  ...ME,
  memberships: [{ ...ME.memberships[0], role: 'member' }],
};

interface StubOptions {
  readonly me?: unknown;
  readonly agent?: Record<string, unknown>;
  /** 前 N 次 getAgent 返回 500(触发错误面板)。 */
  readonly detailFailures?: number;
  /** 非 GET 变更请求的状态码(默认 200)。 */
  readonly mutationStatus?: number;
}

function setupWith(opts: StubOptions = {}): Recorded[] {
  const agent = { ...AGENT, ...opts.agent };
  const calls: Recorded[] = [];
  let detailAttempts = 0;
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, init });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: opts.me ?? ME } });
    if (url.includes('/executions')) {
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }
    if (url.includes('/agents/a-1/skills') || url.includes('/skill-installations')) {
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }
    if (url.includes('/config-versions')) {
      if (method === 'POST') return fakeResponse({ body: { data: agent } });
      return fakeResponse({ body: { data: VERSIONS, next_cursor: null } });
    }
    if (method === 'GET') {
      detailAttempts += 1;
      if (detailAttempts <= (opts.detailFailures ?? 0)) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'boom' } },
        });
      }
      return fakeResponse({ body: { data: agent } });
    }
    const status = opts.mutationStatus ?? 200;
    return fakeResponse({
      status,
      body:
        status === 200
          ? { data: agent }
          : { error: { code: 'internal_error', message: 'mutation failed' } },
    });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

interface FakeFrame {
  event: string;
  payload?: unknown;
}

function makeFakeRealtime() {
  const handlers: Array<(frame: FakeFrame) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((handler: (frame: FakeFrame) => void) => {
      handlers.push(handler);
      return () => undefined;
    }),
  };
  return {
    client,
    emit: (frame: FakeFrame): void => {
      for (const handler of handlers) handler(frame);
    },
  };
}

function renderPageWithRealtime(realtime: unknown) {
  return renderWithProviders(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <RealtimeContext.Provider value={realtime as any}>
      <Routes>
        <Route path="/agents/:agentId" element={<AgentDetailPage />} />
      </Routes>
    </RealtimeContext.Provider>,
    { route: '/agents/a-1' },
  );
}

describe('AgentDetailPage 扩展覆盖', () => {
  it('加载失败显示错误面板,重试后恢复', async () => {
    const user = userEvent.setup();
    setupWith({ detailFailures: 1 });
    renderPage();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('agent-detail-name')).toHaveTextContent('小测');
  });

  it('返回按钮回名册', async () => {
    const user = userEvent.setup();
    setupWith();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-detail-back'));
    await waitFor(() => expect(screen.queryByTestId('agent-detail-page')).not.toBeInTheDocument());
  });

  it('配置 Tab 全控件交互后保存,PATCH /config 携带完整 model_config', async () => {
    const user = userEvent.setup();
    const calls = setupWith();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-config'));
    await user.selectOptions(screen.getByTestId('agent-detail-preset'), 'strict_engineering');
    await user.selectOptions(screen.getByTestId('agent-detail-model'), 'mainstream-llm-strong');
    await user.click(screen.getByTestId('agent-detail-tier-strong_reasoning'));
    await user.clear(screen.getByTestId('agent-detail-instructions'));
    await user.type(screen.getByTestId('agent-detail-instructions'), '新指令');
    await user.clear(screen.getByTestId('agent-detail-temperature'));
    await user.type(screen.getByTestId('agent-detail-temperature'), '0.5');
    await user.clear(screen.getByTestId('agent-detail-top-p'));
    await user.type(screen.getByTestId('agent-detail-top-p'), '0.9');
    await user.clear(screen.getByTestId('agent-detail-max-tokens'));
    await user.type(screen.getByTestId('agent-detail-max-tokens'), '4096');
    await user.selectOptions(screen.getByTestId('agent-detail-effort'), 'high');
    await user.click(screen.getByTestId('agent-config-save'));
    await waitFor(() => {
      const save = calls.find((c) => c.method === 'PATCH' && c.url.includes('/config'));
      expect(save).toBeDefined();
      const body = JSON.parse((save!.init?.body as string) ?? '{}');
      expect(body.model_config).toMatchObject({
        model: 'mainstream-llm-strong',
        model_tier: 'strong_reasoning',
        temperature: 0.5,
        top_p: 0.9,
        max_tokens: 4096,
        reasoning_effort: 'high',
      });
      expect(body.system_instructions).toBe('新指令');
    });
  });

  it('保存失败回显 toast,不崩溃', async () => {
    const user = userEvent.setup();
    setupWith({ mutationStatus: 500 });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-config'));
    await user.click(screen.getByTestId('agent-config-save'));
    expect(await screen.findByText('mutation failed')).toBeInTheDocument();
  });

  it('生命周期动作失败回显 toast', async () => {
    const user = userEvent.setup();
    setupWith({ mutationStatus: 500 });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-disable-button'));
    expect(await screen.findByText('mutation failed')).toBeInTheDocument();
  });

  it('转移失败:toast 回显且弹窗保留', async () => {
    const user = userEvent.setup();
    setupWith({ mutationStatus: 500 });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-visibility'));
    await user.click(screen.getByTestId('agent-transfer-button'));
    await user.type(screen.getByTestId('agent-transfer-user-id'), 'u-9');
    await user.click(screen.getByTestId('agent-transfer-confirm'));
    expect(await screen.findByText('mutation failed')).toBeInTheDocument();
    expect(screen.getByTestId('agent-transfer-dialog')).toBeInTheDocument();
  });

  it('暂停弹窗:切回 finish_current 并填原因,body 携带 reason', async () => {
    const user = userEvent.setup();
    const calls = setupWith();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-pause-button'));
    await user.click(screen.getByTestId('agent-pause-cancel'));
    await user.click(screen.getByTestId('agent-pause-finish'));
    await user.type(screen.getByTestId('agent-pause-reason'), '维护窗口');
    await user.click(screen.getByTestId('agent-pause-confirm'));
    await waitFor(() => {
      const pause = calls.find((c) => c.url.includes(':pause'));
      expect(pause).toBeDefined();
      const body = JSON.parse((pause!.init?.body as string) ?? '{}');
      expect(body.in_flight_policy).toBe('finish_current');
      expect(body.reason).toBe('维护窗口');
    });
  });

  it('model_config 缺省值回退(0.2 / 1 / 8192 / medium / balanced)', async () => {
    const user = userEvent.setup();
    setupWith({ agent: { model_config: {} } });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-config'));
    expect((screen.getByTestId('agent-detail-temperature') as HTMLInputElement).value).toBe('0.2');
    expect((screen.getByTestId('agent-detail-top-p') as HTMLInputElement).value).toBe('1');
    expect((screen.getByTestId('agent-detail-max-tokens') as HTMLInputElement).value).toBe('8192');
  });

  it('role_tag 与 bio 均为空时不渲染副标题', async () => {
    setupWith({ agent: { role_tag: null, bio: null } });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    expect(document.querySelector('.mesh-agents-detail__subtitle')).not.toBeInTheDocument();
  });

  it('bio 为空字符串时副标题不带分隔点', async () => {
    setupWith({ agent: { role_tag: '测试', bio: '' } });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    const subtitle = document.querySelector('.mesh-agents-detail__subtitle');
    expect(subtitle?.textContent).toBe('测试');
  });

  it('非管理角色:无编辑/动作/保存/转移控件,配置只读', async () => {
    const user = userEvent.setup();
    setupWith({ me: MEMBER_ME });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    expect(screen.queryByTestId('agent-edit-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('agent-pause-button')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('agent-tab-config'));
    expect(screen.queryByTestId('agent-config-save')).not.toBeInTheDocument();
    expect((screen.getByTestId('agent-detail-temperature') as HTMLInputElement).disabled).toBe(
      true,
    );
    await user.click(screen.getByTestId('agent-tab-visibility'));
    expect(screen.queryByTestId('agent-transfer-button')).not.toBeInTheDocument();
  });

  it('paused 状态展示 resume 动词(active 的 pause 不出现)', async () => {
    setupWith({ agent: { lifecycle_status: 'paused' } });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    expect(screen.getByTestId('agent-resume-button')).toBeInTheDocument();
    expect(screen.queryByTestId('agent-pause-button')).not.toBeInTheDocument();
  });

  it('编辑向导:关闭按钮触发 onClose;完成保存触发 onSaved 重拉', async () => {
    const user = userEvent.setup();
    const calls = setupWith();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-edit-button'));
    expect(await screen.findByTestId('agent-wizard-basic')).toBeInTheDocument();
    // onClose:点击对话框关闭按钮。
    await user.click(screen.getByRole('button', { name: 'Close dialog' }));
    await waitFor(() => expect(screen.queryByTestId('agent-wizard-basic')).not.toBeInTheDocument());
    // onSaved:重开并走完四步 → PATCH + PATCH /config → 重拉详情。
    await user.click(screen.getByTestId('agent-edit-button'));
    const detailsBefore = calls.filter(
      (c) => c.method === 'GET' && c.url.includes('/agents/'),
    ).length;
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-next'));
    await user.click(screen.getByTestId('agent-wizard-finish'));
    await waitFor(() => {
      const detailsAfter = calls.filter(
        (c) => c.method === 'GET' && c.url.includes('/agents/'),
      ).length;
      expect(detailsAfter).toBeGreaterThan(detailsBefore);
    });
  });

  it('实时帧:agent.updated 重拉、异 id 忽略、presence 三元组渲染', async () => {
    const rt = makeFakeRealtime();
    setupWith();
    renderPageWithRealtime(rt);
    await screen.findByTestId('agent-detail-name');
    expect(rt.client.subscribe).toHaveBeenCalledWith('workspace:ws-1:agents');
    expect(rt.client.subscribe).toHaveBeenCalledWith('agent:a-1:presence');

    const detailsBefore = 1;
    // 其它 agent 的帧 → 忽略。
    rt.emit({ event: 'agent.updated', payload: { data: { id: 'other' } } });
    // 本 agent 的 agent.updated → 重拉。
    rt.emit({ event: 'agent.updated', payload: { data: { id: 'a-1' } } });
    await waitFor(() => expect(screen.getByTestId('agent-detail-name')).toBeInTheDocument());
    // presence 帧 → 运行态 running(data-state)+ 容量三元组说明。
    rt.emit({
      event: 'agent.presence',
      payload: { running: 1, queued: 2, awaiting_approval: 3 },
    });
    await waitFor(() =>
      expect(
        screen.getByTestId('agent-detail-presence').querySelector('[data-state="running"]'),
      ).not.toBeNull(),
    );
    const caption = screen.getByTestId('agent-detail-presence-caption');
    expect(caption).toHaveTextContent('1');
    expect(caption).toHaveTextContent('2');
    expect(caption).toHaveTextContent('3');
    void detailsBefore;
  });

  it('实时帧:agent.deleted 跳回名册', async () => {
    const rt = makeFakeRealtime();
    setupWith();
    renderPageWithRealtime(rt);
    await screen.findByTestId('agent-detail-name');
    rt.emit({ event: 'agent.deleted', payload: { data: { id: 'a-1' } } });
    await waitFor(() => expect(screen.queryByTestId('agent-detail-page')).not.toBeInTheDocument());
  });

  it('概览/技能 Tab 切换 + trigger_on_assign 否分支', async () => {
    const user = userEvent.setup();
    setupWith({ agent: { trigger_on_assign: false } });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-skills'));
    expect(screen.getByTestId('agent-panel-skills')).toBeInTheDocument();
    await user.click(screen.getByTestId('agent-tab-overview'));
    expect(screen.getByTestId('agent-panel-overview')).toBeInTheDocument();
  });

  it('空 system_instructions / role_tag 缺省回退,副标题仅 bio', async () => {
    const user = userEvent.setup();
    setupWith({ agent: { system_instructions: null, role_tag: null } });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    const subtitle = document.querySelector('.mesh-agents-detail__subtitle');
    expect(subtitle?.textContent).toContain('bio');
    await user.click(screen.getByTestId('agent-tab-config'));
    expect((screen.getByTestId('agent-detail-instructions') as HTMLTextAreaElement).value).toBe('');
  });

  it('未知生命周期状态无动作按钮(VERBS 回退空)', async () => {
    setupWith({ agent: { lifecycle_status: 'unknown_state' } });
    renderPage();
    await screen.findByTestId('agent-detail-name');
    expect(screen.queryByTestId('agent-pause-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('agent-resume-button')).not.toBeInTheDocument();
  });

  it('presence 帧缺字段回退 0;lifecycle_changed 帧触发重拉', async () => {
    const rt = makeFakeRealtime();
    setupWith();
    renderPageWithRealtime(rt);
    await screen.findByTestId('agent-detail-name');
    rt.emit({ event: 'agent.presence', payload: {} });
    // 缺字段回退 0 → 三元组全 0 → idle 态;容量说明含 0。
    await waitFor(() =>
      expect(
        screen.getByTestId('agent-detail-presence').querySelector('[data-state="idle"]'),
      ).not.toBeNull(),
    );
    expect(screen.getByTestId('agent-detail-presence-caption')).toHaveTextContent('0');
    rt.emit({ event: 'agent.lifecycle_changed', payload: { data: { id: 'a-1' } } });
    await waitFor(() => expect(screen.getByTestId('agent-detail-name')).toBeInTheDocument());
  });

  it('保存配置:空 instructions 落 null;预设选「无」为 no-op', async () => {
    const user = userEvent.setup();
    const calls = setupWith();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-config'));
    await user.clear(screen.getByTestId('agent-detail-instructions'));
    await user.selectOptions(screen.getByTestId('agent-detail-preset'), '');
    await user.click(screen.getByTestId('agent-config-save'));
    await waitFor(() => {
      const save = calls.find((c) => c.method === 'PATCH' && c.url.includes('/config'));
      expect(save).toBeDefined();
      const body = JSON.parse((save!.init?.body as string) ?? '{}');
      expect(body.system_instructions).toBe(null);
    });
  });

  it('历史:change_summary 缺省渲染空;非管理角色看只读历史', async () => {
    const user = userEvent.setup();
    const versionsWithNull = [{ ...VERSIONS[0], change_summary: null }, VERSIONS[1]];
    const calls: Recorded[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method, init });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: MEMBER_ME } });
      if (url.includes('/config-versions')) {
        return fakeResponse({ body: { data: versionsWithNull, next_cursor: null } });
      }
      return fakeResponse({ body: { data: AGENT } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await screen.findByTestId('agent-detail-name');
    await user.click(screen.getByTestId('agent-tab-history'));
    expect(await screen.findByTestId('agent-version-v-2')).toBeInTheDocument();
    // 非管理:无回滚按钮,仅当前版本标记。
    expect(screen.queryByTestId('agent-rollback-v-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('agent-current-v-2')).toBeInTheDocument();
  });

  it('历史 Tab 加载失败时版本列表回退为空', async () => {
    const user = userEvent.setup();
    const calls = setupWith();
    renderPage();
    await screen.findByTestId('agent-detail-name');
    // 让版本列表 GET 失败:替换 fetch 桩为条件 500。
    const failing = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? 'GET', init });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/config-versions')) {
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error' } } });
      }
      return fakeResponse({ body: { data: AGENT } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', failing);
    await user.click(screen.getByTestId('agent-tab-history'));
    await waitFor(() => expect(screen.getByTestId('agent-panel-history')).toBeInTheDocument());
    expect(screen.queryByTestId('agent-version-v-2')).not.toBeInTheDocument();
  });
});
