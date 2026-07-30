/**
 * 移动端紧凑看板(design-quality §8.3)。
 *
 * 容器 ≤599px 时呈现:仅一个泳道。
 * - 顶部横向 chips 切列(状态点 + 名称 + 计数),可横滚,chips 为可聚焦 button;
 * - 上一个/下一个 IconButton(chevron-left/right,带 label);
 * - 卡片区左右滑动切列(指针式,阈值 50px,仅水平向,不与纵向滚动冲突);
 * - 当前列内快速创建 + 长按移动仍可用。
 *
 * 结构(而非纯视觉)切换,故使用 ResizeObserver hook(文档化:结构性的)。
 */
/* eslint-disable react-refresh/only-export-components -- useContainerWidth 与紧凑组件同模块契约 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon, IconButton } from '../../design';
import { useT } from '../../i18n';
import type { BoardCard } from './projection';
import type { BoardColumn } from './types';
import './board-drag.css';

/** 滑动切列水平阈值(px)。 */
const SWIPE_THRESHOLD = 50;

interface BoardCompactProps {
  readonly columns: readonly BoardColumn[];
  readonly cardsByKey: Readonly<Record<string, readonly BoardCard[]>>;
  readonly activeIndex: number;
  readonly onSelectIndex: (index: number) => void;
  readonly getColumnLabel: (key: string) => string;
  readonly renderCardBody: (column: BoardColumn) => React.ReactNode;
}

/**
 * 容器宽度观察 hook(ResizeObserver)。
 * 文档化说明:紧凑 vs 完整是结构性切换(渲染的 DOM 不同),非纯视觉,
 * 故 §11.2「纯视觉勿用 JS 窗宽」不适用于此结构判定。
 */
export function useContainerWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (el === null) return;
    const measure = (): void => setWidth(el.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

export function BoardCompact(props: BoardCompactProps): React.JSX.Element {
  const { columns, activeIndex, onSelectIndex, getColumnLabel, renderCardBody } = props;
  const t = useT();
  const swipeRef = useRef<{ startX: number; startY: number } | null>(null);

  const goPrev = useCallback(() => {
    onSelectIndex((activeIndex - 1 + columns.length) % columns.length);
  }, [activeIndex, columns.length, onSelectIndex]);

  const goNext = useCallback(() => {
    onSelectIndex((activeIndex + 1) % columns.length);
  }, [activeIndex, columns.length, onSelectIndex]);

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    swipeRef.current = { startX: event.clientX, startY: event.clientY };
  }, []);

  const onPointerUp = useCallback(
    (event: React.PointerEvent) => {
      const start = swipeRef.current;
      swipeRef.current = null;
      if (start === null) return;
      const dx = event.clientX - start.startX;
      const dy = event.clientY - start.startY;
      // 仅水平占优(横向位移大于纵向)且超阈值才切列,避免与纵向滚动冲突。
      if (Math.abs(dx) < SWIPE_THRESHOLD || Math.abs(dx) <= Math.abs(dy)) return;
      if (dx < 0) goNext();
      else goPrev();
    },
    [goNext, goPrev],
  );

  const activeColumn = columns[activeIndex] ?? null;

  return (
    <div className="mesh-board-compact" data-testid="board-compact">
      <div className="mesh-board-compact__nav">
        <IconButton
          label={t('board.compactPrev')}
          className="mesh-board-compact__arrow"
          data-testid="compact-prev"
          onClick={goPrev}
        >
          <Icon name="chevron-left" size={20} />
        </IconButton>
        <div
          className="mesh-board-compact__chips"
          role="tablist"
          aria-label={t('board.compactChipsLabel')}
          data-testid="compact-chips"
        >
          {columns.map((column, index) => (
            <button
              key={column.key}
              type="button"
              role="tab"
              aria-selected={index === activeIndex}
              className={`mesh-board-compact__chip ${index === activeIndex ? 'mesh-board-compact__chip--active' : ''}`.trim()}
              data-testid={`compact-chip-${column.key}`}
              onClick={() => onSelectIndex(index)}
            >
              <span className={`mesh-board__dot mesh-board__dot--${column.key}`} aria-hidden="true" />
              <span>{getColumnLabel(column.key)}</span>
              <span className="mesh-board-compact__chip-count">{column.count}</span>
            </button>
          ))}
        </div>
        <IconButton
          label={t('board.compactNext')}
          className="mesh-board-compact__arrow"
          data-testid="compact-next"
          onClick={goNext}
        >
          <Icon name="chevron-right" size={20} />
        </IconButton>
      </div>

      {activeColumn === null ? null : (
        <div
          className="mesh-board-compact__body"
          data-testid="compact-body"
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
        >
          {renderCardBody(activeColumn)}
        </div>
      )}
    </div>
  );
}
