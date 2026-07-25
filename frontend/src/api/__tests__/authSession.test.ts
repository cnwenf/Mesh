import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import {
  forgotPassword,
  listSessions,
  logout,
  logoutAll,
  mfaDisable,
  mfaEnable,
  mfaSetup,
  mfaVerify,
  refresh,
  resetPassword,
  revokeSession,
  verifyEmail,
} from '../auth';

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
    getToken: () => 'test-token',
    fetchImpl,
  });
}

function calledUrl(fetchImpl: typeof fetch): string {
  return ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string])[0];
}

function calledBody(fetchImpl: typeof fetch): Record<string, unknown> {
  const init = ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit])[1];
  return JSON.parse((init.body as string) ?? '{}') as Record<string, unknown>;
}

const TOKENS = {
  access_token: 'at',
  token_type: 'Bearer',
  expires_in: 900,
  refresh_token: 'rt',
};

describe('auth session API(auth.md §3.1 续期/登出/重置/MFA/会话)', () => {
  it('refresh 发送 refresh_token 并返回新凭证', async () => {
    const fetchImpl = createMockFetch(200, { data: TOKENS });
    const result = await refresh(createClient(fetchImpl), 'rt-old');
    expect(result.access_token).toBe('at');
    expect(calledBody(fetchImpl)).toEqual({ refresh_token: 'rt-old' });
    expect(calledUrl(fetchImpl)).toContain('/api/v1/auth/refresh');
  });

  it('logout 撤销指定 refresh', async () => {
    const fetchImpl = createMockFetch(200, { data: { status: 'ok' } });
    await logout(createClient(fetchImpl), 'rt');
    expect(calledBody(fetchImpl)).toEqual({ refresh_token: 'rt' });
    expect(calledUrl(fetchImpl)).toContain('/api/v1/auth/logout');
  });

  it('logoutAll 撤销全部会话并返回数量', async () => {
    const fetchImpl = createMockFetch(200, { data: { revoked: 3 } });
    const result = await logoutAll(createClient(fetchImpl));
    expect(result.revoked).toBe(3);
    expect(calledUrl(fetchImpl)).toContain('/api/v1/auth/logout-all');
  });

  it('forgotPassword 发送邮箱(恒成功防枚举)', async () => {
    const fetchImpl = createMockFetch(200, { data: { status: 'ok' } });
    await forgotPassword(createClient(fetchImpl), 'a@b.com');
    expect(calledBody(fetchImpl)).toEqual({ email: 'a@b.com' });
  });

  it('resetPassword 发送 token + 新密码', async () => {
    const fetchImpl = createMockFetch(200, { data: { status: 'ok' } });
    await resetPassword(createClient(fetchImpl), 'rst', 'new-pass-1');
    expect(calledBody(fetchImpl)).toEqual({ token: 'rst', new_password: 'new-pass-1' });
  });

  it('verifyEmail 发送验证令牌', async () => {
    const fetchImpl = createMockFetch(200, { data: { status: 'ok' } });
    await verifyEmail(createClient(fetchImpl), 'vt');
    expect(calledBody(fetchImpl)).toEqual({ token: 'vt' });
  });

  it('mfaSetup 返回密钥 + otpauth URI + 备用码', async () => {
    const fetchImpl = createMockFetch(200, {
      data: { secret: 'SEC', otpauth_uri: 'otpauth://totp/x', backup_codes: ['c1', 'c2'] },
    });
    const result = await mfaSetup(createClient(fetchImpl));
    expect(result.secret).toBe('SEC');
    expect(result.backup_codes).toEqual(['c1', 'c2']);
  });

  it('mfaEnable / mfaDisable 发送验证码', async () => {
    const enableFetch = createMockFetch(200, { data: { mfa_enabled: true } });
    await mfaEnable(createClient(enableFetch), '123456');
    expect(calledBody(enableFetch)).toEqual({ code: '123456' });

    const disableFetch = createMockFetch(200, { data: { mfa_enabled: false } });
    await mfaDisable(createClient(disableFetch), '123456');
    expect(calledUrl(disableFetch)).toContain('/api/v1/auth/mfa/disable');
  });

  it('mfaVerify 凭 ticket + code 换会话凭证', async () => {
    const fetchImpl = createMockFetch(200, { data: TOKENS });
    const result = await mfaVerify(createClient(fetchImpl), 'ticket', '123456');
    expect(result.access_token).toBe('at');
    expect(calledBody(fetchImpl)).toEqual({ mfa_ticket: 'ticket', code: '123456' });
  });

  it('listSessions 返回活跃会话', async () => {
    const fetchImpl = createMockFetch(200, {
      data: [
        {
          id: 'ses-1',
          type: 'web',
          user_agent: 'UA',
          ip_address: '127.0.0.1',
          created_at: '2026-07-25T10:00:00Z',
          last_active_at: null,
          expires_at: '2026-08-25T10:00:00Z',
          current: true,
        },
      ],
      next_cursor: null,
    });
    const result = await listSessions(createClient(fetchImpl));
    expect(result[0].id).toBe('ses-1');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/sessions');
  });

  it('revokeSession 发 DELETE 到具体会话', async () => {
    const fetchImpl = createMockFetch(200, { data: { status: 'ok' } });
    await revokeSession(createClient(fetchImpl), 'ses-1');
    const init = ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit])[1];
    expect(init.method).toBe('DELETE');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/sessions/ses-1');
  });
});
