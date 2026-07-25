/**
 * 运行时环境配置。
 * 开发默认指向本地 mock 服务端(e2e/mock-server.mjs);
 * 真实后端经 .env.local 的 VITE_MESH_API_BASE_URL / VITE_MESH_WS_BASE_URL 覆盖。
 */

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8901';
const DEFAULT_WS_BASE_URL = 'ws://127.0.0.1:8901';

export interface MeshEnv {
  apiBaseUrl: string;
  wsBaseUrl: string;
  isDev: boolean;
}

export function resolveEnv(meta: ImportMetaEnv | undefined): MeshEnv {
  return {
    apiBaseUrl: meta?.VITE_MESH_API_BASE_URL ?? DEFAULT_API_BASE_URL,
    wsBaseUrl: meta?.VITE_MESH_WS_BASE_URL ?? DEFAULT_WS_BASE_URL,
    isDev: Boolean(meta?.DEV),
  };
}

export const env: MeshEnv = resolveEnv(import.meta.env as ImportMetaEnv | undefined);
