import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api/client';
import type { CurrentUser } from '../../../api';
import { useAuthStore } from '../../../state/authStore';
import { SecuritySettings } from '../SecuritySettings';
import { renderWithProviders } from '../../../test-utils/render';

const USER: CurrentUser = {
  id: 'u-1',
  email: 'jane@corp.com',
  email_verified: true,
  display_name: 'Jane',
  avatar_url: null,
  status: 'active',
  timezone: 'UTC',
  settings: {},
  mfa_enabled: false,
  last_login_at: null,
  created_at: '2026-07-25T10:00:00Z',
};

/** 按 URL 路由的 fetch 桩:返回 {data} 包络。 */
function routingClient(routes: Record<string, { status?: number; body: unknown }>): MeshApiClient {
  const fetchImpl = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    const match = Object.keys(routes).find((key) => url.includes(key));
    const response = match !== undefined ? routes[match] : { status: 200, body: { data: [] } };
    return new Response(JSON.stringify(response.body), {
      status: response.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://t', getToken: () => 'tok', fetchImpl });
}

describe('SecuritySettings(auth.md §4.2)', () => {
  it('列出活跃会话并可撤销', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/sessions': {
        body: {
          data: [
            {
              id: 'ses-1',
              type: 'web',
              user_agent: 'Chrome',
              ip_address: '127.0.0.1',
              created_at: '2026-07-25T10:00:00Z',
              last_active_at: null,
              expires_at: '2026-08-25T10:00:00Z',
              current: true,
            },
          ],
          next_cursor: null,
        },
      },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await waitFor(() => expect(screen.getByTestId('session-ses-1')).toBeTruthy());
    expect(screen.getByText('Chrome')).toBeTruthy();
    // 撤销后呈现提示
    await user.click(screen.getByRole('button', { name: /Revoke/ }));
    await waitFor(() =>
      expect(screen.getByTestId('security-notice').textContent).toContain('revoked'),
    );
  });

  it('MFA 启用向导:setup 展示密钥/备用码,确认后启用', async () => {
    const user = userEvent.setup();
    const onUserChanged = vi.fn();
    const client = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/mfa/setup': {
        body: { data: { secret: 'SEC', otpauth_uri: 'otpauth://totp/x', backup_codes: ['c1'] } },
      },
      '/api/v1/auth/mfa/enable': { body: { data: { mfa_enabled: true } } },
    });
    renderWithProviders(
      <SecuritySettings client={client} user={USER} onUserChanged={onUserChanged} />,
    );

    await user.click(screen.getByTestId('mfa-enable'));
    await waitFor(() => expect(screen.getByTestId('mfa-secret').textContent).toBe('SEC'));
    expect(screen.getByTestId('mfa-backup-codes').textContent).toContain('c1');

    await user.type(screen.getByTestId('mfa-enable-code'), '123456');
    await user.click(screen.getByTestId('mfa-enable-confirm'));
    await waitFor(() => expect(onUserChanged).toHaveBeenCalled());
  });

  it('解绑第三方账号成功呈现提示', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': {
        body: {
          data: [
            { provider: 'mock', provider_email: 'j@x.com', created_at: '2026-07-25T10:00:00Z' },
            { provider: 'second', provider_email: 'j@y.com', created_at: '2026-07-25T10:00:00Z' },
          ],
          next_cursor: null,
        },
      },
      '/api/v1/auth/oauth/mock': { body: { data: { status: 'ok' } } },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await waitFor(() => expect(screen.getByTestId('oauth-mock')).toBeTruthy());
    await user.click(screen.getByTestId('oauth-unbind-mock'));
    await waitFor(() =>
      expect(screen.getByTestId('security-notice').textContent).toContain('unlinked'),
    );
  });

  it('删最后一种登录方式:前端灰化解绑,服务端兜底错误具名呈现', async () => {
    const user = userEvent.setup();
    // 唯一第三方身份 → 解绑按钮灰化(auth.md §4.2)
    const single = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': {
        body: {
          data: [{ provider: 'mock', provider_email: 'j@x.com', created_at: '2026-07-25T10:00:00Z' }],
          next_cursor: null,
        },
      },
    });
    const { unmount } = renderWithProviders(<SecuritySettings client={single} user={USER} />);
    await waitFor(() => expect(screen.getByTestId('oauth-mock')).toBeTruthy());
    expect((screen.getByTestId('oauth-unbind-mock') as HTMLButtonElement).disabled).toBe(true);
    unmount();

    // 绕过灰化(多身份场景)后服务端仍拒绝 → last_login_method 具名错误
    const client = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': {
        body: {
          data: [
            { provider: 'mock', provider_email: 'j@x.com', created_at: '2026-07-25T10:00:00Z' },
            { provider: 'second', provider_email: 'j@y.com', created_at: '2026-07-25T10:00:00Z' },
          ],
          next_cursor: null,
        },
      },
      '/api/v1/auth/oauth/mock': {
        status: 422,
        body: { error: { code: 'last_login_method', message: 'x' } },
      },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);
    await waitFor(() => expect(screen.getByTestId('oauth-mock')).toBeTruthy());
    await user.click(screen.getByTestId('oauth-unbind-mock'));
    await waitFor(() =>
      expect(screen.getByTestId('security-error').textContent).toContain('last sign-in method'),
    );
  });

  it('撤销会话失败 → 具名错误文案', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      // 更具体的路径在前,避免被通用 /api/v1/sessions 路由先匹配
      '/api/v1/sessions/ses-1': {
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      },
      '/api/v1/sessions': {
        body: {
          data: [
            {
              id: 'ses-1',
              type: 'web',
              user_agent: 'Chrome',
              ip_address: '127.0.0.1',
              created_at: '2026-07-25T10:00:00Z',
              last_active_at: null,
              expires_at: '2026-08-25T10:00:00Z',
              current: true,
            },
          ],
          next_cursor: null,
        },
      },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await waitFor(() => expect(screen.getByTestId('session-ses-1')).toBeTruthy());
    await user.click(screen.getByRole('button', { name: /Revoke/ }));
    await waitFor(() =>
      expect(screen.getByTestId('security-error').textContent).toContain('Something went wrong'),
    );
  });

  it('登出所有会话成功呈现提示;失败呈现错误', async () => {
    const user = userEvent.setup();
    const ok = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/logout-all': { body: { data: { revoked: 2 } } },
    });
    const { unmount } = renderWithProviders(<SecuritySettings client={ok} user={USER} />);
    await user.click(screen.getByTestId('logout-all'));
    await waitFor(() =>
      expect(screen.getByTestId('security-notice').textContent).toContain('revoked'),
    );
    unmount();

    const bad = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/logout-all': {
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      },
    });
    renderWithProviders(<SecuritySettings client={bad} user={USER} />);
    await user.click(screen.getByTestId('logout-all'));
    await waitFor(() =>
      expect(screen.getByTestId('security-error').textContent).toContain('Something went wrong'),
    );
  });

  it('MFA setup 失败 → 错误文案', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/mfa/setup': {
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await user.click(screen.getByTestId('mfa-enable'));
    await waitFor(() =>
      expect(screen.getByTestId('security-error').textContent).toContain('Something went wrong'),
    );
  });

  it('MFA 启用确认失败(验证码无效)→ 具名文案', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/mfa/setup': {
        body: { data: { secret: 'SEC', otpauth_uri: 'otpauth://totp/x', backup_codes: ['c1'] } },
      },
      '/api/v1/auth/mfa/enable': {
        status: 422,
        body: { error: { code: 'invalid_credentials', message: 'x' } },
      },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await user.click(screen.getByTestId('mfa-enable'));
    await waitFor(() => expect(screen.getByTestId('mfa-secret')).toBeTruthy());
    await user.type(screen.getByTestId('mfa-enable-code'), '000000');
    await user.click(screen.getByTestId('mfa-enable-confirm'));
    await waitFor(() =>
      expect(screen.getByTestId('security-error').textContent).toContain('not valid'),
    );
  });
});

