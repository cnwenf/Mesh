/**
 * 旧扁平路由 → 规范深链迁移(§3.4):映射表逐条 + FlatRouteMigration 的
 * replace navigation(query/hash 保留、active workspace 解析序、多工作区 →
 * 选择页 ?next= 保留意图、非旧路由 → not-found)。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import { resetApiClient } from '../../api/instance';
import { I18nProvider } from '../../i18n';
import { renderWithProviders } from '../../test-utils/render';
import { FlatRouteMigration, matchFlatRoute } from '../flatRoutes';
import { WorkspaceProvider } from '../WorkspaceProvider';

function LandingProbe(): React.JSX.Element {
  const location = useLocation();
  const { slug } = useParams<{ slug: string }>();
  return (
    <div>
      <span data-testid="landed-slug">{slug}</span>
      <span data-testid="landed-pathname">{location.pathname}</span>
      <span data-testid="landed-search">{location.search}</span>
      <span data-testid="landed-hash">{location.hash}</span>
    </div>
  );
}

function PickerProbe(): React.JSX.Element {
  const location = useLocation();
  return (
    <div>
      <span data-testid="picker-pathname">{location.pathname}</span>
      <span data-testid="picker-search">{location.search}</span>
    </div>
  );
}

function renderAt(initial: string): void {
  render(
    <I18nProvider requested={null} systemLocales={[]}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/w/:slug/*" element={<LandingProbe />} />
          <Route path="/workspace-picker" element={<PickerProbe />} />
          <Route path="*" element={<FlatRouteMigration />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

function WorkspaceNotFoundHarness(): React.JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  if (slug === undefined) throw new Error('workspace slug is required');
  return (
    <WorkspaceProvider slug={slug}>
      <FlatRouteMigration />
    </WorkspaceProvider>
  );
}

interface MeStubOptions {
  readonly userId?: string;
  readonly memberships: ReadonlyArray<{ workspace_id: string; workspace_slug: string }>;
  readonly lastActiveWorkspaceId?: string | null;
}

function stubMe(options: MeStubOptions): void {
  const userId = options.userId ?? 'u1';
  const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/users/me')) {
      return fakeResponse({
        body: {
          data: {
            user: {
              id: userId,
              email: 'u@example.com',
              display_name: 'U',
              last_active_workspace_id: options.lastActiveWorkspaceId ?? null,
            },
            memberships: options.memberships.map((membership) => ({
              workspace_id: membership.workspace_id,
              workspace_name: membership.workspace_slug.toUpperCase(),
              workspace_slug: membership.workspace_slug,
              role: 'member',
              status: 'active',
              joined_at: null,
            })),
          },
        },
      });
    }
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  });
  vi.stubGlobal('fetch', fetchImpl as unknown as typeof fetch);
  resetApiClient();
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetApiClient();
});

describe('matchFlatRoute(§3.4 旧→新映射表逐条)', () => {
  const cases: ReadonlyArray<[string, string]> = [
    ['/inbox', '/w/acme/inbox'],
    ['/inbox/n-1', '/w/acme/inbox/n-1'],
    ['/board', '/w/acme/board'],
    ['/views/v-1', '/w/acme/views/v-1'],
    ['/members', '/w/acme/members'],
    ['/members/m-1', '/w/acme/members/m-1'],
    ['/projects', '/w/acme/projects'],
    ['/projects/p-1', '/w/acme/projects/p-1'],
    ['/projects/p-1/settings', '/w/acme/projects/p-1/settings'],
    ['/issues', '/w/acme/issues'],
    ['/issues/by-identifier/WEB-124', '/w/acme/issues/by-identifier/WEB-124'],
    ['/issues/0c2f', '/w/acme/issues/0c2f'],
    ['/chat', '/w/acme/chat'],
    ['/chat/s-1', '/w/acme/chat/s-1'],
    ['/squads', '/w/acme/squads'],
    ['/squads/q-1', '/w/acme/squads/q-1'],
    ['/squads/q-1/tasks/t-9', '/w/acme/squads/q-1/tasks/t-9'],
    ['/cycles', '/w/acme/cycles'],
    ['/executions/e-1', '/w/acme/executions/e-1'],
    ['/insights', '/w/acme/insights'],
    ['/agents/a-1', '/w/acme/agents/a-1'],
    ['/automation', '/w/acme/automations/autopilots'],
    ['/autopilots', '/w/acme/automations/autopilots'],
    ['/autopilots/ap-1/edit', '/w/acme/automations/autopilots/ap-1/edit'],
    ['/runtimes', '/w/acme/automations/runtimes'],
    ['/runtimes/r-1', '/w/acme/automations/runtimes/r-1'],
    ['/webhooks', '/w/acme/automations/webhooks'],
    ['/skills', '/w/acme/automations/skills'],
    ['/skills/marketplace', '/w/acme/automations/skills/marketplace'],
    ['/skills/sk-1', '/w/acme/automations/skills/sk-1'],
    ['/automations/skills', '/w/acme/automations/skills'],
    ['/integrations', '/w/acme/automations/integrations'],
    ['/integrations/ig-1', '/w/acme/automations/integrations/ig-1'],
    ['/webhook-subscriptions', '/w/acme/automations/webhook-subscriptions'],
    ['/settings/labels', '/w/acme/settings/labels'],
    ['/settings/members', '/w/acme/settings/members'],
    ['/settings/approvals', '/w/acme/settings/approvals'],
    ['/settings/fields', '/w/acme/settings/fields'],
    ['/settings/danger', '/w/acme/settings/danger'],
  ];

  it.each(cases)('%s → %s', (flat, canonical) => {
    const build = matchFlatRoute(flat);
    expect(build).not.toBeNull();
    expect(build?.('acme')).toBe(canonical);
  });

  it('账号设置 /settings(裸路径)与未知路径不迁移', () => {
    expect(matchFlatRoute('/settings')).toBeNull();
    expect(matchFlatRoute('/not-found')).toBeNull();
    expect(matchFlatRoute('/login')).toBeNull();
    expect(matchFlatRoute('/w/acme/board')).toBeNull();
  });
});

describe('FlatRouteMigration(路由器 replace navigation,§3.4 执行层)', () => {
  it('单一归属:旧书签 /board 刷新 → /w/{slug}/board,query 与 hash 保留', async () => {
    stubMe({ memberships: [{ workspace_id: 'ws-a', workspace_slug: 'alpha' }] });
    renderAt('/board?view=x#card-1');
    await waitFor(() =>
      expect(screen.getByTestId('landed-pathname').textContent).toBe('/w/alpha/board'),
    );
    expect(screen.getByTestId('landed-search').textContent).toBe('?view=x');
    expect(screen.getByTestId('landed-hash').textContent).toBe('#card-1');
    // active workspace 记忆写入(解析成功后记录)。
    expect(window.localStorage.getItem(`mesh.last_workspace:${window.location.host}:u1`)).toBe(
      'alpha',
    );
  });

  it('多工作区 + 本地记忆 → 记忆工作区的规范路由(解析序 ②)', async () => {
    window.localStorage.setItem(`mesh.last_workspace:${window.location.host}:u1`, 'beta');
    stubMe({
      memberships: [
        { workspace_id: 'ws-a', workspace_slug: 'alpha' },
        { workspace_id: 'ws-b', workspace_slug: 'beta' },
      ],
      lastActiveWorkspaceId: 'ws-a',
    });
    renderAt('/inbox');
    await waitFor(() =>
      expect(screen.getByTestId('landed-pathname').textContent).toBe('/w/beta/inbox'),
    );
  });

  it('多工作区 + 服务端提示 → 解析序 ③', async () => {
    stubMe({
      memberships: [
        { workspace_id: 'ws-a', workspace_slug: 'alpha' },
        { workspace_id: 'ws-b', workspace_slug: 'beta' },
      ],
      lastActiveWorkspaceId: 'ws-b',
    });
    renderAt('/members');
    await waitFor(() =>
      expect(screen.getByTestId('landed-pathname').textContent).toBe('/w/beta/members'),
    );
  });

  it('多工作区无上下文 → 工作区选择页,?next= 保留意图路径', async () => {
    stubMe({
      memberships: [
        { workspace_id: 'ws-a', workspace_slug: 'alpha' },
        { workspace_id: 'ws-b', workspace_slug: 'beta' },
      ],
    });
    renderAt('/chat/s-9?tab=x');
    await waitFor(() =>
      expect(screen.getByTestId('picker-pathname').textContent).toBe('/workspace-picker'),
    );
    const next = new URLSearchParams(screen.getByTestId('picker-search').textContent ?? '').get(
      'next',
    );
    expect(next).toBe('/chat/s-9?tab=x');
  });

  it('非旧路由路径 → not-found 呈现', async () => {
    stubMe({ memberships: [{ workspace_id: 'ws-a', workspace_slug: 'alpha' }] });
    renderAt('/definitely-unknown');
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument());
    expect(screen.getByTestId('notfound-page').tagName).toBe('SECTION');
  });

  it('工作区未知路径 → 嵌入 shell 的 not-found 提供当前工作区恢复出口', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes('/workspaces/by-slug/alpha')) {
          return fakeResponse({
            body: {
              data: {
                id: 'ws-a',
                name: 'Alpha',
                slug: 'alpha',
                logo_url: null,
                timezone: 'UTC',
                settings: {},
                my_role: 'member',
                created_at: '2026-08-04T00:00:00Z',
                updated_at: '2026-08-04T00:00:00Z',
              },
            },
          });
        }
        return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
      }) as unknown as typeof fetch,
    );
    resetApiClient();

    renderWithProviders(
      <Routes>
        <Route path="/w/:slug/*" element={<WorkspaceNotFoundHarness />} />
      </Routes>,
      { route: '/w/alpha/definitely-unknown' },
    );

    const workspaceLink = await screen.findByTestId('notfound-workspace');
    expect(workspaceLink).toHaveAttribute('href', '/w/alpha');
    expect(screen.getByTestId('notfound-page').tagName).toBe('SECTION');
  });
});
