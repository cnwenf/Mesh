/**
 * 虚拟化列体(design-quality §11.4 / kanban §5.3:1000 卡片 ≥50fps,焦点/AT 不破)。
 *
 * 包装层测量自身视口高度(flex:1 的确定高度,见 board.css 高度链),仅渲染可见
 * 窗口 + spacer transform。aria-setsize/aria-posinset 经 renderCard 第三参交给
 * 卡片本身承载(卡片即 role=listitem),包装层不再叠加 listitem,避免 AT 树
 * listitem>listitem 双重嵌套(验收 Low 项)。
 *
 * 聚焦/激活的卡片即使越出窗口也始终渲染;聚焦时 scroll-into-view。
 * 卡片须经 CSS 约束等高(.mesh-board__virtual .mesh-board__card,CARD_HEIGHT 常量)。
 */
/* eslint-disable react-refresh/only-export-components -- shouldVirtualize 与虚拟化组件同模块契约 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { CARD_HEIGHT, VIRTUALIZE_THRESHOLD, computeVirtualWindow } from './useVirtualWindow';

/** 虚拟化 a11y 坐标(承载于卡片 listitem 本身)。 */
export interface VirtualItemA11y {
  readonly setsize: number;
  readonly posinset: number;
}

interface VirtualColumnBodyProps {
  readonly cards: readonly { readonly id: string }[];
  /** 渲染单张卡片(index 为原始数据 index;virtualA11y 提供 setsize/posinset)。 */
  readonly renderCard: (
    card: { readonly id: string },
    index: number,
    virtualA11y: VirtualItemA11y,
  ) => ReactNode;
  /** 当前聚焦/激活卡片 id(越窗仍渲染)。 */
  readonly activeCardId: string | null;
  /** 拖拽落点指示线节点(自带样式/testid)与插入 index(null = 列尾;undefined = 不显示)。 */
  readonly indicatorNode?: ReactNode;
  readonly indicatorIndex?: number | null;
}

/** 聚焦卡片超出窗口时的滚动定位。 */
function scrollToCard(container: HTMLElement, index: number, viewportHeight: number): void {
  const top = index * CARD_HEIGHT;
  const bottom = top + CARD_HEIGHT;
  if (top < container.scrollTop) {
    container.scrollTop = top;
  } else if (bottom > container.scrollTop + viewportHeight) {
    container.scrollTop = bottom - viewportHeight;
  }
}

export function VirtualColumnBody(props: VirtualColumnBodyProps): React.JSX.Element {
  const { cards, renderCard, activeCardId, indicatorNode, indicatorIndex } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);

  // 测量滚动容器视口高度(ResizeObserver;flex 确定高度链见 board.css)。
  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;
    const measure = (): void => setViewportHeight(container.clientHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    setScrollTop(event.currentTarget.scrollTop);
  }, []);

  const window = computeVirtualWindow({
    itemCount: cards.length,
    itemHeight: CARD_HEIGHT,
    viewportHeight: viewportHeight === 0 ? 600 : viewportHeight,
    scrollTop,
  });

  // 激活卡片越窗仍渲染。
  const activeIndex = activeCardId === null ? -1 : cards.findIndex((c) => c.id === activeCardId);
  const activeOutOfWindow = activeIndex >= 0 && (activeIndex < window.start || activeIndex >= window.end);

  // 聚焦时 scroll-into-view。
  useEffect(() => {
    const container = containerRef.current;
    if (container === null || activeIndex < 0) return;
    scrollToCard(container, activeIndex, container.clientHeight);
  }, [activeIndex]);

  const visible: ReactNode[] = [];
  for (let i = window.start; i < window.end; i++) {
    const card = cards[i];
    if (card === undefined) continue;
    visible.push(
      <div key={card.id} style={{ height: `${CARD_HEIGHT}px`, boxSizing: 'border-box' }}>
        {renderCard(card, i, { setsize: cards.length, posinset: i + 1 })}
      </div>,
    );
  }

  // 激活卡片越窗:追加渲染。
  let activeNode: ReactNode = null;
  if (activeOutOfWindow && activeIndex >= 0) {
    const card = cards[activeIndex];
    if (card !== undefined) {
      activeNode = (
        <div
          key={`active-${card.id}`}
          style={{ height: `${CARD_HEIGHT}px`, boxSizing: 'border-box' }}
        >
          {renderCard(card, activeIndex, { setsize: cards.length, posinset: activeIndex + 1 })}
        </div>
      );
    }
  }

  // 落点指示线:绝对定位于 index × 行高(null → 列尾),随 hit.index 移位(§9.4.2)。
  const resolvedIndicatorIndex = indicatorIndex === null || indicatorIndex === undefined
    ? cards.length
    : Math.min(Math.max(indicatorIndex, 0), cards.length);
  const showIndicator = indicatorNode !== undefined && indicatorIndex !== undefined;

  return (
    <div
      ref={containerRef}
      className="mesh-board__virtual"
      data-testid="virtual-column-body"
      onScroll={handleScroll}
    >
      {/* 总高度 spacer,撑开滚动区域。 */}
      <div style={{ height: `${window.totalHeight}px`, position: 'relative' }}>
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            transform: `translateY(${window.offsetY}px)`,
          }}
        >
          {visible}
          {activeNode}
        </div>
        {showIndicator ? (
          <div
            aria-hidden="true"
            style={{ position: 'absolute', left: 0, right: 0, top: `${resolvedIndicatorIndex * CARD_HEIGHT}px` }}
          >
            {indicatorNode}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** 是否应启用虚拟化(列内卡片数 ≥ 阈值)。 */
export function shouldVirtualize(cardCount: number): boolean {
  return cardCount >= VIRTUALIZE_THRESHOLD;
}
