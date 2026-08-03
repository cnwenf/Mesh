/**
 * 语义 token 单一事实源(README §6.12 主题契约;design-quality.md §5/§6 设计令牌与排版)。
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
 *
 * 三层令牌(design-quality.md §5.1):
 * - Reference(原始色阶)不进本文件,业务一律不得直接引用;
 * - Semantic(本文件主体):`--color-*`、`--space-*`、`--radius-*` 等跨组件语义;
 * - Component:仅组件确有稳定差异时定义(如按钮 hover 用的 `--color-danger-hover`)。
 *
 * 兼容迁移(design-quality.md §11.3):旧 token(primary/danger/warn/success/info 系)
 * 作为新语义 token 的别名保留一个发布周期,随后删除并经静态扫描阻止回归;
 * 别名组取值与新 token 一致,使存量样式随令牌升级同步换装。
 */

import type { ContrastKind } from './contrast';

/**
 * 全局视口模式边界(design-quality.md §8.1)。
 *
 * CSS 的 @media/@container 不能可靠引用 custom property，因此样式表仍会出现
 * 数字边界；`scripts/check-responsive-contract.mjs` 会从本常量读取允许值并扫描
 * 全部业务 CSS，任何 359/720/800 等近似断点都会令 CI 失败。
 */
export const VIEWPORT_BREAKPOINTS = Object.freeze({
  compact: Object.freeze({ min: 0, max: 599 }),
  medium: Object.freeze({ min: 600, max: 1023 }),
  wide: Object.freeze({ min: 1024, max: 1439 }),
  xwide: Object.freeze({ min: 1440 }),
});

/** token 分组(供生成器输出分组注释;顺序即 CSS 中的声明顺序)。 */
export interface TokenGroup {
  readonly title: string;
  readonly tokens: Readonly<Record<string, string>>;
}

