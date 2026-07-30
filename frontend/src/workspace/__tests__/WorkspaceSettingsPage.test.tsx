/**
 * 工作区设置(SettingsLayout 二级导航 + 子路由分页):
 * - 门控:member 直达无权限态;owner/admin 进入设置外壳;
 * - 二级导航分组 + 危险区仅 owner 可见(权限不可见,hidden);
 * - 索引重定向 → general;子路由切换;
 * - G11 默认主题字段 + hint + admin 门控 + PATCH {settings:{default_theme}};
 * - 基本信息 dirty/save(仅含变更键、logo https 拦截、422 具名、slug 重定向、pristine 禁用)。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Navigate, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { WorkspaceProvider } from '../WorkspaceProvider';
import { WorkspaceSettingsPage } from '../pages/WorkspaceSettingsPage';
import { WorkspaceAuditSection } from '../pages/settings/WorkspaceAuditSection';
import { WorkspaceCustomFieldsSection } from '../pages/settings/WorkspaceCustomFieldsSection';
import { WorkspaceDangerSection } from '../pages/settings/WorkspaceDangerSection';
import { WorkspaceDataSection } from '../pages/settings/WorkspaceDataSection';
import { WorkspaceGeneralSection } from '../pages/settings/WorkspaceGeneralSection';
import { WorkspaceInvitationsSection } from '../pages/settings/WorkspaceInvitationsSection';
import { WorkspaceLabelsSection } from '../pages/settings/WorkspaceLabelsSection';
import { WorkspaceRolesSection } from '../pages/settings/WorkspaceRolesSection';
import { WorkspaceTokensSection } from '../pages/settings/WorkspaceTokensSection';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubClient(fetchImpl: ReturnType<typeof vi.fn>) {
  return {
    list: async (path: string) => {
      const response = await fetchImpl(`http://localhost${path}`, { method: 'GET' });
      if (!response.ok) {
        const { MeshApiError } = await import('../../api/errors');
        throw new MeshApiError({ status: response.status, code: 'not_found', message: 'x' });
      }
      return response.json();
    },
    request: async (method: string, path: string, opts: { body?: unknown } = {}) => {
      const response = await fetchImpl(`http://localhost${path}`, {
        method,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
      const body = (await response.json()) as {
        data?: unknown;
        error?: { code: string; message: string; details?: Record<string, unknown> };
      };
      if (!response.ok) {
        const { MeshApiError } = await import('../../api/errors');
        throw new MeshApiError({
          status: response.status,
          code: body.error?.code ?? 'internal_error',
          message: body.error?.message ?? '',
          details: body.error?.details,
        });
      }
      return body.data;
    },
  };
}

function stubFetch(...responses: Array<{ status: number; body: unknown }>): ReturnType<typeof vi.fn> {
  const fetchImpl = vi.fn();
  for (const response of responses) {
    fetchImpl.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(response.status, response.body)),
    );
  }
  return fetchImpl;
}

const DETAIL = {
  id: 'ws-1',
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: {
    default_locale: 'en',
    default_theme: 'system',
    invitation_max_uses_cap: 100,
  },
  my_role: 'owner',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

interface RenderOptions {
  role?: 'owner' | 'admin' | 'member';
  route?: string;
  /** 邀请路由替换为简单桩(脏导航守卫测试避免触发真实列表请求) */
  stubInvitations?: boolean;
  /** 覆盖工作区 detail 初值(如带 logo 以覆盖清空分支) */
  detail?: typeof DETAIL;
}

