/**
 * 语义 token 单一事实源(README §6.12 主题契约)。
 *
 * `tokens.css`(亮色,`:root`)与 `tokens-dark.css`(暗色,`:root[data-theme='dark']`)
 * 必须与本文件逐项一致 —— 测试解析 CSS 文件断言镜像关系,任何 CSS↔TS 漂移即刻失败。
 *
 * 契约要点:
 * - 一切颜色经语义 token 引用,组件禁止硬编码色值;
 * - 暗色模式 = 整组 token 取值替换(非逐组件改写),颜色 token 一一对应;
 * - 亮/暗两套均满足 WCAG 2.1 AA(4.5:1),由 contrast 测试自证(见 AA_CONTRAST_PAIRS)。
 */

/** 亮色语义 token(`:root`) */
export const LIGHT_TOKENS: Readonly<Record<string, string>> = {
  // 表面与文本
  '--color-bg': '#ffffff',
  '--color-surface': '#f9fafb',
  '--color-surface-raised': '#ffffff',
  '--color-text': '#111827',
  '--color-text-muted': '#4b5563',
  '--color-border': '#d1d5db',
  // 品牌主色
  '--color-primary': '#1d4ed8',
  '--color-primary-contrast': '#ffffff',
  // 状态色(文本/底色两用;与 *-contrast 配对均 ≥4.5:1)
  '--color-danger': '#b91c1c',
  '--color-danger-contrast': '#ffffff',
  '--color-warn': '#92400e',
  '--color-warn-contrast': '#ffffff',
  '--color-success': '#15803d',
  '--color-success-contrast': '#ffffff',
  '--color-info': '#075985',
  '--color-info-contrast': '#ffffff',
  // 焦点环
  '--color-focus-ring': '#2563eb',
  // 遮罩
  '--color-scrim': 'rgba(15, 23, 42, 0.45)',
  // 间距刻度(4/8/12/16/24/32)
  '--space-1': '4px',
  '--space-2': '8px',
  '--space-3': '12px',
  '--space-4': '16px',
  '--space-5': '24px',
  '--space-6': '32px',
  // 圆角
  '--radius-sm': '4px',
  '--radius-md': '8px',
  '--radius-lg': '12px',
  // 字号
  '--font-size-sm': '0.875rem',
  '--font-size-md': '1rem',
  '--font-size-lg': '1.25rem',
  // 字体
  '--font-family':
    "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
  // 阴影
  '--shadow-raised': '0 4px 16px rgba(15, 23, 42, 0.16)',
  // 动效时长
  '--duration-fast': '120ms',
  '--duration-slow': '300ms',
};

/** 暗色语义 token(`:root[data-theme='dark']`,整组替换颜色 token) */
export const DARK_TOKENS: Readonly<Record<string, string>> = {
  '--color-bg': '#0f172a',
  '--color-surface': '#1e293b',
  '--color-surface-raised': '#334155',
  '--color-text': '#f8fafc',
  '--color-text-muted': '#94a3b8',
  '--color-border': '#334155',
  '--color-primary': '#3b82f6',
  '--color-primary-contrast': '#0f172a',
  '--color-danger': '#f87171',
  '--color-danger-contrast': '#450a0a',
  '--color-warn': '#fbbf24',
  '--color-warn-contrast': '#451a03',
  '--color-success': '#4ade80',
  '--color-success-contrast': '#052e16',
  '--color-info': '#38bdf8',
  '--color-info-contrast': '#082f49',
  '--color-focus-ring': '#60a5fa',
  '--color-scrim': 'rgba(0, 0, 0, 0.6)',
  '--shadow-raised': '0 4px 16px rgba(0, 0, 0, 0.55)',
};

/**
 * 两套主题下均须满足 WCAG 2.1 AA(4.5:1)的文本/底色配对。
 * 元素为 token 名(见 LIGHT_TOKENS / DARK_TOKENS),测试按主题代入实际值校验。
 */
export const AA_CONTRAST_PAIRS: ReadonlyArray<readonly [string, string]> = [
  // 正文/弱化文本 对 背景与常规表面
  ['--color-text', '--color-bg'],
  ['--color-text-muted', '--color-bg'],
  ['--color-text', '--color-surface'],
  ['--color-text-muted', '--color-surface'],
  ['--color-text', '--color-surface-raised'],
  // 状态色作文本/图形 对 背景
  ['--color-primary', '--color-bg'],
  ['--color-danger', '--color-bg'],
  ['--color-warn', '--color-bg'],
  ['--color-success', '--color-bg'],
  ['--color-info', '--color-bg'],
  // 状态色作底时其对比文本色
  ['--color-primary-contrast', '--color-primary'],
  ['--color-danger-contrast', '--color-danger'],
  ['--color-warn-contrast', '--color-warn'],
  ['--color-success-contrast', '--color-success'],
  ['--color-info-contrast', '--color-info'],
];
