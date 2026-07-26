import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { DangerZone } from '../DangerZone';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubClient(fetchImpl: ReturnType<typeof vi.fn>) {
  return {
    request: async (method: string, path: string, opts: { body?: unknown } = {}) => {
      const response = await fetchImpl(`http://localhost${path}`, {
        method,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
      const body = (await response.json()) as {
        data?: unknown;
        error?: { code: string; message: string };
      };
      if (!response.ok) {
        const { MeshApiError } = await import('../../api/errors');
        throw new MeshApiError({
          status: response.status,
          code: body.error?.code ?? 'internal_error',
          message: body.error?.message ?? '',
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

function renderDanger(fetchImpl: ReturnType<typeof vi.fn>): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={['/w/acme/settings']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            <DangerZone workspaceId="ws-1" workspaceSlug="acme" client={stubClient(fetchImpl) as never} />
            <Routes>
              <Route path="/" element={<span data-testid="at-home" />} />
              <Route path="*" element={<span />} />
            </Routes>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('DangerZone(slug 二次确认删除,§4.2,W10)', () => {
  it('确认按钮在 slug 匹配前禁用', async () => {
    const user = userEvent.setup();
    renderDanger(stubFetch());

    await user.click(screen.getByTestId('danger-open'));
    const confirm = screen.getByTestId('danger-confirm') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);

    await user.type(screen.getByTestId('danger-confirm-input'), 'wrong');
    expect(confirm.disabled).toBe(true);

    await user.clear(screen.getByTestId('danger-confirm-input'));
    await user.type(screen.getByTestId('danger-confirm-input'), 'acme');
    expect(confirm.disabled).toBe(false);
  });

  it('确认删除成功 → 携带 confirm_slug 请求并返回首页', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 200, body: { data: { status: 'deleted' } } });
    renderDanger(fetchImpl);

    await user.click(screen.getByTestId('danger-open'));
    await user.type(screen.getByTestId('danger-confirm-input'), 'acme');
    await user.click(screen.getByTestId('danger-confirm'));

    await waitFor(() => expect(screen.getByTestId('at-home')).toBeTruthy());
    const [, init] = fetchImpl.mock.calls[0] as [string, { method: string; body: string }];
    expect(init.method).toBe('DELETE');
    expect(JSON.parse(init.body)).toEqual({ confirm_slug: 'acme' });
  });

  it('403 非 owner → 具名错误呈现', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 403,
      body: { error: { code: 'forbidden', message: 'only the workspace owner can delete it' } },
    });
    renderDanger(fetchImpl);

    await user.click(screen.getByTestId('danger-open'));
    await user.type(screen.getByTestId('danger-confirm-input'), 'acme');
    await user.click(screen.getByTestId('danger-confirm'));

    await waitFor(() => expect(screen.getByTestId('danger-error').textContent).toBeTruthy());
  });
});
