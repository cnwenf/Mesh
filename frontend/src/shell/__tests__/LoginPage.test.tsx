/**
 * LoginPage — 产品级登录页基线(MES-107 去脚手架化):
 * 呈现标题/说明与账号表单;开发用 token 直填入口已移除;已登录重定向。
 * 真实邮箱/密码 · MFA · OAuth 流程见 LoginPageReal.test.tsx。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach } from 'vitest';
import { describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { useAuthStore } from '../../state/authStore';
import { renderWithProviders } from '../../test-utils/render';
import { LoginPage } from '../pages/LoginPage';
import type { LoginPageProps } from '../pages/LoginPage';

function renderLogin(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<div data-testid="home-stub" />} />
    </Routes>,
    { route: '/login' },
  );
}

describe('LoginPage', () => {
  beforeEach(() => {
    useAuthStore.getState().clearToken();
  });

  it('呈现标题/说明与账号登录表单,无开发用 token 直填入口', () => {
    renderLogin();
    expect(screen.getByTestId('login-email')).toBeInTheDocument();
    expect(screen.getByTestId('login-password')).toBeInTheDocument();
    expect(screen.getByTestId('login-account-submit')).toBeInTheDocument();
    // 脚手架残留清理(MES-107):dev 令牌块与过时 phaseNote 已移除
    expect(screen.queryByTestId('login-token')).not.toBeInTheDocument();
    expect(screen.queryByTestId('login-submit')).not.toBeInTheDocument();
    expect(screen.queryByText(/Phase 2/)).not.toBeInTheDocument();
  });

  it('已登录时重定向到首页', () => {
    useAuthStore.getState().setToken('already-here');
    renderLogin();
    expect(screen.getByTestId('home-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('login-email')).not.toBeInTheDocument();
  });
});

/** 具名错误与交互补齐所用桩 client:与 LoginPageReal 同形(request 直连 mock fetch)。 */
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

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
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

function renderLoginWithStub(
  fetchImpl: ReturnType<typeof vi.fn>,
  route = '/login',
  props: Partial<LoginPageProps> = {},
): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <ThemeProvider>
        <I18nProvider
          workspaceDefaultLocale={null}
          reporter={{ report: () => undefined, reported: [] }}
        >
          <ToastProvider regionLabel="notifications">
            <Routes>
              <Route
                path="/login"
                element={<LoginPage client={stubClient(fetchImpl) as never} {...props} />}
              />
              <Route path="/" element={<span data-testid="at-home" />} />
            </Routes>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

async function submitCredentials(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
  await user.type(screen.getByTestId('login-password'), 'secret123');
  await user.click(screen.getByTestId('login-account-submit'));
}

