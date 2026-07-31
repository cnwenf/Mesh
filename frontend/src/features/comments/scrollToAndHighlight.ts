/**
 * 评论定位 + 高亮统一辅助(design-quality.md §9.5.5)。
 * 发表成功滚动到新评论、深链锚点跳转共用同一入口,避免两套滚动逻辑漂移。
 *
 * - 滚动:经 scrollIntoView({block:'center'}) 居中;reduced-motion 时走 auto(无平滑动画)。
 * - 高亮:确保元素带闪烁类(复用 mesh-comments__card--highlight);该类的闪烁动画(~1.2s)
 *   与移除由 React 的 highlighted 状态驱动,本辅助只做幂等追加,不持有定时器,避免与
 *   React 类管理竞态。reduced-motion 下 CSS 关闭动画,类仍在但无动效。
 * 纯 DOM 辅助,可经 jsdom scrollIntoView mock 单测。
 */

/** 高亮闪烁类(与 comment.css 中 .mesh-comments__card--highlight 动画对齐)。 */
export const HIGHLIGHT_CLASS = 'mesh-comments__card--highlight';

export interface ScrollToAndHighlightEnv {
  /** 注入 reduced-motion 判定(测试);缺省读 matchMedia。 */
  readonly prefersReducedMotion?: () => boolean;
}

function defaultPrefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * 滚动到元素并带上高亮类。element 为 null 时为无操作(元素尚未渲染/已删除)。
 * reduced-motion:不平滑滚动(状态仍经位置/高亮类可感知)。
 */
export function scrollToAndHighlight(element: HTMLElement | null, env: ScrollToAndHighlightEnv = {}): void {
  if (element === null) return;
  const reducedMotion = (env.prefersReducedMotion ?? defaultPrefersReducedMotion)();
  // 防御:部分环境(如 jsdom)未实现 scrollIntoView;缺失时仅确保高亮类,不抛错。
  if (typeof element.scrollIntoView === 'function') {
    element.scrollIntoView({ block: 'center', behavior: reducedMotion ? 'auto' : 'smooth' });
  }
  element.classList.add(HIGHLIGHT_CLASS);
}
