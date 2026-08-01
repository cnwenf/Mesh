/**
 * agent 技能绑定 Tab 组件测试:列表渲染 / 启停 / 解绑 / 绑定。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AgentSkillsTab } from '../AgentSkillsTab';

const ROW = {
  binding_id: 'b-1',
  skill: {
    id: 's-1',
    name: '发布检查清单',
    slug: 'release-checklist',
    summary: '发布前检查',
    source_type: 'url',
    trust_level: 'untrusted',
    status: 'published',
  },
  skill_version_id: 'v-1',
  version: '1.3.0',
  install_status: 'updated_available',
  enabled: true,
  auto_trigger: true,
  priority: 120,
};

const INSTALLATION = {
  id: 'i-2',
  workspace_id: 'ws-1',
  skill_id: 's-2',
  skill_version_id: 'v-2',
  scope: 'workspace',
  agent_id: null,
  install_status: 'installed',
  auto_update: false,
  granted_capabilities: [
    { capability: 'repo:read', permission: 'read_only' },
    { capability: 'issue:write', permission: 'write' },
    'exec:shell',
  ],
  installed_by: 'm-1',
  installed_at: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const TOOLS = [
  { capability: 'repo:read', permission: 'read_only', enabled: true },
  { capability: 'issue:write', permission: 'write', enabled: false },
  { capability: 'exec:shell', permission: 'confirm_required', enabled: true },
];

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function setup(): { calls: { url: string; method: string; body?: string }[] } {
  const calls: { url: string; method: string; body?: string }[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body as string | undefined });
    if (url.includes('/agents/a-1/tools')) {
      if (method === 'GET') return fakeResponse({ body: { data: TOOLS, next_cursor: null } });
      if (method === 'DELETE') return fakeResponse({ status: 204 });
      return fakeResponse({ body: { data: TOOLS[0] } });
    }
    if (url.includes('/agents/a-1/skills')) {
      if (method === 'GET') return fakeResponse({ body: { data: [ROW], next_cursor: null } });
      return fakeResponse({ body: { data: ROW } }); // bind / patch
    }
    if (url.includes('/skill-installations')) {
      return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
    }
    return fakeResponse({ body: { data: [] } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return { calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AgentSkillsTab', () => {
  it('双列展示技能绑定与 Agent Tools API 返回的真实授权', async () => {
    const { calls } = setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);

    expect(await screen.findByTestId('agent-skills-column')).toBeInTheDocument();
    const tools = screen.getByTestId('agent-tools-column');
    expect(tools).toHaveTextContent('repo:read');
    expect(tools).toHaveTextContent('issue:write');
    expect(tools).toHaveTextContent('exec:shell');
    expect(screen.getByTestId('agent-tool-permission-repo:read')).not.toBeDisabled();
    expect(screen.getByTestId('agent-tool-permission-issue:write')).toHaveValue('write');
    expect(screen.getByTestId('agent-tool-permission-exec:shell')).toHaveValue('confirm_required');
    expect(
      calls.some(({ method, url }) => method === 'GET' && url.endsWith('/api/v1/agents/a-1/tools')),
    ).toBe(true);
  });

  it('工具行以 capability 为行标题且操作名称包含 capability', async () => {
    setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);

    const row = await screen.findByTestId('agent-tool-repo:read');
    expect(within(row).getByRole('rowheader', { name: 'repo:read' })).toBeInTheDocument();
    expect(within(row).getByRole('checkbox', { name: /Enabled.*repo:read/i })).toBeInTheDocument();
    expect(
      within(row).getByRole('combobox', { name: /Permission.*repo:read/i }),
    ).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /Remove.*repo:read/i })).toBeInTheDocument();
  });

  it('技能行以名称为行标题且每个操作名称包含技能名', async () => {
    setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);

    const row = await screen.findByTestId('agent-skill-b-1');
    expect(within(row).getByRole('rowheader', { name: /发布检查清单/ })).toBeInTheDocument();
    expect(
      within(row).getByRole('checkbox', { name: /Enabled.*发布检查清单/i }),
    ).toBeInTheDocument();
    expect(
      within(row).getByRole('checkbox', { name: /Auto trigger.*发布检查清单/i }),
    ).toBeInTheDocument();
    expect(
      within(row).getByRole('spinbutton', { name: /Priority.*发布检查清单/i }),
    ).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /Unbind.*发布检查清单/i })).toBeInTheDocument();
  });

  it('mutation pending 时全局串行并禁用控件，避免双击与乱序写入', async () => {
    const pending = deferred<Response>();
    const mutations: Array<{ url: string; method: string }> = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'GET') {
        if (url.includes('/agents/a-1/tools')) {
          return fakeResponse({ body: { data: TOOLS, next_cursor: null } });
        }
        if (url.includes('/agents/a-1/skills')) {
          return fakeResponse({ body: { data: [ROW], next_cursor: null } });
        }
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      }
      mutations.push({ url, method });
      return pending.promise;
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);

    const enabled = await screen.findByTestId('agent-tool-enabled-repo:read');
    fireEvent.change(screen.getByLabelText('Pick an installation'), {
      target: { value: 'i-2' },
    });
    fireEvent.change(screen.getByLabelText('Capability key'), {
      target: { value: 'net:fetch' },
    });
    expect(screen.getByTestId('agent-skill-bind')).not.toBeDisabled();
    expect(screen.getByTestId('agent-tool-add')).not.toBeDisabled();
    fireEvent.click(enabled);

    const panel = screen.getByTestId('agent-panel-skills');
    expect(panel).toHaveAttribute('aria-busy', 'true');
    expect(enabled).toBeDisabled();
    const skillRow = screen.getByTestId('agent-skill-b-1');
    for (const checkbox of skillRow.querySelectorAll('input[type="checkbox"]')) {
      expect(checkbox).toBeDisabled();
    }
    expect(screen.getByTestId('agent-unbind-b-1')).toBeDisabled();
    expect(screen.getByTestId('agent-tool-permission-repo:read')).toBeDisabled();
    expect(screen.getByTestId('agent-tool-remove-repo:read')).toBeDisabled();
    expect(screen.getByTestId('agent-priority-b-1')).toBeDisabled();
    expect(screen.getByLabelText('Pick an installation')).toBeDisabled();
    expect(screen.getByTestId('agent-skill-bind')).toBeDisabled();
    expect(screen.getByLabelText('Capability key')).toBeDisabled();
    expect(screen.getByLabelText('New capability permission')).toBeDisabled();
    expect(screen.getByTestId('agent-tool-add')).toBeDisabled();

    fireEvent.click(enabled);
    fireEvent.change(screen.getByTestId('agent-tool-permission-repo:read'), {
      target: { value: 'write' },
    });
    fireEvent.click(screen.getByTestId('agent-tool-remove-repo:read'));
    expect(mutations).toHaveLength(1);

    pending.resolve(fakeResponse({ body: { data: TOOLS[0] } }));
    await waitFor(() => {
      expect(screen.getByTestId('agent-panel-skills')).toHaveAttribute('aria-busy', 'false');
    });
    expect(screen.getByTestId('agent-tool-enabled-repo:read')).not.toBeDisabled();
  });

  it('渲染已绑定技能与更新提示', async () => {
    setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    const row = await screen.findByTestId('agent-skill-b-1');
    expect(row.textContent).toContain('发布检查清单');
    expect(row.textContent).toContain('1.3.0');
  });

  it('市场来源与普通安装态使用对应视觉分支', async () => {
    const marketplaceRow = {
      ...ROW,
      install_status: 'installed',
      skill: { ...ROW.skill, source_type: 'marketplace' },
    };
    const builtinRow = {
      ...marketplaceRow,
      binding_id: 'b-2',
      skill: { ...ROW.skill, id: 's-2', source_type: 'builtin' },
    };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/agents/a-1/tools'))
        return fakeResponse({ body: { data: TOOLS, next_cursor: null } });
      if (url.includes('/agents/a-1/skills'))
        return fakeResponse({ body: { data: [marketplaceRow, builtinRow], next_cursor: null } });
      return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);

    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    expect(await screen.findByTestId('agent-skill-b-1')).toHaveTextContent('发布检查清单');
    expect(screen.getByTestId('agent-skill-b-2')).not.toHaveTextContent('update available');
  });

  it('加载失败后可重试恢复', async () => {
    vi.stubGlobal('fetch', (async () =>
      fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      })) as typeof fetch);
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    expect(await screen.findByText('Could not load skills. Please try again.')).toBeInTheDocument();

    setup();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('agent-skill-b-1')).toBeInTheDocument();
  });

  it('解绑 → DELETE /agents/a-1/skills/b-1', async () => {
    const { calls } = setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    fireEvent.click(await screen.findByTestId('agent-unbind-b-1'));
    await waitFor(() => {
      expect(
        calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/agents/a-1/skills/b-1')),
      ).toBe(true);
    });
  });

  it('只读成员看不到绑定/解绑控件', async () => {
    setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage={false} />);
    await screen.findByTestId('agent-skill-b-1');
    expect(screen.queryByTestId('agent-skill-bind')).toBeNull();
    expect(screen.queryByTestId('agent-unbind-b-1')).toBeNull();
    expect(screen.getByTestId('agent-tool-permission-repo:read')).toBeDisabled();
    expect(screen.getByTestId('agent-tool-enabled-repo:read')).toBeDisabled();
    expect(screen.queryByTestId('agent-tool-add')).toBeNull();
    expect(screen.queryByTestId('agent-tool-remove-repo:read')).toBeNull();
  });

  it('从库中绑定 → POST /agents/a-1/skills', async () => {
    const { calls } = setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    await screen.findByTestId('agent-skill-b-1');
    fireEvent.change(screen.getByLabelText('Pick an installation'), {
      target: { value: 'i-2' },
    });
    fireEvent.click(screen.getByTestId('agent-skill-bind'));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/agents/a-1/skills'))).toBe(
        true,
      );
    });
  });

  it('启用与自动触发开关 → PATCH', async () => {
    const { calls } = setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    const row = await screen.findByTestId('agent-skill-b-1');
    const checkboxes = row.querySelectorAll('input[type="checkbox"]');
    fireEvent.click(checkboxes[0]); // enabled toggle
    await waitFor(() => {
      expect(
        calls.filter((c) => c.method === 'PATCH' && c.url.endsWith('/agents/a-1/skills/b-1'))
          .length,
      ).toBe(1);
      expect(screen.getByTestId('agent-panel-skills')).toHaveAttribute('aria-busy', 'false');
    });
    const refreshed = await screen.findByTestId('agent-skill-b-1');
    fireEvent.click(refreshed.querySelectorAll('input[type="checkbox"]')[1]); // auto_trigger toggle
    await waitFor(() => {
      expect(
        calls.filter((c) => c.method === 'PATCH' && c.url.endsWith('/agents/a-1/skills/b-1'))
          .length,
      ).toBe(2);
    });
  });

  it('空绑定列表 → 空态', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/agents/a-1/skills'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      return fakeResponse({ body: { data: [] } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    expect(await screen.findByTestId('agent-panel-skills')).toBeTruthy();
    expect(screen.queryByTestId('agent-skills-table')).toBeNull();
  });

  it('操作失败走 catch 分支', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'GET') {
        if (url.includes('/agents/a-1/skills'))
          return fakeResponse({ body: { data: [ROW], next_cursor: null } });
        if (url.includes('/skill-installations'))
          return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
        return fakeResponse({ body: { data: [] } });
      }
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    const row = await screen.findByTestId('agent-skill-b-1');
    fireEvent.click(row.querySelectorAll('input[type="checkbox"]')[0]); // enabled toggle → catch
    fireEvent.click(screen.getByTestId('agent-unbind-b-1')); // unbind → catch
    fireEvent.change(screen.getByLabelText('Pick an installation'), { target: { value: 'i-2' } });
    fireEvent.click(screen.getByTestId('agent-skill-bind')); // bind → catch
    await waitFor(() => expect(screen.getByTestId('agent-panel-skills')).toBeTruthy());
  });

  it('优先级输入 blur → PATCH priority', async () => {
    const calls: { url: string; method: string; body?: string }[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method, body: init?.body as string | undefined });
      if (method === 'GET') {
        if (url.includes('/agents/a-1/skills'))
          return fakeResponse({ body: { data: [ROW], next_cursor: null } });
        if (url.includes('/skill-installations'))
          return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
        return fakeResponse({ body: { data: [] } });
      }
      return fakeResponse({ body: { data: ROW } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    const input = (await screen.findByTestId('agent-priority-b-1')) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '300' } });
    fireEvent.blur(input);
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'PATCH' && (c.body ?? '').includes('300'))).toBe(true),
    );
  });

  it('启停/解绑 失败走 catch 分支', async () => {
    const failImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'GET') {
        if (url.includes('/agents/a-1/skills'))
          return fakeResponse({ body: { data: [ROW], next_cursor: null } });
        if (url.includes('/skill-installations'))
          return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
        return fakeResponse({ body: { data: [] } });
      }
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', failImpl);
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    const row = await screen.findByTestId('agent-skill-b-1');
    const checkboxes = row.querySelectorAll('input[type="checkbox"]');
    fireEvent.click(checkboxes[0]); // enabled toggle catch
    fireEvent.click(checkboxes[1]); // auto-trigger toggle catch
    fireEvent.click(screen.getByTestId('agent-unbind-b-1')); // unbind catch
    const priority = screen.getByTestId('agent-priority-b-1');
    fireEvent.blur(priority); // unchanged value returns without a request
    fireEvent.change(priority, { target: { value: '500' } });
    fireEvent.blur(priority); // priority mutation catch
    await waitFor(() => expect(screen.getByTestId('agent-panel-skills')).toBeTruthy());
  });

  it('工具开关 → PATCH enabled', async () => {
    const { calls } = setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    fireEvent.click(await screen.findByTestId('agent-tool-enabled-repo:read'));
    await waitFor(() =>
      expect(
        calls.some(
          ({ method, url, body }) =>
            method === 'PATCH' &&
            url.endsWith('/api/v1/agents/a-1/tools/repo%3Aread') &&
            body === JSON.stringify({ enabled: false }),
        ),
      ).toBe(true),
    );
  });

  it('工具权限下拉 → PATCH permission', async () => {
    const { calls } = setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    fireEvent.change(await screen.findByTestId('agent-tool-permission-repo:read'), {
      target: { value: 'write' },
    });
    await waitFor(() =>
      expect(
        calls.some(
          ({ method, url, body }) =>
            method === 'PATCH' &&
            url.endsWith('/api/v1/agents/a-1/tools/repo%3Aread') &&
            body === JSON.stringify({ permission: 'write' }),
        ),
      ).toBe(true),
    );
  });

  it('可绑定并移除 capability 条目', async () => {
    const { calls } = setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    fireEvent.change(await screen.findByLabelText('Capability key'), {
      target: { value: 'net:fetch' },
    });
    fireEvent.change(screen.getByLabelText('New capability permission'), {
      target: { value: 'read_only' },
    });
    fireEvent.click(screen.getByTestId('agent-tool-add'));
    await waitFor(() => {
      expect(
        calls.some(
          ({ method, url, body }) =>
            method === 'POST' &&
            url.endsWith('/api/v1/agents/a-1/tools') &&
            body === JSON.stringify({ capability: 'net:fetch', permission: 'read_only' }),
        ),
      ).toBe(true);
      expect(screen.getByTestId('agent-panel-skills')).toHaveAttribute('aria-busy', 'false');
    });
    fireEvent.click(screen.getByTestId('agent-tool-remove-repo:read'));
    await waitFor(() => {
      expect(
        calls.some(
          ({ method, url }) =>
            method === 'DELETE' && url.endsWith('/api/v1/agents/a-1/tools/repo%3Aread'),
        ),
      ).toBe(true);
    });
  });

  it('工具写操作失败时保留可恢复界面', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method === 'GET') {
        if (url.includes('/agents/a-1/tools'))
          return fakeResponse({ body: { data: TOOLS, next_cursor: null } });
        if (url.includes('/agents/a-1/skills'))
          return fakeResponse({ body: { data: [ROW], next_cursor: null } });
        if (url.includes('/skill-installations'))
          return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      }
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);

    fireEvent.click(await screen.findByTestId('agent-tool-enabled-repo:read'));
    fireEvent.change(screen.getByTestId('agent-tool-permission-repo:read'), {
      target: { value: 'write' },
    });
    fireEvent.change(screen.getByLabelText('Capability key'), {
      target: { value: 'net:fetch' },
    });
    fireEvent.click(screen.getByTestId('agent-tool-add'));
    fireEvent.click(screen.getByTestId('agent-tool-remove-repo:read'));

    await waitFor(() => expect(screen.getByTestId('agent-tools-table')).toBeInTheDocument());
  });
});
