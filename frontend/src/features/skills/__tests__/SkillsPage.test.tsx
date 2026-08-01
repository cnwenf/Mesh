/**
 * 技能库页面组件测试:卡片渲染 / 过滤 / 新建向导 / 实时重拉桩。
 * fetch 打桩(无 MSW,与 agents 页测试同款 fetchStub 模式)。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
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

function setup(): { calls: { url: string; method: string }[] } {
  const calls: { url: string; method: string }[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
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
    const card = await screen.findByTestId('skill-card-s-1');
    expect(screen.getByText('代码评审规范')).toBeTruthy();
    expect(card).toHaveTextContent(/Reviewed|已审查/);
    expect(card).toHaveTextContent('read:code');
    expect(card).toHaveTextContent(/confirm|确认/i);
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
    fireEvent.change(screen.getByTestId('skill-create-summary'), { target: { value: 'S' } });
    fireEvent.click(screen.getByTestId('skill-create-submit'));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/skills'))).toBe(true);
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
