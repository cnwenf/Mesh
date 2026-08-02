/**
 * MES-90 real-stack visual evidence matrix.
 *
 * Mesh API/PostgreSQL/Redis/worker are real. The test uses signed HTTP-mode
 * DingTalk callbacks and a compose-internal OAPI peer that validates one exact
 * synthetic credential relation; it does not claim a live DingTalk enterprise
 * connection. Physical Stream reconnect is covered by the backend real-worker
 * TLS/WSS e2e, not inferred from this UI config.
 */
import { defineConfig } from '@playwright/test';

const suite = process.env.MES90_SUITE ?? 'visual';
if (suite !== 'visual' && suite !== 'functional') {
  throw new Error('MES90_SUITE must be either visual or functional');
}

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (value === undefined || value === '') throw new Error(`${name} is required`);
  return value;
}

const apiBaseUrl = requiredEnv('MES90_API_BASE');
const wsBaseUrl = requiredEnv('MES90_WS_BASE');
const functional = suite === 'functional';
if (functional) {
  if (process.env.MES90_PG_HOST === undefined) requiredEnv('MES90_PG_CONTAINER');
  if (process.env.MES90_REDIS_HOST === undefined) requiredEnv('MES90_REDIS_CONTAINER');
}

const visualProjects = [
  {
    name: 'desktop-light',
    use: { viewport: { width: 1440, height: 900 } },
  },
  {
    name: 'desktop-dark',
    use: { viewport: { width: 1440, height: 900 } },
  },
  {
    name: 'phone-light',
    use: { viewport: { width: 390, height: 844 } },
  },
  {
    name: 'phone-dark',
    use: { viewport: { width: 390, height: 844 } },
  },
] as const;

export default defineConfig({
  testDir: './e2e',
  testMatch: 'real-dingtalk-ui.spec.ts',
  grep: functional ? /@mes90-functional/ : /@mes90-visual/,
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5175',
    headless: true,
    locale: 'en-US',
    timezoneId: 'UTC',
    reducedMotion: 'reduce',
    launchOptions: {
      args: ['--disable-web-security'],
    },
    trace: 'retain-on-failure',
  },
  projects: functional ? [visualProjects[0]] : [...visualProjects],
  webServer: [
    {
      command: 'npm run dev -- --port 5175 --strictPort --host 127.0.0.1',
      url: 'http://127.0.0.1:5175',
      reuseExistingServer: false,
      timeout: 90_000,
      env: {
        VITE_MESH_API_BASE_URL: apiBaseUrl,
        VITE_MESH_WS_BASE_URL: wsBaseUrl,
        VITE_MESH_POLLING_INTERVAL_MS: '1000',
      },
    },
  ],
});
