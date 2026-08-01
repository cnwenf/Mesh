import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import {
  fetchMe,
  fetchPrincipal,
  isAgentPrincipal,
  isSessionTokens,
  login,
  register,
} from '../auth';
import type { CurrentUser } from '../auth';

function createMockFetch(status: number, body: unknown): typeof fetch {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

function createClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => null,
    fetchImpl,
  });
}

function calledUrl(fetchImpl: typeof fetch): string {
  return ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string])[0];
}

function calledInit(fetchImpl: typeof fetch): RequestInit {
  return ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit])[1];
}

const TOKENS = {
  access_token: 'jwt-a',
  token_type: 'Bearer',
  expires_in: 900,
  refresh_token: 'rt-1',
};

const USER: CurrentUser = {
  id: 'u-1',
  email: 'jane@corp.com',
  email_verified: true,
  display_name: 'Jane',
  avatar_url: null,
  status: 'active',
  timezone: 'Asia/Shanghai',
  settings: { locale: 'zh-CN', theme: 'system' },
  mfa_enabled: false,
  last_login_at: null,
  created_at: '2026-07-25T10:00:00Z',
};

describe('auth API(auth.md §3.1 注册/登录/当前用户)', () => {
  it('login POST 凭证并返回会话凭证', async () => {
    const fetchImpl = createMockFetch(200, { data: TOKENS });
    const client = createClient(fetchImpl);

    const result = await login(client, { email: 'jane@corp.com', password: 'secret123' });

    expect(isSessionTokens(result)).toBe(true);
    if (isSessionTokens(result)) {
      expect(result.access_token).toBe('jwt-a');
    }
    expect(calledUrl(fetchImpl)).toContain('/api/v1/auth/login');
    expect(JSON.parse(String(calledInit(fetchImpl).body))).toEqual({
      email: 'jane@corp.com',
      password: 'secret123',
    });
  });

  it('login MFA 质询判别为非凭证结果', async () => {
    const fetchImpl = createMockFetch(200, {
      data: { mfa_required: true, mfa_ticket: 'ticket-1' },
    });
    const client = createClient(fetchImpl);

    const result = await login(client, { email: 'jane@corp.com', password: 'secret123' });

    expect(isSessionTokens(result)).toBe(false);
  });

  it('login 凭证错误返回 422 invalid_credentials', async () => {
    const fetchImpl = createMockFetch(422, {
      error: { code: 'invalid_credentials', message: 'incorrect email or password' },
    });
    const client = createClient(fetchImpl);

    await expect(
      login(client, { email: 'jane@corp.com', password: 'wrong' }),
    ).rejects.toMatchObject({ status: 422, code: 'invalid_credentials' });
  });

  it('register 返回新用户对象', async () => {
    const fetchImpl = createMockFetch(201, { data: USER });
    const client = createClient(fetchImpl);

    const result = await register(client, {
      email: 'jane@corp.com',
      password: 'secret123',
      display_name: 'Jane',
    });

    expect(result.email).toBe('jane@corp.com');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/auth/register');
  });

  it('register 弱口令返回 400 weak_password 与 reason', async () => {
    const fetchImpl = createMockFetch(400, {
      error: {
        code: 'weak_password',
        message: 'weak password',
        details: { reason: 'too_short', min_length: 8 },
      },
    });
    const client = createClient(fetchImpl);

    await expect(
      register(client, { email: 'j@x.com', password: 'a1', display_name: 'J' }),
    ).rejects.toMatchObject({
      status: 400,
      code: 'weak_password',
      details: { reason: 'too_short', min_length: 8 },
    });
  });

  it('register 邮箱已占用返回 409 conflict', async () => {
    const fetchImpl = createMockFetch(409, {
      error: { code: 'conflict', message: 'conflict', details: { field: 'email' } },
    });
    const client = createClient(fetchImpl);

    await expect(
      register(client, { email: 'jane@corp.com', password: 'secret123', display_name: 'Jane' }),
    ).rejects.toMatchObject({ status: 409, code: 'conflict' });
  });

  it('fetchMe GET /api/v1/me', async () => {
    const fetchImpl = createMockFetch(200, { data: USER });
    const client = createClient(fetchImpl);

    const result = await fetchMe(client);

    expect(result.settings.locale).toBe('zh-CN');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/me');
  });

  it('fetchPrincipal preserves the unified agent principal response', async () => {
    const agent = {
      kind: 'agent' as const,
      id: 'member-agent',
      member_type: 'agent' as const,
      workspace_id: 'ws-agent',
      role: 'member',
      name: 'Builder',
      scopes: ['approval:read'],
    };
    const fetchImpl = createMockFetch(200, { data: agent });

    const result = await fetchPrincipal(createClient(fetchImpl));

    expect(isAgentPrincipal(result)).toBe(true);
    if (isAgentPrincipal(result)) expect(result.workspace_id).toBe('ws-agent');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/me');
  });

  it('does not classify a human /me response as an agent principal', () => {
    expect(isAgentPrincipal(USER)).toBe(false);
  });
});
