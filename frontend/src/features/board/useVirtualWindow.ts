/**
 * 虚拟滚动窗口计算(纯 hook,design-quality §11.4 / kanban §5.3)。
 *
 * 给定 itemCount、itemHeight(含间距)、viewportHeight、scrollTop、overscan,
 * 计算可见窗口 {start, end, offsetY, totalHeight}。
 *
 * 超过 200 行评估虚拟化(spec);1000 卡片 ≥50fps。
 */
import { useMemo } from 'react';

/** 虚拟化阈值:列内卡片数 ≥ 此值时启用虚拟滚动(spec '超过 200 行')。 */
export const VIRTUALIZE_THRESHOLD = 200;

/** 卡片固定高度(含 gap),用于虚拟滚动计算。 */
export const CARD_HEIGHT = 84;

/** 虚拟窗口计算结果。 */
export interface VirtualWindow {
  /** 渲染起始 index(含 overscan,已钳制 ≥0)。 */
  readonly start: number;
  /** 渲染结束 index(不含,已钳制 ≤itemCount)。 */
  readonly end: number;
  /** 可见区域顶部偏移(px),用于 spacer transform。 */
  readonly offsetY: number;
  /** 列表总高度(px),用于撑开滚动容器。 */
  readonly totalHeight: number;
}

export interface VirtualWindowInput {
  readonly itemCount: number;
  readonly itemHeight: number;
  readonly viewportHeight: number;
  readonly scrollTop: number;
  readonly overscan?: number;
}

/**
 * 纯计算虚拟窗口(无 hook 版本,便于单元测试直接调用)。
 * 所有值均钳制到合法范围。
 */
export function computeVirtualWindow(input: VirtualWindowInput): VirtualWindow {
  const { itemCount, itemHeight, viewportHeight, scrollTop, overscan = 3 } = input;

  if (itemCount <= 0 || itemHeight <= 0) {
    return { start: 0, end: 0, offsetY: 0, totalHeight: 0 };
  }

  const totalHeight = itemCount * itemHeight;
  const clampedScroll = Math.max(0, Math.min(scrollTop, Math.max(0, totalHeight - viewportHeight)));

  // 可见范围(不含 overscan)。
  const rawStart = Math.floor(clampedScroll / itemHeight);
  const visibleCount = Math.ceil(viewportHeight / itemHeight);
  const rawEnd = rawStart + visibleCount;

  // 加 overscan 并钳制。
  const start = Math.max(0, rawStart - overscan);
  const end = Math.min(itemCount, rawEnd + overscan);
  const offsetY = start * itemHeight;

  return { start, end, offsetY, totalHeight };
}

/**
 * React hook 封装:memo 化虚拟窗口计算。
 */
export function useVirtualWindow(input: VirtualWindowInput): VirtualWindow {
  const { itemCount, itemHeight, viewportHeight, scrollTop, overscan } = input;
  return useMemo(
    () => computeVirtualWindow({ itemCount, itemHeight, viewportHeight, scrollTop, overscan }),
    [itemCount, itemHeight, viewportHeight, scrollTop, overscan],
  );
}
