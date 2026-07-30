import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../api/client';
import { ForgotPasswordPage } from '../pages/ForgotPasswordPage';
import { renderWithProviders } from '../../test-utils/render';

function stubClient(status: number, body: unknown): MeshApiClient {
  const fetchImpl = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://t', getToken: () => 'tok', fetchImpl });
}

describe('ForgotPasswordPage(auth.md §4.1 / A4)', () => {
  it('提交邮箱后呈现已发送提示(恒成功防枚举)', async () => {
    const user = userEvent.setup();
    const client = stubClient(200, { data: { status: 'ok' } });
    renderWithProviders(<ForgotPasswordPage client={client} />);

    await user.type(screen.getByTestId('forgot-email'), 'jane@corp.com');
    await user.click(screen.getByTestId('forgot-submit'));

    await waitFor(() => expect(screen.getByTestId('forgot-sent')).toBeTruthy());
  });

  it('提供返回登录链接', () => {
    const client = stubClient(200, { data: { status: 'ok' } });
    renderWithProviders(<ForgotPasswordPage client={client} />);
    expect(screen.getByTestId('forgot-back')).toBeTruthy();
  });

  it('传输/服务端失败 → 可操作错误(不泄露账号存在性),保留表单供重试(§9.1/§9.2)', async () => {
    const user = userEvent.setup();
    const client = stubClient(500, { error: { code: 'internal_error', message: 'boom' } });
    renderWithProviders(<ForgotPasswordPage client={client} />);

    await user.type(screen.getByTestId('forgot-email'), 'jane@corp.com');
    await user.click(screen.getByTestId('forgot-submit'));

    const error = await screen.findByTestId('forgot-error');
    // 通用文案,不含「账号不存在/存在」之类泄露信息。
    expect(error.textContent).toContain('could not process');
    expect(screen.queryByTestId('forgot-sent')).toBeNull();
    // 表单与邮箱保留 → 重提交即恢复动作。
    expect(screen.getByTestId('forgot-submit')).toBeTruthy();
    expect((screen.getByTestId('forgot-email') as HTMLInputElement).value).toBe('jane@corp.com');
  });

  it('失败后重提交成功 → 切换到已发送态(恢复动作生效)', async () => {
    const user = userEvent.setup();
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code: 'internal_error', message: 'boom' } }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: { status: 'ok' } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ) as unknown as typeof fetch;
    const client = new MeshApiClient({ baseUrl: 'http://t', getToken: () => 'tok', fetchImpl });
    renderWithProviders(<ForgotPasswordPage client={client} />);

    await user.type(screen.getByTestId('forgot-email'), 'jane@corp.com');
    await user.click(screen.getByTestId('forgot-submit'));
    await screen.findByTestId('forgot-error');

    await user.click(screen.getByTestId('forgot-submit'));
    await waitFor(() => expect(screen.getByTestId('forgot-sent')).toBeTruthy());
  });
});

describe('ForgotPasswordPage(默认 client 回落)', () => {
  it('未注入 client 时经全局默认 client 发起重置(恒成功呈现不变)', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: { status: 'ok' } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchImpl);
    const user = userEvent.setup();
    renderWithProviders(<ForgotPasswordPage />);

    await user.type(screen.getByTestId('forgot-email'), 'jane@corp.com');
    await user.click(screen.getByTestId('forgot-submit'));

    await waitFor(() => expect(screen.getByTestId('forgot-sent')).toBeTruthy());
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/auth/forgot-password');
    expect(String(init.body)).toContain('jane@corp.com');
  });
});