function renderSettings(
  fetchImpl: ReturnType<typeof vi.fn>,
  opts: RenderOptions = {},
): ReturnType<typeof render> {
  const role = opts.role ?? 'owner';
  const route = opts.route ?? '/w/acme/settings/general';
  let current = { ...DETAIL, my_role: role };
  const wrapper = vi.fn(async (...args: [string, { method?: string; body?: string }]) => {
    const [url, init] = args;
    if (url.includes('/by-slug/')) {
      return jsonResponse(200, { data: current });
    }
    if (url.includes('/members')) {
      return jsonResponse(404, { error: { code: 'not_found', message: 'x' } });
    }
    if (init.method === 'PATCH' && typeof init.body === 'string') {
      current = { ...current, ...(JSON.parse(init.body) as Record<string, unknown>) };
    }
    return fetchImpl(...args);
  });

  const invitations = opts.stubInvitations ? (
    <div data-testid="invitations-stub" />
  ) : (
    <WorkspaceInvitationsSection />
  );

  const tree = (): React.JSX.Element => (
    <MemoryRouter initialEntries={[route]}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            <WorkspaceProvider slug="acme" client={stubClient(wrapper) as never}>
              <Routes>
                <Route path="/w/:workspaceSlug/settings" element={<WorkspaceSettingsPage />}>
                  <Route index element={<Navigate to="general" replace />} />
                  <Route path="general" element={<WorkspaceGeneralSection />} />
                  <Route path="invitations" element={invitations} />
                  <Route path="roles" element={<WorkspaceRolesSection />} />
                  <Route path="labels" element={<WorkspaceLabelsSection />} />
                  <Route path="custom-fields" element={<WorkspaceCustomFieldsSection />} />
                  <Route path="data" element={<WorkspaceDataSection />} />
                  <Route path="tokens" element={<WorkspaceTokensSection />} />
                  <Route path="audit" element={<WorkspaceAuditSection />} />
                  <Route path="danger" element={<WorkspaceDangerSection />} />
                </Route>
                <Route path="*" element={<span data-testid="navigated" />} />
              </Routes>
            </WorkspaceProvider>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>
  );
  return render(tree());
}

describe('工作区设置门控与导航(§4.1/§3.2)', () => {
  it('member 直达 → 无权限态(异常态矩阵)', async () => {
    renderSettings(stubFetch(), { role: 'member' });
    await waitFor(() => expect(screen.getByTestId('ws-settings-denied')).toBeTruthy());
  });

  it('索引路由重定向到 general(基本信息可见)', async () => {
    renderSettings(stubFetch(), { route: '/w/acme/settings' });
    await waitFor(() => expect(screen.getByTestId('ws-basic-info')).toBeTruthy());
    expect(screen.getByTestId('settings-nav-general').className).toContain('is-active');
  });

  it('owner → 二级导航含全部项(含危险区)', async () => {
    renderSettings(stubFetch(), { role: 'owner' });
    await waitFor(() => expect(screen.getByTestId('ws-settings')).toBeTruthy());
    for (const key of [
      'general',
      'invitations',
      'roles',
      'labels',
      'custom-fields',
      'data',
      'tokens',
      'audit',
      'danger',
    ]) {
      expect(screen.getByTestId(`settings-nav-${key}`)).toBeTruthy();
    }
  });

  it('admin → 危险区导航不可见(权限不可见,非禁用)', async () => {
    renderSettings(stubFetch(), { role: 'admin' });
    await waitFor(() => expect(screen.getByTestId('ws-settings')).toBeTruthy());
    expect(screen.getByTestId('settings-nav-general')).toBeTruthy();
    expect(screen.queryByTestId('settings-nav-danger')).toBeNull();
  });

  it('子路由切换:invitations 呈现邀请区、general 内容卸载', async () => {
    renderSettings(stubFetch(), { route: '/w/acme/settings/invitations', stubInvitations: true });
    await waitFor(() => expect(screen.getByTestId('invitations-stub')).toBeTruthy());
    expect(screen.queryByTestId('ws-basic-info')).toBeNull();
    expect(screen.getByTestId('settings-nav-invitations').className).toContain('is-active');
  });
});

