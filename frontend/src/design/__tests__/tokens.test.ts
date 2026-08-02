import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { WCAG_AA_LARGE_RATIO, WCAG_AA_RATIO, contrastRatio } from '../contrast';
import { AA_CONTRAST_PAIRS, DARK_TOKENS, LIGHT_TOKENS } from '../tokenValues';

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
const componentsCss = readFileSync(
  path.resolve(process.cwd(), 'src/design/components/components.css'),
  'utf8',
);
const baseCss = readFileSync(path.resolve(process.cwd(), 'src/design/base.css'), 'utf8');

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

function cssFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? cssFiles(target) : entry.name.endsWith('.css') ? [target] : [];
  });
}

describe('设计 token(README §6.12 主题契约)', () => {
  it('MES-108 接受基线在语义层保留明暗表面、品牌与中性主操作的独立设计事实', () => {
    expect(LIGHT_TOKENS).toMatchObject({
      '--color-bg': '#f3f3f4',
      '--color-canvas': '#fbfbfb',
      '--color-surface': '#ffffff',
      '--color-surface-raised': '#ffffff',
      '--color-surface-hover': '#f4f4f5',
      '--color-surface-selected': '#eeeef0',
      '--color-text-strong': '#09090b',
      '--color-text': '#09090b',
      '--color-text-muted': '#64636e',
      '--color-text-disabled': '#81818b',
      '--color-text-faint-base': '#81818b',
      '--color-placeholder': '#64636e',
      '--color-control-disabled-text': '#8b8b94',
      '--color-border-subtle': '#ececef',
      '--color-border': '#e4e4e7',
      '--color-input-border-base': '#e4e4e7',
      '--color-input-border': '#8b8b94',
      '--color-input-border-hover': '#71717a',
      '--color-primary': '#18181b',
      '--color-primary-contrast': '#fafafa',
      '--color-brand-base': '#2171cc',
      '--color-brand-soft-base': '#eaf3fd',
      '--color-surface-pressed': '#d4d4d8',
      '--color-success-base': '#1c882d',
      '--color-success-soft-base': '#eaf7ec',
      '--color-warning-base': '#dca400',
      '--color-warning-soft-base': '#fff7da',
      '--color-danger-base': '#e7000b',
      '--color-danger-soft-base': '#fff0f1',
      '--color-info-base': '#0072d5',
      '--color-info-soft-base': '#eaf4ff',
      '--color-category-violet': '#7958d8',
      '--color-category-orange': '#e4761b',
      '--shadow-1': '0 1px 2px rgb(15 23 42 / 4%), 0 1px 1px rgb(15 23 42 / 3%)',
      '--shadow-2': '0 8px 24px rgb(15 23 42 / 8%), 0 2px 6px rgb(15 23 42 / 5%)',
      '--shadow-3': '0 16px 40px rgb(15 23 42 / 14%), 0 3px 10px rgb(15 23 42 / 8%)',
      '--radius-lg': '10px',
      '--radius-xl': '14px',
      '--shell-sidebar-expanded': '256px',
      '--ease-enter': 'cubic-bezier(0.22, 1, 0.36, 1)',
      '--ease-exit': 'cubic-bezier(0.22, 1, 0.36, 1)',
      '--ease-move': 'cubic-bezier(0.22, 1, 0.36, 1)',
      '--font-family':
        "'Inter', ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      '--font-family-mono': "'SFMono-Regular', Consolas, 'Liberation Mono', monospace",
    });

    expect(DARK_TOKENS).toMatchObject({
      '--color-bg': '#0c0c0e',
      '--color-canvas': '#111114',
      '--color-surface': '#18181b',
      '--color-surface-raised': '#1e1e21',
      '--color-surface-hover': '#27272a',
      '--color-surface-selected': '#2d2d31',
      '--color-text-strong': '#fafafa',
      '--color-text': '#fafafa',
      '--color-text-muted': '#9f9fa9',
      '--color-text-disabled': '#7f7f89',
      '--color-text-faint-base': '#7f7f89',
      '--color-placeholder': '#9f9fa9',
      '--color-control-disabled-text': '#8b8b94',
      '--color-border-subtle': 'rgb(255 255 255 / 6%)',
      '--color-border': 'rgb(255 255 255 / 10%)',
      '--color-input-border-base': 'rgb(255 255 255 / 15%)',
      '--color-input-border': '#71717a',
      '--color-input-border-hover': '#8b8b94',
      '--color-primary': '#e4e4e7',
      '--color-primary-contrast': '#18181b',
      '--color-brand-base': '#4390ee',
      '--color-brand-soft-base': 'rgb(67 144 238 / 14%)',
      '--color-surface-pressed': '#3f3f46',
      '--color-success-base': '#4aa651',
      '--color-success-soft-base': 'rgb(74 166 81 / 14%)',
      '--color-warning-base': '#cb9400',
      '--color-warning-soft-base': 'rgb(203 148 0 / 15%)',
      '--color-danger-base': '#ff6467',
      '--color-danger-soft-base': 'rgb(255 100 103 / 14%)',
      '--color-info-base': '#0f92f7',
      '--color-info-soft-base': 'rgb(15 146 247 / 14%)',
      '--color-category-violet': '#a78bfa',
      '--color-category-orange': '#fb923c',
      '--shadow-1': '0 1px 2px rgb(0 0 0 / 20%), 0 1px 1px rgb(0 0 0 / 16%)',
      '--shadow-2': '0 10px 28px rgb(0 0 0 / 30%), 0 2px 8px rgb(0 0 0 / 18%)',
      '--shadow-3': '0 20px 48px rgb(0 0 0 / 46%), 0 4px 12px rgb(0 0 0 / 28%)',
    });

    expect(LIGHT_TOKENS['--color-primary']).not.toBe(LIGHT_TOKENS['--color-accent']);
    expect(LIGHT_TOKENS['--color-warning-base']).not.toBe(LIGHT_TOKENS['--color-warning-fg']);
    expect(LIGHT_TOKENS['--color-input-border-base']).not.toBe(
      LIGHT_TOKENS['--color-input-border'],
    );
    expect(LIGHT_TOKENS['--color-text-faint-base']).not.toBe(LIGHT_TOKENS['--color-placeholder']);
    expect(LIGHT_TOKENS['--color-text-disabled']).not.toBe(
      LIGHT_TOKENS['--color-control-disabled-text'],
    );
  });

  it('MES-108 主按钮、输入边界和禁用文字消费各自的语义 token', () => {
    expect(componentsCss).toMatch(
      /\.mesh-button--primary\s*\{[^}]*background-color:\s*var\(--color-primary\);[^}]*color:\s*var\(--color-primary-contrast\);/s,
    );
    expect(componentsCss).toMatch(
      /\.mesh-field__control\s*\{[^}]*border:\s*1px solid var\(--color-input-border\);/s,
    );
    expect(componentsCss).toMatch(
      /\.mesh-field__control:hover:not\(:disabled, :focus\)\s*\{[^}]*border-color:\s*var\(--color-input-border-hover\);/s,
    );
    expect(componentsCss).toMatch(
      /\.mesh-field__control:disabled\s*\{[^}]*color:\s*var\(--color-control-disabled-text\);/s,
    );
    expect(componentsCss).toMatch(
      /\.mesh-field__control::placeholder\s*\{[^}]*color:\s*var\(--color-placeholder\);/s,
    );
    expect(baseCss).toMatch(
      /input::placeholder,\s*textarea::placeholder\s*\{[^}]*color:\s*var\(--color-placeholder\);[^}]*opacity:\s*1;/s,
    );
    expect(componentsCss).toMatch(
      /\.mesh-button:disabled\s*\{[^}]*background-color:\s*var\(--color-surface-pressed\);[^}]*color:\s*var\(--color-control-disabled-text\);/s,
    );
    expect(componentsCss).toMatch(
      /\.mesh-button:active:not\(:disabled\)\s*\{[^}]*transform:\s*translateY\(1px\);/s,
    );
  });

  it('MES-108 中性 primary 仅由共享主操作消费，品牌、选中、状态与链接使用 accent', () => {
    const designRoot = path.resolve(process.cwd(), 'src');
    const forbidden = cssFiles(designRoot)
      .map((file) => ({ file, normalized: file.split(path.sep).join('/') }))
      .filter(({ normalized }) => !normalized.endsWith('/design/components/components.css'))
      .filter(({ normalized }) => !/\/design\/tokens(?:-dark|-print)?\.css$/u.test(normalized))
      .flatMap(({ file }) => {
        const source = readFileSync(file, 'utf8');
        return source.includes('var(--color-primary)') ||
          source.includes('var(--color-primary-contrast)')
          ? [path.relative(process.cwd(), file)]
          : [];
      });
    expect(forbidden).toEqual([]);
  });

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
