import { networkInterfaces } from 'node:os';
import { defineConfig } from '@playwright/test';

/**
 * MES-129 真实 e2e 专用配置:HTTP 非安全上下文(非 localhost / 非 HTTPS)写请求验证。
 *
 * 前置:后端栈运行中(docker compose up postgres redis minio api worker gateway;
 * 共享机上 8000/8081/9000 常被其他栈占用,本仓 .env 已把端口改到 18100/18101/18102,
 * 可用 MES_E2E_API_PORT / MES_E2E_WS_PORT 覆盖)。
 *
 * 关键差异(vs playwright.real.config.ts):dev server 绑 0.0.0.0,浏览器经**本机
 * LAN IP**(自动探测,MES_E2E_HTTP_HOST 可覆盖)访问 → `isSecureContext=false`、
 * `crypto.randomUUID=undefined`——正是 MES-129 的故障上下文(用例内断言前置)。
 *
 * 后端 v0.1.0 未开 CORS(生产经 nginx 同源反代),页面源(LAN IP)与 API 源
 * (127.0.0.1)跨域,故沿用 real 配置的 --disable-web-security 启动参数——仅联调
 * 验证用途,非产品行为。
 */
function detectLanHost(): string {
  if (process.env.MES_E2E_HTTP_HOST) return process.env.MES_E2E_HTTP_HOST;
  for (const ifaces of Object.values(networkInterfaces())) {
    for (const iface of ifaces ?? []) {
      if (iface.family === 'IPv4' && !iface.internal) return iface.address;
    }
  }
  throw new Error(
    'MES-129 e2e 需要非回环 IPv4 地址以复现非安全上下文;未探测到网卡 IP,请设置 MES_E2E_HTTP_HOST',
  );
}

const HOST = detectLanHost();
const PORT = Number(process.env.MES_E2E_WEB_PORT ?? 5176);
const API_PORT = process.env.MES_E2E_API_PORT ?? '18100';
const WS_PORT = process.env.MES_E2E_WS_PORT ?? '18101';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes129-insecure-http.spec.ts'],
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://${HOST}:${PORT}`,
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `npm run dev -- --host 0.0.0.0 --port ${PORT} --strictPort`,
      // 就绪探针走回环即可;用例自身经 LAN IP 访问以构成非安全上下文。
      url: `http://127.0.0.1:${PORT}`,
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: `http://127.0.0.1:${API_PORT}`,
        VITE_MESH_WS_BASE_URL: `ws://127.0.0.1:${WS_PORT}`,
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