describe('G11 工作区默认主题入口(theme.md §4.1)', () => {
  it('admin 可见默认主题字段 + hint「成员未单独设置时生效」', async () => {
    renderSettings(stubFetch(), { role: 'admin' });
    await waitFor(() => expect(screen.getByTestId('ws-default-theme-select')).toBeTruthy());
    expect(screen.getByTestId('ws-default-theme-hint').textContent).toBe(
      'Applies to members who have not chosen their own theme.',
    );
  });

  it('改默认主题保存 → PATCH 载荷含 settings.default_theme', async () => {
    const user = userEvent.setup();
    const api = stubFetch({ status: 200, body: { data: { ...DETAIL, settings: { ...DETAIL.settings, default_theme: 'dark' } } } });
    renderSettings(api, { role: 'admin' });
    await waitFor(() => expect(screen.getByTestId('ws-default-theme-select')).toBeTruthy());

    await user.selectOptions(screen.getByTestId('ws-default-theme-select'), 'dark');
    await user.click(screen.getByTestId('ws-save'));

    await waitFor(() => {
      const [, init] = api.mock.calls[0] as [string, { method: string; body: string }];
      expect(init.method).toBe('PATCH');
      expect(JSON.parse(init.body)).toEqual({ settings: { default_theme: 'dark' } });
    });
  });

  it('member 无默认主题字段(整体设置页被门控)', async () => {
    renderSettings(stubFetch(), { role: 'member' });
    await waitFor(() => expect(screen.getByTestId('ws-settings-denied')).toBeTruthy());
    expect(screen.queryByTestId('ws-default-theme-select')).toBeNull();
  });
});

