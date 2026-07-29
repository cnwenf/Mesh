/**
 * 语义 token 单一事实源(README §6.12 主题契约)。
 *
 * `tokens.css`(亮色,`:root`)、`tokens-dark.css`(暗色,`:root[data-theme='dark']`)
 * 与 `tokens-print.css`(打印强制亮色)均由 `scripts/gen-tokens.mjs` 从本文件生成
 * (文件首行带「禁止手改」标记)。新增/修改 token 只改本文件,再 `npm run gen:tokens`;
 * 测试解析生成产物断言镜像关系,作为第二道防线,任何 CSS↔TS 漂移即刻失败。
 *
 * 契约要点:
 * - 一切颜色经语义 token 引用,组件禁止硬编码色值;
 * - 暗色模式 = 整组 token 取值替换(非逐组件改写),颜色 token 一一对应;
 * - 亮/暗两套均满足 WCAG 2.1 AA,由对比度关卡自证(见 AA_CONTRAST_PAIRS):
 *   正文 ≥4.5:1、大文本与图形元件 ≥3:1。
 */

import type { ContrastKind } from './contrast';

/** token 分组(供生成器输出分组注释;顺序即 CSS 中的声明顺序)。 */
export interface TokenGroup {
  readonly title: string;
  readonly tokens: Readonly<Record<string, string>>;
}

/** 亮色语义 token 分组(`:root`)。 */
export const LIGHT_TOKEN_GROUPS: ReadonlyArray<TokenGroup> = [
  {
    title: '表面与文本',
    tokens: {
      '--color-bg': '#ffffff',
      '--color-surface': '#f9fafb',
      '--color-surface-raised': '#ffffff',
      '--color-surface-hover': '#f3f4f6',
      '--color-surface-sunken': '#f1f5f9',
      '--color-text': '#111827',
      '--color-text-muted': '#4b5563',
      '--color-border': '#d1d5db',
    },
  },
  {
    title: '品牌主色',
    tokens: {
      '--color-primary': '#1d4ed8',
      '--color-primary-contrast': '#ffffff',
    },
  },
  {
    title: '状态色(文本/底色两用,与 *-contrast 配对均 ≥4.5:1;-bg 为浅底变体)',
    tokens: {
      '--color-danger': '#b91c1c',
      '--color-danger-contrast': '#ffffff',
      '--color-danger-bg': '#fee2e2',
      '--color-warn': '#92400e',
      '--color-warn-contrast': '#ffffff',
      '--color-warn-bg': '#fef3c7',
      '--color-success': '#15803d',
      '--color-success-contrast': '#ffffff',
      '--color-success-bg': '#dcfce7',
      '--color-info': '#075985',
      '--color-info-contrast': '#ffffff',
      '--color-info-bg': '#e0f2fe',
      '--color-primary-bg': '#dbeafe',
    },
  },
  {
    title: '选区/强调(::selection 与 <mark>,亮/暗各定义,禁浏览器默认色)',
    tokens: {
      '--color-selection-bg': '#bfdbfe',
      '--color-selection-text': '#111827',
      '--color-mark-bg': '#fde68a',
      '--color-mark-text': '#111827',
    },
  },
  {
    title: '骨架屏(中性 skeleton,与主题无关的加载占位)',
    tokens: {
      '--color-skeleton-base': '#7d8590',
      '--color-skeleton-highlight': '#adb5bd',
    },
  },
  {
    title: '代码高亮(markdown 代码块,亮/暗各一套,各前景对 code-bg ≥4.5:1)',
    tokens: {
      '--color-code-bg': '#f3f4f6',
      '--color-code-text': '#111827',
      '--color-code-keyword': '#6d28d9',
      '--color-code-string': '#166534',
      '--color-code-comment': '#4b5563',
    },
  },
  {
    title: '焦点环',
    tokens: {
      '--color-focus-ring': '#2563eb',
    },
  },
  {
    title: '遮罩',
    tokens: {
      '--color-scrim': 'rgba(15, 23, 42, 0.45)',
    },
  },
  {
    title: '间距刻度(4/8/12/16/24/32)',
    tokens: {
      '--space-1': '4px',
      '--space-2': '8px',
      '--space-3': '12px',
      '--space-4': '16px',
      '--space-5': '24px',
      '--space-6': '32px',
    },
  },
  {
    title: '圆角',
    tokens: {
      '--radius-sm': '4px',
      '--radius-md': '8px',
      '--radius-lg': '12px',
    },
  },
  {
    title: '字号',
    tokens: {
      '--font-size-sm': '0.875rem',
      '--font-size-md': '1rem',
      '--font-size-lg': '1.25rem',
    },
  },
  {
    title: '字体',
    tokens: {
      '--font-family':
        "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
    },
  },
  {
    title: '阴影',
    tokens: {
      '--shadow-raised': '0 4px 16px rgba(15, 23, 42, 0.16)',
    },
  },
  {
    title: '动效时长',
    tokens: {
      '--duration-fast': '120ms',
      '--duration-slow': '300ms',
    },
  },
];

/** 亮色语义 token(`:root`),由 LIGHT_TOKEN_GROUPS 展平而来(键序与分组一致)。 */
export const LIGHT_TOKENS: Readonly<Record<string, string>> = Object.freeze(
  Object.assign({}, ...LIGHT_TOKEN_GROUPS.map((group) => group.tokens)) as Record<string, string>,
);

