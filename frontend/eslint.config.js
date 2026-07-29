import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import meshRules from './eslint-rules/index.js';

export default tseslint.config(
  { ignores: ['dist', 'coverage', 'playwright-report', 'test-results', 'node_modules'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      // 本地规则插件(不发包;theme.md §5.4 AST 级硬编码色值门禁)
      mesh: meshRules,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['**/design/tokens.css*'],
              message: 'Import semantic tokens via design/base.css at the app root only.',
            },
          ],
        },
      ],
    },
  },
  {
    // AST 级硬编码色值门禁(theme.md §5.4):应用源码颜色位一律 var(--<语义 token>)。
    // 禁整文件白名单:数据色例外(§2.5)经「行级注释 + theme-lint-exemptions.json」
    // 逐文件登记(labels 数据色板已登记清偿)。__tests__ 夹具含数据色 mock,非样式取色。
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['**/__tests__/**'],
    plugins: { mesh: meshRules },
    rules: { 'mesh/no-hardcoded-colors': 'error' },
  },
);
