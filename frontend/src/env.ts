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

/** 实时网关路径(README §6.7 / §6.16;nginx 同源反代 location /ws) */
const WS_GATEWAY_PATH = '/ws';

/**
 * 实时网关绝对 URL(MES-106)。WebSocket 构造器只接受**绝对** ws(s):// URL,
 * 相对地址(`/ws`)直接抛 SyntaxError——同源部署(Dockerfile 以空
 * VITE_MESH_WS_BASE_URL 构建,nginx 反代 /ws → gateway)必须由页面 location
 * 派生:https 页面用 wss,http 页面用 ws(公网 HTTP 场景据此可用)。
 *
 * 显式基址(http://… / https://…)归一为对应 ws(s):// scheme,杜绝运行期
 * 构造错误;基址尾斜杠剔除后拼 /ws。location 可注入以便单测(缺省 window)。
 */
export function resolveWsGatewayUrl(
  wsBaseUrl: string,
  location: Pick<Location, 'protocol' | 'host'> = window.location,
): string {
  const trimmed = wsBaseUrl.trim();
  const base =
    trimmed === ''
      ? (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host
      : trimmed.replace(/^https:\/\//, 'wss://').replace(/^http:\/\//, 'ws://');
  return base.replace(/\/+$/, '') + WS_GATEWAY_PATH;
}
