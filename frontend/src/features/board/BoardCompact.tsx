/**
 * 移动端紧凑看板(design-quality §8.3)。
 *
 * compact 视口(0–599px,§8.1 模式表)时呈现:仅一个泳道。
 * - 顶部横向 chips 切列(状态点 + 名称 + 计数),可横滚,chips 为可聚焦 button;
 * - 上一个/下一个 IconButton(chevron-left/right,带 label);
 * - 卡片区左右滑动切列(指针式,阈值 50px,仅水平向,不与纵向滚动冲突);
 * - 当前列内快速创建 + 长按移动仍可用。
 *
 * 形态判定经 matchMedia 视口模式(§8.1 模式表),而非容器宽度测量——
 * 测量时序在负载下不确定(平板视口下内容器宽可 ≤599,观测回调与截图时机竞争,
 * 看板视觉用例间歇红,验收第 3 轮打回),视口模式即时可得且稳定。
 */
/* eslint-disable react-refresh/only-export-components -- useIsCompactViewport 与紧凑组件同模块契约 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon, IconButton } from '../../design';
import { useT } from '../../i18n';
import type { BoardCard } from './projection';
import type { BoardColumn } from './types';
import './board-drag.css';

/** 滑动切列水平阈值(px)。 */
const SWIPE_THRESHOLD = 50;

/** compact 视口判定媒体(§8.1 模式表:compact = 0–599px)。 */
const COMPACT_MEDIA_QUERY = '(max-width: 599px)';

interface BoardCompactProps {
  readonly columns: readonly BoardColumn[];
  readonly cardsByKey: Readonly<Record<string, readonly BoardCard[]>>;
  readonly activeIndex: number;
  readonly onSelectIndex: (index: number) => void;
  readonly getColumnLabel: (key: string) => string;
  readonly renderCardBody: (column: BoardColumn) => React.ReactNode;
}

/**
 * compact 视口判定 hook(matchMedia)。
 * 文档化说明:紧凑 vs 完整虽为结构性切换(渲染的 DOM 不同),但判定基准是
 * §8.1 视口模式(0–599px),非容器内容宽度;matchMedia 即时可得、窗口变化经
 * change 监听更新,不引入测量时序不确定性(§11.2 禁的是「JS 读窗宽做纯视觉
 * 布局」,视口模式驱动的结构切换不在此列)。
 */
export function useIsCompactViewport(): boolean {
  const matches = (): boolean =>
    typeof window.matchMedia === 'function' && window.matchMedia(COMPACT_MEDIA_QUERY).matches;
  const [isCompact, setIsCompact] = useState(matches);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia(COMPACT_MEDIA_QUERY);
    const onChange = (): void => setIsCompact(media.matches);
    onChange();
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);
  return isCompact;
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