describe('基本信息 dirty/save(§4.2)', () => {
  it('改名保存 → PATCH 仅含变更键', async () => {
    const user = userEvent.setup();
    const updated = { ...DETAIL, name: 'Acme2' };
    const api = stubFetch({ status: 200, body: { data: updated } });
    renderSettings(api);
    await waitFor(() => expect(screen.getByTestId('ws-name-input')).toBeTruthy());

    const nameInput = screen.getByTestId('ws-name-input') as HTMLInputElement;
    await user.clear(nameInput);
    await user.type(nameInput, 'Acme2');
    await user.click(screen.getByTestId('ws-save'));

    await waitFor(() => {
      const [, init] = api.mock.calls[0] as [string, { method: string; body: string }];
      expect(JSON.parse(init.body)).toEqual({ name: 'Acme2' });
    });
  });

  it('logo 非 https → 客户端即时拦截(§6.16)', async () => {
    const user = userEvent.setup();
    const api = stubFetch();
    renderSettings(api);
    await waitFor(() => screen.getByTestId('ws-logo-input'));

    await user.type(screen.getByTestId('ws-logo-input'), 'http://evil.example/x.png');
    await user.click(screen.getByTestId('ws-save'));

    expect(screen.getByTestId('ws-basic-error').textContent).toBe('Logo URL must start with https://');
    expect(api).not.toHaveBeenCalled();
  });

  it('422 unsupported_locale → 受支持清单呈现', async () => {
    const user = userEvent.setup();
    const api = stubFetch({
      status: 422,
      body: { error: { code: 'unsupported_locale', message: 'x', details: { supported: ['zh-CN', 'en'] } } },
    });
    renderSettings(api);
    await waitFor(() => screen.getByTestId('ws-timezone-select'));

    await user.selectOptions(screen.getByTestId('ws-timezone-select'), 'Asia/Shanghai');
    await user.click(screen.getByTestId('ws-save'));

    await waitFor(() =>
      expect(screen.getByTestId('ws-basic-error').textContent).toBe(
        'Unsupported locale. Supported: zh-CN, en',
      ),
    );
  });

  it('slug 变更保存成功 → 重定向提示', async () => {
    const user = userEvent.setup();
    const updated = { ...DETAIL, slug: 'acme-corp' };
    const api = stubFetch({ status: 200, body: { data: updated } });
    renderSettings(api);
    await waitFor(() => screen.getByTestId('ws-slug-input'));

    const slugInput = screen.getByTestId('ws-slug-input') as HTMLInputElement;
    await user.clear(slugInput);
    await user.type(slugInput, 'acme-corp');
    await user.click(screen.getByTestId('ws-save'));

    await waitFor(() => expect(screen.getByText('Slug changed — old links will redirect.')).toBeTruthy());
  });

  it('无变更时保存按钮禁用', async () => {
    renderSettings(stubFetch());
    await waitFor(() => screen.getByTestId('ws-save'));
    expect((screen.getByTestId('ws-save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('保存返回 slug_taken → 具名错误态', async () => {
    const user = userEvent.setup();
    const api = stubFetch({ status: 409, body: { error: { code: 'slug_taken', message: 'taken' } } });
    renderSettings(api);
    await waitFor(() => expect(screen.getByTestId('ws-name-input')).toBeTruthy());
    const nameInput = screen.getByTestId('ws-name-input') as HTMLInputElement;
    await user.clear(nameInput);
    await user.type(nameInput, 'Acme4');
    await user.click(screen.getByTestId('ws-save'));
    await waitFor(() => expect(screen.getByTestId('ws-basic-error')).toBeTruthy());
  });

  it('保存返回 invalid_timezone → 具名错误态', async () => {
    const user = userEvent.setup();
    const api = stubFetch({ status: 422, body: { error: { code: 'invalid_timezone', message: 'bad tz' } } });
    renderSettings(api);
    await waitFor(() => expect(screen.getByTestId('ws-timezone-select')).toBeTruthy());
    await user.selectOptions(screen.getByTestId('ws-timezone-select'), 'Asia/Shanghai');
    await user.click(screen.getByTestId('ws-save'));
    await waitFor(() =>
      expect(screen.getByTestId('ws-basic-error').textContent).toBe('Invalid IANA timezone.'),
    );
  });

  it('改时区保存 → PATCH 含 timezone;清空 logo → logo_url=null', async () => {
    const user = userEvent.setup();
    const detailWithLogo = { ...DETAIL, logo_url: 'https://cdn.example/x.png' };
    let current = { ...detailWithLogo };
    const api = vi.fn(async (...args: [string, { method?: string; body?: string }]) => {
      const [url, init] = args;
      if (url.includes('/by-slug/')) return jsonResponse(200, { data: current });
      if (url.includes('/members')) return jsonResponse(404, { error: { code: 'not_found', message: 'x' } });
      if (init.method === 'PATCH' && typeof init.body === 'string') {
        current = { ...current, ...(JSON.parse(init.body) as Record<string, unknown>) };
      }
      return jsonResponse(200, { data: current });
    });
    renderSettings(api);
    await waitFor(() => expect(screen.getByTestId('ws-logo-input')).toBeTruthy());

    // 清空 logo(覆盖 logo_url=null 分支)
    await user.clear(screen.getByTestId('ws-logo-input'));
    await user.click(screen.getByTestId('ws-save'));
    await waitFor(() => {
      const [, init] = api.mock.calls[0] as [string, { method: string; body: string }];
      expect(JSON.parse(init.body)).toEqual({ logo_url: null });
    });
  });
});

describe('危险区分页(owner,§5.3)', () => {
  it('owner 直达 /danger → 危险区可见', async () => {
    renderSettings(stubFetch(), { role: 'owner', route: '/w/acme/settings/danger' });
    await waitFor(() => expect(screen.getByTestId('danger-zone')).toBeTruthy());
  });

  it('admin 深链 /danger → 无权限态(导航已隐藏)', async () => {
    renderSettings(stubFetch(), { role: 'admin', route: '/w/acme/settings/danger' });
    await waitFor(() => expect(screen.getByTestId('ws-danger-denied')).toBeTruthy());
    expect(screen.queryByTestId('danger-zone')).toBeNull();
  });
});
