import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api/client';
import type { CurrentUser } from '../../../api';
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
