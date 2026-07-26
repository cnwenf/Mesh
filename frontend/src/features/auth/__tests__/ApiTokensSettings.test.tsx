import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api/client';
import { ApiTokensSettings } from '../ApiTokensSettings';
import { renderWithProviders } from '../../../test-utils/render';

const TOKEN = {
  id: 'tok-1',
  name: 'ci',
  prefix: 'mesh_pat_Ab3',
  scopes: ['issue:read'],
  role_override: null,
  owner_member_id: 'mem-1',
  expires_at: null,
  last_used_at: null,
  revoked_at: null,
  created_at: '2026-07-25T10:00:00Z',
};

function routingClient(routes: Record<string, { status?: number; body: unknown }>): MeshApiClient {
  const fetchImpl = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const match = Object.keys(routes).find((key) => {
      const [path, keyMethod] = key.split('@');
      if (!url.includes(path)) return false;
      return keyMethod === undefined || keyMethod === method;
    });
    const response = match !== undefined ? routes[match] : { status: 200, body: { data: [] } };
    return new Response(JSON.stringify(response.body), {
      status: response.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://t', getToken: () => 'tok', fetchImpl });
}

describe('ApiTokensSettings(auth.md §4.3 / §3.2)', () => {
  it('列出 token 仅展示 prefix(掩码),无明文/哈希', async () => {
    const client = routingClient({
      '/api/v1/workspaces/ws-1/api-tokens': { body: { data: [TOKEN], next_cursor: null } },
    });
    renderWithProviders(<ApiTokensSettings client={client} workspaceId="ws-1" />);

    await waitFor(() => expect(screen.getByTestId('token-tok-1')).toBeTruthy());
    expect(screen.getByText('mesh_pat_Ab3…')).toBeTruthy();
    // 列表绝不展示完整明文
    expect(screen.queryByText(/mesh_pat_Ab3-plaintext/)).toBeNull();
  });

  it('创建后一次性展示明文 token', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/workspaces/ws-1/api-tokens@GET': { body: { data: [], next_cursor: null } },
      '/api/v1/workspaces/ws-1/api-tokens@POST': {
        status: 201,
        body: { data: { ...TOKEN, token: 'mesh_pat_Ab3-plaintext-shown-once' } },
      },
    });
    renderWithProviders(<ApiTokensSettings client={client} workspaceId="ws-1" />);

    await user.click(screen.getByTestId('token-create'));
    await user.type(screen.getByTestId('token-name'), 'ci');
    await user.type(screen.getByTestId('token-scopes'), 'issue:read, comment:write');
    await user.click(screen.getByTestId('token-create-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('token-plaintext').textContent).toBe(
        'mesh_pat_Ab3-plaintext-shown-once',
      ),
    );
  });

  it('撤销 token 呈现提示', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/workspaces/ws-1/api-tokens@GET': { body: { data: [TOKEN], next_cursor: null } },
      '/api/v1/workspaces/ws-1/api-tokens/tok-1@DELETE': { body: { data: { status: 'ok' } } },
    });
    renderWithProviders(<ApiTokensSettings client={client} workspaceId="ws-1" />);

    await waitFor(() => expect(screen.getByTestId('token-revoke-tok-1')).toBeTruthy());
    await user.click(screen.getByTestId('token-revoke-tok-1'));
    await waitFor(() =>
      expect(screen.getByTestId('tokens-notice').textContent).toContain('revoked'),
    );
  });

  it('创建失败 → 错误提示(明文卡不出现)', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/workspaces/ws-1/api-tokens@GET': { body: { data: [], next_cursor: null } },
      '/api/v1/workspaces/ws-1/api-tokens@POST': {
        status: 400,
        body: { error: { code: 'validation_error', message: 'x' } },
      },
    });
    renderWithProviders(<ApiTokensSettings client={client} workspaceId="ws-1" />);

    await user.click(screen.getByTestId('token-create'));
    await user.type(screen.getByTestId('token-name'), 'ci');
    await user.type(screen.getByTestId('token-scopes'), 'issue:read');
    await user.click(screen.getByTestId('token-create-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('tokens-notice').textContent).toContain('Something went wrong'),
    );
    expect(screen.queryByTestId('token-plaintext')).toBeNull();
  });

  it('撤销失败 → 错误提示', async () => {
    const user = userEvent.setup();
    const client = routingClient({
      '/api/v1/workspaces/ws-1/api-tokens@GET': { body: { data: [TOKEN], next_cursor: null } },
      '/api/v1/workspaces/ws-1/api-tokens/tok-1@DELETE': {
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      },
    });
    renderWithProviders(<ApiTokensSettings client={client} workspaceId="ws-1" />);

    await waitFor(() => expect(screen.getByTestId('token-revoke-tok-1')).toBeTruthy());
    await user.click(screen.getByTestId('token-revoke-tok-1'));
    await waitFor(() =>
      expect(screen.getByTestId('tokens-notice').textContent).toContain('Something went wrong'),
    );
  });
});
