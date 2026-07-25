import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, expect, it, beforeEach, afterEach } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { InvitationCreatePanel, fullInviteUrl } from '../InvitationCreatePanel';

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

const CREATED_LINK = {
  id: 'inv-1',
  email: null,
  role: 'member',
  status: 'active',
  max_uses: 10,
  used_count: 0,
  expires_at: '2026-08-01T00:00:00Z',
  token_prefix: 'invtk_Ab3Xy9zzzz',
  invited_by: 'mem-1',
  created_at: '2026-07-25T00:00:00Z',
  invite_link: '/invite/invtk_Ab3Xy9zzzzzzzzzzzzzzzzzzzzzzz',
};

function renderPanel(fetchImpl: ReturnType<typeof vi.fn>, onCreated = vi.fn()) {
  return renderWithProviders(
    <InvitationCreatePanel
      workspaceId="ws-1"
      caps={{ maxUsesCap: 100, lifetimeHoursCap: 720 }}
      onCreated={onCreated}
      client={stubClient(fetchImpl) as never}
    />,
  );
}

describe('fullInviteUrl', () => {
  it('站内路径拼接当前 origin', () => {
    expect(fullInviteUrl('/invite/invtk_x')).toBe(`${window.location.origin}/invite/invtk_x`);
  });
});

describe('InvitationCreatePanel(邀请创建,§4.2/§4.3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('链接模式默认提交 → 创建成功呈现一次性链接卡 + 回调', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 201, body: { data: [CREATED_LINK], next_cursor: null } });
    const onCreated = vi.fn();
    renderPanel(fetchImpl, onCreated);

    await user.click(screen.getByTestId('invite-submit'));

    await waitFor(() => expect(screen.getByTestId('invite-link-url')).toBeTruthy());
    expect(screen.getByTestId('invite-link-url').textContent).toContain('/invite/invtk_');
    expect(onCreated).toHaveBeenCalled();
    const [, init] = fetchImpl.mock.calls[0] as [string, { method: string; body: string }];
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ role: 'member' });
  });

  it('显式 max_uses / expires_in_hours 随请求提交', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 201, body: { data: [CREATED_LINK], next_cursor: null } });
    renderPanel(fetchImpl);

    await user.type(screen.getByTestId('invite-max-uses'), '5');
    await user.type(screen.getByTestId('invite-expires-hours'), '72');
    await user.click(screen.getByTestId('invite-submit'));

    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    const [, init] = fetchImpl.mock.calls[0] as [string, { body: string }];
    expect(JSON.parse(init.body)).toEqual({ role: 'member', max_uses: 5, expires_in_hours: 72 });
  });

  it('邮箱模式 chips 随 emails 提交', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({ status: 201, body: { data: [CREATED_LINK], next_cursor: null } });
    renderPanel(fetchImpl);

    await user.click(screen.getByTestId('invite-mode-email'));
    await user.type(screen.getByTestId('email-chips-input'), 'jane@corp.com{Enter}');
    await user.click(screen.getByTestId('invite-submit'));

    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    const [, init] = fetchImpl.mock.calls[0] as [string, { body: string }];
    expect(JSON.parse(init.body)).toEqual({ role: 'member', emails: ['jane@corp.com'] });
  });

  it('422 invitation_limits_exceeded(max_uses)→ caps 具名文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 422,
      body: {
        error: {
          code: 'invitation_limits_exceeded',
          message: 'x',
          details: { max_uses: 500, cap: 100 },
        },
      },
    });
    renderPanel(fetchImpl);

    await user.click(screen.getByTestId('invite-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('invite-create-error').textContent).toBe(
        'Max uses 500 exceeds the workspace cap of 100.',
      ),
    );
  });

  it('422 invitation_limits_exceeded(expires_in_hours)→ caps 具名文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 422,
      body: {
        error: {
          code: 'invitation_limits_exceeded',
          message: 'x',
          details: { expires_in_hours: 9000, cap: 720 },
        },
      },
    });
    renderPanel(fetchImpl);

    await user.click(screen.getByTestId('invite-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('invite-create-error').textContent).toBe(
        'Lifetime 9000h exceeds the workspace cap of 720h.',
      ),
    );
  });

  it('409 conflict(同邮箱 active 邀请)→ 具名文案', async () => {
    const user = userEvent.setup();
    const fetchImpl = stubFetch({
      status: 409,
      body: { error: { code: 'conflict', message: 'x', details: { email: 'jane@corp.com' } } },
    });
    renderPanel(fetchImpl);
    await user.click(screen.getByTestId('invite-mode-email'));
    await user.type(screen.getByTestId('email-chips-input'), 'jane@corp.com{Enter}');
    await user.click(screen.getByTestId('invite-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('invite-create-error').textContent).toBe(
        'An active invitation for jane@corp.com already exists.',
      ),
    );
  });

  it('复制链接调用 clipboard 并提示成功', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    const fetchImpl = stubFetch({ status: 201, body: { data: [CREATED_LINK], next_cursor: null } });
    renderPanel(fetchImpl);

    await user.click(screen.getByTestId('invite-submit'));
    await user.click(await screen.findByTestId('invite-copy'));

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(String(writeText.mock.calls[0][0])).toContain('/invite/invtk_');
  });

  it('clipboard 失败 → 降级提示', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
      configurable: true,
    });
    const fetchImpl = stubFetch({ status: 201, body: { data: [CREATED_LINK], next_cursor: null } });
    renderPanel(fetchImpl);

    await user.click(screen.getByTestId('invite-submit'));
    await user.click(await screen.findByTestId('invite-copy'));

    await waitFor(() =>
      expect(screen.getByText('Copy failed — select the link manually.')).toBeTruthy(),
    );
  });
});