/** 亮色语义 token 分组(`:root`)。 */
export const LIGHT_TOKEN_GROUPS: ReadonlyArray<TokenGroup> = [
  {
    title: '表面层级(design-quality.md §5.2:canvas→surface→raised 三层 + 交互表面)',
    tokens: {
      '--color-canvas': '#f7f8fa',
      '--color-bg': '#f7f8fa',
      '--color-surface': '#ffffff',
      '--color-surface-subtle': '#f1f3f5',
      '--color-surface-raised': '#ffffff',
      '--color-surface-hover': '#f4f5f7',
      '--color-surface-pressed': '#e9ecf0',
      '--color-surface-selected': '#eef2ff',
      '--color-surface-sunken': '#f1f5f9',
    },
  },
  {
    title: '文本层级(strong/text/muted/disabled 四级)',
    tokens: {
      '--color-text-strong': '#16181d',
      '--color-text': '#2b2f36',
      // 起始值 #667085 在 surface-subtle 上为 4.48:1,低于 AA;
      // 按 §5.2「必要时只调整值、不改变语义名」加深至 4.96:1。
      '--color-text-muted': '#5f6980',
      '--color-text-disabled': '#98a2b3',
    },
  },
  {
    title: '边界(subtle/border/strong 三级)',
    tokens: {
      '--color-border-subtle': '#eaecf0',
      '--color-border': '#d7dce3',
      '--color-border-strong': '#b8c0cc',
    },
  },
  {
    title: '品牌强调色(accent 系,旧 primary 系别名见下)',
    tokens: {
      '--color-accent': '#4f46e5',
      '--color-accent-hover': '#4338ca',
      '--color-accent-pressed': '#3730a3',
      '--color-accent-soft': '#eef2ff',
      '--color-accent-contrast': '#ffffff',
    },
  },
  {
    title: '旧主色别名(迁移期保留一个发布周期,取值与 accent 系一致)',
    tokens: {
      '--color-primary': '#4f46e5',
      '--color-primary-contrast': '#ffffff',
      '--color-primary-bg': '#eef2ff',
    },
  },
  {
    title: '状态色三元组(fg/bg/border;颜色不作唯一信号,组件须配图标+文案)',
    tokens: {
      '--color-success-fg': '#15803d',
      '--color-success-bg': '#dcfce7',
      '--color-success-border': '#86efac',
      '--color-warning-fg': '#92400e',
      '--color-warning-bg': '#fef3c7',
      '--color-warning-border': '#fcd34d',
      '--color-danger-fg': '#b91c1c',
      '--color-danger-bg': '#fee2e2',
      '--color-danger-border': '#fca5a5',
      '--color-info-fg': '#075985',
      '--color-info-bg': '#e0f2fe',
      '--color-info-border': '#7dd3fc',
      '--color-neutral-fg': '#475467',
      '--color-neutral-bg': '#f2f4f7',
      '--color-neutral-border': '#d0d5dd',
    },
  },
  {
    title: '危险按钮交互态(组件级令牌:default/hover/pressed)',
    tokens: {
      '--color-danger-hover': '#991b1b',
      '--color-danger-pressed': '#7f1d1d',
    },
  },
  {
    title: '旧状态色别名(迁移期保留,取值与三元组 fg/bg 一致;-bg 键即三元组同名键,不重复声明)',
    tokens: {
      '--color-danger': '#b91c1c',
      '--color-danger-contrast': '#ffffff',
      '--color-warn': '#92400e',
      '--color-warn-contrast': '#ffffff',
      '--color-warn-bg': '#fef3c7',
      '--color-success': '#15803d',
      '--color-success-contrast': '#ffffff',
      '--color-info': '#075985',
      '--color-info-contrast': '#ffffff',
    },
  },
  {
    title: '头像稳定配色(姓名 hash 取色,h0–h7 八组;亮/暗各自校准非简单反色)',
    tokens: {
      '--color-avatar-h0-bg': '#4f46e5',
      '--color-avatar-h0-fg': '#ffffff',
      '--color-avatar-h1-bg': '#0369a1',
      '--color-avatar-h1-fg': '#ffffff',
      '--color-avatar-h2-bg': '#047857',
      '--color-avatar-h2-fg': '#ffffff',
      '--color-avatar-h3-bg': '#b45309',
      '--color-avatar-h3-fg': '#ffffff',
      '--color-avatar-h4-bg': '#be123c',
      '--color-avatar-h4-fg': '#ffffff',
      '--color-avatar-h5-bg': '#7c3aed',
      '--color-avatar-h5-fg': '#ffffff',
      '--color-avatar-h6-bg': '#0f766e',
      '--color-avatar-h6-fg': '#ffffff',
      '--color-avatar-h7-bg': '#475467',
      '--color-avatar-h7-fg': '#ffffff',
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
    title: '遮罩(design-quality.md §5.2)',
    tokens: {
      '--color-scrim': 'rgba(15, 23, 42, 0.52)',
    },
  },
  {
    title:
      '间距刻度(design-quality.md §5.3;存量 space-1..6 保持原值,新增 0/0-5/1-5/8/10/12/16(§5.3 表中 0_5/1_5 记法的 kebab 形式))',
    tokens: {
      '--space-0': '0px',
      '--space-0-5': '2px',
      '--space-1': '4px',
      '--space-1-5': '6px',
      '--space-2': '8px',
      '--space-3': '12px',
      '--space-4': '16px',
      '--space-5': '24px',
      '--space-6': '32px',
      '--space-8': '32px',
      '--space-10': '40px',
      '--space-12': '48px',
      '--space-16': '64px',
    },
  },
  {
    title: '布局变量(外壳/页边距/内容宽度,主题无关)',
    tokens: {
      '--shell-sidebar-expanded': '240px',
      '--shell-sidebar-collapsed': '64px',
      '--shell-topbar-offset': '69px',
      '--shell-mobile-nav-offset': 'env(safe-area-inset-bottom)',
      '--page-gutter-compact': '16px',
      '--page-gutter-medium': '24px',
      '--page-gutter-wide': '32px',
      '--content-readable': '720px',
      '--content-form': '640px',
      '--content-standard': '1120px',
      '--content-wide': '1440px',
    },
  },
  {
    title: '圆角(design-quality.md §5.4:xs/sm/md/lg/xl/full 六档)',
    tokens: {
      '--radius-xs': '4px',
      '--radius-sm': '6px',
      '--radius-md': '8px',
      '--radius-lg': '12px',
      '--radius-xl': '16px',
      '--radius-full': '999px',
    },
  },
  {
    title: '字号(旧三档兼容)',
    tokens: {
      '--font-size-sm': '0.875rem',
      '--font-size-md': '1rem',
      '--font-size-lg': '1.25rem',
    },
  },
  {
    title: 'Type scale 字号(design-quality.md §6.2:display→micro 十一档)',
    tokens: {
      '--font-size-display-lg': '2.25rem',
      '--font-size-display-sm': '1.875rem',
      '--font-size-title-1': '1.5rem',
      '--font-size-title-2': '1.25rem',
      '--font-size-title-3': '1.125rem',
      '--font-size-body-lg': '1rem',
      '--font-size-body': '0.875rem',
      '--font-size-body-sm': '0.8125rem',
      '--font-size-caption': '0.75rem',
      '--font-size-micro': '0.6875rem',
      // 表单控件专用:iOS 聚焦缩放门槛 16px(§6.2),不随桌面密度缩小。
      '--font-size-control': '1rem',
    },
  },
  {
    title: 'Type scale 行高(与字号一一对应)',
    tokens: {
      '--line-height-display-lg': '2.75rem',
      '--line-height-display-sm': '2.375rem',
      '--line-height-title-1': '2rem',
      '--line-height-title-2': '1.75rem',
      '--line-height-title-3': '1.625rem',
      '--line-height-body-lg': '1.625rem',
      '--line-height-body': '1.375rem',
      '--line-height-body-sm': '1.25rem',
      '--line-height-caption': '1.125rem',
      '--line-height-micro': '1rem',
    },
  },
  {
    title: '字重(展示标题 650 需可变字体;回退环境按 600 渲染)',
    tokens: {
      '--font-weight-regular': '400',
      '--font-weight-medium': '500',
      '--font-weight-semibold': '600',
      '--font-weight-display': '650',
    },
  },
  {
    title:
      '字体配对(design-quality.md §6.1:Display=Manrope+CJK,UI=Inter+CJK,等宽=JetBrains Mono;自托管字体文件随页面批次加载,未加载时回退系统栈)',
    tokens: {
      '--font-family':
        "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', 'Noto Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
      '--font-family-display':
        "'Manrope', 'Inter', system-ui, -apple-system, 'Segoe UI', 'Noto Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
      '--font-family-mono':
        "'JetBrains Mono', 'SFMono-Regular', ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace",
    },
  },
  {
    title: '阴影(design-quality.md §5.4:1 轻浮起 / 2 浮层 / 3 对话框;暗色配合边框使用)',
    tokens: {
      '--shadow-1': '0 1px 2px rgba(15, 23, 42, 0.06), 0 1px 3px rgba(15, 23, 42, 0.1)',
      '--shadow-2': '0 2px 4px rgba(15, 23, 42, 0.06), 0 4px 8px rgba(15, 23, 42, 0.08)',
      '--shadow-3': '0 4px 8px rgba(15, 23, 42, 0.08), 0 12px 32px rgba(15, 23, 42, 0.16)',
      '--shadow-raised': '0 4px 16px rgba(15, 23, 42, 0.16)',
    },
  },
  {
    title:
      '动效时长(design-quality.md §5.5:instant/fast/standard/deliberate/slow;旧 duration-* 兼容)',
    tokens: {
      '--motion-instant': '0ms',
      '--motion-fast': '100ms',
      '--motion-standard': '160ms',
      '--motion-deliberate': '240ms',
      '--motion-slow': '360ms',
      '--duration-fast': '120ms',
      '--duration-slow': '300ms',
    },
  },
  {
    title: '动效缓动(进入/退出/移动三条标准曲线)',
    tokens: {
      '--ease-enter': 'cubic-bezier(0.2, 0.8, 0.2, 1)',
      '--ease-exit': 'cubic-bezier(0.4, 0, 1, 1)',
      '--ease-move': 'cubic-bezier(0.2, 0, 0, 1)',
    },
  },
  {
    title: 'z-index 层级(base/sticky/dropdown/overlay/toast,禁业务自造近似值)',
    tokens: {
      '--z-base': '0',
      '--z-sticky': '100',
      '--z-dropdown': '200',
      '--z-overlay': '300',
      '--z-toast': '400',
    },
  },
];

/** 亮色语义 token(`:root`),由 LIGHT_TOKEN_GROUPS 展平而来(键序与分组一致)。 */
export const LIGHT_TOKENS: Readonly<Record<string, string>> = Object.freeze(
  Object.assign({}, ...LIGHT_TOKEN_GROUPS.map((group) => group.tokens)) as Record<string, string>,
);

/** 暗色语义 token(`:root[data-theme='dark']`,整组替换颜色 token;非颜色 token 主题无关不覆盖)。 */
export const DARK_TOKENS: Readonly<Record<string, string>> = Object.freeze({
  '--color-canvas': '#0f1115',
  '--color-bg': '#0f1115',
  '--color-surface': '#171a21',
  '--color-surface-subtle': '#1d212a',
  '--color-surface-raised': '#222732',
  '--color-surface-hover': '#252b36',
  '--color-surface-pressed': '#2b3240',
  '--color-surface-selected': '#24263f',
  '--color-surface-sunken': '#162032',
  '--color-text-strong': '#f4f6f8',
  '--color-text': '#d7dbe0',
  '--color-text-muted': '#9aa3af',
  '--color-text-disabled': '#697386',
  '--color-border-subtle': '#252b35',
  '--color-border': '#343c49',
  '--color-border-strong': '#4a5565',
  '--color-accent': '#818cf8',
  '--color-accent-hover': '#a5b4fc',
  '--color-accent-pressed': '#6366f1',
  '--color-accent-soft': '#24263f',
  '--color-accent-contrast': '#10131a',
  '--color-primary': '#818cf8',
  '--color-primary-contrast': '#10131a',
  '--color-primary-bg': '#24263f',
  '--color-success-fg': '#4ade80',
  '--color-success-bg': '#052e16',
  '--color-success-border': '#14532d',
  '--color-warning-fg': '#fbbf24',
  '--color-warning-bg': '#451a03',
  '--color-warning-border': '#78350f',
  '--color-danger-fg': '#f87171',
  '--color-danger-bg': '#450a0a',
  '--color-danger-border': '#7f1d1d',
  '--color-info-fg': '#38bdf8',
  '--color-info-bg': '#082f49',
  '--color-info-border': '#0c4a6e',
  '--color-neutral-fg': '#98a2b3',
  '--color-neutral-bg': '#252b36',
  '--color-neutral-border': '#343c49',
  '--color-danger-hover': '#fca5a5',
  '--color-danger-pressed': '#fecaca',
  // 旧状态色别名(-bg 键即三元组同名键,不重复声明)
  '--color-danger': '#f87171',
  '--color-danger-contrast': '#450a0a',
  '--color-warn': '#fbbf24',
  '--color-warn-contrast': '#451a03',
  '--color-warn-bg': '#451a03',
  '--color-success': '#4ade80',
  '--color-success-contrast': '#052e16',
  '--color-info': '#38bdf8',
  '--color-info-contrast': '#082f49',
  '--color-avatar-h0-bg': '#312e81',
  '--color-avatar-h0-fg': '#c7d2fe',
  '--color-avatar-h1-bg': '#0c4a6e',
  '--color-avatar-h1-fg': '#bae6fd',
  '--color-avatar-h2-bg': '#064e3b',
  '--color-avatar-h2-fg': '#a7f3d0',
  '--color-avatar-h3-bg': '#78350f',
  '--color-avatar-h3-fg': '#fde68a',
  '--color-avatar-h4-bg': '#881337',
  '--color-avatar-h4-fg': '#fecdd3',
  '--color-avatar-h5-bg': '#4c1d95',
  '--color-avatar-h5-fg': '#ddd6fe',
  '--color-avatar-h6-bg': '#134e4a',
  '--color-avatar-h6-fg': '#99f6e4',
  '--color-avatar-h7-bg': '#334155',
  '--color-avatar-h7-fg': '#cbd5e1',
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
  '--color-focus-ring': '#93c5fd',
  '--color-scrim': 'rgba(0, 0, 0, 0.72)',
  // 暗色阴影加深;组件在暗色下另以 border 补层级(§5.4:不能只靠黑色阴影)。
  '--shadow-1': '0 1px 2px rgba(0, 0, 0, 0.4), 0 1px 3px rgba(0, 0, 0, 0.5)',
  '--shadow-2': '0 2px 4px rgba(0, 0, 0, 0.4), 0 4px 8px rgba(0, 0, 0, 0.45)',
  '--shadow-3': '0 4px 8px rgba(0, 0, 0, 0.5), 0 12px 32px rgba(0, 0, 0, 0.6)',
  '--shadow-raised': '0 4px 16px rgba(0, 0, 0, 0.55)',
});

/**
 * Appica UI 的原始 token → Mesh 语义 token 适配层(MES-158)。
 *
 * Appica 组件内部使用 `--foreground` / `--background` / `--primary` 等
 * 原始变量。这里仅做别名映射，不建立第二套颜色值；亮暗切换仍由上方
 * LIGHT_TOKENS / DARK_TOKENS 整组替换。`scripts/gen-tokens.mjs` 将本表生成
 * `appica-tokens.css`，确保组件库的色板、字阶、间距、圆角与阴影都以 Mesh
 * token 为唯一事实源。
 */
export const APPICA_TOKEN_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  '--foreground': 'var(--color-text)',
  '--foreground-subtle': 'var(--color-text-disabled)',
  '--foreground-muted': 'var(--color-text-muted)',
  '--foreground-strong': 'var(--color-text)',
  '--foreground-emphasis': 'var(--color-text-strong)',
  '--foreground-intense': 'var(--color-text-strong)',
  '--foreground-inverse': 'var(--color-accent-contrast)',
  '--background': 'var(--color-surface)',
  '--background-subtle': 'var(--color-surface-subtle)',
  '--background-muted': 'var(--color-surface-hover)',
  '--background-strong': 'var(--color-surface-pressed)',
  '--background-inverse': 'var(--color-text-strong)',
  '--border': 'var(--color-border)',
  '--border-muted': 'var(--color-border-subtle)',
  '--border-strong': 'var(--color-border-strong)',
  '--border-emphasis': 'var(--color-text-disabled)',
  '--border-intense': 'var(--color-text-muted)',
  '--border-inverse': 'var(--color-surface)',
  '--border-overlay': 'var(--color-border)',
  '--primary': 'var(--color-accent)',
  '--primary-subtle': 'var(--color-accent-soft)',
  '--primary-soft': 'var(--color-accent-soft)',
  '--primary-muted': 'var(--color-accent-hover)',
  '--primary-strong': 'var(--color-accent-pressed)',
  '--primary-foreground': 'var(--color-accent-contrast)',
  '--secondary': 'var(--color-info-border)',
  '--secondary-subtle': 'var(--color-info-bg)',
  '--secondary-soft': 'var(--color-info-bg)',
  '--secondary-muted': 'var(--color-info-border)',
  '--secondary-strong': 'var(--color-info-fg)',
  '--secondary-emphasis': 'var(--color-info-fg)',
  '--secondary-intense': 'var(--color-info-fg)',
  '--secondary-foreground': 'var(--color-info-fg)',
  '--error': 'var(--color-danger-fg)',
  '--error-subtle': 'var(--color-danger-bg)',
  '--error-soft': 'var(--color-danger-bg)',
  '--error-muted': 'var(--color-danger-border)',
  '--error-strong': 'var(--color-danger-fg)',
  '--error-emphasis': 'var(--color-danger-fg)',
  '--error-intense': 'var(--color-danger-fg)',
  '--error-foreground': 'var(--color-danger-contrast)',
  '--success': 'var(--color-success-fg)',
  '--success-subtle': 'var(--color-success-bg)',
  '--success-soft': 'var(--color-success-bg)',
  '--success-muted': 'var(--color-success-border)',
  '--success-strong': 'var(--color-success-fg)',
  '--success-emphasis': 'var(--color-success-fg)',
  '--success-intense': 'var(--color-success-fg)',
  '--success-foreground': 'var(--color-success-contrast)',
  '--warning': 'var(--color-warning-fg)',
  '--warning-subtle': 'var(--color-warning-bg)',
  '--warning-soft': 'var(--color-warning-bg)',
  '--warning-muted': 'var(--color-warning-border)',
  '--warning-strong': 'var(--color-warning-fg)',
  '--warning-emphasis': 'var(--color-warning-fg)',
  '--warning-intense': 'var(--color-warning-fg)',
  '--warning-foreground': 'var(--color-warn-contrast)',
  '--info': 'var(--color-info-fg)',
  '--info-subtle': 'var(--color-info-bg)',
  '--info-soft': 'var(--color-info-bg)',
  '--info-muted': 'var(--color-info-border)',
  '--info-strong': 'var(--color-info-fg)',
  '--info-emphasis': 'var(--color-info-fg)',
  '--info-intense': 'var(--color-info-fg)',
  '--info-foreground': 'var(--color-info-contrast)',
  '--focus-ring': 'var(--color-focus-ring)',
  '--focus-ring-input': 'var(--color-focus-ring)',
  '--focus-ring-primary': 'var(--color-focus-ring)',
  '--focus-ring-secondary': 'var(--color-info-border)',
  '--focus-ring-error': 'var(--color-danger-border)',
  '--focus-ring-success': 'var(--color-success-border)',
  '--focus-ring-warning': 'var(--color-warning-border)',
  '--focus-ring-info': 'var(--color-info-border)',
  '--focus-ring-light': 'var(--color-accent-contrast)',
  '--font-sans': 'var(--font-family)',
  '--font-mono': 'var(--font-family-mono)',
  '--spacing': 'var(--space-1)',
  '--text-xs': 'var(--font-size-caption)',
  '--text-xs--line-height': 'var(--line-height-caption)',
  '--text-sm': 'var(--font-size-body)',
  '--text-sm--line-height': 'var(--line-height-body)',
  '--text-base': 'var(--font-size-body-lg)',
  '--text-base--line-height': 'var(--line-height-body-lg)',
  '--text-lg': 'var(--font-size-title-3)',
  '--text-lg--line-height': 'var(--line-height-title-3)',
  '--text-xl': 'var(--font-size-title-2)',
  '--text-xl--line-height': 'var(--line-height-title-2)',
  '--text-2xl': 'var(--font-size-title-1)',
  '--text-2xl--line-height': 'var(--line-height-title-1)',
  '--text-3xl': 'var(--font-size-display-sm)',
  '--text-3xl--line-height': 'var(--line-height-display-sm)',
  '--text-4xl': 'var(--font-size-display-lg)',
  '--text-4xl--line-height': 'var(--line-height-display-lg)',
  '--border-width': '1px',
  '--radius': 'var(--radius-lg)',
  '--radius-4xs': 'var(--radius-xs)',
  '--radius-3xs': 'var(--radius-xs)',
  '--radius-2xs': 'var(--radius-sm)',
  '--radius-2xl': 'var(--radius-xl)',
  '--radius-3xl': 'var(--radius-xl)',
  '--radius-4xl': 'var(--radius-full)',
  '--shadow-color': 'color-mix(in srgb, var(--color-text-muted) 18%, transparent)',
  '--shadow-2xs': 'var(--shadow-1)',
  '--shadow-xs': 'var(--shadow-1)',
  '--shadow-sm': 'var(--shadow-1)',
  '--shadow-md': 'var(--shadow-2)',
  '--shadow-lg': 'var(--shadow-2)',
  '--shadow-xl': 'var(--shadow-3)',
  '--shadow-2xl': 'var(--shadow-3)',
  '--shadow': 'var(--shadow-2)',
  '--selection-color': 'var(--color-selection-bg)',
  '--opacity-disabled': '0.55',
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
 * 瞬态 pressed 表面与 disabled 态不参与正文对比判定(WCAG 豁免),不登记。
 */
export const AA_CONTRAST_PAIRS: ReadonlyArray<ContrastPair> = [
  // 强文本 对 全部静态表面(text 组,≥4.5:1)
  { fg: '--color-text-strong', bg: '--color-bg' },
  { fg: '--color-text-strong', bg: '--color-canvas' },
  { fg: '--color-text-strong', bg: '--color-surface' },
  { fg: '--color-text-strong', bg: '--color-surface-subtle' },
  { fg: '--color-text-strong', bg: '--color-surface-raised' },
  { fg: '--color-text-strong', bg: '--color-surface-hover' },
  { fg: '--color-text-strong', bg: '--color-surface-pressed' },
  { fg: '--color-text-strong', bg: '--color-surface-selected' },
  // 正文 对 背景与常规表面(text 组)
  { fg: '--color-text', bg: '--color-bg' },
  { fg: '--color-text', bg: '--color-canvas' },
  { fg: '--color-text', bg: '--color-surface' },
  { fg: '--color-text', bg: '--color-surface-subtle' },
  { fg: '--color-text', bg: '--color-surface-raised' },
  { fg: '--color-text', bg: '--color-surface-hover' },
  { fg: '--color-text', bg: '--color-surface-pressed' },
  { fg: '--color-text', bg: '--color-surface-selected' },
  { fg: '--color-text', bg: '--color-surface-sunken' },
  // 弱化文本 对 静态表面(text 组;pressed 为瞬态不登记)
  { fg: '--color-text-muted', bg: '--color-bg' },
  { fg: '--color-text-muted', bg: '--color-canvas' },
  { fg: '--color-text-muted', bg: '--color-surface' },
  { fg: '--color-text-muted', bg: '--color-surface-subtle' },
  { fg: '--color-text-muted', bg: '--color-surface-raised' },
  { fg: '--color-text-muted', bg: '--color-surface-hover' },
  { fg: '--color-text-muted', bg: '--color-surface-sunken' },
  // 强调色作链接/文本(text 组)
  { fg: '--color-accent', bg: '--color-bg' },
  { fg: '--color-accent', bg: '--color-surface' },
  { fg: '--color-accent', bg: '--color-surface-raised' },
  { fg: '--color-accent', bg: '--color-accent-soft' },
  { fg: '--color-accent-hover', bg: '--color-surface' },
  // 强调色作底时其对比文本(text 组;pressed 瞬态不登记)
  { fg: '--color-accent-contrast', bg: '--color-accent' },
  { fg: '--color-accent-contrast', bg: '--color-accent-hover' },
  { fg: '--color-text-strong', bg: '--color-accent-soft' },
  // 旧主色别名(text 组)
  { fg: '--color-primary', bg: '--color-bg' },
  { fg: '--color-primary-contrast', bg: '--color-primary' },
  { fg: '--color-primary', bg: '--color-primary-bg' },
  // 状态三元组 fg 对 bg(text 组)
  { fg: '--color-success-fg', bg: '--color-success-bg' },
  { fg: '--color-warning-fg', bg: '--color-warning-bg' },
  { fg: '--color-danger-fg', bg: '--color-danger-bg' },
  { fg: '--color-info-fg', bg: '--color-info-bg' },
  { fg: '--color-neutral-fg', bg: '--color-neutral-bg' },
  // 危险按钮交互态文本(text 组)
  { fg: '--color-danger-contrast', bg: '--color-danger-fg' },
  { fg: '--color-danger-contrast', bg: '--color-danger-hover' },
  { fg: '--color-danger-contrast', bg: '--color-danger-pressed' },
  // 旧状态色别名(text 组)
  { fg: '--color-danger', bg: '--color-bg' },
  { fg: '--color-warn', bg: '--color-bg' },
  { fg: '--color-success', bg: '--color-bg' },
  { fg: '--color-info', bg: '--color-bg' },
  { fg: '--color-danger-contrast', bg: '--color-danger' },
  { fg: '--color-warn-contrast', bg: '--color-warn' },
  { fg: '--color-success-contrast', bg: '--color-success' },
  { fg: '--color-info-contrast', bg: '--color-info' },
  { fg: '--color-danger', bg: '--color-danger-bg' },
  { fg: '--color-warn', bg: '--color-warn-bg' },
  { fg: '--color-success', bg: '--color-success-bg' },
  { fg: '--color-info', bg: '--color-info-bg' },
  // 头像八组 fg 对 bg(text 组,亮/暗各自校准)
  { fg: '--color-avatar-h0-fg', bg: '--color-avatar-h0-bg' },
  { fg: '--color-avatar-h1-fg', bg: '--color-avatar-h1-bg' },
  { fg: '--color-avatar-h2-fg', bg: '--color-avatar-h2-bg' },
  { fg: '--color-avatar-h3-fg', bg: '--color-avatar-h3-bg' },
  { fg: '--color-avatar-h4-fg', bg: '--color-avatar-h4-bg' },
  { fg: '--color-avatar-h5-fg', bg: '--color-avatar-h5-bg' },
  { fg: '--color-avatar-h6-fg', bg: '--color-avatar-h6-bg' },
  { fg: '--color-avatar-h7-fg', bg: '--color-avatar-h7-bg' },
  // 选区/强调与代码高亮(text 组)
  { fg: '--color-selection-text', bg: '--color-selection-bg' },
  { fg: '--color-mark-text', bg: '--color-mark-bg' },
  { fg: '--color-code-text', bg: '--color-code-bg' },
  { fg: '--color-code-keyword', bg: '--color-code-bg' },
  { fg: '--color-code-string', bg: '--color-code-bg' },
  { fg: '--color-code-comment', bg: '--color-code-bg' },
  // 大文本组(≥24px,或 ≥18.66px 加粗;阈值 3:1)
  { fg: '--color-primary', bg: '--color-surface', kind: 'large-text' },
  { fg: '--color-accent', bg: '--color-surface-subtle', kind: 'large-text' },
  { fg: '--color-text-muted', bg: '--color-surface-raised', kind: 'large-text' },
  // 图形元件组(阈值 3:1)
  { fg: '--color-focus-ring', bg: '--color-bg' },
  { fg: '--color-focus-ring', bg: '--color-canvas' },
  { fg: '--color-skeleton-base', bg: '--color-bg', kind: 'graphic' },
  { fg: '--color-skeleton-base', bg: '--color-canvas', kind: 'graphic' },
];
