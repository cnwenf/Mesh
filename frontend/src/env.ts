/**
 * 运行时环境配置。
 * 开发默认指向本地 mock 服务端(e2e/mock-server.mjs);
 * 真实后端经 .env.local 的 VITE_MESH_API_BASE_URL / VITE_MESH_WS_BASE_URL 覆盖。
 * 演示频道与降级轮询间隔亦可经 env 覆盖(真实后端联调时指向真实频道)。
 * OAuth 登录提供商经 VITE_MESH_OAUTH_PROVIDERS 配置(逗号分隔 ID;vendor 中立):
 * dev 默认 mock 提供商,生产默认空(不渲染按钮组,由运营方按需启用)。
 */

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8901';
const DEFAULT_WS_BASE_URL = 'ws://127.0.0.1:8901';
const DEFAULT_DEMO_CHANNEL = 'workspace:ws-1:issues';
const DEFAULT_POLLING_INTERVAL_MS = 4_000;  // autopilot.md §3.5: 3~5s fallback polling
const DEV_OAUTH_PROVIDERS: readonly string[] = ['mock'];

export interface MeshEnv {
  apiBaseUrl: string;
  wsBaseUrl: string;
  /** 骨架演示区订阅的频道(真实后端联调:workspace:<uuid>:issues) */
  demoChannel: string;
  /** WS 断开后的降级轮询间隔(kanban §3.5 默认 30s;e2e 可调小) */
  pollingIntervalMs: number;
  /** 登录页「使用第三方账号登录」按钮组渲染的提供商 ID 列表(auth.md §4.1) */
  oauthProviders: readonly string[];
  isDev: boolean;
}

function pollingInterval(raw: string | undefined): number {
  if (!raw) return DEFAULT_POLLING_INTERVAL_MS;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_POLLING_INTERVAL_MS;
}

function oauthProviders(raw: string | undefined, isDev: boolean): readonly string[] {
  if (raw !== undefined) {
    return raw
      .split(',')
      .map((provider) => provider.trim())
      .filter((provider) => provider.length > 0);
  }
  return isDev ? DEV_OAUTH_PROVIDERS : [];
}

export function resolveEnv(meta: ImportMetaEnv | undefined): MeshEnv {
  const isDev = Boolean(meta?.DEV);
  return {
    apiBaseUrl: meta?.VITE_MESH_API_BASE_URL ?? DEFAULT_API_BASE_URL,
    wsBaseUrl: meta?.VITE_MESH_WS_BASE_URL ?? DEFAULT_WS_BASE_URL,
    demoChannel: meta?.VITE_MESH_DEMO_CHANNEL ?? DEFAULT_DEMO_CHANNEL,
    pollingIntervalMs: pollingInterval(meta?.VITE_MESH_POLLING_INTERVAL_MS),
    oauthProviders: oauthProviders(meta?.VITE_MESH_OAUTH_PROVIDERS, isDev),
    isDev,
  };
}

export const env: MeshEnv = resolveEnv(import.meta.env as ImportMetaEnv | undefined);
