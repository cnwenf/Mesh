import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { getApiClient, resetApiClient } from '../instance';
import { useAuthStore } from '../../state/authStore';

describe('API 客户端单例(instance.ts)', () => {
  afterEach(() => {
    resetApiClient();
  });

  it('getApiClient 返回 MeshApiClient 实例', () => {
    const client = getApiClient();
    expect(client).toBeInstanceOf(MeshApiClient);
  });

  it('getApiClient 多次调用返回同一实例(单例)', () => {
    const first = getApiClient();
    const second = getApiClient();
    expect(first).toBe(second);
  });

  it('resetApiClient 后重新创建新实例', () => {
    const first = getApiClient();
    resetApiClient();
    const second = getApiClient();
    expect(first).not.toBe(second);
    expect(second).toBeInstanceOf(MeshApiClient);
  });
});

describe('全局单例 401 兜底接通(MES-106)', () => {
  afterEach(() => {
    resetApiClient();
    useAuthStore.getState().clearToken();
    vi.unstubAllGlobals();
  });

  it('受保护端点 401 → 清 token + 整页跳 /login?next=<当前路径>', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { pathname: '/board', search: '', assign });
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'expired' } }), {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );
    useAuthStore.getState().setToken('tok_dead');
    const client = getApiClient();
    await expect(client.request('GET', '/api/v1/workspaces')).rejects.toMatchObject({
      status: 401,
    });
    expect(useAuthStore.getState().token).toBeNull();
    expect(assign).toHaveBeenCalledWith(`/login?next=${encodeURIComponent('/board')}`);
  });

  it('鉴权豁免端点 401(登录失败)→ 不跳登录页', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { pathname: '/login', search: '', assign });
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: 'invalid_credentials', message: '' } }), {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );
    const client = getApiClient();
    await expect(client.request('POST', '/api/v1/auth/login', { body: {} })).rejects.toMatchObject({
      status: 401,
    });
    expect(assign).not.toHaveBeenCalled();
  });
});
