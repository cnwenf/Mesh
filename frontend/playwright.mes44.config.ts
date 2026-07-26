import { defineConfig } from '@playwright/test';

/**
 * MES-44 真实 UI 实操回归专用配置:仅运行 real-mes44-regression.spec.ts。
 *
 * 前置:一个**包含 issue 模块**的后端栈运行中(postgres+redis+api+worker+gateway,
 * MESH_AUTH_MODE=dev)。注意:早期 v0.1.0 镜像不含 issue 模块,必须用当前源码重建
 * (docker compose build api worker gateway && docker compose up -d,或下方隔离拉起)。
 *
 * 后端端口经环境变量配置,默认 8000/8081(与 docker-compose 一致):
 *   MES44_API_PORT / MES44_WS_PORT  —— REST / 实时网关端口
 *   MES44_DEV_PORT                  —— 本配置拉起的 vite dev server 端口(默认 5175,
 *                                      以避开 playwright.real.config.ts 的 5174)
 * spec 内的 REST 准备同样读取 MES44_API_PORT(默认 8000)。
 *
 * 隔离拉起当前源码后端(不触碰共享栈)的参考步骤:
 *   docker run -d --name mesh-pg-44 -p 5434:5432 -e POSTGRES_USER=mesh \
 *     -e POSTGRES_PASSWORD=mesh -e POSTGRES_DB=mesh postgres:16
 *   cd backend && python -m venv .venv && . .venv/bin/activate \
 *     && pip install -r requirements.lock && pip install -e .
 *   MESH_DATABASE_URL=postgresql+asyncpg://mesh:mesh@127.0.0.1:5434/mesh \
 *   MESH_APP_DATABASE_URL=postgresql+asyncpg://mesh:mesh@127.0.0.1:5434/mesh \
 *   MESH_REDIS_URL=redis://127.0.0.1:6379/1 MESH_AUTH_MODE=dev \
 *   MESH_JWT_SECRET=dev python -m alembic upgrade head
 *   # 同上 env 启动 api(--port 8100)与 gateway(mesh.realtime.app:create_app --port 8181)
 *   MES44_API_PORT=8100 MES44_WS_PORT=8181 npx playwright test --config playwright.mes44.config.ts
 *
 * 后端 v0.1.0 未开 CORS(生产经 nginx 反代同源),故以 --disable-web-security 启动
 * 浏览器 —— 仅联调验证用途,非产品行为。
 */
const apiPort = process.env.MES44_API_PORT ?? '8000';
const wsPort = process.env.MES44_WS_PORT ?? '8081';
const devPort = process.env.MES44_DEV_PORT ?? '5175';

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes44-regression.spec.ts'],
  timeout: 180_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${devPort}`,
    headless: true,
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `npm run dev -- --port ${devPort} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${devPort}`,
      reuseExistingServer: true,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: `http://127.0.0.1:${apiPort}`,
        VITE_MESH_WS_BASE_URL: `ws://127.0.0.1:${wsPort}`,
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