/** 暗色语义 token(`:root[data-theme='dark']`,整组替换颜色 token) */
export const DARK_TOKENS: Readonly<Record<string, string>> = Object.freeze({
  '--color-bg': '#0f172a',
  '--color-surface': '#1e293b',
  '--color-surface-raised': '#334155',
  '--color-surface-hover': '#263449',
  '--color-surface-sunken': '#162032',
  '--color-text': '#f8fafc',
  '--color-text-muted': '#94a3b8',
  '--color-border': '#334155',
  '--color-primary': '#3b82f6',
  '--color-primary-contrast': '#0f172a',
  '--color-danger': '#f87171',
  '--color-danger-contrast': '#450a0a',
  '--color-danger-bg': '#450a0a',
  '--color-warn': '#fbbf24',
  '--color-warn-contrast': '#451a03',
  '--color-warn-bg': '#451a03',
  '--color-success': '#4ade80',
  '--color-success-contrast': '#052e16',
  '--color-success-bg': '#052e16',
  '--color-info': '#38bdf8',
  '--color-info-contrast': '#082f49',
  '--color-info-bg': '#082f49',
  '--color-primary-bg': '#101828',
  '--color-selection-bg': '#1e3a8a',
  '--color-selection-text': '#f8fafc',
  '--color-mark-bg': '#78350f',
  '--color-mark-text': '#fef3c7',
  '--color-skeleton-base': '#64748b',
  '--color-skeleton-highlight': '#94a3b8',
  '--color-code-bg': '#0b1220',
  '--color-code-text': '#e2e8f0',
  '--color-code-keyword': '#c4b5fd',
  '--color-code-string': '#86efac',
  '--color-code-comment': '#94a3b8',
  '--color-focus-ring': '#60a5fa',
  '--color-scrim': 'rgba(0, 0, 0, 0.6)',
  '--shadow-raised': '0 4px 16px rgba(0, 0, 0, 0.55)',
});

/**
 * 对比度配对:元素为 token 名(见 LIGHT_TOKENS / DARK_TOKENS),
 * `kind` 缺省 'text'(阈值 4.5:1);'large-text'/'graphic' 阈值 3:1。
 * 两套主题下逐对校验,任一不达标即 CI 失败。
 */
export interface ContrastPair {
  readonly fg: string;
  readonly bg: string;
  readonly kind?: ContrastKind;
}

/**
 * 两套主题下均须满足 WCAG 2.1 AA 的配对登记表。
 * 新增颜色 token 须先在此登记对应配对再合入。
 */
export const AA_CONTRAST_PAIRS: ReadonlyArray<ContrastPair> = [
  // 正文/弱化文本 对 背景与常规表面(text 组,≥4.5:1)
  { fg: '--color-text', bg: '--color-bg' },
  { fg: '--color-text-muted', bg: '--color-bg' },
  { fg: '--color-text', bg: '--color-surface' },
  { fg: '--color-text-muted', bg: '--color-surface' },
  { fg: '--color-text', bg: '--color-surface-raised' },
  // 悬停/下沉表面上的正文(text 组)
  { fg: '--color-text', bg: '--color-surface-hover' },
  { fg: '--color-text', bg: '--color-surface-sunken' },
  // 状态色作文本/图形 对 背景(text 组)
  { fg: '--color-primary', bg: '--color-bg' },
  { fg: '--color-danger', bg: '--color-bg' },
  { fg: '--color-warn', bg: '--color-bg' },
  { fg: '--color-success', bg: '--color-bg' },
  { fg: '--color-info', bg: '--color-bg' },
  // 状态色作底时其对比文本(text 组)
  { fg: '--color-primary-contrast', bg: '--color-primary' },
  { fg: '--color-danger-contrast', bg: '--color-danger' },
  { fg: '--color-warn-contrast', bg: '--color-warn' },
  { fg: '--color-success-contrast', bg: '--color-success' },
  { fg: '--color-info-contrast', bg: '--color-info' },
  // 状态浅底上的状态文本(text 组,浅底深字/暗底亮字)
  { fg: '--color-danger', bg: '--color-danger-bg' },
  { fg: '--color-warn', bg: '--color-warn-bg' },
  { fg: '--color-success', bg: '--color-success-bg' },
  { fg: '--color-info', bg: '--color-info-bg' },
  { fg: '--color-primary', bg: '--color-primary-bg' },
  // 选区/强调与代码高亮(text 组)
  { fg: '--color-selection-text', bg: '--color-selection-bg' },
  { fg: '--color-mark-text', bg: '--color-mark-bg' },
  { fg: '--color-code-text', bg: '--color-code-bg' },
  { fg: '--color-code-keyword', bg: '--color-code-bg' },
  { fg: '--color-code-string', bg: '--color-code-bg' },
  { fg: '--color-code-comment', bg: '--color-code-bg' },
  // 大文本组(≥24px,或 ≥18.66px 加粗;阈值 3:1)
  { fg: '--color-primary', bg: '--color-surface', kind: 'large-text' },
  { fg: '--color-text-muted', bg: '--color-surface-raised', kind: 'large-text' },
  // 图形元件组(阈值 3:1)
  { fg: '--color-focus-ring', bg: '--color-bg', kind: 'graphic' },
  { fg: '--color-skeleton-base', bg: '--color-bg', kind: 'graphic' },
];