describe('LoginPage(具名错误与交互补齐 · auth.md §6.14)', () => {
  beforeEach(() => {
    useAuthStore.getState().clearToken();
  });

  it('弱口令 reason needs_letter_and_digit → 字母+数字文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 400,
      body: {
        error: {
          code: 'weak_password',
          message: 'x',
          details: { reason: 'needs_letter_and_digit' },
        },
      },
    });
    renderLoginWithStub(fetchImpl);
    await user.click(screen.getByTestId('login-mode-register'));
    await user.type(screen.getByTestId('login-display-name'), 'Jane');
    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'password');
    await user.click(screen.getByTestId('login-account-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe(
        'Password must contain both letters and digits.',
      ),
    );
  });

  it('弱口令无 details 时回退通用弱口令文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 400,
      body: { error: { code: 'weak_password', message: 'x' } },
    });
    renderLoginWithStub(fetchImpl);
    await user.click(screen.getByTestId('login-mode-register'));
    await user.type(screen.getByTestId('login-display-name'), 'Jane');
    await user.type(screen.getByTestId('login-email'), 'jane@corp.com');
    await user.type(screen.getByTestId('login-password'), 'qwerty12345');
    await user.click(screen.getByTestId('login-account-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toContain('too common'),
    );
  });

  it('423 account_locked → 锁定具名文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 423,
      body: { error: { code: 'account_locked', message: 'locked' } },
    });
    renderLoginWithStub(fetchImpl);
    await submitCredentials(user);

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe(
        'Too many failed attempts. Please try again later.',
      ),
    );
  });

  it('429 rate_limited → 限流具名文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 429,
      body: { error: { code: 'rate_limited', message: 'slow down' } },
    });
    renderLoginWithStub(fetchImpl);
    await submitCredentials(user);

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe(
        'Too many requests. Please slow down and try again.',
      ),
    );
  });

  it('未知业务 code → error.<code> 透传映射', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 418,
      body: { error: { code: 'teapot', message: 'short and stout' } },
    });
    renderLoginWithStub(fetchImpl);
    await submitCredentials(user);

    // dev 环境对缺失键附加 ⚠ 标记;核心断言为 error.<code> 键透传
    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toContain('error.teapot'),
    );
  });

  it('非 API 异常(网络层错误)→ 网络错误文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    renderLoginWithStub(fetchImpl);
    await submitCredentials(user);

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe(
        'Network error. Please check your connection and try again.',
      ),
    );
  });

  it('MFA 验证码错误 → 二步界面呈现具名错误', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 200, body: { data: { mfa_required: true, mfa_ticket: 'ticket' } } },
      {
        status: 422,
        body: { error: { code: 'invalid_credentials', message: 'bad code' } },
      },
    );
    renderLoginWithStub(fetchImpl);
    await submitCredentials(user);
    await waitFor(() => expect(screen.getByTestId('mfa-code')).toBeTruthy());

    await user.type(screen.getByTestId('mfa-code'), '000000');
    await user.click(screen.getByTestId('mfa-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe(
        'That code is not valid. Please try again.',
      ),
    );
  });

  it('MFA 验证服务异常 → 经通用错误映射呈现', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 200, body: { data: { mfa_required: true, mfa_ticket: 'ticket' } } },
      { status: 500, body: { error: { code: 'internal_error', message: 'boom' } } },
    );
    renderLoginWithStub(fetchImpl);
    await submitCredentials(user);
    await waitFor(() => expect(screen.getByTestId('mfa-code')).toBeTruthy());

    await user.type(screen.getByTestId('mfa-code'), '123456');
    await user.click(screen.getByTestId('mfa-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('login-error').textContent).toBe(
        'An internal error occurred. Please try again.',
      ),
    );
  });

  it('注册模式切回登录模式:错误清除且登录态控件复原', async () => {
    const user = userEvent.setup();
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(422, {
          error: { code: 'invalid_credentials', message: 'incorrect email or password' },
        }),
      );
    renderLoginWithStub(fetchImpl);
    await submitCredentials(user);
    await waitFor(() => expect(screen.getByTestId('login-error')).toBeTruthy());

    // 切到注册再切回登录:错误提示清除,登录模式控件(记住我/忘记密码)复原
    await user.click(screen.getByTestId('login-mode-register'));
    expect(screen.getByTestId('login-display-name')).toBeTruthy();
    await user.click(screen.getByTestId('login-mode-login'));

    expect(screen.queryByTestId('login-error')).toBeNull();
    expect(screen.queryByTestId('login-display-name')).toBeNull();
    expect(screen.getByTestId('login-remember')).toBeTruthy();
    expect(screen.getByTestId('login-forgot')).toBeTruthy();
    expect(screen.getByTestId('login-mode-login').getAttribute('aria-selected')).toBe('true');
  });

  it('勾选记住我后登录请求携带 remember:true', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 200,
      body: {
        data: { access_token: 'jwt-access', token_type: 'Bearer', expires_in: 900 },
      },
    });
    renderLoginWithStub(fetchImpl);
    await user.click(screen.getByTestId('login-remember'));
    await submitCredentials(user);

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
    const [url, init] = fetchImpl.mock.calls[0] as [string, { body: string }];
    expect(url).toContain('/auth/login');
    expect(JSON.parse(init.body) as { remember: boolean }).toMatchObject({ remember: true });
  });

  it('空提供商 ID → 标签回退空串(vendor 中立透传,不崩溃)', () => {
    renderLoginWithStub(stubFetch(), '/login', { oauthProviders: [''] });
    const button = screen.getByTestId('oauth-provider-');
    expect(button.textContent).toBe('');
  });
});
