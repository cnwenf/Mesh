import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { WorkspaceProvider } from '../WorkspaceProvider';
import { WorkspaceSettingsPage } from '../pages/WorkspaceSettingsPage';

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
  settings: { default_locale: 'en', invitation_max_uses_cap: 100 },
  my_role: 'owner',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

function renderSettings(
  fetchImpl: ReturnType<typeof vi.fn>,
  role: 'owner' | 'admin' | 'member' = 'owner',
): ReturnType<typeof render> {
  // 有状态桩:PATCH 合并进当前 detail,by-slug 返回现行值(模拟服务端真源)。
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

  const tree = (): React.JSX.Element => (
    <MemoryRouter initialEntries={['/w/acme/settings']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            <WorkspaceProvider slug="acme" client={stubClient(wrapper) as never}>
              <Routes>
                <Route path="/w/:workspaceSlug/settings" element={<WorkspaceSettingsPage />} />
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

describe('WorkspaceSettingsPage(设置页门控与基本信息,§4.1/§4.2)', () => {
  it('member 直达 → 无权限态(§6.12 异常态矩阵)', async () => {
    renderSettings(stubFetch(), 'member');
    await waitFor(() => expect(screen.getByTestId('ws-settings-denied')).toBeTruthy());
  });

  it('owner → 全部节区可见(含危险区)', async () => {
    renderSettings(stubFetch(), 'owner');
    await waitFor(() => expect(screen.getByTestId('ws-settings')).toBeTruthy());
    expect(screen.getByTestId('ws-basic-info')).toBeTruthy();
    expect(screen.getByTestId('invitation-create')).toBeTruthy();
    expect(screen.getByTestId('roles-section')).toBeTruthy();
    expect(screen.getByTestId('danger-zone')).toBeTruthy();
  });

  it('admin → 无危险区', async () => {
    renderSettings(stubFetch(), 'admin');
    await waitFor(() => expect(screen.getByTestId('ws-settings')).toBeTruthy());
    expect(screen.queryByTestId('danger-zone')).toBeNull();
  });

  it('改名保存 → PATCH 仅含变更键 + 成功提示', async () => {
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
      expect(init.method).toBe('PATCH');
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

    expect(screen.getByTestId('ws-basic-error').textContent).toBe(
      'Logo URL must start with https://',
    );
    expect(api).not.toHaveBeenCalled();
  });

  it('422 unsupported_locale → 受支持清单呈现', async () => {
    const user = userEvent.setup();
    const api = stubFetch({
      status: 422,
      body: {
        error: {
          code: 'unsupported_locale',
          message: 'x',
          details: { locale: 'fr', supported: ['zh-CN', 'en'] },
        },
      },
    });
    renderSettings(api);
    await waitFor(() => screen.getByTestId('ws-locale-select'));

    // 改时区制造 dirty 并触发 PATCH(返回 422)
    const tz = screen.getByTestId('ws-timezone-select') as HTMLSelectElement;
    await user.selectOptions(tz, 'Asia/Shanghai');
    await user.click(screen.getByTestId('ws-save'));

    await waitFor(() =>
      expect(screen.getByTestId('ws-basic-error').textContent).toBe(
        'Unsupported locale. Supported: zh-CN, en',
      ),
    );
  });

  it('slug 变更保存成功 → 重定向提示并规范化导航至新 slug', async () => {
    const user = userEvent.setup();
    const updated = { ...DETAIL, slug: 'acme-corp' };
    const api = stubFetch({ status: 200, body: { data: updated } });
    renderSettings(api);
    await waitFor(() => screen.getByTestId('ws-slug-input'));

    const slugInput = screen.getByTestId('ws-slug-input') as HTMLInputElement;
    await user.clear(slugInput);
    await user.type(slugInput, 'acme-corp');
    await user.click(screen.getByTestId('ws-save'));

    // 旧链接重定向提示(W6);设置页在新 slug 路由下仍在(未跳出)
    await waitFor(() =>
      expect(screen.getByText('Slug changed — old links will redirect.')).toBeTruthy(),
    );
    expect(screen.getByTestId('ws-settings')).toBeTruthy();
  });

  it('无变更时保存按钮禁用', async () => {
    renderSettings(stubFetch());
    await waitFor(() => screen.getByTestId('ws-save'));
    expect((screen.getByTestId('ws-save') as HTMLButtonElement).disabled).toBe(true);
  });
});
