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
 * 兼容迁移(design-quality.md §11.3):旧 danger/warn/success/info 系作为新语义
 * token 的别名保留一个发布周期,随后删除并经静态扫描阻止回归;别名组取值与
 * 新 token 一致,使存量样式随令牌升级同步换装。primary 已按 MES-108 接受
 * 基线恢复为中性主操作,品牌链接/焦点继续使用 accent 系。
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
    title: '表面层级(design-quality.md §5.2:canvas→surface→raised 三层 + 交互表面)',
    tokens: {
      '--color-canvas': '#fbfbfb',
      '--color-bg': '#f3f3f4',
      '--color-surface': '#ffffff',
      '--color-surface-subtle': '#f4f4f5',
      '--color-surface-raised': '#ffffff',
      '--color-surface-hover': '#f4f4f5',
      '--color-surface-pressed': '#d4d4d8',
      '--color-surface-selected': '#eeeef0',
      '--color-surface-sunken': '#f3f3f4',
    },
  },
  {
    title: '文本层级(strong/text/muted/disabled 四级)',
    tokens: {
      '--color-text-strong': '#09090b',
      '--color-text': '#09090b',
      '--color-text-muted': '#64636e',
      '--color-text-disabled': '#81818b',
      '--color-text-faint-base': '#81818b',
      '--color-placeholder': '#64636e',
      '--color-control-disabled-text': '#8b8b94',
    },
  },
  {
    title: '边界(subtle/border/strong 三级)',
    tokens: {
      '--color-border-subtle': '#ececef',
      '--color-border': '#e4e4e7',
      '--color-border-strong': '#d4d4d8',
      '--color-input-border-base': '#e4e4e7',
      // 原始 input-line 仅 1.27:1；字段边界须满足 WCAG 1.4.11 的 3:1。
      '--color-input-border': '#8b8b94',
      '--color-input-border-hover': '#71717a',
    },
  },
  {
    title: '接受基线原始色(精确追溯;组件使用下方经 AA 校准的语义色)',
    tokens: {
      '--color-brand-base': '#2171cc',
      '--color-brand-soft-base': '#eaf3fd',
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
    },
  },
  {
    title: '品牌强调色(accent 系经 AA 校准,与中性 primary 主操作分离)',
    tokens: {
      // 原始 #2171cc 在 shell 上仅 4.41:1;轻微加深以满足正文 AA。
      '--color-accent': '#206dc4',
      '--color-accent-hover': '#1b5da8',
      '--color-accent-pressed': '#174f8f',
      '--color-accent-soft': '#eef7ff',
      '--color-accent-contrast': '#ffffff',
    },
  },
  {
    title: '中性主操作(primary 系;品牌链接/焦点不得复用)',
    tokens: {
      '--color-primary': '#18181b',
      '--color-primary-contrast': '#fafafa',
      '--color-primary-bg': '#eeeef0',
      '--color-primary-hover': 'color-mix(in srgb, var(--color-primary) 84%, transparent)',
      '--color-primary-pressed': 'color-mix(in srgb, var(--color-primary) 84%, transparent)',
    },
  },
  {
    title: '状态色三元组(fg/bg/border;颜色不作唯一信号,组件须配图标+文案)',
    tokens: {
      // 原始色仍由 *-base 精确保留;前景仅作满足软底正文 AA 的最小加深。
      '--color-success-fg': '#187c29',
      '--color-success-bg': '#eaf7ec',
      '--color-success-border': '#8bcf95',
      '--color-warning-fg': '#896500',
      '--color-warning-bg': '#fff7da',
      '--color-warning-border': '#e4c95f',
      '--color-danger-fg': '#dc000a',
      '--color-danger-bg': '#fff0f1',
      '--color-danger-border': '#ef9da2',
      '--color-info-fg': '#006bc7',
      '--color-info-bg': '#eaf4ff',
      '--color-info-border': '#8cc5ef',
      '--color-neutral-fg': '#64636e',
      '--color-neutral-bg': '#f4f4f5',
      '--color-neutral-border': '#d4d4d8',
    },
  },
  {
    title: '危险按钮交互态(组件级令牌:default/hover/pressed)',
    tokens: {
      '--color-danger-hover': '#c70009',
      '--color-danger-pressed': '#ad0008',
    },
  },
  {
    title: '旧状态色别名(迁移期保留,取值与三元组 fg/bg 一致;-bg 键即三元组同名键,不重复声明)',
    tokens: {
      '--color-danger': '#dc000a',
      '--color-danger-contrast': '#ffffff',
      '--color-warn': '#896500',
      '--color-warn-contrast': '#ffffff',
      '--color-warn-bg': '#fff7da',
      '--color-success': '#187c29',
      '--color-success-contrast': '#ffffff',
      '--color-info': '#006bc7',
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
      '--shell-sidebar-expanded': '256px',
      '--shell-sidebar-collapsed': '64px',
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
      '--radius-lg': '10px',
      '--radius-xl': '14px',
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
      '字体配对(design-quality.md §6.1:Display/UI=Inter+CJK,等宽=SFMono;字体不可用时回退系统栈)',
    tokens: {
      '--font-family':
        "'Inter', ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      '--font-family-display':
        "'Inter', ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      '--font-family-mono': "'SFMono-Regular', Consolas, 'Liberation Mono', monospace",
    },
  },
  {
    title: '阴影(design-quality.md §5.4:1 轻浮起 / 2 浮层 / 3 对话框;暗色配合边框使用)',
    tokens: {
      '--shadow-1': '0 1px 2px rgb(15 23 42 / 4%), 0 1px 1px rgb(15 23 42 / 3%)',
      '--shadow-2': '0 8px 24px rgb(15 23 42 / 8%), 0 2px 6px rgb(15 23 42 / 5%)',
      '--shadow-3': '0 16px 40px rgb(15 23 42 / 14%), 0 3px 10px rgb(15 23 42 / 8%)',
      '--shadow-raised': '0 8px 24px rgb(15 23 42 / 8%), 0 2px 6px rgb(15 23 42 / 5%)',
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
      '--ease-enter': 'cubic-bezier(0.22, 1, 0.36, 1)',
      '--ease-exit': 'cubic-bezier(0.22, 1, 0.36, 1)',
      '--ease-move': 'cubic-bezier(0.22, 1, 0.36, 1)',
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
  '--color-canvas': '#111114',
  '--color-bg': '#0c0c0e',
  '--color-surface': '#18181b',
  '--color-surface-subtle': '#1e1e21',
  '--color-surface-raised': '#1e1e21',
  '--color-surface-hover': '#27272a',
  '--color-surface-pressed': '#3f3f46',
  '--color-surface-selected': '#2d2d31',
  '--color-surface-sunken': '#111114',
  '--color-text-strong': '#fafafa',
  '--color-text': '#fafafa',
  '--color-text-muted': '#9f9fa9',
  '--color-text-disabled': '#7f7f89',
  '--color-text-faint-base': '#7f7f89',
  '--color-placeholder': '#9f9fa9',
  '--color-control-disabled-text': '#8b8b94',
  '--color-border-subtle': 'rgb(255 255 255 / 6%)',
  '--color-border': 'rgb(255 255 255 / 10%)',
  '--color-border-strong': 'rgb(255 255 255 / 15%)',
  '--color-input-border-base': 'rgb(255 255 255 / 15%)',
  '--color-input-border': '#71717a',
  '--color-input-border-hover': '#8b8b94',
  '--color-brand-base': '#4390ee',
  '--color-brand-soft-base': 'rgb(67 144 238 / 14%)',
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
  '--color-accent': '#4390ee',
  '--color-accent-hover': '#68a7f3',
  '--color-accent-pressed': '#2f7fdc',
  '--color-accent-soft': '#1e2939',
  '--color-accent-contrast': '#0c0c0e',
  '--color-primary': '#e4e4e7',
  '--color-primary-contrast': '#18181b',
  '--color-primary-bg': '#2d2d31',
  '--color-primary-hover': 'color-mix(in srgb, var(--color-primary) 84%, transparent)',
  '--color-primary-pressed': 'color-mix(in srgb, var(--color-primary) 84%, transparent)',
  '--color-success-fg': '#4aa651',
  '--color-success-bg': '#1f2c23',
  '--color-success-border': '#3f7544',
  '--color-warning-fg': '#cb9400',
  '--color-warning-bg': '#332b17',
  '--color-warning-border': '#8d6b12',
  '--color-danger-fg': '#ff6467',
  '--color-danger-bg': '#382326',
  '--color-danger-border': '#a94b50',
  '--color-info-fg': '#0f92f7',
  '--color-info-bg': '#17293a',
  '--color-info-border': '#16689f',
  '--color-neutral-fg': '#9f9fa9',
  '--color-neutral-bg': '#27272a',
  '--color-neutral-border': 'rgb(255 255 255 / 15%)',
  '--color-danger-hover': '#ff7b7e',
  '--color-danger-pressed': '#ff9294',
  // 旧状态色别名(-bg 键即三元组同名键,不重复声明)
  '--color-danger': '#ff6467',
  '--color-danger-contrast': '#18181b',
  '--color-warn': '#cb9400',
  '--color-warn-contrast': '#18181b',
  '--color-warn-bg': '#332b17',
  '--color-success': '#4aa651',
  '--color-success-contrast': '#18181b',
  '--color-info': '#0f92f7',
  '--color-info-contrast': '#18181b',
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
  '--shadow-1': '0 1px 2px rgb(0 0 0 / 20%), 0 1px 1px rgb(0 0 0 / 16%)',
  '--shadow-2': '0 10px 28px rgb(0 0 0 / 30%), 0 2px 8px rgb(0 0 0 / 18%)',
  '--shadow-3': '0 20px 48px rgb(0 0 0 / 46%), 0 4px 12px rgb(0 0 0 / 28%)',
  '--shadow-raised': '0 10px 28px rgb(0 0 0 / 30%), 0 2px 8px rgb(0 0 0 / 18%)',
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
  // 启用表单 placeholder 不是 disabled 豁免项(text 组)
  { fg: '--color-placeholder', bg: '--color-surface' },
  { fg: '--color-placeholder', bg: '--color-surface-subtle' },
  { fg: '--color-placeholder', bg: '--color-surface-raised' },
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
  // 启用字段边界属于 UI component(graphic 组,WCAG 1.4.11 ≥3:1)
  { fg: '--color-input-border', bg: '--color-canvas', kind: 'graphic' },
  { fg: '--color-input-border', bg: '--color-surface-subtle', kind: 'graphic' },
  { fg: '--color-input-border', bg: '--color-surface-raised', kind: 'graphic' },
  { fg: '--color-input-border-hover', bg: '--color-canvas', kind: 'graphic' },
  { fg: '--color-input-border-hover', bg: '--color-surface-subtle', kind: 'graphic' },
  { fg: '--color-input-border-hover', bg: '--color-surface-raised', kind: 'graphic' },
  { fg: '--color-skeleton-base', bg: '--color-bg', kind: 'graphic' },
  { fg: '--color-skeleton-base', bg: '--color-canvas', kind: 'graphic' },
];
