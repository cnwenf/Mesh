import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import {
  listIdentities,
  oauthBindUrl,
  oauthCallbackLogin,
  oauthLoginUrl,
  oauthRedirectUri,
  unbindIdentity,
} from '../oauth';

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

function calledInit(fetchImpl: typeof fetch): RequestInit {
  return ((fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit])[1];
}

describe('oauth API(auth.md §1.2 A5/A6, §3.1)', () => {
  it('listIdentities 请求 identities 端点', async () => {
    const fetchImpl = createMockFetch(200, {
      data: [{ provider: 'mock', provider_email: 'a@b.com', created_at: '2026-07-25T10:00:00Z' }],
      next_cursor: null,
    });
    const result = await listIdentities(createClient(fetchImpl));
    expect(result[0].provider).toBe('mock');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/auth/oauth/identities');
  });

  it('unbindIdentity 发 DELETE 到对应 provider', async () => {
    const fetchImpl = createMockFetch(200, { data: { status: 'ok' } });
    await unbindIdentity(createClient(fetchImpl), 'mock');
    expect(calledInit(fetchImpl).method).toBe('DELETE');
    expect(calledUrl(fetchImpl)).toContain('/api/v1/auth/oauth/mock');
  });

  it('oauthLoginUrl 构造 start 跳转 URL(携带 redirect_uri)', () => {
    const url = oauthLoginUrl('http://localhost:8901', 'mock', 'http://app/cb');
    expect(url).toBe(
      'http://localhost:8901/api/v1/auth/oauth/mock/start?redirect_uri=http%3A%2F%2Fapp%2Fcb',
    );
  });

  it('oauthBindUrl 构造 bind 跳转 URL 并去掉尾斜杠', () => {
    const url = oauthBindUrl('http://localhost:8901/', 'mock', 'http://app/cb');
    expect(url).toContain('/api/v1/auth/oauth/mock/bind?redirect_uri=');
    expect(url.startsWith('http://localhost:8901/api')).toBe(true);
  });

  it('oauthRedirectUri 指向当前站点的前端回调路由(与 M1 精确白名单协同)', () => {
    expect(oauthRedirectUri('mock')).toBe(
      `${window.location.origin}/auth/oauth/callback/mock`,
    );
  });

  it('oauthCallbackLogin 用 code+state 交换会话凭证', async () => {
    const fetchImpl = createMockFetch(200, {
      data: {
        access_token: 'jwt-oauth',
        token_type: 'Bearer',
        expires_in: 900,
        refresh_token: 'rt-oauth',
      },
    });
    const tokens = await oauthCallbackLogin(createClient(fetchImpl), 'mock', 'code-1', 'state-1');
    expect(tokens.access_token).toBe('jwt-oauth');
    const url = calledUrl(fetchImpl);
    expect(url).toContain('/api/v1/auth/oauth/mock/callback?');
    expect(url).toContain('code=code-1');
    expect(url).toContain('state=state-1');
    expect(calledInit(fetchImpl).method ?? 'GET').toBe('GET');
  });

  it('oauthCallbackLogin 拒绝非法 provider slug(防路径注入)', async () => {
    const fetchImpl = createMockFetch(200, { data: {} });
    for (const bad of ['../me', 'mock/../x', 'a b', '']) {
      await expect(oauthCallbackLogin(createClient(fetchImpl), bad, 'c', 's')).rejects.toThrow(
        /Invalid OAuth provider slug/,
      );
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
