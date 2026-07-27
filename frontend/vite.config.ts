import react from '@vitejs/plugin-react';
import { configDefaults, defineConfig } from 'vitest/config';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    css: false,
    // e2e/ 归 Playwright(test:e2e),不由 vitest 收集
    exclude: [...configDefaults.exclude, 'e2e/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'json', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/main.tsx',
        'src/vite-env.d.ts',
        'src/**/*.d.ts',
        'src/types/**',
        'src/**/__tests__/**',
        'src/test-utils/**',
      ],
      // README §3.2 / MES-16 acceptance: overall coverage gate >= 90%
      thresholds: {
        lines: 90,
        functions: 90,
        branches: 90,
        statements: 90,
        // 目录级 per-file 门禁不在这里写 glob 键——vitest thresholds 只认
        // 全局数值键 + perFile/100 布尔,glob 键会被静默忽略(MES-60 第 2 轮
        // 验收 R1:labels/auth/attachments/agents 先例皆是此类空操作)。真实执行的逐文件
        // 门禁见 scripts/verify-perfile-coverage.mjs(已接入 test:coverage)。
      },
    },
  },
});
