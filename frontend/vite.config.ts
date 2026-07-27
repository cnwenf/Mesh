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
        // 验收 REJECT #3:为新增管理面板加目录级阈值,防止新增代码的分支/函数
        // 覆盖率被全局门禁掩盖(整体过线而单文件不过)。
        'src/features/labels/**/*.tsx': {
          lines: 90,
          functions: 90,
          branches: 90,
          statements: 90,
        },
        // MES-55 验收建议:安全守卫目录(防开放重定向的 safeNextPath 等)
        // 比照 labels 先例设 perFile 阈值,覆盖缺口永久可见、不被全局门禁掩盖。
        'src/features/auth/**/*.ts': {
          lines: 90,
          functions: 90,
          branches: 90,
          statements: 90,
        },
        // MES-59:附件模块(签名直传流水线 / 隔离区可见性闸门 / 实时合并)
        // 比照 labels/auth 先例设目录级阈值,新增代码覆盖率不被全局门禁掩盖。
        'src/features/attachments/**/*.ts': {
          lines: 90,
          functions: 90,
          branches: 90,
          statements: 90,
        },
        'src/features/attachments/**/*.tsx': {
          lines: 90,
          functions: 90,
          branches: 90,
          statements: 90,
        },
      },
    },
  },
});
