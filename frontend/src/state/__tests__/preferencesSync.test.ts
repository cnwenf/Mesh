import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../api/client';
import type { UserPreferences } from '../settingsStore';
import {
  syncLocaleToServer,
  syncPreferencesToServer,
  syncThemeToServer,
  syncTimezoneToServer,
  toUpdatePayload,
} from '../preferencesSync';

function createMockClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({
    baseUrl: 'http://localhost:8901',
    getToken: () => 'test-token',
    fetchImpl,
  });
}

function successFetch(): typeof fetch {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ data: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

function errorFetch(status: number, code: string, message: string): typeof fetch {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ error: { code, message } }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

describe('preferencesSync(偏好服务端同步,auth.md §3.1)', () => {
  describe('toUpdatePayload', () => {
    it('将完整偏好映射为 PATCH 请求体', () => {
      const prefs: UserPreferences = { theme: 'dark', locale: 'zh-CN', timezone: 'Asia/Shanghai' };
      const payload = toUpdatePayload(prefs);
      expect(payload).toEqual({
        timezone: 'Asia/Shanghai',
        settings: { locale: 'zh-CN', theme: 'dark' },
      });
    });

    it('locale 为 null 时不包含 locale 字段', () => {
      const prefs: UserPreferences = { theme: 'system', locale: null, timezone: 'UTC' };
      const payload = toUpdatePayload(prefs);
      expect(payload.timezone).toBe('UTC');
      expect(payload.settings?.locale).toBeUndefined();
      expect(payload.settings?.theme).toBe('system');
    });

    it('theme 为 null 时发送显式 null(清除、恢复跟随工作区默认,§3.2)', () => {
      const prefs: UserPreferences = { theme: null, locale: null, timezone: 'UTC' };
      const payload = toUpdatePayload(prefs);
      expect(payload.settings?.theme).toBeNull();
    });
  });

  describe('syncPreferencesToServer', () => {
    it('成功时不调用 onError', async () => {
      const client = createMockClient(successFetch());
      const onError = vi.fn();
      const prefs: UserPreferences = { theme: 'light', locale: 'en', timezone: 'UTC' };

      await syncPreferencesToServer(client, prefs, { onError });

      expect(onError).not.toHaveBeenCalled();
    });

    it('422 unsupported_locale 经 onError 上报', async () => {
      const client = createMockClient(errorFetch(422, 'unsupported_locale', 'not supported'));
      const onError = vi.fn();
      const prefs: UserPreferences = { theme: 'light', locale: 'fr-FR', timezone: 'UTC' };

      await syncPreferencesToServer(client, prefs, { onError });

      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({
          code: 'unsupported_locale',
          status: 422,
        }),
      );
    });

    it('422 invalid_timezone 经 onError 上报', async () => {
      const client = createMockClient(errorFetch(422, 'invalid_timezone', 'bad tz'));
      const onError = vi.fn();
      const prefs: UserPreferences = { theme: 'light', locale: null, timezone: 'Bad/Zone' };

      await syncPreferencesToServer(client, prefs, { onError });

      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({
          code: 'invalid_timezone',
          status: 422,
        }),
      );
    });

    it('422 invalid_theme_mode 经 onError 上报(theme.md §3.3 具名码)', async () => {
      const client = createMockClient(errorFetch(422, 'invalid_theme_mode', 'unsupported theme'));
      const onError = vi.fn();
      const prefs: UserPreferences = { theme: 'light', locale: null, timezone: 'UTC' };

      await syncPreferencesToServer(client, prefs, { onError });

      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({
          code: 'invalid_theme_mode',
          status: 422,
        }),
      );
    });

    it('网络错误静默降级(code=network)', async () => {
      const fetchImpl = vi.fn().mockRejectedValue(new Error('net')) as unknown as typeof fetch;
      const client = createMockClient(fetchImpl);
      const onError = vi.fn();
      const prefs: UserPreferences = { theme: 'dark', locale: null, timezone: 'UTC' };

      await syncPreferencesToServer(client, prefs, { onError });

      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ code: 'network', status: 0 }),
      );
    });

    it('500 服务端错误上报 code=server', async () => {
      const client = createMockClient(errorFetch(500, 'internal_error', 'oops'));
      const onError = vi.fn();
      const prefs: UserPreferences = { theme: 'dark', locale: null, timezone: 'UTC' };

      await syncPreferencesToServer(client, prefs, { onError });

      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ code: 'server', status: 500 }),
      );
    });

    it('无 onError 时不抛错', async () => {
      const client = createMockClient(errorFetch(500, 'internal_error', 'oops'));
      const prefs: UserPreferences = { theme: 'dark', locale: null, timezone: 'UTC' };

      await expect(syncPreferencesToServer(client, prefs)).resolves.toBeUndefined();
    });
  });

  describe('syncThemeToServer', () => {
    it('仅发送 settings.theme 字段', async () => {
      const fetchImpl = successFetch();
      const client = createMockClient(fetchImpl);

      await syncThemeToServer(client, 'dark');

      const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
        string,
        RequestInit,
      ];
      const body = JSON.parse(options.body as string);
      expect(body.settings.theme).toBe('dark');
      expect(body.timezone).toBeUndefined();
    });

    it('错误经 onError 上报', async () => {
      const client = createMockClient(errorFetch(500, 'internal_error', 'fail'));
      const onError = vi.fn();

      await syncThemeToServer(client, 'light', { onError });

      expect(onError).toHaveBeenCalledTimes(1);
    });

    it('theme=null 发送显式 null(清除、恢复跟随工作区默认)', async () => {
      const fetchImpl = successFetch();
      const client = createMockClient(fetchImpl);

      await syncThemeToServer(client, null);

      const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
        string,
        RequestInit,
      ];
      expect(options.body).toContain('"theme":null');
      const body = JSON.parse(options.body as string) as { settings: { theme: null } };
      expect(body.settings.theme).toBeNull();
    });

    it('422 invalid_theme_mode 经 onError 归一上报', async () => {
      const client = createMockClient(errorFetch(422, 'invalid_theme_mode', 'unsupported theme'));
      const onError = vi.fn();

      await syncThemeToServer(client, 'dark', { onError });

      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ code: 'invalid_theme_mode', status: 422 }),
      );
    });
  });

  describe('syncLocaleToServer', () => {
    it('发送 settings.locale 字段', async () => {
      const fetchImpl = successFetch();
      const client = createMockClient(fetchImpl);

      await syncLocaleToServer(client, 'zh-CN');

      const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
        string,
        RequestInit,
      ];
      const body = JSON.parse(options.body as string);
      expect(body.settings.locale).toBe('zh-CN');
    });

    it('locale=null 发送显式 null(清除服务端偏好,后端 pop 语义)', async () => {
      const fetchImpl = successFetch();
      const client = createMockClient(fetchImpl);

      await syncLocaleToServer(client, null);

      const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
        string,
        RequestInit,
      ];
      const body = JSON.parse(options.body as string);
      expect(body.settings.locale).toBeNull();
    });

    it('422 unsupported_locale 经 onError 上报', async () => {
      const client = createMockClient(errorFetch(422, 'unsupported_locale', 'bad'));
      const onError = vi.fn();

      await syncLocaleToServer(client, 'xx-YY', { onError });

      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ code: 'unsupported_locale' }),
      );
    });
  });

  describe('syncTimezoneToServer', () => {
    it('仅发送 timezone 字段', async () => {
      const fetchImpl = successFetch();
      const client = createMockClient(fetchImpl);

      await syncTimezoneToServer(client, 'America/New_York');

      const [, options] = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0] as [
        string,
        RequestInit,
      ];
      const body = JSON.parse(options.body as string);
      expect(body.timezone).toBe('America/New_York');
      expect(body.settings).toBeUndefined();
    });

    it('422 invalid_timezone 经 onError 上报', async () => {
      const client = createMockClient(errorFetch(422, 'invalid_timezone', 'bad tz'));
      const onError = vi.fn();

      await syncTimezoneToServer(client, 'Invalid/Zone', { onError });

      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ code: 'invalid_timezone' }),
      );
    });
  });
});
