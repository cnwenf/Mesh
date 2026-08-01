/**
 * agent 技能绑定 Tab 组件测试:列表渲染 / 启停 / 解绑 / 绑定。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
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

function setup(): { calls: { url: string; method: string }[] } {
  const calls: { url: string; method: string }[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
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
  it('双列展示技能绑定与有效工具权限,权限为真实只读值', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/agents/a-1/skills')) {
        if (method === 'GET') return fakeResponse({ body: { data: [ROW], next_cursor: null } });
        return fakeResponse({ body: { data: ROW } });
      }
      if (url.includes('/skill-installations')) {
        return fakeResponse({
          body: {
            data: [{ ...INSTALLATION, skill_id: 's-1' }],
            next_cursor: null,
          },
        });
      }
      return fakeResponse({ body: { data: [] } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);

    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);

    expect(await screen.findByTestId('agent-skills-column')).toBeInTheDocument();
    const tools = screen.getByTestId('agent-tools-column');
    expect(tools).toHaveTextContent('repo:read');
    expect(tools).toHaveTextContent('issue:write');
    expect(tools).toHaveTextContent('exec:shell');
    expect(screen.getByTestId('agent-tool-permission-repo:read')).toBeDisabled();
    expect(screen.getByTestId('agent-tool-permission-issue:write')).toHaveValue('write');
    expect(screen.getByTestId('agent-tool-permission-exec:shell')).toHaveValue('confirm_required');
  });

  it('渲染已绑定技能与更新提示', async () => {
    setup();
    renderWithProviders(<AgentSkillsTab workspaceId="ws-1" agentId="a-1" canManage />);
    const row = await screen.findByTestId('agent-skill-b-1');
    expect(row.textContent).toContain('发布检查清单');
    expect(row.textContent).toContain('1.3.0');
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
    fireEvent.click(checkboxes[1]); // auto_trigger toggle
    await waitFor(() => {
      expect(
        calls.filter((c) => c.method === 'PATCH' && c.url.endsWith('/agents/a-1/skills/b-1'))
          .length,
      ).toBeGreaterThanOrEqual(2);
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
    fireEvent.click(row.querySelectorAll('input[type="checkbox"]')[0]); // enabled toggle catch
    fireEvent.click(screen.getByTestId('agent-unbind-b-1')); // unbind catch
    await waitFor(() => expect(screen.getByTestId('agent-panel-skills')).toBeTruthy());
  });
});
