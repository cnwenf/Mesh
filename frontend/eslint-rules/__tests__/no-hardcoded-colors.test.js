/**
 * ESLint 规则 `mesh/no-hardcoded-colors` 单测(RuleTester,flat config)。
 * 逐分支覆盖:JSX style 对象 / SVG fill|stroke / 对象表达式 style 的命中与放行,
 * 以及「上一行注释 + theme-lint-exemptions.json 登记」例外双要件。
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { RuleTester } from 'eslint';
import { noHardcodedColors } from '../no-hardcoded-colors.js';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const FIXTURE_ANY = path.join(FRONTEND_ROOT, 'test/lint-fixtures/eslint-fixture.tsx');
const FIXTURE_LINES = path.join(FRONTEND_ROOT, 'test/lint-fixtures/eslint-fixture-lines.tsx');
const UNREGISTERED = path.join(FRONTEND_ROOT, 'src/features/__unregistered__/X.tsx');

const ruleTester = new RuleTester({
  languageOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
});

ruleTester.run('mesh/no-hardcoded-colors', noHardcodedColors, {
  valid: [
    {
      name: 'JSX style 经 var(--*) 取色放行',
      code: 'const el = <div style={{ color: "var(--color-text)" }} />;',
    },
    {
      name: 'SVG fill=currentColor 放行(任意位置)',
      code: 'const el = <svg fill="currentColor" />;',
    },
    {
      name: '关键字 transparent / none 放行',
      code: 'const el = <div style={{ background: "transparent", border: "none" }} />;',
    },
    {
      name: '非颜色键不检查',
      code: 'const el = <div style={{ padding: "8px", margin: "0" }} />;',
    },
    {
      name: 'JSX 表达式容器内 var(--*) 放行',
      code: 'const el = <svg fill={"var(--color-primary)"} />;',
    },
    {
      name: '非静态值(函数调用)不参与静态门禁',
      code: 'const el = <div style={{ color: resolveColor() }} />;',
    },
    {
      name: '含插值的模板串不静态判定',
      code: 'const el = <div style={{ color: `${dynamicColor}` }} />;',
    },
    {
      name: 'var(--*) 带 fallback 放行',
      code: 'const el = <div style={{ border: "1px solid var(--color-border, #d0d7de)" }} />;',
    },
    {
      name: '例外:注释 + 登记(lines=[])放行',
      code: [
        'const el = (',
        '  <div',
        '    // mesh-data-color: 数据可视化类别固定色(fixture)',
        '    style={{ color: "#123456" }}',
        '  />',
        ');',
      ].join('\n'),
      filename: FIXTURE_ANY,
    },
    {
      name: '例外:行级登记命中行放行(第 4 行)',
      code: [
        'const styles = {',
        '  swatch: {',
        '    // mesh-data-color: 固定色板(数据色)',
        '    backgroundColor: "#654321",',
        '  },',
        '};',
      ].join('\n'),
      filename: FIXTURE_LINES,
    },
  ],
  invalid: [
    {
      name: 'JSX style 内 #hex 命中',
      code: 'const el = <div style={{ color: "#fff" }} />;',
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: '对象表达式 style backgroundColor(camelCase 归一)rgb() 命中',
      code: 'const styles = { card: { backgroundColor: "rgb(1, 2, 3)" } };',
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: 'SVG fill 属性 #hex 命中',
      code: 'const el = <svg fill="#000" />;',
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: 'SVG stroke 命名色命中',
      code: 'const el = <svg stroke="tomato" />;',
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: '模板串(无插值)色值命中',
      code: 'const el = <div style={{ color: `#abcdef` }} />;',
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: 'boxShadow 内 rgba 命中',
      code: 'const el = <div style={{ boxShadow: "0 1px 2px rgba(0, 0, 0, 0.5)" }} />;',
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: '仅注释无登记 → 仍命中',
      code: [
        'const el = (',
        '  <div',
        '    // mesh-data-color: 任意原因',
        '    style={{ color: "#123456" }}',
        '  />',
        ');',
      ].join('\n'),
      filename: UNREGISTERED,
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: '仅登记无注释 → 仍命中',
      code: 'const el = <div style={{ color: "#123456" }} />;',
      filename: FIXTURE_ANY,
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: '例外注释非上一行 → 不视为例外',
      code: [
        '// mesh-data-color: 注释隔着一行',
        'const wrapper = null;',
        'const el = <div style={{ color: "#123456" }} />;',
      ].join('\n'),
      filename: FIXTURE_ANY,
      errors: [{ messageId: 'hardcoded' }],
    },
    {
      name: '行级登记:未登记行仍命中(第 1 行对象,登记仅第 4 行)',
      code: 'const styles = { a: { color: "red" }, b: { color: "blue" } };',
      filename: FIXTURE_LINES,
      errors: [{ messageId: 'hardcoded' }, { messageId: 'hardcoded' }],
    },
  ],
});
