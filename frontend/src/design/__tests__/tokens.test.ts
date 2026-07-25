import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { contrastRatio } from '../contrast';
import { AA_CONTRAST_PAIRS, DARK_TOKENS, LIGHT_TOKENS } from '../tokenValues';

// vitest 以项目根为 cwd 运行;直接读 CSS 原文断言与 tokenValues.ts 的镜像关系。
const tokensCss = readFileSync(path.resolve(process.cwd(), 'src/design/tokens.css'), 'utf8');
const tokensDarkCss = readFileSync(
  path.resolve(process.cwd(), 'src/design/tokens-dark.css'),
  'utf8',
);

function assertMirror(css: string, tokens: Readonly<Record<string, string>>): void {
  for (const [name, value] of Object.entries(tokens)) {
    expect(css, `CSS 缺少 \`${name}: ${value};\`(与 tokenValues.ts 漂移)`).toContain(
      `${name}: ${value};`,
    );
  }
}

describe('设计 token(README §6.12 主题契约)', () => {
  it('tokens.css 在 :root 声明亮色集,且与 tokenValues.ts 逐项一致(单一事实源防漂移)', () => {
    expect(tokensCss).toMatch(/:root\s*\{/);
    assertMirror(tokensCss, LIGHT_TOKENS);
  });

  it("tokens-dark.css 在 :root[data-theme='dark'] 整组覆盖,且与 tokenValues.ts 逐项一致", () => {
    expect(tokensDarkCss).toContain(":root[data-theme='dark']");
    assertMirror(tokensDarkCss, DARK_TOKENS);
  });

  it('暗色颜色 token 与亮色集一一对应(整组替换,无遗漏/无多余)', () => {
    const lightColors = Object.keys(LIGHT_TOKENS)
      .filter((name) => name.startsWith('--color-'))
      .sort();
    const darkColors = Object.keys(DARK_TOKENS)
      .filter((name) => name.startsWith('--color-'))
      .sort();
    expect(darkColors).toEqual(lightColors);
  });

  it('亮色集包含必需的全部语义 token', () => {
    const required = [
      '--color-bg',
      '--color-surface',
      '--color-surface-raised',
      '--color-text',
      '--color-text-muted',
      '--color-border',
      '--color-primary',
      '--color-primary-contrast',
      '--color-danger',
      '--color-danger-contrast',
      '--color-warn',
      '--color-warn-contrast',
      '--color-success',
      '--color-success-contrast',
      '--color-info',
      '--color-info-contrast',
      '--color-focus-ring',
      '--space-1',
      '--space-2',
      '--space-3',
      '--space-4',
      '--space-5',
      '--space-6',
      '--radius-sm',
      '--radius-md',
      '--radius-lg',
      '--font-size-sm',
      '--font-size-md',
      '--font-size-lg',
      '--font-family',
      '--shadow-raised',
      '--duration-fast',
      '--duration-slow',
    ];
    for (const name of required) {
      expect(LIGHT_TOKENS, `缺少语义 token ${name}`).toHaveProperty(name);
    }
    // 间距刻度 4/8/12/16/24/32
    expect(LIGHT_TOKENS['--space-1']).toBe('4px');
    expect(LIGHT_TOKENS['--space-2']).toBe('8px');
    expect(LIGHT_TOKENS['--space-3']).toBe('12px');
    expect(LIGHT_TOKENS['--space-4']).toBe('16px');
    expect(LIGHT_TOKENS['--space-5']).toBe('24px');
    expect(LIGHT_TOKENS['--space-6']).toBe('32px');
  });

  it.each(['light', 'dark'] as const)(
    '%s 主题:所有必需文本/底色配对满足 WCAG 2.1 AA(≥4.5:1)',
    (theme) => {
      const tokens = theme === 'light' ? LIGHT_TOKENS : DARK_TOKENS;
      for (const [fgName, bgName] of AA_CONTRAST_PAIRS) {
        const fg = tokens[fgName];
        const bg = tokens[bgName];
        const ratio = contrastRatio(fg, bg);
        expect(
          ratio,
          `${theme} 主题 ${fgName}(${fg}) on ${bgName}(${bg}) 对比度 ${ratio.toFixed(2)} < 4.5`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    },
  );
});
