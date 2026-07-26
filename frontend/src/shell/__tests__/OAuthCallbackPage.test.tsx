import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { useAuthStore } from '../../state/authStore';
import { OAuthCallbackPage } from '../pages/OAuthCallbackPage';

const TOKENS = {
  access_token: 'jwt-oauth',
  token_type: 'Bearer',
  expires_in: 900,
  refresh_token: 'rt-oauth',
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

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderCallback(
  fetchImpl: ReturnType<typeof vi.fn>,
  route: string,
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
                path="/auth/oauth/callback/:provider"
                element={<OAuthCallbackPage client={stubClient(fetchImpl) as never} />}
              />
              <Route path="/" element={<span data-testid="at-home" />} />
              <Route path="/invite/:token" element={<span data-testid="at-invite" />} />
              <Route path="/login" element={<span data-testid="at-login" />} />
            </Routes>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('OAuthCallbackPage(auth.md §4.1 / §4.5 step 5)', () => {
  afterEach(() => {
    useAuthStore.getState().clearToken();
    sessionStorage.removeItem('mesh.oauth.next');
  });

  it('code+state 交换成功 → 写入会话并回跳首页', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { data: TOKENS }));
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=mockcode&state=mockstate');

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
    expect(useAuthStore.getState().token).toBe('jwt-oauth');
    expect(useAuthStore.getState().refreshToken).toBe('rt-oauth');
    const [url] = fetchImpl.mock.calls[0] as [string];
    expect(url).toContain('/api/v1/auth/oauth/mock/callback?code=mockcode&state=mockstate');
  });

  it('携带 sessionStorage 回跳目标时回跳原路径(并清除键)', async () => {
    sessionStorage.setItem('mesh.oauth.next', '/invite/invtk_x');
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { data: TOKENS }));
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=c&state=s');

    await waitFor(() => expect(screen.getByTestId('at-invite')).toBeTruthy());
    expect(sessionStorage.getItem('mesh.oauth.next')).toBeNull();
  });

  it('next 仅接受站内路径(防开放重定向)', async () => {
    sessionStorage.setItem('mesh.oauth.next', '//evil.example');
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { data: TOKENS }));
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=c&state=s');

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
  });

  it('next 反斜杠变体 /\\evil.example(协议相对绕过)→ 回落首页', async () => {
    sessionStorage.setItem('mesh.oauth.next', '/\\evil.example');
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { data: TOKENS }));
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=c&state=s');

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
  });

  it('next 控制字符变体 TAB 夹带(WHATWG 解析器归一化绕过)→ 回落首页', async () => {
    // 不在源码中书写控制字符字面量:经字符码构造 `/<TAB>/evil.example`
    sessionStorage.setItem('mesh.oauth.next', '/' + String.fromCharCode(9) + '/evil.example');
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { data: TOKENS }));
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=c&state=s');

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
  });

  it('next 绝对 URL(https://evil.example)→ 回落首页', async () => {
    sessionStorage.setItem('mesh.oauth.next', 'https://evil.example');
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { data: TOKENS }));
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=c&state=s');

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
  });

  it('缺少 code/state → 具名错误 + 返回登录入口', async () => {
    const fetchImpl = vi.fn();
    renderCallback(fetchImpl, '/auth/oauth/callback/mock');

    await waitFor(() =>
      expect(screen.getByTestId('oauth-callback-error').textContent).toContain(
        'invalid or has expired',
      ),
    );
    expect(fetchImpl).not.toHaveBeenCalled();

    await userEvent.setup().click(screen.getByTestId('oauth-callback-back'));
    await waitFor(() => expect(screen.getByTestId('at-login')).toBeTruthy());
  });

  it('无效 state(后端 400 invalid_oauth_state)→ 具名错误', async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(
      jsonResponse(400, {
        error: { code: 'invalid_oauth_state', message: 'invalid or expired OAuth state' },
      }),
    );
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=c&state=bogus');

    await waitFor(() =>
      expect(screen.getByTestId('oauth-callback-error').textContent).toContain(
        'invalid or has expired',
      ),
    );
    expect(useAuthStore.getState().token).toBeNull();
  });

  it('redirect_uri 未授权(后端 422 redirect_uri_not_allowed)→ 具名错误', async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(
      jsonResponse(422, {
        error: { code: 'redirect_uri_not_allowed', message: 'redirect_uri is not allowed' },
      }),
    );
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=c&state=s');

    await waitFor(() =>
      expect(screen.getByTestId('oauth-callback-error').textContent).toContain('not allowed'),
    );
  });

  it('交换服务异常(500)→ 通用错误', async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(
      jsonResponse(500, { error: { code: 'internal_error', message: 'x' } }),
    );
    renderCallback(fetchImpl, '/auth/oauth/callback/mock?code=c&state=s');

    await waitFor(() =>
      expect(screen.getByTestId('oauth-callback-error').textContent).toContain(
        'Something went wrong',
      ),
    );
  });

  it('非法 provider slug(路径注入)→ 不发请求,通用错误', async () => {
    const fetchImpl = vi.fn();
    // %2F 经路由解码为 ../me → slug 守卫拒绝
    renderCallback(fetchImpl, '/auth/oauth/callback/mock%2F..%2Fme?code=c&state=s');

    await waitFor(() =>
      expect(screen.getByTestId('oauth-callback-error').textContent).toContain(
        'Something went wrong',
      ),
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
