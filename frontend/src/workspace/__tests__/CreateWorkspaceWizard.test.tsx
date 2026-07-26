import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../design';
import { I18nProvider } from '../../i18n';
import { CreateWorkspaceWizard, suggestSlug } from '../CreateWorkspaceWizard';
import type { ReactNode } from 'react';

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

const CREATED = {
  id: 'ws-new',
  name: 'Acme Team',
  slug: 'acme-team',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'en' },
  my_role: 'owner',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

function renderWizard(fetchImpl: ReturnType<typeof vi.fn>): ReturnType<typeof render> {
  const tree = (children: ReactNode): React.JSX.Element => (
    <MemoryRouter initialEntries={['/']}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
          <ToastProvider regionLabel="notifications">
            {children}
            <Routes>
              <Route path="/" element={<span data-testid="at-root" />} />
              <Route path="/w/:workspaceSlug" element={<span data-testid="at-new-ws" />} />
            </Routes>
          </ToastProvider>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>
  );
  return render(
    tree(<CreateWorkspaceWizard open onClose={() => undefined} client={stubClient(fetchImpl) as never} />),
  );
}

describe('suggestSlug(名称 → slug 建议)', () => {
  it('小写化、非字母数字转连字符、去首尾连字符、截断 32', () => {
    expect(suggestSlug('Acme Team!')).toBe('acme-team');
    expect(suggestSlug('  Hello   World  ')).toBe('hello-world');
    expect(suggestSlug('x'.repeat(40))).toBe('x'.repeat(32));
  });
});

describe('CreateWorkspaceWizard(创建向导,§4.2/§4.3)', () => {
  it('名称 → slug(自动建议)→ 跳过邀请 → 创建 → 进入新工作区', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      // slug 占用探测:404 = 可用
      { status: 404, body: { error: { code: 'not_found', message: 'workspace not found' } } },
      // 创建工作区
      { status: 201, body: { data: CREATED } },
    );
    renderWizard(fetchImpl);

    await user.type(screen.getByTestId('ws-wizard-name-input'), 'Acme Team');
    await user.click(screen.getByTestId('ws-wizard-next'));

    // 自动建议 slug
    const slugInput = screen.getByTestId('ws-wizard-slug-input') as HTMLInputElement;
    expect(slugInput.value).toBe('acme-team');

    await user.click(screen.getByTestId('ws-wizard-next-slug'));
    await waitFor(() => expect(screen.getByTestId('ws-wizard-invite')).toBeTruthy());

    await user.click(screen.getByTestId('ws-wizard-skip'));
    await waitFor(() => expect(screen.getByTestId('at-new-ws')).toBeTruthy());

    // POST /workspaces 请求体
    const [, init] = fetchImpl.mock.calls[1] as [string, { method: string; body: string }];
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ name: 'Acme Team', slug: 'acme-team' });
  });

  it('slug 格式非法时下一步禁用并提示', async () => {
    const user = userEvent.setup();
    renderWizard(stubFetch());

    await user.type(screen.getByTestId('ws-wizard-name-input'), 'A');
    await user.click(screen.getByTestId('ws-wizard-next'));
    const slugInput = screen.getByTestId('ws-wizard-slug-input');
    await user.clear(slugInput);
    await user.type(slugInput, 'Bad Slug');

    expect(screen.getByText('Slug format is invalid.')).toBeTruthy();
    expect((screen.getByTestId('ws-wizard-next-slug') as HTMLButtonElement).disabled).toBe(true);
  });

  it('slug 已被占用(探测 200)→ 占用提示且不前进', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 200, body: { data: CREATED } });
    renderWizard(fetchImpl);

    await user.type(screen.getByTestId('ws-wizard-name-input'), 'Acme');
    await user.click(screen.getByTestId('ws-wizard-next'));
    await user.click(screen.getByTestId('ws-wizard-next-slug'));

    expect(screen.getByTestId('ws-wizard-slug-check').textContent).toBe('This slug is already taken.');
    expect(screen.queryByTestId('ws-wizard-invite')).toBeNull();
  });

  it('创建 409 slug_taken → 回退 slug 步并呈现具名错误', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 404, body: { error: { code: 'not_found', message: 'workspace not found' } } },
      {
        status: 409,
        body: { error: { code: 'slug_taken', message: 'slug taken', details: { slug: 'acme' } } },
      },
    );
    renderWizard(fetchImpl);

    await user.type(screen.getByTestId('ws-wizard-name-input'), 'Acme');
    await user.click(screen.getByTestId('ws-wizard-next'));
    await user.click(screen.getByTestId('ws-wizard-next-slug'));
    await user.click(await screen.findByTestId('ws-wizard-skip'));

    await waitFor(() => expect(screen.getByTestId('ws-wizard-slug')).toBeTruthy());
    expect(screen.getByTestId('ws-wizard-error').textContent).toBe('This slug is already taken');
  });

  it('带邮箱邀请完成 → 创建后发起邀请;邀请失败不阻塞进入', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch(
      { status: 404, body: { error: { code: 'not_found', message: 'workspace not found' } } },
      { status: 201, body: { data: CREATED } },
      // 邀请失败
      { status: 422, body: { error: { code: 'invitation_limits_exceeded', message: 'x' } } },
    );
    renderWizard(fetchImpl);

    await user.type(screen.getByTestId('ws-wizard-name-input'), 'Acme Team');
    await user.click(screen.getByTestId('ws-wizard-next'));
    await user.click(screen.getByTestId('ws-wizard-next-slug'));
    await waitFor(() => screen.getByTestId('ws-wizard-invite'));

    await user.type(screen.getByTestId('email-chips-input'), 'jane@corp.com{Enter}');
    await user.click(screen.getByTestId('ws-wizard-create'));

    // 邀请失败仍进入新工作区
    await waitFor(() => expect(screen.getByTestId('at-new-ws')).toBeTruthy());
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it('名称为空时下一步禁用', () => {
    renderWizard(stubFetch());
    expect((screen.getByTestId('ws-wizard-next') as HTMLButtonElement).disabled).toBe(true);
  });
});
