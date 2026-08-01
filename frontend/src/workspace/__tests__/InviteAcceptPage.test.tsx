import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MAIN_CONTENT_ID } from '../../a11y';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { useAuthStore } from '../../state/authStore';
import { InviteAcceptPage } from '../pages/InviteAcceptPage';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubClient(fetchImpl: ReturnType<typeof vi.fn>) {
  return {
    request: async (
      method: string,
      path: string,
      opts: { body?: unknown; query?: Record<string, string> } = {},
    ) => {
      const qs = opts.query !== undefined ? '?' + new URLSearchParams(opts.query).toString() : '';
      const response = await fetchImpl(`http://localhost${path}${qs}`, {
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

function stubFetch(
  ...responses: Array<{ status: number; body: unknown }>
): ReturnType<typeof vi.fn> {
  const fetchImpl = vi.fn();
  for (const response of responses) {
    fetchImpl.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(response.status, response.body)),
    );
  }
  return fetchImpl;
}

const PREVIEW_VALID = {
  valid: true,
  workspace_name: 'Acme Team',
  workspace_logo_url: null,
  role: 'member',
  expires_at: '2026-08-01T00:00:00Z',
};

const ACCEPT_OK = {
  member: { id: 'mem-9', role: 'member', status: 'active' },
  workspace: { id: 'ws-1', name: 'Acme Team', slug: 'acme' },
};

function renderInvite(
  fetchImpl: ReturnType<typeof vi.fn>,
  token = 'invtk_x',
): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[`/invite/${token}`]}>
      <ThemeProvider>
        <I18nProvider
          workspaceDefaultLocale={null}
          reporter={{ report: () => undefined, reported: [] }}
        >
          <ToastProvider regionLabel="notifications">
            <Routes>
              <Route
                path="/invite/:token"
                element={<InviteAcceptPage client={stubClient(fetchImpl) as never} />}
              />
              <Route path="/login" element={<span data-testid="at-login" />} />
              <Route path="/w/:workspaceSlug" element={<span data-testid="at-workspace" />} />
            </Routes>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('InviteAcceptPage(邀请接受页,§4.3/§4.4)', () => {
  afterEach(() => {
    useAuthStore.getState().clearToken();
  });

  it('使用公共流程外壳并提供 skip link、稳定 main 与唯一 h1', async () => {
    const fetchImpl = stubFetch({ status: 200, body: { data: PREVIEW_VALID } });
    const { container } = renderInvite(fetchImpl);

    await waitFor(() => expect(screen.getByTestId('invite-preview')).toBeTruthy());
    expect(container.querySelector('.mesh-skip-link')?.getAttribute('href')).toBe(
      `#${MAIN_CONTENT_ID}`,
    );
    expect(screen.getByRole('main').id).toBe(MAIN_CONTENT_ID);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });

  it('preview 无效四 reason 各呈 UI 态', async () => {
    for (const reason of ['not_found', 'expired', 'exhausted', 'revoked'] as const) {
      const fetchImpl = stubFetch({ status: 200, body: { data: { valid: false, reason } } });
      const { unmount } = renderInvite(fetchImpl);
      await waitFor(() => expect(screen.getByTestId(`invite-reason-${reason}`)).toBeTruthy());
      unmount();
    }
  });

  it('preview 网络失败 → not_found 同形(不泄漏)', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('network'));
    renderInvite(fetchImpl);
    await waitFor(() => expect(screen.getByTestId('invite-reason-not_found')).toBeTruthy());
  });

  it('有效 + 未登录 → 登录引导,点击跳 /login 携带 next 回跳', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 200, body: { data: PREVIEW_VALID } });
    renderInvite(fetchImpl);

    await waitFor(() => expect(screen.getByTestId('invite-preview')).toBeTruthy());
    expect(screen.getByText(/Acme Team/)).toBeTruthy();

    await user.click(screen.getByTestId('invite-login'));
    await waitFor(() => expect(screen.getByTestId('at-login')).toBeTruthy());
  });

  it('有效 + 已登录 → 接受成功 → 进入工作区(重加入同成功态)', async () => {
    useAuthStore.getState().setToken('jwt-user');
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 200, body: { data: PREVIEW_VALID } },
      { status: 200, body: { data: ACCEPT_OK } },
    );
    renderInvite(fetchImpl);

    await waitFor(() => expect(screen.getByTestId('invite-accept')).toBeTruthy());
    await user.click(screen.getByTestId('invite-accept'));

    await waitFor(() => expect(screen.getByTestId('invite-accepted')).toBeTruthy());
    expect(screen.getByTestId('invite-accepted').textContent).toContain('Acme Team');

    await user.click(screen.getByTestId('invite-enter'));
    await waitFor(() => expect(screen.getByTestId('at-workspace')).toBeTruthy());

    // accept 以 token 请求体提交
    const [, init] = fetchImpl.mock.calls[1] as [string, { method: string; body: string }];
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ token: 'invtk_x' });
  });

  it('接受 422 invitation_invalid → details.reason 四态呈现', async () => {
    useAuthStore.getState().setToken('jwt-user');
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 200, body: { data: PREVIEW_VALID } },
      {
        status: 422,
        body: {
          error: {
            code: 'invitation_invalid',
            message: 'invitation is not valid',
            details: { reason: 'exhausted' },
          },
        },
      },
    );
    renderInvite(fetchImpl);

    await waitFor(() => screen.getByTestId('invite-accept'));
    await user.click(screen.getByTestId('invite-accept'));

    await waitFor(() => expect(screen.getByTestId('invite-reason-exhausted')).toBeTruthy());
  });

  it('接受其他错误 → not_found 同形兜底', async () => {
    useAuthStore.getState().setToken('jwt-user');
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 200, body: { data: PREVIEW_VALID } },
      { status: 500, body: { error: { code: 'internal_error', message: 'x' } } },
    );
    renderInvite(fetchImpl);

    await waitFor(() => screen.getByTestId('invite-accept'));
    await user.click(screen.getByTestId('invite-accept'));

    await waitFor(() => expect(screen.getByTestId('invite-reason-not_found')).toBeTruthy());
  });

  it('token 不出现在文案中(仅经路径/请求体传递)', async () => {
    const fetchImpl = stubFetch({ status: 200, body: { data: PREVIEW_VALID } });
    renderInvite(fetchImpl, 'invtk_SecretToken123');
    await waitFor(() => expect(screen.getByTestId('invite-preview')).toBeTruthy());
    expect(document.body.textContent).not.toContain('invtk_SecretToken123');
  });
});
