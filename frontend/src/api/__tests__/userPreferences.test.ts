import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import {
  ERROR_INVALID_TIMEZONE,
  ERROR_UNSUPPORTED_LOCALE,
  fetchCurrentUserPreferences,
  updatePreferences,
} from '../userPreferences';
import type { ServerUserPreferences } from '../userPreferences';

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

describe('userPreferences API(auth.md §3.1 PATCH /api/v1/users/me)', () => {
  it('updatePreferences 发送 PATCH 请求并返回更新后的用户对象', async () => {
    const responseBody: ServerUserPreferences = {
      timezone: 'Asia/Shanghai',
      settings: { locale: 'zh-CN', theme: 'dark' },
    };
    const fetchImpl = createMockFetch(200, { data: responseBody });
    const client = createClient(fetchImpl);

    const result = await updatePreferences(client, {
      timezone: 'Asia/Shanghai',
      settings: { locale: 'zh-CN', theme: 'dark' },
    });

    expect(result).toEqual(responseBody);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(url).toContain('/api/v1/users/me');
    expect(options.method).toBe('PATCH');
    const sentBody = JSON.parse(options.body as string);
    expect(sentBody.timezone).toBe('Asia/Shanghai');
    expect(sentBody.settings.locale).toBe('zh-CN');
    expect(sentBody.settings.theme).toBe('dark');
  });

  it('updatePreferences 422 unsupported_locale 抛出 MeshApiError', async () => {
    const fetchImpl = createMockFetch(422, {
      error: { code: 'unsupported_locale', message: 'locale not supported' },
    });
    const client = createClient(fetchImpl);

    await expect(
      updatePreferences(client, { settings: { locale: 'fr-FR' } }),
    ).rejects.toMatchObject({
      status: 422,
      code: ERROR_UNSUPPORTED_LOCALE,
    });
  });

  it('updatePreferences 422 invalid_timezone 抛出 MeshApiError', async () => {
    const fetchImpl = createMockFetch(422, {
      error: { code: 'invalid_timezone', message: 'invalid IANA timezone' },
    });
    const client = createClient(fetchImpl);

    await expect(
      updatePreferences(client, { timezone: 'Invalid/Zone' }),
    ).rejects.toMatchObject({
      status: 422,
      code: ERROR_INVALID_TIMEZONE,
    });
  });

  it('fetchCurrentUserPreferences 发送 GET 请求并返回偏好', async () => {
    const responseBody: ServerUserPreferences = {
      timezone: 'America/New_York',
      settings: { locale: 'en', theme: 'light' },
    };
    const fetchImpl = createMockFetch(200, { data: responseBody });
    const client = createClient(fetchImpl);

    const result = await fetchCurrentUserPreferences(client);

    expect(result).toEqual(responseBody);
    const [url, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(url).toContain('/api/v1/me');
    expect(options.method).toBe('GET');
  });

  it('网络错误抛出 MeshApiError status=0', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('network')) as unknown as typeof fetch;
    const client = createClient(fetchImpl);

    await expect(updatePreferences(client, { timezone: 'UTC' })).rejects.toMatchObject({
      status: 0,
      code: 'network',
    });
  });

  it('错误常量值正确', () => {
    expect(ERROR_UNSUPPORTED_LOCALE).toBe('unsupported_locale');
    expect(ERROR_INVALID_TIMEZONE).toBe('invalid_timezone');
  });
});
