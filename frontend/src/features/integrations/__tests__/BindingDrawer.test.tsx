/**
 * BindingDrawer 组件测试(integrations.md §4.2):绑定表 + 创建抽屉(IM/VCS 匹配
 * 规则、作用域项目、目标 agent 留空仅审计)+ 删除 + 409 冲突 toast + RBAC。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { BindingDrawer } from '../BindingDrawer';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const BINDING = {
  id: 'b-1',
  integration_id: 'int-1',
  provider: 'slack',
  provider_tenant_key: 'T1',
  scope: 'workspace',
  project_id: null,
  external_ref: 'C123',
  match_config: {},
  bound_agent_id: 'agent-1',
  status: 'active',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

const AGENT = {
  id: 'agent-1',
  member_type: 'agent',
  role: 'member',
  status: 'active',
  display_name: '值班 Agent',
  joined_at: null,
  profile: null,
};

const PROJECT = { id: 'proj-1', name: 'INFRA', key: 'INF', status: 'active' };

interface Recorded {
  url: string;
  method: string;
  body: unknown;
}

function setup(opts: { readonly bindings?: unknown[]; readonly status?: number } = {}): Recorded[] {
  const calls: Recorded[] = [];
  const bindings = opts.bindings ?? [BINDING];
  const status = opts.status ?? 200;
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (/\/integrations\/int-1\/bindings/.test(url) && method === 'GET')
      return fakeResponse({ body: { data: bindings, next_cursor: null } });
    if (url.includes('/members')) return fakeResponse({ body: { data: [AGENT], next_cursor: null } });
    if (url.includes('/projects')) return fakeResponse({ body: { data: [PROJECT], next_cursor: null } });
    if (method === 'POST' && /\/integrations\/int-1\/bindings/.test(url)) {
      if (status !== 200)
        return fakeResponse({ status, body: { error: { code: 'binding_conflict', message: 'conflict' } } });
      return fakeResponse({ body: { data: BINDING } });
    }
    if (method === 'DELETE') return fakeResponse({ status: 204 });
    return fakeResponse({ body: { data: [], next_cursor: null } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderDrawer(kind: 'im_slack' | 'vcs_github' = 'im_slack', isAdmin = true) {
  return renderWithProviders(
    <BindingDrawer workspaceId="ws-1" integrationId="int-1" integrationKind={kind} isAdmin={isAdmin} />,
  );
}

describe('BindingDrawer', () => {
  it('renders the binding table with agent name', async () => {
    setup();
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('binding-row-b-1')).toBeInTheDocument());
    expect(screen.getByTestId('binding-agent-b-1').textContent).toBe('值班 Agent');
  });

  it('hides the create button for non-admins', async () => {
    setup();
    renderDrawer('im_slack', false);
    await waitFor(() => expect(screen.getByTestId('binding-row-b-1')).toBeInTheDocument());
    expect(screen.queryByTestId('binding-create')).toBeNull();
  });

  it('creates an IM binding with match config and project scope', async () => {
    const calls = setup();
    renderDrawer('im_slack');
    await waitFor(() => expect(screen.getByTestId('binding-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('binding-create'));
    await userEvent.type(screen.getByTestId('binding-external-ref'), 'C999');
    await userEvent.selectOptions(screen.getByTestId('binding-scope'), 'project');
    await userEvent.selectOptions(screen.getByTestId('binding-project'), 'proj-1');
    await userEvent.click(screen.getByTestId('binding-trigger-mention'));
    await userEvent.type(screen.getByTestId('binding-keyword-include'), '值班, 线上');
    await userEvent.selectOptions(screen.getByTestId('binding-agent-select'), 'agent-1');
    await userEvent.click(screen.getByTestId('binding-submit'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.method === 'POST' && /\/integrations\/int-1\/bindings$/.test(call.url)),
      ).toBe(true),
    );
    const createCall = calls.find(
      (call) => call.method === 'POST' && /\/integrations\/int-1\/bindings$/.test(call.url),
    );
    const body = createCall?.body as Record<string, unknown>;
    expect(body.external_ref).toBe('C999');
    expect(body.scope).toBe('project');
    expect(body.project_id).toBe('proj-1');
    expect((body.match_config as Record<string, unknown>).trigger_on).toContain('mention');
    expect((body.match_config as Record<string, unknown>).keyword_include).toEqual(['值班', '线上']);
    expect(body.bound_agent_id).toBe('agent-1');
  });

  it('creates a VCS binding with events and branch pattern; empty agent = audit only', async () => {
    const calls = setup();
    renderDrawer('vcs_github');
    await waitFor(() => expect(screen.getByTestId('binding-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('binding-create'));
    await userEvent.type(screen.getByTestId('binding-external-ref'), 'owner/repo');
    await userEvent.click(screen.getByTestId('binding-vcs-event-pull_request'));
    await userEvent.type(screen.getByTestId('binding-branch-pattern'), '^main$');
    await userEvent.click(screen.getByTestId('binding-submit'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.method === 'POST' && /\/integrations\/int-1\/bindings$/.test(call.url)),
      ).toBe(true),
    );
    const createCall = calls.find(
      (call) => call.method === 'POST' && /\/integrations\/int-1\/bindings$/.test(call.url),
    );
    const body = createCall?.body as Record<string, unknown>;
    expect((body.match_config as Record<string, unknown>).vcs_events).toContain('pull_request');
    expect((body.match_config as Record<string, unknown>).branch_pattern).toBe('^main$');
    expect(body.bound_agent_id).toBeNull();
  });

  it('shows the audit-only hint inside the drawer', async () => {
    setup();
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('binding-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('binding-create'));
    await waitFor(() => expect(screen.getByTestId('binding-audit-hint')).toBeInTheDocument());
  });

  it('deletes a binding', async () => {
    const calls = setup();
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('binding-delete-b-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('binding-delete-b-1'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/integration-bindings/b-1') && call.method === 'DELETE')).toBe(true),
    );
  });

  it('surfaces a binding_conflict error toast', async () => {
    setup({ status: 409 });
    renderDrawer('im_slack');
    await waitFor(() => expect(screen.getByTestId('binding-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('binding-create'));
    await userEvent.type(screen.getByTestId('binding-external-ref'), 'C999');
    await userEvent.click(screen.getByTestId('binding-submit'));
    await waitFor(() => expect(screen.getByText(/already bound/)).toBeInTheDocument());
  });

  it('shows the empty state without bindings', async () => {
    setup({ bindings: [] });
    renderDrawer();
    await waitFor(() => expect(screen.getByText(/No bindings yet/)).toBeInTheDocument());
  });

  it('shows the error state on load failure', async () => {
    const impl = (async () =>
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderDrawer();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
  });

  it('retries after a load error', async () => {
    const calls: Recorded[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method ?? 'GET', body: undefined });
      return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderDrawer();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
    const before = calls.length;
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(before));
  });

  it('closes the drawer via cancel and the close button', async () => {
    setup();
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('binding-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('binding-create'));
    await waitFor(() => expect(screen.getByTestId('binding-external-ref')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('binding-external-ref')).toBeNull());
    await userEvent.click(screen.getByTestId('binding-create'));
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('binding-external-ref')).toBeNull());
  });

  it('falls back to the agent id for an unknown bound agent', async () => {
    setup({ bindings: [{ ...BINDING, id: 'b-2', bound_agent_id: 'ghost-agent' }] });
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('binding-agent-b-2')).toBeInTheDocument());
    expect(screen.getByTestId('binding-agent-b-2').textContent).toBe('ghost-agent');
  });

  it('sends keyword_exclude and renders a project-scoped audit-only binding', async () => {
    const projectBinding = {
      ...BINDING,
      id: 'b-3',
      scope: 'project',
      project_id: 'proj-1',
      bound_agent_id: null,
      external_ref: 'owner/repo',
    };
    const calls = setup({ bindings: [projectBinding] });
    renderDrawer('im_slack');
    await waitFor(() => expect(screen.getByTestId('binding-row-b-3')).toBeInTheDocument());
    expect(screen.getByTestId('binding-agent-b-3').textContent).toContain('Audit only');
    // create with keyword_exclude populated
    await userEvent.click(screen.getByTestId('binding-create'));
    await userEvent.type(screen.getByTestId('binding-external-ref'), 'C555');
    await userEvent.type(screen.getByTestId('binding-keyword-exclude'), '忽略');
    await userEvent.click(screen.getByTestId('binding-submit'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.method === 'POST' && /\/integrations\/int-1\/bindings$/.test(call.url)),
      ).toBe(true),
    );
    const createCall = calls.find(
      (call) => call.method === 'POST' && /\/integrations\/int-1\/bindings$/.test(call.url),
    );
    expect((createCall?.body as Record<string, unknown>).match_config).toMatchObject({
      keyword_exclude: ['忽略'],
    });
  });

  it('keeps submit disabled for project scope without a project and toggles triggers', async () => {
    setup();
    renderDrawer('im_slack');
    await waitFor(() => expect(screen.getByTestId('binding-create')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('binding-create'));
    await userEvent.type(screen.getByTestId('binding-external-ref'), 'C999');
    await userEvent.selectOptions(screen.getByTestId('binding-scope'), 'project');
    // project scope but no project selected → disabled
    expect(screen.getByTestId('binding-submit')).toBeDisabled();
    // toggle a trigger on then off (filter branch)
    await userEvent.click(screen.getByTestId('binding-trigger-keyword'));
    await userEvent.click(screen.getByTestId('binding-trigger-keyword'));
    await userEvent.selectOptions(screen.getByTestId('binding-scope'), 'workspace');
    expect(screen.getByTestId('binding-submit')).toBeEnabled();
  });
});
