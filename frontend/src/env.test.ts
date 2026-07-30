import { describe, expect, it } from 'vitest';
import { resolveEnv, resolveWsGatewayUrl } from './env';

describe('resolveEnv(运行时配置)', () => {
  it('无 VITE_MESH_* 时使用默认值(mock 服务端)', () => {
    const env = resolveEnv(undefined);
    expect(env.apiBaseUrl).toBe('http://127.0.0.1:8901');
    expect(env.wsBaseUrl).toBe('ws://127.0.0.1:8901');
    expect(env.pollingIntervalMs).toBe(4_000);
    expect(env.isDev).toBe(false);
    // 生产默认不渲染第三方登录按钮组(由运营方经 env 显式启用)
    expect(env.oauthProviders).toEqual([]);
  });

  it('dev 默认启用 mock 提供商(第三方登录按钮组)', () => {
    const env = resolveEnv({ DEV: true } as unknown as ImportMetaEnv);
    expect(env.oauthProviders).toEqual(['mock']);
  });

  it('VITE_MESH_* 覆盖生效(真实后端联调)', () => {
    const env = resolveEnv({
      VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8000',
      VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8081',
      VITE_MESH_POLLING_INTERVAL_MS: '500',
      VITE_MESH_OAUTH_PROVIDERS: ' mock , corp-sso ',
      DEV: true,
    } as unknown as ImportMetaEnv);
    expect(env.apiBaseUrl).toBe('http://127.0.0.1:8000');
    expect(env.wsBaseUrl).toBe('ws://127.0.0.1:8081');
    expect(env.pollingIntervalMs).toBe(500);
    expect(env.isDev).toBe(true);
    expect(env.oauthProviders).toEqual(['mock', 'corp-sso']);
  });

  it('VITE_MESH_OAUTH_PROVIDERS 空串 → 显式关闭提供商(即使 dev)', () => {
    const env = resolveEnv({
      VITE_MESH_OAUTH_PROVIDERS: '',
      DEV: true,
    } as unknown as ImportMetaEnv);
    expect(env.oauthProviders).toEqual([]);
  });

  it('非法轮询间隔回退默认值', () => {
    const env = resolveEnv({
      VITE_MESH_POLLING_INTERVAL_MS: 'not-a-number',
    } as unknown as ImportMetaEnv);
    expect(env.pollingIntervalMs).toBe(4_000);
    const zero = resolveEnv({ VITE_MESH_POLLING_INTERVAL_MS: '0' } as unknown as ImportMetaEnv);
    expect(zero.pollingIntervalMs).toBe(4_000);
  });
});

describe('resolveWsGatewayUrl(实时网关绝对 URL,MES-106)', () => {
  const httpLocation = { protocol: 'http:', host: 'mesh.example.com' };
  const httpsLocation = { protocol: 'https:', host: 'mesh.example.com' };

  it('wsBaseUrl 为空(同源部署)→ 由页面 location 派生绝对 ws://(公网 HTTP 场景)', () => {
    expect(resolveWsGatewayUrl('', httpLocation)).toBe('ws://mesh.example.com/ws');
  });

  it('https 页面 → wss://(安全上下文必须加密 WS)', () => {
    expect(resolveWsGatewayUrl('', httpsLocation)).toBe('wss://mesh.example.com/ws');
  });

  it('显式 ws:// 基址原样拼接 /ws', () => {
    expect(resolveWsGatewayUrl('ws://127.0.0.1:8901', httpLocation)).toBe('ws://127.0.0.1:8901/ws');
  });

  it('显式基址尾斜杠剔除(不产生 //ws)', () => {
    expect(resolveWsGatewayUrl('wss://gw.example.com/', httpsLocation)).toBe(
      'wss://gw.example.com/ws',
    );
    expect(resolveWsGatewayUrl('wss://gw.example.com///', httpsLocation)).toBe(
      'wss://gw.example.com/ws',
    );
  });

  it('显式 http(s):// 基址归一为 ws(s)://(WebSocket 构造器拒绝 http scheme)', () => {
    expect(resolveWsGatewayUrl('http://gw.example.com', httpLocation)).toBe(
      'ws://gw.example.com/ws',
    );
    expect(resolveWsGatewayUrl('https://gw.example.com', httpsLocation)).toBe(
      'wss://gw.example.com/ws',
    );
  });

  it('空白基址视同空(同源派生)', () => {
    expect(resolveWsGatewayUrl('   ', httpLocation)).toBe('ws://mesh.example.com/ws');
  });

  it('缺省 location 取 window.location(同源派生,scheme 随页面协议)', () => {
    const expected =
      (window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + '/ws';
    expect(resolveWsGatewayUrl('')).toBe(expected);
  });
});
