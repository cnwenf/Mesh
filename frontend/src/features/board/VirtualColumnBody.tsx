/**
 * 虚拟化列体(design-quality §11.4 / kanban §5.3:1000 卡片 ≥50fps,焦点/AT 不破)。
 *
 * 包装层:测量滚动容器高度,仅渲染可见窗口 + spacer transform;
 * 每个卡片携带 aria-setsize/aria-posinset 与 role=listitem;
 * 聚焦/激活的卡片即使越出窗口也始终渲染;聚焦时 scroll-into-view。
 *
 * 卡片须等高(标题单行截断 + 固定高度,CARD_HEIGHT 常量)。
 */
/* eslint-disable react-refresh/only-export-components -- shouldVirtualize 与虚拟化组件同模块契约 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { CARD_HEIGHT, VIRTUALIZE_THRESHOLD, computeVirtualWindow } from './useVirtualWindow';

interface VirtualColumnBodyProps {
  readonly cards: readonly { readonly id: string }[];
  /** 渲染单张卡片(index 为原始数据 index)。 */
  readonly renderCard: (card: { readonly id: string }, index: number) => ReactNode;
  /** 当前聚焦/激活卡片 id(越窗仍渲染)。 */
  readonly activeCardId: string | null;
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
  const { cards, renderCard, activeCardId } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);

  // 测量滚动容器视口高度(ResizeObserver)。
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
      <div
        key={card.id}
        role="listitem"
        aria-setsize={cards.length}
        aria-posinset={i + 1}
        style={{ height: `${CARD_HEIGHT}px`, boxSizing: 'border-box' }}
      >
        {renderCard(card, i)}
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
          role="listitem"
          aria-setsize={cards.length}
          aria-posinset={activeIndex + 1}
          style={{ height: `${CARD_HEIGHT}px`, boxSizing: 'border-box' }}
        >
          {renderCard(card, activeIndex)}
        </div>
      );
    }
  }

  return (
    <div
      ref={containerRef}
      className="mesh-board__virtual"
      data-testid="virtual-column-body"
      onScroll={handleScroll}
      style={{ height: '100%', overflowY: 'auto', position: 'relative' }}
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
      </div>
    </div>
  );
}

/** 是否应启用虚拟化(列内卡片数 ≥ 阈值)。 */
export function shouldVirtualize(cardCount: number): boolean {
  return cardCount >= VIRTUALIZE_THRESHOLD;
}