describe('SecuritySettings MFA 停用流(auth.md §4.2)', () => {
  const MFA_USER: CurrentUser = { ...USER, mfa_enabled: true };

  it('已启用态:输入验证码确认后停用(成功提示 + 通知父级)', async () => {
    const user = userEvent.setup();
    const onUserChanged = vi.fn();
    const client = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/mfa/disable': { body: { data: {} } },
    });
    renderWithProviders(
      <SecuritySettings client={client} user={MFA_USER} onUserChanged={onUserChanged} />,
    );

    expect(screen.getByText('Two-factor authentication is enabled.')).toBeTruthy();
    await user.click(screen.getByTestId('mfa-disable'));
    await user.type(screen.getByTestId('mfa-disable-code'), '123456');
    await user.click(screen.getByTestId('mfa-disable-confirm'));
    await waitFor(() =>
      expect(screen.getByTestId('security-notice').textContent).toContain('disabled'),
    );
    expect(onUserChanged).toHaveBeenCalled();
  });

  it('停用失败(验证码无效)→ 具名文案', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/sessions': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
      '/api/v1/auth/mfa/disable': {
        status: 422,
        body: { error: { code: 'invalid_credentials', message: 'x' } },
      },
    });
    renderWithProviders(<SecuritySettings client={client} user={MFA_USER} />);

    await user.click(screen.getByTestId('mfa-disable'));
    await user.type(screen.getByTestId('mfa-disable-code'), '000000');
    await user.click(screen.getByTestId('mfa-disable-confirm'));
    await waitFor(() =>
      expect(screen.getByTestId('security-error').textContent).toContain('not valid'),
    );
  });
});

