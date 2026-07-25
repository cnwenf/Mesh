import { describe, expect, it } from 'vitest';
import { resolveEnv } from './env';

describe('resolveEnv(运行时配置)', () => {
  it('无 VITE_MESH_* 时使用默认值(mock 服务端)', () => {
    const env = resolveEnv(undefined);
    expect(env.apiBaseUrl).toBe('http://127.0.0.1:8901');
    expect(env.wsBaseUrl).toBe('ws://127.0.0.1:8901');
    expect(env.demoChannel).toBe('workspace:ws-1:issues');
    expect(env.pollingIntervalMs).toBe(30_000);
    expect(env.isDev).toBe(false);
  });

  it('VITE_MESH_* 覆盖生效(真实后端联调)', () => {
    const env = resolveEnv({
      VITE_MESH_API_BASE_URL: 'http://127.0.0.1:8000',
      VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:8081',
      VITE_MESH_DEMO_CHANNEL: 'workspace:abc:issues',
      VITE_MESH_POLLING_INTERVAL_MS: '500',
      DEV: true,
    } as unknown as ImportMetaEnv);
    expect(env.apiBaseUrl).toBe('http://127.0.0.1:8000');
    expect(env.wsBaseUrl).toBe('ws://127.0.0.1:8081');
    expect(env.demoChannel).toBe('workspace:abc:issues');
    expect(env.pollingIntervalMs).toBe(500);
    expect(env.isDev).toBe(true);
  });

  it('非法轮询间隔回退默认值', () => {
    const env = resolveEnv({
      VITE_MESH_POLLING_INTERVAL_MS: 'not-a-number',
    } as unknown as ImportMetaEnv);
    expect(env.pollingIntervalMs).toBe(30_000);
    const zero = resolveEnv({ VITE_MESH_POLLING_INTERVAL_MS: '0' } as unknown as ImportMetaEnv);
    expect(zero.pollingIntervalMs).toBe(30_000);
  });
});
