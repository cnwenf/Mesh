/**
 * 技能库页面组件测试:卡片渲染 / 过滤 / 新建向导 / 实时重拉桩。
 * fetch 打桩(无 MSW,与 agents 页测试同款 fetchStub 模式)。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import { SkillsPage } from '../SkillsPage';

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

const SKILLS = [
  {
    id: 's-1',
    workspace_id: 'ws-1',
    source_id: 'src-1',
    source_type: 'user',
    trust_level: 'reviewed',
    name: '代码评审规范',
    slug: 'code-review-sop',
    summary: '评审 SOP',
    status: 'published',
    current_version_id: 'v-1',
    current_version: '1.0.0',
    has_scripts: true,
    install_status: 'installed',
    required_capabilities: ['read:code'],
    tags: ['review'],
    icon: null,
    created_by: 'm-1',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

function setup(): { calls: { url: string; method: string; body?: string }[] } {
  const calls: { url: string; method: string; body?: string }[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({
      url,
      method,
      body: typeof init?.body === 'string' ? init.body : undefined,
    });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/skills') && method === 'POST') {
      return fakeResponse({ status: 201, body: { data: SKILLS[0] } });
    }
    if (url.includes('/skills')) return fakeResponse({ body: { data: SKILLS, next_cursor: null } });
    return fakeResponse({ body: { data: [] } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return { calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SkillsPage', () => {
  it('渲染技能卡片与来源/状态标签', async () => {
    setup();
    renderWithProviders(<SkillsPage />);
    expect(await screen.findByTestId('skill-card-s-1')).toBeTruthy();
    expect(screen.getByTestId('data-view')).toHaveClass('mesh-skills');
    expect(screen.getByText('代码评审规范')).toBeTruthy();
  });

  it('skill and marketplace links preserve the workspace slug on legacy-route fallback', async () => {
    setup();
    renderWithProviders(
      <Routes>
        <Route path="/skills" element={<SkillsPage />} />
        <Route
          path="/w/:workspaceSlug/automations/skills/:skillId"
          element={<div data-testid="canonical-skill-detail" />}
        />
      </Routes>,
      { route: '/skills' },
    );
    fireEvent.click(await screen.findByText('代码评审规范'));
    expect(await screen.findByTestId('canonical-skill-detail')).toBeTruthy();
  });

  it('搜索触发带 q 参数的列表拉取', async () => {
    const { calls } = setup();
    renderWithProviders(<SkillsPage />);
    await screen.findByTestId('skill-card-s-1');
    fireEvent.change(screen.getByTestId('skills-search'), { target: { value: '评审' } });
    await waitFor(() => {
      expect(calls.some((c) => c.url.includes('q=') && c.url.includes('/skills'))).toBe(true);
    });
  });

  it('新建对话框提交 → POST /skills', async () => {
    const { calls } = setup();
    renderWithProviders(<SkillsPage />);
    fireEvent.click(await screen.findByTestId('skills-create-open'));
    fireEvent.change(screen.getByTestId('skill-create-name'), { target: { value: 'N' } });
    fireEvent.change(screen.getByTestId('skill-create-slug'), {
      target: { value: 'new-skill' },
    });
    fireEvent.change(screen.getByTestId('skill-create-summary'), { target: { value: 'S' } });
    fireEvent.change(screen.getByTestId('skill-create-tags'), {
      target: { value: 'one, , two' },
    });
    fireEvent.click(screen.getByTestId('skill-create-submit'));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/skills'))).toBe(true);
    });
    const createCall = calls.find((c) => c.method === 'POST' && c.url.endsWith('/skills'));
    expect(JSON.parse(createCall?.body ?? '{}')).toMatchObject({
      name: 'N',
      slug: 'new-skill',
      summary: 'S',
      tags: ['one', 'two'],
    });
  });

  it('必填校验:名称/摘要为空不发请求', async () => {
    const { calls } = setup();
    renderWithProviders(<SkillsPage />);
    fireEvent.click(await screen.findByTestId('skills-create-open'));
    fireEvent.click(screen.getByTestId('skill-create-submit'));
    expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/skills'))).toHaveLength(0);
  });

  it('创建对话框可取消', async () => {
    setup();
    renderWithProviders(<SkillsPage />);
    fireEvent.click(await screen.findByTestId('skills-create-open'));
    expect(screen.getByTestId('skill-create-name')).toBeTruthy();
    fireEvent.click(screen.getByText(/取消|Cancel/));
    await waitFor(() => expect(screen.queryByTestId('skill-create-name')).toBeNull());
  });

  it('列表错误态', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<SkillsPage />);
    expect(await screen.findByText(/Something went wrong|技能加载失败/)).toBeTruthy();
  });

  it('新建失败 → 错误提示(catch 分支)', async () => {
    const calls: { method: string; url: string }[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ method, url });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && url.endsWith('/skills')) {
        return fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'x' } } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<SkillsPage />);
    fireEvent.click(await screen.findByTestId('skills-create-open'));
    fireEvent.change(screen.getByTestId('skill-create-name'), { target: { value: 'N' } });
    fireEvent.change(screen.getByTestId('skill-create-summary'), { target: { value: 'S' } });
    fireEvent.click(screen.getByTestId('skill-create-submit'));
    // onCreate catch branch fires (re-throws → CreateSkillDialog shows createFailed)
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/skills'))).toBe(true),
    );
    expect(await screen.findByText(/创建失败|create the skill/i)).toBeTruthy();
  });

  it('打开导入向导(onDone 接线存在)', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<SkillsPage />);
    fireEvent.click(await screen.findByTestId('skills-import-open'));
    // wizard opens on the source step (onDone wired to refresh the list)
    expect(await screen.findByTestId('import-step-source')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Close dialog|关闭对话框/i }));
    await waitFor(() => expect(screen.queryByTestId('import-step-source')).toBeNull());
  });

  it('状态筛选变更触发重拉', async () => {
    let fetches = 0;
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      fetches += 1;
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<SkillsPage />);
    await screen.findByTestId('skills-page-title');
    const before = fetches;
    fireEvent.change(screen.getByTestId('skills-status-filter'), {
      target: { value: 'published' },
    });
    await waitFor(() => expect(fetches).toBeGreaterThan(before));
  });

  it('来源筛选变更把 source_type 传给列表接口', async () => {
    const { calls } = setup();
    renderWithProviders(<SkillsPage />);
    await screen.findByTestId('skill-card-s-1');
    fireEvent.change(screen.getByTestId('skills-source-filter'), {
      target: { value: 'marketplace' },
    });
    await waitFor(() => {
      expect(calls.some((call) => call.url.includes('source_type=marketplace'))).toBe(true);
    });
  });

  it('游标分页点击加载更多并追加下一页', async () => {
    const calls: string[] = [];
    const second = { ...SKILLS[0], id: 's-2', name: '第二个技能' };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('cursor=next-page')) {
        return fakeResponse({ body: { data: [second], next_cursor: null } });
      }
      return fakeResponse({ body: { data: SKILLS, next_cursor: 'next-page' } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<SkillsPage />);
    await screen.findByTestId('skill-card-s-1');
    fireEvent.click(screen.getByTestId('skills-load-more'));
    expect(await screen.findByTestId('skill-card-s-2')).toBeTruthy();
    expect(calls.some((url) => url.includes('cursor=next-page'))).toBe(true);
  });

  it('稀疏技能与更新提示覆盖可选卡片元数据', async () => {
    const sparse = {
      ...SKILLS[0],
      id: 's-sparse',
      source_type: null,
      current_version_id: null,
      current_version: null,
      has_scripts: false,
      install_status: null,
    };
    const updated = {
      ...SKILLS[0],
      id: 's-updated',
      source_type: 'custom',
      current_version_id: null,
      current_version: null,
      has_scripts: false,
      install_status: 'updated_available',
    };
    const impl = (async (input: RequestInfo | URL) => {
      if (String(input).includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({ body: { data: [sparse, updated], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<SkillsPage />);
    const sparseCard = await screen.findByTestId('skill-card-s-sparse');
    const updatedCard = await screen.findByTestId('skill-card-s-updated');
    expect(sparseCard.querySelector('.mesh-skills__card-version')).toBeNull();
    expect(sparseCard.querySelector('.mesh-skills__card-install')).toBeNull();
    expect(updatedCard.querySelector('.mesh-skills__update-flag')).not.toBeNull();
  });

  it('无工作区与成员查询失败时显示对应状态', async () => {
    const noWorkspaceFetch = (async (input: RequestInfo | URL) => {
      if (String(input).includes('/users/me')) {
        return fakeResponse({ body: { data: { ...ME, memberships: [] } } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', noWorkspaceFetch);
    const noWorkspace = renderWithProviders(<SkillsPage />);
    expect(
      await screen.findByText(/not a member of any workspace|还不是任何工作区的成员/i),
    ).toBeTruthy();
    noWorkspace.unmount();

    const failedMembershipFetch = (async () =>
      fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      })) as typeof fetch;
    vi.stubGlobal('fetch', failedMembershipFetch);
    renderWithProviders(<SkillsPage />);
    expect(await screen.findByText(/Could not load skills|技能加载失败/i)).toBeTruthy();
  });

  it('实时事件仅处理工作区频道,提示审批/更新并在卸载时退订', async () => {
    const { calls } = setup();
    const handlers: Array<(frame: { channel: string; event: string }) => void> = [];
    const realtimeClient = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn((handler: (frame: { channel: string; event: string }) => void) => {
        handlers.push(handler);
        return vi.fn();
      }),
    };
    const realtime: RealtimeContextValue = {
      state: 'connected',
      client: realtimeClient as never,
    };
    const rendered = renderWithProviders(
      <RealtimeContext.Provider value={realtime}>
        <SkillsPage />
      </RealtimeContext.Provider>,
    );
    await screen.findByTestId('skill-card-s-1');
    expect(realtimeClient.subscribe).toHaveBeenCalledWith('workspace:ws-1:skills');
    const initial = calls.filter(
      (call) => call.method === 'GET' && call.url.includes('/skills'),
    ).length;
    act(() => {
      handlers[0]({ channel: 'workspace:other:skills', event: 'skill.approval_required' });
      handlers[0]({ channel: 'workspace:ws-1:skills', event: 'skill.approval_required' });
      handlers[0]({ channel: 'workspace:ws-1:skills', event: 'skill.update_available' });
      handlers[0]({ channel: 'workspace:ws-1:skills', event: 'skill.changed' });
    });
    await waitFor(() => {
      expect(
        calls.filter((call) => call.method === 'GET' && call.url.includes('/skills')).length,
      ).toBeGreaterThan(initial);
    });
    expect(screen.getByText(/waiting for review|等待审阅/i)).toBeTruthy();
    expect(screen.getByText(/update|更新/i)).toBeTruthy();
    rendered.unmount();
    expect(realtimeClient.unsubscribe).toHaveBeenCalledWith('workspace:ws-1:skills');
  });
});

describe('SkillsPage 只读成员', () => {
  it('隐藏新建/导入按钮', async () => {
    const meMember = {
      ...ME,
      memberships: [{ ...ME.memberships[0], role: 'member' }],
    };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: meMember } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<SkillsPage />);
    await screen.findByTestId('skills-page-title');
    expect(screen.queryByTestId('skills-create-open')).toBeNull();
    expect(screen.queryByTestId('skills-import-open')).toBeNull();
    expect(screen.getByTestId('skills-market-link')).toBeTruthy();
  });
});
