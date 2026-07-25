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
});
