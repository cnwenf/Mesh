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
    // 单测墙钟上限:v8 覆盖率插桩显著拖慢渲染/请求链(同 vitest.setup.ts 把
    // asyncUtilTimeout 1s→5s 的理由)。默认 5s 的 it 超时在插桩 + 串行异步周期
    // (如 data-jobs 导入向导 create 失败双周期)下会偶发触顶,与逻辑无关——CI 专用
    // runner 较快故不显,慢速共享机/高负载下显形。给足 headroom 让门禁只校验行为/
    // 覆盖率,不校验机器速度。
    testTimeout: 15000,
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
