/**
 * 响应式模式的 TypeScript 入口(design-quality.md §8.1)。
 * 纯视觉布局仍由 CSS media/container query 负责；需要在测试、测量或非视觉逻辑
 * 中描述视口模式时统一调用这里，禁止组件散落窗口宽度魔数。
 */
import { VIEWPORT_BREAKPOINTS } from './tokenValues';

export type ViewportMode = 'compact' | 'medium' | 'wide' | 'xwide';

export { VIEWPORT_BREAKPOINTS };

export function viewportMode(width: number): ViewportMode {
  if (!Number.isFinite(width) || width < 0) {
    throw new RangeError('viewport width must be a finite non-negative number');
  }
  if (width <= VIEWPORT_BREAKPOINTS.compact.max) return 'compact';
  if (width <= VIEWPORT_BREAKPOINTS.medium.max) return 'medium';
  if (width <= VIEWPORT_BREAKPOINTS.wide.max) return 'wide';
  return 'xwide';
}
