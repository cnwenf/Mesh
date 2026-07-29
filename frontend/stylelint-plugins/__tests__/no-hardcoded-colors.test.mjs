/**
 * stylelint 规则 `mesh/no-hardcoded-colors` 单测。
 *
 * 经 stylelint.lint() 直驱(等价于官方 testRule 的 accept/reject 断言,
 * 且不绑定特定测试框架的全局假设):逐分支覆盖命中/放行/例外三组语义。
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import stylelint from 'stylelint';
import { describe, expect, it } from 'vitest';
import plugin, { messages, ruleName } from '../no-hardcoded-colors.mjs';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const UNREGISTERED = path.join(FRONTEND_ROOT, 'src/features/__unregistered__/x.css');
const FIXTURE_ANY = path.join(FRONTEND_ROOT, 'test/lint-fixtures/fixture-any.css');
const FIXTURE_LINES = path.join(FRONTEND_ROOT, 'test/lint-fixtures/fixture-lines.css');

async function lintRule(code, codeFilename = UNREGISTERED) {
  const result = await stylelint.lint({
    code,
    codeFilename,
    config: {
      plugins: [plugin],
      rules: { [ruleName]: true },
    },
  });
  return result.results[0].warnings.filter((warning) => warning.rule === ruleName);
}

describe('mesh/no-hardcoded-colors(stylelint)', () => {
  describe('命中:颜色位上的颜色字面量', () => {
    it('#hex 命中', async () => {
      const warnings = await lintRule('a { color: #fff; }');
      expect(warnings).toHaveLength(1);
      expect(warnings[0].line).toBe(1);
      expect(warnings[0].text).toContain(ruleName);
      expect(warnings[0].text).toContain('#fff');
    });

    it('#rrggbb 与 #rrggbbaa 命中', async () => {
      const warnings = await lintRule('a { color: #1a7f37; background: #11223344; }');
      expect(warnings).toHaveLength(2);
    });

    it('rgb( / rgba( 命中', async () => {
      const warnings = await lintRule(
        'a { background-color: rgb(255, 0, 0); border-color: rgba(0, 0, 0, 0.5); }',
      );
      expect(warnings).toHaveLength(2);
    });

    it('hsl( / hsla( 命中', async () => {
      const warnings = await lintRule(
        'a { color: hsl(0, 100%, 50%); outline-color: hsla(0, 0%, 0%, 0.3); }',
      );
      expect(warnings).toHaveLength(2);
    });

    it('oklch( / oklab( 命中', async () => {
      const warnings = await lintRule(
        'a { color: oklch(0.7 0.15 180); fill: oklab(0.5 0.1 0.1); }',
      );
      expect(warnings).toHaveLength(2);
    });

    it('CSS 命名色命中(独立词元匹配)', async () => {
      const warnings = await lintRule('a { color: tomato; background-color: rebeccapurple; }');
      expect(warnings).toHaveLength(2);
    });

    it('box-shadow / text-shadow 颜色位命中', async () => {
      const warnings = await lintRule(
        'a { box-shadow: 0 1px 2px rgba(0, 0, 0, 0.5); text-shadow: 1px 1px 0 #000; }',
      );
      expect(warnings).toHaveLength(2);
    });

    it('fill / stroke 命中', async () => {
      const warnings = await lintRule('svg { fill: #000; stroke: red; }');
      expect(warnings).toHaveLength(2);
    });

    it('border-*-color 族(border-inline-start-color)命中', async () => {
      const warnings = await lintRule('a { border-inline-start-color: #fff; }');
      expect(warnings).toHaveLength(1);
    });

    it('shorthand border 中的命名色命中', async () => {
      const warnings = await lintRule('a { border: 1px solid tomato; }');
      expect(warnings).toHaveLength(1);
    });

    it('报错消息含规则名与修复指引', async () => {
      const warnings = await lintRule('a { color: #abc; }');
      expect(warnings[0].text).toContain('var(--');
      expect(warnings[0].text).toContain('mesh-data-color');
      expect(warnings[0].text).toContain('theme-lint-exemptions.json');
      expect(messages.rejected('color', '#abc')).toContain(ruleName);
    });
  });

  describe('放行:token 取色 / 关键字 / 非颜色位', () => {
    it('var(--*) 值放行', async () => {
      const warnings = await lintRule(
        'a { color: var(--color-text); background: var(--color-surface); }',
      );
      expect(warnings).toHaveLength(0);
    });

    it('var(--*) 带 fallback(含 hex)放行——颜色经 token 解析,fallback 为降级值', async () => {
      const warnings = await lintRule('a { border: 1px solid var(--color-border, #d0d7de); }');
      expect(warnings).toHaveLength(0);
    });

    it('关键字 transparent/currentColor/inherit/none 等放行', async () => {
      const warnings = await lintRule(
        'a { background: transparent; border: none; color: currentColor; outline: inherit; box-shadow: none; }',
      );
      expect(warnings).toHaveLength(0);
    });

    it('非颜色位属性不检查', async () => {
      const warnings = await lintRule(
        'a { width: 100px; transition: color 0.3s; content: "#fff"; }',
      );
      expect(warnings).toHaveLength(0);
    });

    it('自定义属性声明(--*)是 token 落点,不命中', async () => {
      const warnings = await lintRule(':root { --color-foo: #123456; }');
      expect(warnings).toHaveLength(0);
    });

    it('forced-colors 块内系统色放行', async () => {
      const warnings = await lintRule(
        '@media (forced-colors: active) { a { color: CanvasText; background: Canvas; border-color: ButtonBorder; } }',
      );
      expect(warnings).toHaveLength(0);
    });

    it('forced-colors 块外系统色命中', async () => {
      const warnings = await lintRule('a { color: Canvas; }');
      expect(warnings).toHaveLength(1);
    });
  });

  describe('例外:上一行注释 + theme-lint-exemptions.json 登记双要件', () => {
    it('注释 + 登记(lines=[])放行', async () => {
      const code = [
        '.foo {',
        '  /* mesh-data-color: 技能图标固定色板(数据色非主题) */',
        '  color: #1a7f37;',
        '}',
      ].join('\n');
      const warnings = await lintRule(code, FIXTURE_ANY);
      expect(warnings).toHaveLength(0);
    });

    it('仅注释无登记 → 仍命中', async () => {
      const code = ['.foo {', '  /* mesh-data-color: 任意原因 */', '  color: #1a7f37;', '}'].join(
        '\n',
      );
      const warnings = await lintRule(code, UNREGISTERED);
      expect(warnings).toHaveLength(1);
    });

    it('仅登记无注释 → 仍命中', async () => {
      const code = ['.foo {', '  color: #1a7f37;', '}'].join('\n');
      const warnings = await lintRule(code, FIXTURE_ANY);
      expect(warnings).toHaveLength(1);
    });

    it('例外注释原因为空 → 不视为例外', async () => {
      const code = ['.foo {', '  /* mesh-data-color: */', '  color: #1a7f37;', '}'].join('\n');
      const warnings = await lintRule(code, FIXTURE_ANY);
      expect(warnings).toHaveLength(1);
    });

    it('行级登记:仅放行登记行(第 5 行),其余行仍命中', async () => {
      const code = [
        '.a {',
        '  /* mesh-data-color: 数据色一 */',
        '  color: red;',
        '  /* mesh-data-color: 数据色二 */',
        '  border-color: #abc;',
        '}',
      ].join('\n');
      const warnings = await lintRule(code, FIXTURE_LINES);
      expect(warnings).toHaveLength(1);
      expect(warnings[0].line).toBe(3);
    });
  });
});
