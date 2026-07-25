/**
 * CyclesPage + CreateCycleDialog 测试(project.md §1.2.5/§4.1 周期页)。
 * 列表渲染(名称/状态/起止)、状态筛选、创建对话框(起止校验:ends < starts 不发
 * 请求;合法创建)、状态切换(planned→active→completed),完成 auto_roll 周期时
 * 响应 next_cycle → 顺延 toast + 新行入列、空态。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import type { RecordedCall } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { CyclesPage } from '../CyclesPage';

const ME = {
  user: { id: 'usr-owner', email: 'owner@acme.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'Team',
      workspace_slug: 'team',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

function makeCycle(overrides: Record<string, unknown> = {}) {
  return {
    id: 'cyc-1',
    project_id: null,
    name: 'Sprint 12',
    starts_at: '2026-08-01',
    ends_at: '2026-08-14',
    state: 'planned',
    auto_roll: false,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

interface StubOptions {
  readonly cycles?: readonly ReturnType<typeof makeCycle>[];
  /** PATCH 响应附带 next_cycle(auto_roll 顺延) */
  readonly nextCycle?: ReturnType<typeof makeCycle>;
}

function stubFetch(opts: StubOptions = {}) {
  const calls: RecordedCall[] = [];
  const cycles = opts.cycles ?? [makeCycle()];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, init });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (method === 'GET' && url.includes('/cycles')) {
      const params = new URL(url).searchParams;
      const state = params.get('state');
      const data =
        state === null ? cycles : cycles.filter((cycle) => cycle.state === state);
      return fakeResponse({ body: { data, next_cursor: null } });
    }
    if (method === 'POST' && url.includes('/cycles')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return fakeResponse({
        status: 201,
        body: { data: makeCycle({ id: 'cyc-new', state: 'planned', ...body }) },
      });
    }
    if (method === 'PATCH' && url.includes('/cycles/')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      const data =
        opts.nextCycle !== undefined && body.state === 'completed'
          ? { ...makeCycle(), ...body, next_cycle: opts.nextCycle }
          : { ...makeCycle(), ...body };
      return fakeResponse({ body: { data } });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

describe('CyclesPage', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the cycle list with state badges and dates', async () => {
    stubFetch({
      cycles: [makeCycle(), makeCycle({ id: 'cyc-2', name: 'Sprint 11', state: 'active' })],
    });
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    expect(await screen.findByText('Sprint 12')).toBeDefined();
    expect(screen.getByText('Sprint 11')).toBeDefined();
    expect(within(screen.getByTestId('cycle-row-cyc-1')).getByText('Planned')).toBeDefined();
    expect(within(screen.getByTestId('cycle-row-cyc-2')).getByText('Active')).toBeDefined();
  });

  it('filters by state via the select', async () => {
    const calls = stubFetch({
      cycles: [makeCycle(), makeCycle({ id: 'cyc-2', name: 'Sprint 11', state: 'active' })],
    });
    const user = userEvent.setup();
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    await screen.findByText('Sprint 12');
    await user.selectOptions(screen.getByTestId('cycles-state-filter'), 'active');
    await waitFor(() => {
      const lists = calls.filter(
        (c) => (c.init?.method ?? 'GET') === 'GET' && c.url.includes('state=active'),
      );
      expect(lists.length).toBeGreaterThan(0);
    });
    expect(screen.queryByText('Sprint 12')).toBeNull();
    expect(screen.getByText('Sprint 11')).toBeDefined();
  });

  it('rejects ends before starts without a request', async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    await user.click(await screen.findByTestId('new-cycle-button'));
    await user.type(screen.getByTestId('cycle-name-input'), 'Bad cycle');
    fireEvent.change(screen.getByTestId('cycle-starts-input'), {
      target: { value: '2026-08-14' },
    });
    fireEvent.change(screen.getByTestId('cycle-ends-input'), {
      target: { value: '2026-08-01' },
    });
    // 起止倒置:行内错误文案 + 提交禁用(不依赖点击,故无请求)
    expect(
      await screen.findByText('End date must be on or after the start date.'),
    ).toBeDefined();
    const submit = screen.getByTestId('create-cycle-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(
      calls.filter((c) => (c.init?.method ?? 'GET') === 'POST' && c.url.includes('/cycles')),
    ).toHaveLength(0);
  });

  it('creates a cycle via the dialog', async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    await user.click(await screen.findByTestId('new-cycle-button'));
    await user.type(screen.getByTestId('cycle-name-input'), 'Sprint 13');
    fireEvent.change(screen.getByTestId('cycle-starts-input'), {
      target: { value: '2026-08-15' },
    });
    fireEvent.change(screen.getByTestId('cycle-ends-input'), {
      target: { value: '2026-08-28' },
    });
    await user.click(screen.getByTestId('cycle-auto-roll'));
    await user.click(screen.getByTestId('create-cycle-submit'));
    await waitFor(() => {
      const posts = calls.filter(
        (c) => (c.init?.method ?? 'GET') === 'POST' && c.url.includes('/cycles'),
      );
      expect(posts.length).toBe(1);
      const body = String(posts[0].init?.body);
      expect(body).toContain('"name":"Sprint 13"');
      expect(body).toContain('"auto_roll":true');
    });
    expect(await screen.findByText('Sprint 13')).toBeDefined();
  });

  it('activates a planned cycle and completes an active one', async () => {
    const calls = stubFetch({
      cycles: [makeCycle(), makeCycle({ id: 'cyc-2', name: 'Sprint 11', state: 'active' })],
    });
    const user = userEvent.setup();
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    await user.click(await screen.findByTestId('cycle-activate-cyc-1'));
    await waitFor(() => {
      const patches = calls.filter(
        (c) => (c.init?.method ?? 'GET') === 'PATCH' && c.url.includes('/cycles/cyc-1'),
      );
      expect(patches.length).toBe(1);
      expect(String(patches[0].init?.body)).toContain('"state":"active"');
    });
    await user.click(screen.getByTestId('cycle-complete-cyc-2'));
    await waitFor(() => {
      const patches = calls.filter(
        (c) => (c.init?.method ?? 'GET') === 'PATCH' && c.url.includes('/cycles/cyc-2'),
      );
      expect(patches.length).toBe(1);
      expect(String(patches[0].init?.body)).toContain('"state":"completed"');
    });
  });

  it('shows the rolled toast and adds the next cycle on auto-roll completion', async () => {
    stubFetch({
      cycles: [makeCycle({ auto_roll: true, state: 'active' })],
      nextCycle: makeCycle({ id: 'cyc-next', name: 'Sprint 13', state: 'planned' }),
    });
    const user = userEvent.setup();
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    await user.click(await screen.findByTestId('cycle-complete-cyc-1'));
    expect((await screen.findAllByText(/Sprint 13/)).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByTestId('cycle-row-cyc-next')).toBeDefined();
  });

  it('renders the empty state when there are no cycles', async () => {
    stubFetch({ cycles: [] });
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    expect(
      await screen.findByText('No cycles match the current filters.'),
    ).toBeDefined();
  });

  it('shows the dialog error when creating a cycle fails', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && url.includes('/cycles')) {
        return fakeResponse({ status: 400, body: { error: { code: 'validation_error', message: 'x' } } });
      }
      if (method === 'GET' && url.includes('/cycles')) {
        return fakeResponse({ body: { data: [makeCycle()], next_cursor: null } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const user = userEvent.setup();
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    await user.click(await screen.findByTestId('new-cycle-button'));
    await user.type(screen.getByTestId('cycle-name-input'), 'Doomed');
    fireEvent.change(screen.getByTestId('cycle-starts-input'), { target: { value: '2026-08-01' } });
    fireEvent.change(screen.getByTestId('cycle-ends-input'), { target: { value: '2026-08-14' } });
    await user.click(screen.getByTestId('create-cycle-submit'));
    expect(await screen.findByTestId('create-cycle-error')).toBeDefined();
  });

  it('shows a danger toast when a state transition fails', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'PATCH' && url.includes('/cycles/')) {
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
      }
      if (method === 'GET' && url.includes('/cycles')) {
        return fakeResponse({ body: { data: [makeCycle()], next_cursor: null } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    const user = userEvent.setup();
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    await user.click(await screen.findByTestId('cycle-activate-cyc-1'));
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeDefined();
  });

  it('renders the no-workspace empty state and the auto-roll tag', async () => {
    // 无工作区
    const implNoWs = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) {
        return fakeResponse({ body: { data: { ...ME, memberships: [] } } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nf' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', implNoWs);
    const { unmount } = renderWithProviders(<CyclesPage />, { route: '/cycles' });
    expect(
      await screen.findByText('You are not a member of any workspace yet.'),
    ).toBeDefined();
    unmount();
    // auto_roll 标签
    stubFetch({ cycles: [makeCycle({ auto_roll: true })] });
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    expect(await screen.findByText(/auto-roll/)).toBeDefined();
  });

  it('renders the error state when the cycle list fails to load', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderWithProviders(<CyclesPage />, { route: '/cycles' });
    expect(await screen.findByText('Something went wrong')).toBeDefined();
  });
});
