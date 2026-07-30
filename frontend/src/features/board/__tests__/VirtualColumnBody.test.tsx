/**
 * 虚拟化列体测试(design-quality §11.4 / kanban §5.3):
 * 仅渲染可见窗口、aria-setsize/aria-posinset、越窗激活项仍渲染、滚动平移窗口、
 * shouldVirtualize 阈值(200)。
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { VirtualColumnBody, shouldVirtualize } from '../VirtualColumnBody';
import type { VirtualItemA11y } from '../VirtualColumnBody';

/** 可编程 ResizeObserver:捕获回调以注入实测视口高度。 */
let roCallback: (() => void) | null = null;
class ProgrammableRO {
  constructor(cb: () => void) {
    roCallback = cb;
  }
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
const originalResizeObserver = window.ResizeObserver;

const cards = Array.from({ length: 300 }, (_, i) => ({ id: `c${i}` }));

/** 卡片即 listitem,AT 坐标经第三参承载(与 BoardColumns 契约一致)。 */
function renderItem(card: { id: string }, _index: number, a11y: VirtualItemA11y): React.JSX.Element {
  return (
    <div
      data-testid={`item-${card.id}`}
      role="listitem"
      aria-setsize={a11y.setsize}
      aria-posinset={a11y.posinset}
    >
      {card.id}
    </div>
  );
}

function renderVirtual(items = cards, activeCardId: string | null = null) {
  renderWithProviders(
    <VirtualColumnBody cards={items} activeCardId={activeCardId} renderCard={renderItem} />,
  );
}

describe('VirtualColumnBody 虚拟化渲染', () => {
  it('300 卡片仅渲染可见窗口(600px 视口 / 84px 卡 + overscan)', () => {
    renderVirtual();
    const items = screen.getAllByRole('listitem');
    // 可见 ceil(600/84)=8 + overscan 3(前)+ 3(后)= start0,end11 → 11 项。
    expect(items).toHaveLength(11);
    expect(screen.getByTestId('item-c0')).toBeInTheDocument();
    expect(screen.queryByTestId('item-c100')).not.toBeInTheDocument();
  });

  it('窗口项携带 aria-setsize / aria-posinset(AT 可达,§10.2)', () => {
    renderVirtual();
    const items = screen.getAllByRole('listitem');
    expect(items[0]).toHaveAttribute('aria-setsize', '300');
    expect(items[0]).toHaveAttribute('aria-posinset', '1');
    expect(items[10]).toHaveAttribute('aria-posinset', '11');
  });

  it('越出窗口的激活项仍被渲染(键盘移动/聚焦不破)', () => {
    renderVirtual(cards, 'c200');
    expect(screen.getByTestId('item-c200')).toBeInTheDocument();
    const active = screen.getByTestId('item-c200').closest('[aria-posinset]');
    expect(active).toHaveAttribute('aria-posinset', '201');
  });

  it('滚动后窗口平移', () => {
    renderVirtual();
    const container = screen.getByTestId('virtual-column-body');
    fireEvent.scroll(container, { target: { scrollTop: 8400 } });
    // rawStart=100 → start 97;item-c97 出现,item-c0 离开窗口。
    expect(screen.getByTestId('item-c97')).toBeInTheDocument();
    expect(screen.queryByTestId('item-c0')).not.toBeInTheDocument();
  });

  it('空列表渲染 0 项', () => {
    renderVirtual([]);
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
  });

  it('落点指示线按 index 移位;null 置于列尾;未提供不渲染', () => {
    const indicator = <div data-testid="board-drop-indicator" />;
    const { rerender } = render(
      <VirtualColumnBody cards={cards} activeCardId={null} renderCard={renderItem} />,
    );
    expect(screen.queryByTestId('board-drop-indicator')).toBeNull();
    // index=2 → top = 2 × 84 = 168px。
    rerender(
      <VirtualColumnBody
        cards={cards}
        activeCardId={null}
        renderCard={renderItem}
        indicatorNode={indicator}
        indicatorIndex={2}
      />,
    );
    const positioned = screen.getByTestId('board-drop-indicator').closest('div[style*="top"]');
    expect(positioned?.getAttribute('style')).toContain('top: 168px');
    // null → 列尾:300 × 84 = 25200px。
    rerender(
      <VirtualColumnBody
        cards={cards}
        activeCardId={null}
        renderCard={renderItem}
        indicatorNode={indicator}
        indicatorIndex={null}
      />,
    );
    const tail = screen.getByTestId('board-drop-indicator').closest('div[style*="top"]');
    expect(tail?.getAttribute('style')).toContain('top: 25200px');
  });

  it('激活项在视口上方 → 向上滚动定位;在视口内 → 不滚动', () => {
    const { rerender } = renderWithProvidersAndRerender();
    const container = screen.getByTestId('virtual-column-body');
    // 注入真实视口高度,使「在视口内」分支成立。
    Object.defineProperty(container, 'clientHeight', { configurable: true, value: 600 });
    // 先滚到下方(index 100 起)。
    fireEvent.scroll(container, { target: { scrollTop: 8400 } });
    // 激活 c0(位于当前滚动位置上方)→ scrollTop 归 0。
    rerender(<VirtualColumnBody cards={cards} activeCardId="c0" renderCard={renderItem} />);
    expect(container.scrollTop).toBe(0);
    // 激活视口内卡片(c1)→ 不再滚动(scrollTop 保持 0)。
    rerender(<VirtualColumnBody cards={cards} activeCardId="c1" renderCard={renderItem} />);
    expect(container.scrollTop).toBe(0);
  });
});

/** 提供 rerender 的渲染辅助(滚动定位用例;组件无上下文依赖,用裸 render 以保留 rerender 树)。 */
function renderWithProvidersAndRerender() {
  const utils = render(
    <VirtualColumnBody cards={cards} activeCardId={null} renderCard={renderItem} />,
  );
  return {
    rerender: (ui: React.ReactElement) => utils.rerender(ui),
  };
}

describe('shouldVirtualize 阈值', () => {
  it('200 为界:199 否 / 200 是', () => {
    expect(shouldVirtualize(199)).toBe(false);
    expect(shouldVirtualize(200)).toBe(true);
    expect(shouldVirtualize(1000)).toBe(true);
  });
});

describe('VirtualColumnBody 实测视口高度', () => {
  afterEach(() => {
    window.ResizeObserver = originalResizeObserver;
  });

  it('ResizeObserver 注入视口高度后按真实视口计算窗口', () => {
    window.ResizeObserver = ProgrammableRO as unknown as typeof ResizeObserver;
    renderVirtual();
    const container = screen.getByTestId('virtual-column-body');
    Object.defineProperty(container, 'clientHeight', { configurable: true, value: 168 });
    act(() => {
      roCallback?.();
    });
    // 视口 168px:可见 ceil(168/84)=2 + overscan(前 3 后 3)→ start0 end5 → 5 项。
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
  });
});
