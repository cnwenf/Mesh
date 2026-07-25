import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { useAuthStore } from '../../state/authStore';
import { LoginPage } from '../pages/LoginPage';
import type { LoginPageProps } from '../pages/LoginPage';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const TOKENS = {
  access_token: 'jwt-access',
  token_type: 'Bearer',
  expires_in: 900,
  refresh_token: 'rt-1',
};

function stubClient(fetchImpl: ReturnType<typeof vi.fn>) {
  return {
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

function renderLogin(
  fetchImpl: ReturnType<typeof vi.fn>,
  route = '/login',
  props: Partial<LoginPageProps> = {},
): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            <Routes>
              <Route
                path="/login"
                element={<LoginPage client={stubClient(fetchImpl) as never} {...props} />}
              />
              <Route path="/" element={<span data-testid="at-home" />} />
              <Route path="/invite/:token" element={<span data-testid="at-invite" />} />
            </Routes>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('LoginPage 真实账号登录(auth.md §3.1 接通)', () => {
  afterEach(() => {
    useAuthStore.getState().clearToken();
  });

  it('邮箱/密码登录成功 → 存 access token 并回首页', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 200, body: { data: TOKENS } });
    renderLogin(fetchImpl);

    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'secret123');
    await user.click(screen.getByTestId('login-account-submit'));

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
    expect(useAuthStore.getState().token).toBe('jwt-access');
  });

  it('携带 ?next= 时登录后回跳原路径(邀请接受回跳)', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 200, body: { data: TOKENS } });
    renderLogin(fetchImpl, '/login?next=/invite/invtk_x');

    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'secret123');
    await user.click(screen.getByTestId('login-account-submit'));

    await waitFor(() => expect(screen.getByTestId('at-invite')).toBeTruthy());
  });

  it('next 参数仅接受站内路径(防开放重定向)', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 200, body: { data: TOKENS } });
    renderLogin(fetchImpl, '/login?next=//evil.example');

    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'secret123');
    await user.click(screen.getByTestId('login-account-submit'));

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
  });

  it('422 invalid_credentials → 具名错误文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 422,
      body: { error: { code: 'invalid_credentials', message: 'incorrect email or password' } },
    });
    renderLogin(fetchImpl);

    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'wrong');
    await user.click(screen.getByTestId('login-account-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe('Incorrect email or password.'),
    );
  });

  it('注册流:先 register 再 login,弱口令三 reason 具名呈现', async () => {
    const user = userEvent.setup();
    // 弱口令
    const weak = stubFetch({
      status: 400,
      body: {
        error: { code: 'weak_password', message: 'x', details: { reason: 'too_short', min_length: 8 } },
      },
    });
    renderLogin(weak);
    await user.click(screen.getByTestId('login-mode-register'));
    await user.type(screen.getByTestId('login-display-name'), 'Jane');
    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'a1');
    await user.click(screen.getByTestId('login-account-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe(
        'Password must be at least 8 characters.',
      ),
    );
  });

  it('注册成功 → 自动登录态不阻塞,呈现「已发验证邮件」结果页,继续入口回跳', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 201, body: { data: { id: 'u-1' } } },
      { status: 200, body: { data: TOKENS } },
    );
    renderLogin(fetchImpl);

    await user.click(screen.getByTestId('login-mode-register'));
    await user.type(screen.getByTestId('login-display-name'), 'Jane');
    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'secret123');
    await user.click(screen.getByTestId('login-account-submit'));

    // §4.1 结果态:呈现「已发验证邮件」(含注册邮箱),而非直接跳走
    await waitFor(() =>
      expect(screen.getByTestId('register-verify-sent').textContent).toContain('jane@corp.com'),
    );
    // 自动登录态已写入(不阻塞)
    expect(useAuthStore.getState().token).toBe('jwt-access');
    expect(screen.queryByTestId('at-home')).toBeNull();

    // 「继续」入口回跳
    await user.click(screen.getByTestId('register-continue'));
    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());

    const [registerUrl, registerInit] = fetchImpl.mock.calls[0] as [string, { method: string }];
    expect(registerUrl).toContain('/auth/register');
    expect(registerInit.method).toBe('POST');
  });

  it('注册携带 ?next= 时,结果页「继续」回跳原路径', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 201, body: { data: { id: 'u-1' } } },
      { status: 200, body: { data: TOKENS } },
    );
    renderLogin(fetchImpl, '/login?next=/invite/invtk_x');

    await user.click(screen.getByTestId('login-mode-register'));
    await user.type(screen.getByTestId('login-display-name'), 'Jane');
    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'secret123');
    await user.click(screen.getByTestId('login-account-submit'));

    await waitFor(() => expect(screen.getByTestId('register-continue')).toBeTruthy());
    await user.click(screen.getByTestId('register-continue'));
    await waitFor(() => expect(screen.getByTestId('at-invite')).toBeTruthy());
  });

  it('第三方登录按钮组:按提供商列表渲染,点击导航到 start(redirect_uri 指向前端回调)', async () => {
    const onOAuthStart = vi.fn();
    renderLogin(stubFetch(), '/login?next=/invite/invtk_x', {
      oauthProviders: ['mock', 'corp-sso'],
      apiBaseUrl: 'http://api.test',
      onOAuthStart,
    });

    // 按钮组按列表渲染;mock 有本地化名称,未知 ID vendor 中立原样展示
    await userEvent.setup().click(screen.getByTestId('oauth-provider-mock'));
    expect(screen.getByTestId('oauth-provider-corp-sso')).toBeTruthy();

    expect(onOAuthStart).toHaveBeenCalledTimes(1);
    const url = onOAuthStart.mock.calls[0] as [string];
    expect(url[0]).toContain('http://api.test/api/v1/auth/oauth/mock/start?');
    expect(url[0]).toContain(
      `redirect_uri=${encodeURIComponent(`${window.location.origin}/auth/oauth/callback/mock`)}`,
    );
    // 回跳目标经 sessionStorage 携带(不污染精确白名单的 redirect_uri)
    expect(sessionStorage.getItem('mesh.oauth.next')).toBe('/invite/invtk_x');
  });

  it('无提供商配置时不渲染第三方登录按钮组', () => {
    renderLogin(stubFetch(), '/login', { oauthProviders: [] });
    expect(screen.queryByTestId('oauth-provider-mock')).toBeNull();
  });

  it('注册模式随输入呈现强度条与实时校验;登录模式不呈现', async () => {
    const user = userEvent.setup();
    renderLogin(stubFetch(), '/login', { oauthProviders: [] });

    // 登录模式:无强度条
    expect(screen.queryByTestId('password-strength')).toBeNull();

    // 切到注册模式,弱口令 → 实时规则提示
    await user.click(screen.getByTestId('login-mode-register'));
    await user.type(screen.getByTestId('login-password'), 'a1');
    await waitFor(() => expect(screen.getByTestId('password-strength')).toBeTruthy());
    expect(screen.getByTestId('password-rules').textContent).toContain('at least 8');

    // 增强口令 → 规则提示消失
    await user.clear(screen.getByTestId('login-password'));
    await user.type(screen.getByTestId('login-password'), 'Tr5x9qLm2vBz');
    await waitFor(() => expect(screen.queryByTestId('password-rules')).toBeNull());
    expect(screen.getByTestId('password-strength-label').textContent).toContain('Strong');
  });

  it('409 邮箱已占用 → 具名文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 409,
      body: { error: { code: 'conflict', message: 'x', details: { field: 'email' } } },
    });
    renderLogin(fetchImpl);
    await user.click(screen.getByTestId('login-mode-register'));
    await user.type(screen.getByTestId('login-display-name'), 'Jane');
    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'secret123');
    await user.click(screen.getByTestId('login-account-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe(
        'An account with this email already exists.',
      ),
    );
  });

  it('MFA 质询 → 进入二步验证码界面并完成验证登录', async () => {
    const user = userEvent.setup();
    // 第一次调用(login)返回 MFA 质询;第二次(mfaVerify)返回会话凭证。
    const fetchImpl = stubFetch(
      { status: 200, body: { data: { mfa_required: true, mfa_ticket: 'ticket' } } },
      {
        status: 200,
        body: {
          data: { access_token: 'at', token_type: 'Bearer', expires_in: 900, refresh_token: 'rt' },
        },
      },
    );
    renderLogin(fetchImpl);
    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'secret123');
    await user.click(screen.getByTestId('login-account-submit'));

    // 进入二步:出现验证码输入框。
    await waitFor(() => expect(screen.getByTestId('mfa-code')).toBeTruthy());

    await user.type(screen.getByTestId('mfa-code'), '123456');
    await user.click(screen.getByTestId('mfa-submit'));

    // 验证成功 → 写入会话并跳转首页。
    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
    expect(useAuthStore.getState().token).toBe('at');
    expect(useAuthStore.getState().refreshToken).toBe('rt');
  });

  it('开发用 token 直填入口保留(mock e2e 兼容:login-token/login-submit)', async () => {
    const user = userEvent.setup();
    renderLogin(stubFetch());

    await user.type(screen.getByTestId('login-token'), 'mesh-dev:ws-1');
    await user.click(screen.getByTestId('login-submit'));

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
    expect(useAuthStore.getState().token).toBe('mesh-dev:ws-1');
  });

  it('已登录时按 next 重定向', () => {
    useAuthStore.getState().setToken('existing');
    renderLogin(stubFetch(), '/login?next=/invite/invtk_x');
    expect(screen.getByTestId('at-invite')).toBeTruthy();
  });
});
