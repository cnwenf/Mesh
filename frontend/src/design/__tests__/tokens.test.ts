import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { WCAG_AA_LARGE_RATIO, WCAG_AA_RATIO, contrastRatio } from '../contrast';
import { AA_CONTRAST_PAIRS, APPICA_TOKEN_ALIASES, DARK_TOKENS, LIGHT_TOKENS } from '../tokenValues';

// vitest 以项目根为 cwd 运行;直接读 CSS 原文断言与 tokenValues.ts 的镜像关系。
const tokensCss = readFileSync(path.resolve(process.cwd(), 'src/design/tokens.css'), 'utf8');
const tokensDarkCss = readFileSync(
  path.resolve(process.cwd(), 'src/design/tokens-dark.css'),
  'utf8',
);
const tokensPrintCss = readFileSync(
  path.resolve(process.cwd(), 'src/design/tokens-print.css'),
  'utf8',
);
const appicaTokensCss = readFileSync(
  path.resolve(process.cwd(), 'src/design/appica-tokens.css'),
  'utf8',
);

/** 生成产物的首行「禁止手改」标记(gen-tokens.mjs 契约)。 */
const GENERATED_HEADER =
  '/* 本文件由 scripts/gen-tokens.mjs 从 src/design/tokenValues.ts 生成 —— 禁止手改。 */';

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

  it('三个生成产物首行均为「禁止手改」生成标记(gen:tokens 幂等防线)', () => {
    for (const [name, css] of [
      ['tokens.css', tokensCss],
      ['tokens-dark.css', tokensDarkCss],
      ['tokens-print.css', tokensPrintCss],
    ] as const) {
      expect(css.split('\n')[0], `${name} 首行应为生成标记`).toBe(GENERATED_HEADER);
    }
  });

  it('Appica token bridge 由同一事实源生成并逐项映射 Mesh 语义 token', () => {
    expect(appicaTokensCss.split('\n')[0]).toBe(GENERATED_HEADER);
    expect(appicaTokensCss).toContain(':root');
    for (const [name, value] of Object.entries(APPICA_TOKEN_ALIASES)) {
      expect(appicaTokensCss, `Appica bridge 缺少 ${name}: ${value}`).toContain(
        `${name}: ${value};`,
      );
    }
  });

  it('tokens-print.css 在 @media print 强制亮色:颜色 token 取 LIGHT_TOKENS 值,非颜色 token 不出现', () => {
    // Arrange
    expect(tokensPrintCss).toContain('@media print');
    expect(tokensPrintCss).toContain(":root, :root[data-theme='dark']");

    // Act:解析 print 块内声明的自定义属性
    const body = tokensPrintCss.slice(
      tokensPrintCss.indexOf('{', tokensPrintCss.indexOf(':root,')),
    );
    const declared = new Map<string, string>();
    for (const match of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
      declared.set(match[1], match[2].trim());
    }

    // Assert:与 LIGHT_TOKENS 的颜色子集逐项一致(既无遗漏也无多余)
    const lightColors = Object.fromEntries(
      Object.entries(LIGHT_TOKENS).filter(([name]) => name.startsWith('--color-')),
    );
    expect(Object.fromEntries(declared)).toEqual(lightColors);
    for (const name of Object.keys(LIGHT_TOKENS)) {
      if (!name.startsWith('--color-')) {
        expect(declared, `非颜色 token ${name} 不应出现在打印覆盖中`).not.toHaveProperty(name);
      }
    }
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
      // 选区/强调(theme.md §2.4 选区组)
      '--color-selection-bg',
      '--color-selection-text',
      '--color-mark-bg',
      '--color-mark-text',
      // 骨架屏
      '--color-skeleton-base',
      '--color-skeleton-highlight',
      // 代码高亮(theme.md §4.3 双主题代码色板)
      '--color-code-bg',
      '--color-code-text',
      '--color-code-keyword',
      '--color-code-string',
      '--color-code-comment',
      // 悬停/下沉表面与状态浅底(存量债务迁移的语义落点)
      '--color-surface-hover',
      '--color-surface-sunken',
      '--color-danger-bg',
      '--color-warn-bg',
      '--color-success-bg',
      '--color-info-bg',
      '--color-primary-bg',
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

  it('配对表为对象形式,fg/bg 均引用已登记 token,kind 合法且缺省为 text', () => {
    expect(AA_CONTRAST_PAIRS.length).toBeGreaterThan(0);
    for (const pair of AA_CONTRAST_PAIRS) {
      expect(pair, `配对缺少 fg/bg:${JSON.stringify(pair)}`).toHaveProperty('fg');
      expect(pair).toHaveProperty('bg');
      expect(LIGHT_TOKENS, `fg 未登记 token ${pair.fg}`).toHaveProperty(pair.fg);
      expect(LIGHT_TOKENS, `bg 未登记 token ${pair.bg}`).toHaveProperty(pair.bg);
      if (pair.kind !== undefined) {
        expect(['text', 'large-text', 'graphic']).toContain(pair.kind);
      }
    }
    // 三个维度均有登记(CI 报告按组单列)
    const kinds = new Set(AA_CONTRAST_PAIRS.map((pair) => pair.kind ?? 'text'));
    expect(kinds).toEqual(new Set(['text', 'large-text', 'graphic']));
  });

  it.each(['light', 'dark'] as const)(
    '%s 主题:所有登记配对按 kind 满足 WCAG 2.1 AA(text ≥4.5,large-text/graphic ≥3)',
    (theme) => {
      // Arrange
      const tokens = theme === 'light' ? LIGHT_TOKENS : DARK_TOKENS;

      for (const pair of AA_CONTRAST_PAIRS) {
        // Act
        const fg = tokens[pair.fg];
        const bg = tokens[pair.bg];
        const kind = pair.kind ?? 'text';
        const threshold = kind === 'text' ? WCAG_AA_RATIO : WCAG_AA_LARGE_RATIO;
        const ratio = contrastRatio(fg, bg);

        // Assert
        expect(
          ratio,
          `${theme} 主题 [${kind}] ${pair.fg}(${fg}) on ${pair.bg}(${bg}) 对比度 ${ratio.toFixed(2)} < ${threshold}`,
        ).toBeGreaterThanOrEqual(threshold);
      }
    },
  );
});
