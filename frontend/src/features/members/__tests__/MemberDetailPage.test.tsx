/**
 * 成员详情页(§3.4 规范深链 /w/{ws}/members/{member_id}):
 * 人类 → 资料卡;agent 名册行 → 别名路由 /w/{ws}/agents/{agent_id}(Agent 入口去重)。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { resetApiClient } from '../../../api/instance';
import { ToastProvider } from '../../../design';
import { I18nProvider } from '../../../i18n';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
import { MemberDetailPage } from '../MemberDetailPage';

const DETAIL = {
  id: 'ws-1',
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'en' },
  my_role: 'member',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

function AgentLanding(): React.JSX.Element {
  const { agentId } = useParams<{ agentId: string }>();
  return <span data-testid="agent-landing">{agentId}</span>;
}

function renderAt(memberId: string, member: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/workspaces/by-slug/')) {
        return fakeResponse({ body: { data: DETAIL } });
      }
      if (url.includes(`/members/${memberId}`)) {
        return fakeResponse({ body: { data: member } });
      }
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
    }) as unknown as typeof fetch,
  );
  resetApiClient();
  render(
    <I18nProvider requested={null} systemLocales={[]}>
      <ToastProvider regionLabel="notifications">
      <MemoryRouter initialEntries={[`/w/acme/members/${memberId}`]}>
        <Routes>
          <Route
            path="/w/:workspaceSlug/members/:memberId"
            element={
              <WorkspaceProvider slug="acme">
                <MemberDetailPage />
              </WorkspaceProvider>
            }
          />
          <Route path="/w/:workspaceSlug/agents/:agentId" element={<AgentLanding />} />
        </Routes>
      </MemoryRouter>
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  resetApiClient();
});

describe('MemberDetailPage', () => {
  it('人类成员 → 资料卡(名称/邮箱/角色/状态/在办 issue 数)', async () => {
    renderAt('m-1', {
      id: 'm-1',
      member_type: 'human',
      role: 'admin',
      status: 'active',
      display_name: 'Zhang San',
      joined_at: '2026-01-01T00:00:00Z',
      profile: {
        id: 'u-1',
        full_name: 'Zhang San',
        email: 'zhang@example.com',
        avatar_url: null,
      },
      display_override: null,
      disabled_at: null,
      counts: { open_issues_assigned: 3 },
    });
    await waitFor(() => expect(screen.getByTestId('member-detail')).toBeInTheDocument());
    expect(screen.getByTestId('member-detail-name').textContent).toBe('Zhang San');
    expect(screen.getByTestId('member-detail-email').textContent).toBe('zhang@example.com');
    expect(screen.getByTestId('member-detail-role').textContent).toBe('Admin');
    expect(screen.getByTestId('member-detail-status').textContent).toBe('Active');
    expect(screen.getByTestId('member-detail-open-issues').textContent).toBe('3');
    expect(screen.getByTestId('member-detail-joined')).toBeInTheDocument();
  });

  it('agent 名册行 → 别名路由 /w/{slug}/agents/{agent_id}(同页 agent 详情)', async () => {
    renderAt('m-2', {
      id: 'm-2',
      member_type: 'agent',
      role: 'member',
      status: 'active',
      display_name: 'Code Helper',
      joined_at: null,
      profile: {
        id: 'ag-9',
        name: 'Code Helper',
        description: null,
        avatar_url: null,
        is_active: true,
      },
      display_override: null,
      disabled_at: null,
      counts: { open_issues_assigned: 0 },
    });
    await waitFor(() => expect(screen.getByTestId('agent-landing')).toBeInTheDocument());
    expect(screen.getByTestId('agent-landing').textContent).toBe('ag-9');
  });

  it('profile 缺失/加入时间为空 → 兜底呈现(邮箱占位,不渲染 joined)', async () => {
    renderAt('m-3', {
      id: 'm-3',
      member_type: 'human',
      role: 'guest',
      status: 'disabled',
      display_name: 'No Profile',
      joined_at: null,
      profile: null,
      display_override: null,
      disabled_at: '2026-02-01T00:00:00Z',
      counts: { open_issues_assigned: 0 },
    });
    await waitFor(() => expect(screen.getByTestId('member-detail')).toBeInTheDocument());
    expect(screen.getByTestId('member-detail-email').textContent).toBe('—');
    expect(screen.queryByTestId('member-detail-joined')).not.toBeInTheDocument();
  });

  it('错误态点击重试 → 重新加载成功', async () => {
    let failOnce = true;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/workspaces/by-slug/')) {
          return fakeResponse({ body: { data: DETAIL } });
        }
        if (url.includes('/members/m-r')) {
          if (failOnce) {
            failOnce = false;
            return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
          }
          return fakeResponse({
            body: {
              data: {
                id: 'm-r',
                member_type: 'human',
                role: 'member',
                status: 'active',
                display_name: 'Retried',
                joined_at: null,
                profile: { id: 'u-r', full_name: 'Retried', email: 'r@example.com', avatar_url: null },
                display_override: null,
                disabled_at: null,
                counts: { open_issues_assigned: 1 },
              },
            },
          });
        }
        return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
      }) as unknown as typeof fetch,
    );
    resetApiClient();
    render(
      <I18nProvider requested={null} systemLocales={[]}>
        <ToastProvider regionLabel="notifications">
          <MemoryRouter initialEntries={['/w/acme/members/m-r']}>
            <Routes>
              <Route
                path="/w/:workspaceSlug/members/:memberId"
                element={
                  <WorkspaceProvider slug="acme">
                    <MemberDetailPage />
                  </WorkspaceProvider>
                }
              />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('member-detail-error')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByTestId('member-detail-name').textContent).toBe('Retried'));
  });

  it('工作区不存在/非成员 → not_found 异常态(§6.12,不泄漏存在性)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } }),
      ) as unknown as typeof fetch,
    );
    resetApiClient();
    render(
      <I18nProvider requested={null} systemLocales={[]}>
        <ToastProvider regionLabel="notifications">
          <MemoryRouter initialEntries={['/w/ghost/members/m-1']}>
            <Routes>
              <Route
                path="/w/:workspaceSlug/members/:memberId"
                element={
                  <WorkspaceProvider slug="ghost">
                    <MemberDetailPage />
                  </WorkspaceProvider>
                }
              />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('member-detail-not-found')).toBeInTheDocument());
  });

  it('工作区端点失败 → 错误态,重试后恢复', async () => {
    let bySlugFailOnce = true;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/workspaces/by-slug/')) {
          if (bySlugFailOnce) {
            bySlugFailOnce = false;
            return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
          }
          return fakeResponse({ body: { data: DETAIL } });
        }
        if (url.includes('/members/m-w')) {
          return fakeResponse({
            body: {
              data: {
                id: 'm-w',
                member_type: 'human',
                role: 'member',
                status: 'active',
                display_name: 'Ws Recovered',
                joined_at: null,
                profile: { id: 'u-w', full_name: 'W', email: 'w@example.com', avatar_url: null },
                display_override: null,
                disabled_at: null,
                counts: { open_issues_assigned: 0 },
              },
            },
          });
        }
        return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
      }) as unknown as typeof fetch,
    );
    resetApiClient();
    render(
      <I18nProvider requested={null} systemLocales={[]}>
        <ToastProvider regionLabel="notifications">
          <MemoryRouter initialEntries={['/w/acme/members/m-w']}>
            <Routes>
              <Route
                path="/w/:workspaceSlug/members/:memberId"
                element={
                  <WorkspaceProvider slug="acme">
                    <MemberDetailPage />
                  </WorkspaceProvider>
                }
              />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('member-detail-ws-error')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() =>
      expect(screen.getByTestId('member-detail-name').textContent).toBe('Ws Recovered'),
    );
  });

  it('agent 名册行但 profile 缺失 → 回落资料卡呈现(无别名路由可解析)', async () => {
    renderAt('m-4', {
      id: 'm-4',
      member_type: 'agent',
      role: 'member',
      status: 'active',
      display_name: 'Ghost Agent',
      joined_at: null,
      profile: null,
      display_override: null,
      disabled_at: null,
      counts: { open_issues_assigned: 0 },
    });
    await waitFor(() => expect(screen.getByTestId('member-detail')).toBeInTheDocument());
    expect(screen.queryByTestId('agent-landing')).not.toBeInTheDocument();
  });

  it('成员端点失败 → 错误态(可重试)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/workspaces/by-slug/')) {
          return fakeResponse({ body: { data: DETAIL } });
        }
        return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
      }) as unknown as typeof fetch,
    );
    resetApiClient();
    render(
      <I18nProvider requested={null} systemLocales={[]}>
        <ToastProvider regionLabel="notifications">
      <MemoryRouter initialEntries={['/w/acme/members/m-x']}>
          <Routes>
            <Route
              path="/w/:workspaceSlug/members/:memberId"
              element={
                <WorkspaceProvider slug="acme">
                  <MemberDetailPage />
                </WorkspaceProvider>
              }
            />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('member-detail-error')).toBeInTheDocument());
  });
});
