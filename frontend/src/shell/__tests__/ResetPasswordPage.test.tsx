import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../api/client';
import { ResetPasswordPage } from '../pages/ResetPasswordPage';
import { renderWithProviders } from '../../test-utils/render';

function stubClient(responses: Array<{ status: number; body: unknown }>): MeshApiClient {
  let index = 0;
  const fetchImpl = vi.fn().mockImplementation(async () => {
    const response = responses[Math.min(index, responses.length - 1)];
    index += 1;
    return new Response(JSON.stringify(response.body), {
      status: response.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://t', getToken: () => 'tok', fetchImpl });
}

describe('ResetPasswordPage(auth.md §4.1 / A4)', () => {
  it('从 URL 预填重置码,成功后呈现完成态', async () => {
    const user = userEvent.setup();
    const client = stubClient([{ status: 200, body: { data: { status: 'ok' } } }]);
    renderWithProviders(<ResetPasswordPage client={client} />, { route: '/reset?token=RST' });

    expect((screen.getByTestId('reset-code') as HTMLInputElement).value).toBe('RST');
    await user.type(screen.getByTestId('reset-password'), 'new-pass-1');
    await user.click(screen.getByTestId('reset-submit'));

    await waitFor(() => expect(screen.getByTestId('reset-done')).toBeTruthy());
  });

  it('弱口令呈现强度错误', async () => {
    const user = userEvent.setup();
    const client = stubClient([
      { status: 400, body: { error: { code: 'weak_password', message: 'x', details: { reason: 'too_short' } } } },
    ]);
    renderWithProviders(<ResetPasswordPage client={client} />, { route: '/reset?token=RST' });

    await user.type(screen.getByTestId('reset-password'), 'short');
    await user.click(screen.getByTestId('reset-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('reset-error').textContent).toContain('at least 8'),
    );
  });

  it('无效/过期令牌呈现具名错误', async () => {
    const user = userEvent.setup();
    const client = stubClient([
      { status: 401, body: { error: { code: 'unauthorized', message: 'x' } } },
    ]);
    renderWithProviders(<ResetPasswordPage client={client} />, { route: '/reset?token=BAD' });

    await user.type(screen.getByTestId('reset-password'), 'new-pass-1');
    await user.click(screen.getByTestId('reset-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('reset-error').textContent).toContain('invalid or has expired'),
    );
  });

  it('新密码随输入呈现强度条与实时校验(§4.1)', async () => {
    const user = userEvent.setup();
    const client = stubClient([{ status: 200, body: { data: { status: 'ok' } } }]);
    renderWithProviders(<ResetPasswordPage client={client} />, { route: '/reset?token=RST' });

    expect(screen.queryByTestId('password-strength')).toBeNull();
    await user.type(screen.getByTestId('reset-password'), 'short');
    await waitFor(() => expect(screen.getByTestId('password-strength')).toBeTruthy());
    expect(screen.getByTestId('password-rules').textContent).toContain('at least 8');
  });

  it('弱口令 needs_letter_and_digit 呈现字母+数字文案', async () => {
    const user = userEvent.setup();
    const client = stubClient([
      {
        status: 400,
        body: {
          error: { code: 'weak_password', message: 'x', details: { reason: 'needs_letter_and_digit' } },
        },
      },
    ]);
    renderWithProviders(<ResetPasswordPage client={client} />, { route: '/reset?token=RST' });

    await user.type(screen.getByTestId('reset-password'), 'password');
    await user.click(screen.getByTestId('reset-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('reset-error').textContent).toContain('letters and digits'),
    );
  });

  it('弱口令无 details 时回退通用弱口令文案', async () => {
    const user = userEvent.setup();
    const client = stubClient([
      { status: 400, body: { error: { code: 'weak_password', message: 'x' } } },
    ]);
    renderWithProviders(<ResetPasswordPage client={client} />, { route: '/reset?token=RST' });

    await user.type(screen.getByTestId('reset-password'), 'qwerty12345');
    await user.click(screen.getByTestId('reset-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('reset-error').textContent).toContain('too common'),
    );
  });
});

describe('ResetPasswordPage(默认 client 回落 / 无 URL 令牌)', () => {
  it('URL 无 token 时重置码默认空,手动录入后经全局默认 client 完成重置', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: { status: 'ok' } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchImpl);
    const user = userEvent.setup();
    renderWithProviders(<ResetPasswordPage />, { route: '/reset' });

    expect((screen.getByTestId('reset-code') as HTMLInputElement).value).toBe('');
    await user.type(screen.getByTestId('reset-code'), 'MANUAL-CODE');
    await user.type(screen.getByTestId('reset-password'), 'new-pass-1');
    await user.click(screen.getByTestId('reset-submit'));

    await waitFor(() => expect(screen.getByTestId('reset-done')).toBeTruthy());
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/auth/reset-password');
    expect(String(init.body)).toContain('"token":"MANUAL-CODE"');
  });
});
