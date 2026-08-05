import { defineConfig } from '@playwright/test';

function configuredPort(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 1 || value > 65_535) {
    throw new Error(`${name} must be an integer TCP port`);
  }
  return value;
}

/**
 * MES-187 business-depth acceptance against the isolated production-auth
 * Compose stack. The functional journey runs on desktop/light; every project
 * performs real UI interaction and captures the representative board, member
 * drawer, and project disclosure in the desktop/phone × light/dark matrix.
 */
const frontendPort = configuredPort('MES187_FRONTEND_PORT', 18_740);

export default defineConfig({
  testDir: './e2e',
  testMatch: ['real-mes187-business-depth.spec.ts'],
  timeout: 300_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  outputDir: 'test-results/mes187-business-depth-artifacts',
  use: {
    baseURL: `http://127.0.0.1:${String(frontendPort)}`,
    headless: true,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-light',
      use: { viewport: { width: 1280, height: 800 }, colorScheme: 'light' },
    },
    {
      name: 'desktop-dark',
      use: { viewport: { width: 1280, height: 800 }, colorScheme: 'dark' },
    },
    {
      name: 'phone-light',
      use: {
        viewport: { width: 390, height: 844 },
        colorScheme: 'light',
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: 'phone-dark',
      use: {
        viewport: { width: 390, height: 844 },
        colorScheme: 'dark',
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
});