describe('SecuritySettings 修改密码表单(auth.md §4.2,MES-39)', () => {
  /** 记录每次请求的 URL 与 JSON body(用于断言提交载荷)。 */
  function capturingClient(
    capture: Array<{ url: string; body: unknown }>,
    routes: Record<string, { status?: number; body: unknown }>,
  ): MeshApiClient {
    const fetchImpl = vi.fn().mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        capture.push({
          url,
          body:
            typeof init?.body === 'string' && init.body.length > 0
              ? JSON.parse(init.body)
              : undefined,
        });
        const match = Object.keys(routes).find((key) => url.includes(key));
        const response =
          match !== undefined ? routes[match] : { status: 200, body: { data: [] } };
        return new Response(JSON.stringify(response.body), {
          status: response.status ?? 200,
          headers: { 'Content-Type': 'application/json' },
        });
      },
    ) as unknown as typeof fetch;
    return new MeshApiClient({ baseUrl: 'http://t', getToken: () => 'tok', fetchImpl });
  }

  const BASE_ROUTES = {
    '/api/v1/sessions': { body: { data: [], next_cursor: null } },
    '/api/v1/auth/oauth/identities': { body: { data: [], next_cursor: null } },
  };

  beforeEach(() => {
    useAuthStore.setState({ token: 'access-tok' });
  });

  afterEach(() => {
    useAuthStore.setState({ token: null });
  });

  it('展开表单 → 强度条实时评估 → 提交成功:具名提示、刷新会话态、会话经 sid 识别(body 无 refresh)', async () => {
    const user = userEvent.setup();
    const onUserChanged = vi.fn();
    const capture: Array<{ url: string; body: unknown }> = [];
    const client = capturingClient(capture, {
      ...BASE_ROUTES,
      '/api/v1/auth/change-password': { body: { data: { status: 'ok' } } },
    });
    renderWithProviders(
      <SecuritySettings client={client} user={USER} onUserChanged={onUserChanged} />,
    );

    // 折叠态 → 点击展开
    await user.click(screen.getByTestId('change-password-toggle'));
    expect(screen.getByTestId('change-password-form')).toBeTruthy();

    await user.type(screen.getByTestId('cp-old'), 'old-pass-1');
    await user.type(screen.getByTestId('cp-new'), 'a-new-passw0rd');
    // 强度条随输入实时渲染:12 位含字母数字 → 满分 4(strong)
    const meter = screen.getByTestId('password-strength');
    expect(meter.getAttribute('data-score')).toBe('4');
    await user.type(screen.getByTestId('cp-confirm'), 'a-new-passw0rd');

    await user.click(screen.getByTestId('cp-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('security-notice').textContent).toContain('Password updated'),
    );
    expect(onUserChanged).toHaveBeenCalled();

    const sent = capture.find((entry) => entry.url.includes('/api/v1/auth/change-password'));
    // R7-M1/R4-H1:当前会话经 access JWT 的 sid 识别——body 不带 refresh_token。
    expect(sent?.body).toEqual({
      old_password: 'old-pass-1',
      new_password: 'a-new-passw0rd',
    });
  });

  it('旧密码错误 → 具名错误文案(invalid_credentials)', async () => {
    const user = userEvent.setup();
    const client = capturingClient([], {
      ...BASE_ROUTES,
      '/api/v1/auth/change-password': {
        status: 422,
        body: { error: { code: 'invalid_credentials', message: 'x' } },
      },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await user.click(screen.getByTestId('change-password-toggle'));
    await user.type(screen.getByTestId('cp-old'), 'wrong-pass-1');
    await user.type(screen.getByTestId('cp-new'), 'a-new-passw0rd');
    await user.type(screen.getByTestId('cp-confirm'), 'a-new-passw0rd');
    await user.click(screen.getByTestId('cp-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('security-error').textContent).toContain(
        'Current password is incorrect',
      ),
    );
  });

  it('服务端弱口令 → 按 details.reason 映射文案(weak_password)', async () => {
    const user = userEvent.setup();
    const client = capturingClient([], {
      ...BASE_ROUTES,
      '/api/v1/auth/change-password': {
        status: 400,
        body: {
          error: {
            code: 'weak_password',
            message: 'x',
            details: { reason: 'too_short', min_length: 8 },
          },
        },
      },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await user.click(screen.getByTestId('change-password-toggle'));
    await user.type(screen.getByTestId('cp-old'), 'old-pass-1');
    await user.type(screen.getByTestId('cp-new'), 'short1');
    await user.type(screen.getByTestId('cp-confirm'), 'short1');
    await user.click(screen.getByTestId('cp-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('security-error').textContent).toContain('at least 8 characters'),
    );
  });

  it('两次新密码不一致:实时提示且阻止提交(不发请求)', async () => {
    const user = userEvent.setup();
    const capture: Array<{ url: string; body: unknown }> = [];
    const client = capturingClient(capture, {
      ...BASE_ROUTES,
      '/api/v1/auth/change-password': { body: { data: { status: 'ok' } } },
    });
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await user.click(screen.getByTestId('change-password-toggle'));
    await user.type(screen.getByTestId('cp-old'), 'old-pass-1');
    await user.type(screen.getByTestId('cp-new'), 'a-new-passw0rd');
    await user.type(screen.getByTestId('cp-confirm'), 'different-pass1');
    // 实时不一致提示
    await waitFor(() =>
      expect(screen.getByTestId('cp-mismatch').textContent).toContain('do not match'),
    );
    // 提交按钮灰化,点击不发请求
    expect((screen.getByTestId('cp-submit') as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByTestId('cp-submit'));
    expect(capture.filter((entry) => entry.url.includes('change-password'))).toEqual([]);
  });

  it('强度条实时态:弱口令列出未满足规则,改正后消失', async () => {
    const user = userEvent.setup();
    const client = capturingClient([], BASE_ROUTES);
    renderWithProviders(<SecuritySettings client={client} user={USER} />);

    await user.click(screen.getByTestId('change-password-toggle'));
    const input = screen.getByTestId('cp-new');
    await user.type(input, 'short1');
    // 未满足规则实时播报(长度 + 字母数字)
    expect(screen.getByTestId('password-rules').textContent).toContain('at least 8 characters');
    await user.clear(input);
    await user.type(input, 'a-new-passw0rd');
    expect(screen.queryByTestId('password-rules')).toBeNull();
    expect(screen.getByTestId('password-strength').getAttribute('data-score')).toBe('4');
  });
});
