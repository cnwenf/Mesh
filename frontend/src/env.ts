/**
 * 运行时环境配置。
 * 开发默认指向本地 mock 服务端(e2e/mock-server.mjs);
 * 真实后端经 .env.local 的 VITE_MESH_API_BASE_URL / VITE_MESH_WS_BASE_URL 覆盖。
 * 演示频道与降级轮询间隔亦可经 env 覆盖(真实后端联调时指向真实频道)。
 */

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8901';
const DEFAULT_WS_BASE_URL = 'ws://127.0.0.1:8901';
const DEFAULT_DEMO_CHANNEL = 'workspace:ws-1:issues';
const DEFAULT_POLLING_INTERVAL_MS = 30_000;

export interface MeshEnv {
  apiBaseUrl: string;
  wsBaseUrl: string;
  /** 骨架演示区订阅的频道(真实后端联调:workspace:<uuid>:issues) */
  demoChannel: string;
  /** WS 断开后的降级轮询间隔(kanban §3.5 默认 30s;e2e 可调小) */
  pollingIntervalMs: number;
  isDev: boolean;
}

function pollingInterval(raw: string | undefined): number {
  if (!raw) return DEFAULT_POLLING_INTERVAL_MS;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_POLLING_INTERVAL_MS;
}

export function resolveEnv(meta: ImportMetaEnv | undefined): MeshEnv {
  return {
    apiBaseUrl: meta?.VITE_MESH_API_BASE_URL ?? DEFAULT_API_BASE_URL,
    wsBaseUrl: meta?.VITE_MESH_WS_BASE_URL ?? DEFAULT_WS_BASE_URL,
    demoChannel: meta?.VITE_MESH_DEMO_CHANNEL ?? DEFAULT_DEMO_CHANNEL,
    pollingIntervalMs: pollingInterval(meta?.VITE_MESH_POLLING_INTERVAL_MS),
    isDev: Boolean(meta?.DEV),
  };
}

export const env: MeshEnv = resolveEnv(import.meta.env as ImportMetaEnv | undefined);
